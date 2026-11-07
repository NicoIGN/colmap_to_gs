import os
import re
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
PANORAMAX_REPLACE_REQUEST=oui
PANORAMAX_QUALITY=HD
BBOX_LAMBERT93="4,3,2,1"
PANORAMAX_API_URL=example.test
SPLAT_NAME=bad.ply
SPLAT_MERGE_STRENGTH=0
SPLAT_FILTER_MODE=aply
SPLAT_CLEANER=bad
CLEANGS_MODE=bad
CLEANGS_MIN_VIEWS=0
CLEANGS_SKY_CONFIDENCE=0.1
CLEANGS_MAX_REMOVAL_FRACTION=1
SPLAT_FILTER_SIZE_FACTOR=1
SPLAT_SPARSE_BBOX_MODE=yes
SPLAT_SPARSE_BBOX_MARGIN=-0.1
SPLATS_PER_IMAGE=0
GROWTH_FACTOR=1
REFINE_STOP_ITER=1.5
REFINE_STOP_NUM_ITER=-1
FLOATER_SUPPRESSION=medium
TRAIN_MASKS=maybe
TRAIN_MASKS_BACKEND=bad
TRAIN_MASKS_MODE=hide
TRAIN_MASKS_CONFIDENCE=0
TRAIN_MASKS_DILATE=0.5
TRAIN_MASKS_MAX_FRACTION=2
SLURM_CPUS_PER_TASK=0
SLURM_BLOCK_PARALLELISM=-1
SLURM_GPU_TYPE=gpu:a40
SLURM_MEMORY=64GB
SLURM_TIME_LIMIT=48:99:00
""", "slurm")
        self.assertEqual(result.returncode, 1)
        for name in ("SFM_MATCHER", "SFM_FEATURES", "SFM_PAIRS", "TRAIN_PRESET",
                     "NUM_BLOCKS", "NUM_ITER", "CAP_MAX", "SFM_THREADS",
                     "SFM_PAIR_MAX_DISTANCE", "OVERLAP_MAX_FRACTION", "CLEAN_GS", "PANORAMAX_REPLACE_REQUEST",
                     "PANORAMAX_QUALITY", "BBOX_LAMBERT93", "PANORAMAX_API_URL",
                     "SPLAT_NAME", "SPLAT_MERGE_STRENGTH", "SLURM_CPUS_PER_TASK",
                     "SPLAT_FILTER_MODE", "SPLAT_FILTER_SIZE_FACTOR",
                     "SPLAT_CLEANER", "CLEANGS_MODE", "CLEANGS_MIN_VIEWS",
                     "CLEANGS_SKY_CONFIDENCE", "CLEANGS_MAX_REMOVAL_FRACTION",
                     "SPLAT_SPARSE_BBOX_MODE", "SPLAT_SPARSE_BBOX_MARGIN",
                     "SPLATS_PER_IMAGE", "GROWTH_FACTOR", "REFINE_STOP_ITER", "REFINE_STOP_NUM_ITER",
                     "FLOATER_SUPPRESSION", "TRAIN_MASKS", "TRAIN_MASKS_BACKEND", "TRAIN_MASKS_MODE",
                     "TRAIN_MASKS_CONFIDENCE", "TRAIN_MASKS_DILATE", "TRAIN_MASKS_MAX_FRACTION",
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
SPLATS_PER_IMAGE=""
GROWTH_FACTOR=""
REFINE_STOP_ITER=""
REFINE_STOP_NUM_ITER=""
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

    def test_example_covers_pipeline_parameters_with_actual_defaults(self):
        example = (ROOT / "config.sh.example").read_text()
        assignments = re.findall(r'^(?:export )?([A-Z][A-Z0-9_]*)="([^"]*)"$',
                                 example, re.MULTILINE)
        values = dict(assignments)
        self.assertEqual(len(values), len(assignments), "Duplicate config parameters")
        required = {"OUTPUT_DIR", "BBOX_LAMBERT93", "NUM_BLOCKS", "NUM_ITER",
                    "CAP_MAX", "TRAIN_PRESET", "SLURM_ENV_SETUP", "SLURM_CONDA_ENV",
                    "SLURM_CONDA_BASE", "MVK_CONFIG_LOG_LEVEL"}
        for script in ("run_pipeline.sh", "launch.sh"):
            defaults = re.findall(
                r'^([A-Z][A-Z0-9_]*)="\$\{\1:-([^}]*)\}"$',
                (ROOT / script).read_text(), re.MULTILINE)
            required.update(name for name, _ in defaults)
            for name, default in defaults:
                self.assertIn(name, values)
                self.assertEqual(values[name], "" if "$" in default else default, name)
        self.assertFalse(required - values.keys(), required - values.keys())
        block_defaults = dict(re.findall(
            r'^([A-Z][A-Z0-9_]*)="\$\{\1:-([^}]*)\}"$',
            (ROOT.parents[1] / "scripts" / "run_spirula_blocks.sh").read_text(),
            re.MULTILINE))
        for name in ("NUM_BLOCKS", "NUM_ITER", "CAP_MAX", "TRAIN_PRESET"):
            self.assertEqual(values[name], block_defaults[name], name)
        self.assertIn('SFM_THREADS="${SFM_THREADS:-${SLURM_CPUS_PER_TASK:-}}"',
                      (ROOT / "run_pipeline.sh").read_text())
        self.assertEqual(values["SFM_THREADS"], "")

    def test_training_masks_require_model_and_labels(self):
        result, _ = self.execute("validate_config.sh", 'TRAIN_MASKS=on\nTRAIN_MASKS_LABELS=" , "\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn("TRAIN_MASKS_MODEL", result.stderr)
        self.assertIn("TRAIN_MASKS_LABELS", result.stderr)
        result, _ = self.execute("validate_config.sh", """
TRAIN_MASKS=on
TRAIN_MASKS_MODEL=/models/segformer
TRAIN_MASKS_LABELS="sky,person,car"
TRAIN_MASKS_MODE=ignore
TRAIN_MASKS_DILATE=0.003
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        result, _ = self.execute("validate_config.sh", """
TRAIN_MASKS=on
TRAIN_MASKS_BACKEND=spirula
TRAIN_MASKS_SPIRULA_MODEL=/models/sam3-q4_0.ggml
TRAIN_MASKS_LABELS="sky,person"
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        result, _ = self.execute("validate_config.sh",
                                "TRAIN_MASKS=on\nTRAIN_MASKS_BACKEND=spirula\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("TRAIN_MASKS_SPIRULA_MODEL", result.stderr)
        self.assertNotIn("TRAIN_MASKS_MODEL doit", result.stderr)

    def test_nonfinite_and_malformed_bbox_rejected(self):
        for bbox in ("nan,0,1,1", "0,0,inf,1", "0,0,1e9999,1",
                     "0,0,1", "0,0,1,1,2", "0,0,0,1"):
            result, _ = self.execute("validate_config.sh", f'BBOX_LAMBERT93="{bbox}"\n')
            self.assertEqual(result.returncode, 1, bbox)
            self.assertIn("BBOX_LAMBERT93", result.stderr)


if __name__ == "__main__":
    unittest.main()
