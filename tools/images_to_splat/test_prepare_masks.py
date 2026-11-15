import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import prepare_masks


class MaskResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.images = self.output / "images"
        self.images.mkdir()
        for name in ("a.jpg", "b.jpg"):
            Image.new("RGB", (2, 2)).save(self.images / name)
        self.masks = self.output / "masks"
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

    def test_generation_failure_is_propagated(self):
        self.generate.side_effect = RuntimeError("license")
        with self.assertRaisesRegex(RuntimeError, "license"):
            prepare_masks.prepare()
        self.assertFalse((self.masks / "masks.json").exists())


if __name__ == "__main__":
    unittest.main()
