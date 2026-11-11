import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mask_policies import generate


class SharedMaskGenerationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = {
            "python": "mask-python", "backend": "spirula", "model": "/model.bin",
            "labels": ["cloud", "sky"], "confidence": 0.3, "dilate": 0.002,
            "max_fraction": 0.9, "device": "gpu",
        }
        self.output = self.root / "masks"
        self.output.mkdir()
        (self.output / "masks.json").write_text(json.dumps({"status": "complete"}))
        environment = patch.dict(os.environ, {
            "SPIRULA_BIN": "spirula", "MASKS_ACCEPT_LICENSE": "true",
        })
        environment.start()
        self.addCleanup(environment.stop)

    @patch("mask_policies.subprocess.run")
    def test_shared_generator_path_and_native_options(self, run):
        generate(self.settings, self.root / "images", self.output)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], [
            "mask-python",
            str(Path(__file__).resolve().parents[3] /
                "tools/segmentation_masks/generate_masks.py"),
        ])
        self.assertTrue(Path(command[1]).is_file())
        self.assertIn("--accept-spirula-license", command)
        self.assertEqual(command[command.index("--spirula-device") + 1], "gpu")
        self.assertEqual(command[command.index("--device") + 1], "auto")
        self.assertEqual(command[command.index("--labels") + 1], "cloud,sky")
        self.assertTrue(run.call_args.kwargs["check"])

    @patch("mask_policies.subprocess.run")
    def test_segmentation_keeps_its_device_without_native_license(self, run):
        self.settings.update(backend="segmentation", device="cuda")
        generate(self.settings, self.root / "images", self.output)
        command = run.call_args.args[0]
        self.assertNotIn("--accept-spirula-license", command)
        self.assertEqual(command[command.index("--spirula-device") + 1], "auto")
        self.assertEqual(command[command.index("--device") + 1], "cuda")

    @patch("mask_policies.subprocess.run")
    def test_incomplete_report_is_rejected(self, run):
        (self.output / "masks.json").write_text('{"status": "partial"}')
        with self.assertRaisesRegex(ValueError, "Masques incomplets"):
            generate(self.settings, self.root / "images", self.output)

    @patch("mask_policies.subprocess.run", side_effect=subprocess.CalledProcessError(7, "mask"))
    def test_generator_failure_is_propagated(self, run):
        with self.assertRaises(subprocess.CalledProcessError):
            generate(self.settings, self.root / "images", self.output)


if __name__ == "__main__":
    unittest.main()
