import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[2] / "tools/prepare/prepare_images.py"


class PhaseScopedSignatureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source, self.output = root / "source", root / "workspace"
        self.source.mkdir()
        Image.new("RGB", (8, 8)).save(self.source / "one.jpg")
        self.env = dict(os.environ, SFM_IMAGES_DIR=str(self.source), OUTPUT_DIR=str(self.output),
                        SFM_IMAGES_RECURSIVE="false", SFM_MASKS="off", TRAIN_MASKS="off",
                        COLMAP_METHOD="spirula", SFM_SP_PREFILTER_NEIGHBORS="16",
                        SFM_MIN_TRI_ANGLE="2", PIPELINE_RESUME="true")
        self.assertEqual(self.run_script().returncode, 0)
        (self.output / ".prepare_done").touch()

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args, **changes):
        env = dict(self.env, **changes)
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              capture_output=True, text=True)

    def marker(self):
        return json.loads((self.output / "inputs.json").read_text())

    def test_spirula_prefilter_change_does_not_block_train_check(self):
        changed = {"SFM_SP_PREFILTER_NEIGHBORS": "8"}
        result = self.run_script("--check", "--phase", "train", **changed)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_script("--phase", "prepare", **changed)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_shared_sfm_change_still_blocks_full_check(self):
        result = self.run_script("--phase", "prepare", SFM_MIN_TRI_ANGLE="4")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_inactive_backend_parameters_are_ignored(self):
        result = self.run_script("--phase", "prepare", SFM_NS_CAMERA_TYPE="fisheye")
        self.assertEqual(result.returncode, 0, result.stderr)

        nerfstudio = {"COLMAP_METHOD": "nerfstudio", "SFM_COLMAP_MATCHING": "exhaustive",
                      "SFM_COLMAP_USE_GPU": "false", "OUTPUT_DIR": str(self.output.parent / "ns")}
        self.assertEqual(self.run_script("--phase", "prepare", **nerfstudio).returncode, 0)
        (self.output.parent / "ns" / ".prepare_done").touch()
        result = self.run_script("--check", "--phase", "train",
                                 SFM_SP_PREFILTER_NEIGHBORS="8", **nerfstudio)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mask_setup_keeps_recorded_sfm_values(self):
        result = self.run_script("--phase", "mask-setup", SFM_MIN_TRI_ANGLE="4")
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = self.marker()["settings"]
        self.assertEqual(settings["SFM_MIN_TRI_ANGLE"], "2")
        self.assertEqual(settings["SFM_SP_PREFILTER_NEIGHBORS"], "16")


if __name__ == "__main__":
    unittest.main()
