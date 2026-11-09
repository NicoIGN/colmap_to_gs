import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_masks as masks


class PrepareMasksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data = root / "data"
        (self.data / "images").mkdir(parents=True)
        (self.data / "images/a.jpg").write_bytes(b"jpeg")
        self.workspace = root / "sfm"
        model = root / "sam3.ggml"
        model.write_bytes(b"model")
        binary = root / "spirula"
        binary.write_bytes(b"binary")
        env = {"PYTHON_BIN": sys.executable, "SPIRULA_BIN": str(binary),
               "TRAIN_MASKS": "on", "TRAIN_MASKS_MODE": "ignore"}
        for prefix in ("TRAIN_MASKS", "SFM_MASKS"):
            for key, value in {
                "LABELS": "sky,person,car", "BACKEND": "spirula",
                "MODEL": "", "SPIRULA_MODEL": str(model), "SPIRULA_DEVICE": "auto",
                "PYTHON": sys.executable, "DEVICE": "auto",
                "CONFIDENCE": "0.5", "DILATE": "0.002", "MAX_FRACTION": "0.95",
            }.items():
                env[f"{prefix}_{key}"] = value
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, env).start()
        self.prepare_images = patch.object(masks, "prepare_images").start()
        self.calls = []

        def generate(settings, images, output):
            output.mkdir(parents=True, exist_ok=True)
            (output / "masks.json").write_text(json.dumps({"status": "complete"}))
            self.calls.append((settings, output.name))

        patch.object(masks, "generate", side_effect=generate).start()

    def run_prepare(self):
        self.workspace.mkdir(exist_ok=True)
        masks.prepare(self.data, self.workspace)

    def test_equal_labels_share_one_generation_regardless_of_order(self):
        os.environ["SFM_MASKS_LABELS"] = "Car,sky, PERSON,sky"
        self.run_prepare()
        self.assertEqual([name for _, name in self.calls], ["train_masks"])
        features = self.workspace / "feature_masks"
        self.assertTrue(features.is_symlink())
        self.assertEqual(features.resolve(), self.workspace / "train_masks")
        self.assertTrue(json.loads((self.workspace / "sfm_mask_policy.json").read_text())
                        ["shared_with_training"])

    def test_distinct_labels_or_thresholds_generate_separately(self):
        os.environ["SFM_MASKS_CONFIDENCE"] = "0.7"
        self.run_prepare()
        self.assertEqual([name for _, name in self.calls], ["train_masks", "feature_masks"])
        self.assertFalse((self.workspace / "feature_masks").is_symlink())

    def test_only_sfm_masks(self):
        os.environ["TRAIN_MASKS"] = "off"
        self.run_prepare()
        self.assertEqual([name for _, name in self.calls], ["feature_masks"])

    def test_completed_sfm_rejects_changed_settings_or_source_images(self):
        self.run_prepare()
        (self.workspace / "sparse").mkdir()
        self.run_prepare()
        before = len(self.calls)
        os.environ["SFM_MASKS_LABELS"] = "sky"
        with self.assertRaisesRegex(ValueError, "CLEAN_GS"):
            self.run_prepare()
        self.assertEqual(len(self.calls), before)
        os.environ["SFM_MASKS_LABELS"] = "sky,person,car"
        (self.data / "images/a.jpg").write_bytes(b"new-image")
        with self.assertRaisesRegex(ValueError, "CLEAN_GS"):
            self.run_prepare()

    def test_existing_unmasked_sfm_and_feature_directory_are_not_overwritten(self):
        self.workspace.mkdir()
        (self.workspace / "features").mkdir()
        with self.assertRaisesRegex(ValueError, "sans signature"):
            self.run_prepare()
        self.assertEqual(self.calls, [])
        (self.workspace / "features").rmdir()
        (self.workspace / "feature_masks").mkdir()
        with self.assertRaisesRegex(ValueError, "dossier réel"):
            self.run_prepare()

    def test_training_policy_can_change_without_rebuilding_sfm(self):
        self.run_prepare()
        (self.workspace / "train_masks/a.jpg.png").write_bytes(b"original-mask")
        (self.workspace / "sparse").mkdir()
        os.environ["TRAIN_MASKS_LABELS"] = "sky"
        self.calls.clear()
        self.run_prepare()
        self.assertEqual([name for _, name in self.calls], ["train_masks", "feature_masks"])
        self.assertFalse((self.workspace / "feature_masks").is_symlink())
        self.assertEqual((self.workspace / "feature_masks/a.jpg.png").read_bytes(),
                         b"original-mask")

    def test_check_only_validates_without_computing(self):
        self.run_prepare()
        self.calls.clear()
        masks.prepare(self.data, self.workspace, check_only=True)
        self.assertEqual(self.calls, [])
        os.environ["TRAIN_MASKS_LABELS"] = "sky"
        with self.assertRaisesRegex(ValueError, "non préparée"):
            masks.prepare(self.data, self.workspace, check_only=True)

    def test_same_labels_different_backend_do_not_share(self):
        model = Path(self.temp.name) / "segformer"
        model.mkdir()
        os.environ["SFM_MASKS_BACKEND"] = "segmentation"
        os.environ["SFM_MASKS_MODEL"] = str(model)
        self.run_prepare()
        self.assertEqual([name for _, name in self.calls], ["train_masks", "feature_masks"])

    def test_stale_symlink_is_removed_without_touching_its_target(self):
        self.workspace.mkdir()
        target = Path(self.temp.name) / "unrelated"
        target.mkdir()
        sentinel = target / "keep.txt"
        sentinel.write_text("keep")
        (self.workspace / "feature_masks").symlink_to(target, target_is_directory=True)
        self.run_prepare()
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual((self.workspace / "feature_masks").resolve(),
                         self.workspace / "train_masks")

    def test_training_symlink_target_is_never_modified(self):
        self.workspace.mkdir()
        target = Path(self.temp.name) / "unrelated"
        target.mkdir()
        (self.workspace / "train_masks").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "refuser de modifier"):
            self.run_prepare()
        self.assertEqual(self.calls, [])

    def test_previous_generator_signature_is_compatible(self):
        self.run_prepare()
        marker = self.workspace / "sfm_mask_policy.json"
        report = json.loads(marker.read_text())
        image = self.data / "images/a.jpg"
        model = Path(os.environ["SFM_MASKS_SPIRULA_MODEL"])
        binary = Path(os.environ["SPIRULA_BIN"]).resolve()
        payload = [masks.policy("SFM_MASKS"),
                   [("images/a.jpg", image.stat().st_size, image.stat().st_mtime_ns)],
                   [], [(str(model), model.stat().st_size, model.stat().st_mtime_ns)],
                   (str(binary), binary.stat().st_size, binary.stat().st_mtime_ns),
                   "5b46a95fa64db889a1d0c16740a4790c1b3e1a7e26e74d2f3145534ad6045379"]
        (self.workspace / "sparse").mkdir()
        for code_hash in (
                "5b46a95fa64db889a1d0c16740a4790c1b3e1a7e26e74d2f3145534ad6045379",
                "6f6363b524a637850821662d19792cf2d4397ce644513c327dd3d52aa773c74e"):
            payload[-1] = code_hash
            report["signature"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            marker.write_text(json.dumps(report))
            self.run_prepare()
            self.assertNotEqual(json.loads(marker.read_text())["signature"], report["signature"])


if __name__ == "__main__":
    unittest.main()
