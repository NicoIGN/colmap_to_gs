import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/masks"))
import masks as prepare_masks


class MaskResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.images = self.output / "images"
        self.images.mkdir()
        for name in ("a.jpg", "b.jpg"):
            Image.new("RGB", (2, 2)).save(self.images / name)
        self.masks = self.output / "train_masks"
        self.masks.mkdir()
        self.policy = dict(backend="spirula", model="/model", confidence=.5,
                           dilate=0., max_fraction=.8, labels=["sky"])
        self.enterContext(patch.dict(os.environ, OUTPUT_DIR=str(self.output), PIPELINE_RESUME="true"))
        self.enterContext(patch.object(prepare_masks, "policies", return_value={
            "SFM_MASKS": self.policy, "TRAIN_MASKS": self.policy,
        }))
        self.generate = self.enterContext(patch.object(prepare_masks, "generate",
                                                       side_effect=self.finish))

    def finish(self, settings, images, directory):
        for image in images.glob("*.jpg"):
            Image.new("L", (2, 2), 255).save(directory / (image.name + ".png"))
        (directory / "masks.json").write_text(json.dumps({
            "status": "complete", "settings": {}, "images": {},
        }))

    def report(self, status, **changes):
        settings = dict(backend="spirula", model="/model", confidence=.5,
                        dilate_fraction=0., max_masked_fraction=.8, labels=["sky"])
        settings.update(changes)
        (self.masks / "masks.json").write_text(json.dumps({
            "status": status, "settings": settings, "images": {},
        }))

    def test_empty_directory_after_license_failure_regenerates(self):
        prepare_masks.prepare()
        self.generate.assert_called_once()
        self.assertTrue((self.masks / "b.jpg.png").is_file())

    def test_partial_generation_resumes(self):
        self.report("partial")
        Image.new("L", (2, 2), 255).save(self.masks / "a.jpg.png")
        prepare_masks.prepare()
        self.generate.assert_called_once()

    def test_separate_mask_policies_resume_independently(self):
        train = dict(self.policy, labels=["person"])
        with patch.object(prepare_masks, "policies", return_value={
            "SFM_MASKS": self.policy, "TRAIN_MASKS": train,
        }):
            self.masks.rmdir()
            (self.output / "feature_masks").mkdir()
            (self.output / "train_masks").mkdir()
            prepare_masks.prepare()
        self.assertEqual(self.generate.call_count, 2)
        self.assertTrue((self.output / "feature_masks/b.jpg.png").is_file())
        self.assertTrue((self.output / "train_masks/b.jpg.png").is_file())

    def test_generated_masks_are_validated_before_sfm(self):
        def incomplete(settings, images, directory):
            Image.new("L", (2, 2), 255).save(directory / "a.jpg.png")
            self.report("complete")
        self.generate.side_effect = incomplete
        with self.assertRaisesRegex(ValueError, "b.jpg.png"):
            prepare_masks.prepare()

    def test_complete_masks_do_not_run_sam_even_with_partial_report(self):
        self.finish(self.policy, self.images, self.masks)
        self.report("partial")
        prepare_masks.prepare()
        self.generate.assert_not_called()

    def test_refuses_corruption_even_after_first_missing_mask(self):
        self.report("partial")
        (self.masks / "b.jpg.png").write_bytes(b"corrupt")
        with self.assertRaises(OSError):
            prepare_masks.prepare()
        self.generate.assert_not_called()

    def test_refuses_missing_masks_without_provenance_or_with_complete_report(self):
        for status in (None, "complete"):
            with self.subTest(status=status):
                if status:
                    self.report(status)
                else:
                    (self.masks / "orphan.txt").write_text("unknown")
                with self.assertRaisesRegex(ValueError, "Masque attendu absent"):
                    prepare_masks.prepare()
                self.generate.assert_not_called()

    def test_refuses_changed_policy_and_downstream_artifacts(self):
        self.report("partial", labels=["person"])
        with self.assertRaisesRegex(ValueError, "Labels"):
            prepare_masks.prepare()
        self.report("partial")
        (self.output / "sfm").mkdir()
        with self.assertRaisesRegex(ValueError, "déjà susceptibles"):
            prepare_masks.prepare()
        self.generate.assert_not_called()

    def test_external_masks_remain_strict(self):
        self.policy.clear()
        self.policy["external_dir"] = str(self.masks)
        with self.assertRaisesRegex(ValueError, "Masque attendu absent"):
            prepare_masks.prepare()
        self.generate.assert_not_called()

    def test_external_masks_are_copied_and_signed_without_generation(self):
        external = self.output / "external"
        external.mkdir()
        for image in self.images.glob("*.jpg"):
            Image.new("L", (2, 2), 255).save(external / (image.name + ".png"))
        self.masks.rmdir()
        self.policy.clear()
        self.policy["external_dir"] = str(external)
        prepare_masks.prepare()
        self.generate.assert_not_called()
        self.assertFalse(self.masks.is_symlink())
        self.assertTrue((self.masks / "a.jpg.png").is_file())
        report = json.loads((self.masks / "masks.json").read_text())
        self.assertEqual(report["settings"]["external_dir"], str(external))

    def test_changed_images_refuse_existing_masks(self):
        prepare_masks.prepare()
        self.generate.assert_called_once()
        prepare_masks.prepare()
        self.generate.assert_called_once()
        (self.images / "a.jpg").write_bytes(b"new-photo")
        with self.assertRaises(ValueError):
            prepare_masks.prepare()
        self.generate.assert_called_once()

    def test_existing_masks_report_verification_progress(self):
        self.finish(None, self.images, self.masks)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            prepare_masks.validate_masks(self.images, self.masks)
        self.assertIn("Vérification des masques", output.getvalue())
        self.assertIn("2/2 (100.0%)", output.getvalue())
        self.assertIn("Vérification des masques terminée : 2 masques vérifiés", output.getvalue())

    def test_shared_policies_link_feature_masks_to_training_masks(self):
        prepare_masks.prepare()
        feature = self.output / "feature_masks"
        self.assertTrue(feature.is_symlink())
        self.assertEqual(feature.resolve(), self.masks.resolve())

    def test_generation_failure_is_propagated(self):
        self.generate.side_effect = RuntimeError("license")
        with self.assertRaisesRegex(RuntimeError, "license"):
            prepare_masks.prepare()
        self.assertFalse((self.masks / "masks.json").exists())

    def test_sfm_marker_refuses_existing_sfm_with_other_policy(self):
        prepare_masks.prepare()
        (self.output / "sfm" / "sparse").mkdir(parents=True)
        other = dict(self.policy, labels=["person"])
        with patch.object(prepare_masks, "policies", return_value={
            "SFM_MASKS": other, "TRAIN_MASKS": other,
        }):
            with self.assertRaisesRegex(ValueError, "autre politique"):
                prepare_masks.prepare()

    def test_check_accepts_prepared_masks_and_rejects_missing_marker(self):
        prepare_masks.prepare()
        settings = prepare_masks.policies()
        prepare_masks.check(self.output, settings, perspective=False)
        (self.output / "sfm" / "sfm_mask_policy.json").unlink()
        with self.assertRaisesRegex(ValueError, "non préparée"):
            prepare_masks.check(self.output, settings, perspective=False)


if __name__ == "__main__":
    unittest.main()
