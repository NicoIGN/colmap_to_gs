import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent


class ValidateConfigTest(unittest.TestCase):
    def execute(self, script, extra="", mode=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = root / "config.sh"
        config.write_text(
            f'OUTPUT_DIR="{root / "output"}"\n'
            'BBOX_LAMBERT93="652600,6863300,652900,6863500"\n'
            'NUM_BLOCKS=3\nNUM_ITER=15000\nCAP_MAX=4000000\nTRAIN_PRESET=3dgs\n'
            + extra
        )
        command = ["bash", str(ROOT / script), str(config)]
        if mode:
            command.append(mode)
        result = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                env=dict(os.environ))
        return result, root

    def test_typo_stops_local_and_slurm_before_side_effects(self):
        for script in ("run_pipeline.sh", "launch.sh"):
            with self.subTest(script=script):
                result, root = self.execute(script, 'SFM_MATCHER="lightblue"\n')
                self.assertEqual(result.returncode, 1)
                self.assertIn("SFM_MATCHER='lightblue'", result.stderr)
                self.assertIn("bruteforce|lightglue", result.stderr)
                self.assertNotIn("sbatch introuvable", result.stderr)
                self.assertFalse((root / "output").exists())

    def test_all_invalid_values_are_reported_together(self):
        result, root = self.execute("validate_config.sh", """
SFM_MATCHER=lightblue
SFM_FEATURES=alikedd
SFM_PAIRS=prefliter
TRAIN_PRESET=3dgz
NUM_BLOCKS=0
NUM_ITER=-1
CAP_MAX=1.5
SFM_THREADS=4.5
SFM_PAIR_MAX_DISTANCE=nan
OVERLAP_MAX_FRACTION=1.1
CLEAN_GS=flase
PANORAMAX_QUALITY=HD
BBOX_LAMBERT93="4,3,2,1"
PANORAMAX_API_URL=example.test
SPLAT_NAME=bad.ply
SPLAT_MERGE_STRENGTH=0
SLURM_CPUS_PER_TASK=0
SLURM_BLOCK_PARALLELISM=-1
SLURM_GPU_TYPE=gpu:a40
SLURM_MEMORY=64GB
SLURM_TIME_LIMIT=48:99:00
""", "slurm")
        self.assertEqual(result.returncode, 1)
        for name in ("SFM_MATCHER", "SFM_FEATURES", "SFM_PAIRS", "TRAIN_PRESET",
                     "NUM_BLOCKS", "NUM_ITER", "CAP_MAX", "SFM_THREADS",
                     "SFM_PAIR_MAX_DISTANCE", "OVERLAP_MAX_FRACTION", "CLEAN_GS",
                     "PANORAMAX_QUALITY", "BBOX_LAMBERT93", "PANORAMAX_API_URL",
                     "SPLAT_NAME", "SPLAT_MERGE_STRENGTH", "SLURM_CPUS_PER_TASK",
                     "SLURM_BLOCK_PARALLELISM", "SLURM_GPU_TYPE", "SLURM_MEMORY",
                     "SLURM_TIME_LIMIT"):
            self.assertIn(name, result.stderr)
        self.assertFalse((root / "output").exists())

    def test_defaults_and_optional_empty_values_are_valid(self):
        for mode in ("local", "slurm"):
            result, root = self.execute("validate_config.sh", """
SFM_PAIR_MAX_DISTANCE=""
SFM_THREADS=""
PANORAMAX_IMAGE_MAX=""
SLURM_BLOCK_PARALLELISM=""
SFM_MATCHER=bruteforce
""", mode)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Paramètres valides", result.stdout)
            self.assertFalse((root / "output").exists())

    def test_documented_example_is_valid_without_installed_tools(self):
        result = subprocess.run(
            ["bash", str(ROOT / "validate_config.sh"), str(ROOT / "config.sh.example"), "slurm"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonfinite_and_malformed_bbox_rejected(self):
        for bbox in ("nan,0,1,1", "0,0,inf,1", "0,0,1e9999,1",
                     "0,0,1", "0,0,1,1,2", "0,0,0,1"):
            result, _ = self.execute("validate_config.sh", f'BBOX_LAMBERT93="{bbox}"\n')
            self.assertEqual(result.returncode, 1, bbox)
            self.assertIn("BBOX_LAMBERT93", result.stderr)


if __name__ == "__main__":
    unittest.main()
