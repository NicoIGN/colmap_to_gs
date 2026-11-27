import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "scripts/helper.sh"


class HelperTest(unittest.TestCase):
    def test_direct_helper_loads_defaults_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["bash", str(HELPER), "COLMAP_METHOD", "HLOC_CAMERA_MODEL"],
                cwd=directory, text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("COLMAP_METHOD", result.stdout)
        self.assertIn("HLOC_CAMERA_MODEL", result.stdout)
        self.assertIn("OPENCV", result.stdout)

    def test_sourced_helper_and_unknown_phase(self):
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; monparam --phase slurm', "_", str(HELPER)],
            text=True, capture_output=True, env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SLURM_PARTITION", result.stdout)
        result = subprocess.run(
            ["bash", str(HELPER), "--phase", "inconnue"],
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Phase inconnue", result.stdout)
