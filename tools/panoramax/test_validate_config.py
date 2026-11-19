import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DEFAULT_FILES = sorted((REPO / "config" / "defaults").glob("*.default.sh")) + [ROOT / "config.default.sh"]


def effective_panoramax_values():
    """Valeurs exportées après chargement des défauts Panoramax, comme le pipeline réel."""
    loader = 'set -a; source "$1"; load_pipeline_defaults "$2" panoramax; set +a; env -0'
    result = subprocess.run(
        ["bash", "-c", loader, "_", str(REPO / "tools/libs/scripts/pipeline_config.sh"), str(REPO)],
        capture_output=True, check=True,
    )
    values = {}
    for item in result.stdout.split(b"\0"):
        name, sep, value = item.partition(b"=")
        if sep:
            values[name.decode()] = value.decode()
    return values


class ValidateConfigTest(unittest.TestCase):
    def test_hloc_360_requires_projection_and_disallows_spirula_pair_filter(self):
        extra = ('COLMAP_METHOD=hloc\nPANORAMAX_PICTURE_TYPE=360\n'
                 'SFM_EQUIRECTANGULAR_VIEWS=true\nTRAIN_BACKEND=nerfstudio\n')
        result, _ = self.execute("validate_config.sh", extra, "slurm")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result, _ = self.execute("validate_config.sh", extra + 'SFM_SP_PAIR_MAX_DISTANCE=50\n', "slurm")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("réservé à Spirula", result.stderr)
        result, _ = self.execute("validate_config.sh",
                                 'COLMAP_METHOD=hloc\nPANORAMAX_PICTURE_TYPE=all\n', "slurm")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PANORAMAX_PICTURE_TYPE=360", result.stderr)

    def test_invalid_geometry_rejected_local_and_slurm(self):
        for script in ("../../scripts/panoramax_to_3dgs.sh", "launch.sh"):
            with self.subTest(script=script):
                result, root = self.execute(script, """
SFM_MAX_ERROR=0
SFM_MIN_INLIERS=1.5
SFM_MIN_TRI_ANGLE=91
SFM_SP_BA_LOSS=bad
SFM_SP_BA_LOSS_PARAM=nan
""")
                self.assertEqual(result.returncode, 1)
                for name in ("SFM_MAX_ERROR", "SFM_MIN_INLIERS", "SFM_MIN_TRI_ANGLE",
                             "SFM_SP_BA_LOSS", "SFM_SP_BA_LOSS_PARAM"):
                    self.assertIn(name, result.stderr)
                self.assertFalse((root / "output").exists())

    def execute(self, script, extra="", mode=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = root / "config.sh"
        config.write_text(
            f'OUTPUT_DIR="{root / "output"}"\n'
            'PANORAMAX_BBOX="652600,6863300,652900,6863500"\n'
            'SPLIT_NUM_BLOCKS=3\nTRAIN_NUM_ITER=15000\nTRAIN_SP_CAP_MAX=4000000\nTRAIN_SP_PRESET=3dgs\n'
            + extra
        )
        command = ["bash", str(ROOT / script), str(config)]
        if mode:
            command.append(mode)
        result = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                env=dict(os.environ))
        return result, root

    def test_typo_stops_local_and_slurm_before_side_effects(self):
        for script in ("../../scripts/panoramax_to_3dgs.sh", "launch.sh"):
            with self.subTest(script=script):
                result, root = self.execute(script, 'SFM_SP_MATCHER="lightblue"\n')
                self.assertEqual(result.returncode, 1)
                self.assertIn("SFM_SP_MATCHER='lightblue'", result.stderr)
                self.assertIn("bruteforce|lightglue", result.stderr)
                self.assertNotIn("sbatch introuvable", result.stderr)
                self.assertFalse((root / "output").exists())

    def test_all_invalid_values_are_reported_together(self):
        result, root = self.execute("validate_config.sh", """
SFM_SP_MATCHER=lightblue
SFM_SP_FEATURES=alikedd
SFM_SP_PAIRS=prefliter
TRAIN_SP_PRESET=3dgz
SPLIT_NUM_BLOCKS=0
TRAIN_NUM_ITER=-1
TRAIN_SP_CAP_MAX=1.5
NUM_CPUS=4.5
SFM_SP_PAIR_MAX_DISTANCE=nan
SPLIT_OVERLAP_MAX_FRACTION=1.1
TRAIN_CLEAN=flase
PANORAMAX_REPLACE_REQUEST=oui
PANORAMAX_QUALITY=HD
PANORAMAX_BBOX="4,3,2,1"
PANORAMAX_BBOX_EPSG=0
PANORAMAX_API_URL=example.test
EXPORT_SPLAT_NAME=bad.ply
MERGE_STRENGTH=0
POSTPROCESS_FILTER_MODE=aply
POSTPROCESS_CLEANER=bad
CLEANGS_MODE=bad
CLEANGS_MIN_VIEWS=0
CLEANGS_SKY_CONFIDENCE=0.1
CLEANGS_MAX_REMOVAL_FRACTION=1
POSTPROCESS_FILTER_SIZE_FACTOR=1
POSTPROCESS_SPARSE_BBOX_MODE=yes
POSTPROCESS_SPARSE_BBOX_MARGIN=-0.1
TRAIN_SP_SPLATS_PER_IMAGE=0
TRAIN_SP_GROWTH_FACTOR=1
TRAIN_SP_REFINE_STOP_ITER=1.5
TRAIN_SP_REFINE_STOP_NUM_ITER=-1
TRAIN_SP_FLOATER_SUPPRESSION=medium
TRAIN_MASKS=maybe
TRAIN_MASKS_BACKEND=bad
TRAIN_MASKS_MODE=hide
MASKS_ACCEPT_LICENSE=maybe
TRAIN_MASKS_CONFIDENCE=-1
TRAIN_MASKS_DILATE=0.5
TRAIN_MASKS_MAX_FRACTION=2
NUM_GPUS=-1
SLURM_GPU_TYPE=gpu:a40
SLURM_MEMORY=64GB
SLURM_TIME_LIMIT=48:99:00
""", "slurm")
        self.assertEqual(result.returncode, 1)
        for name in ("SFM_SP_MATCHER", "SFM_SP_FEATURES", "SFM_SP_PAIRS", "TRAIN_SP_PRESET",
                     "SPLIT_NUM_BLOCKS", "TRAIN_NUM_ITER", "TRAIN_SP_CAP_MAX", "NUM_CPUS",
                     "SFM_SP_PAIR_MAX_DISTANCE", "SPLIT_OVERLAP_MAX_FRACTION", "TRAIN_CLEAN", "PANORAMAX_REPLACE_REQUEST",
                     "PANORAMAX_QUALITY", "PANORAMAX_BBOX", "PANORAMAX_BBOX_EPSG", "PANORAMAX_API_URL",
                     "EXPORT_SPLAT_NAME", "MERGE_STRENGTH",
                     "POSTPROCESS_FILTER_MODE", "POSTPROCESS_FILTER_SIZE_FACTOR",
                     "POSTPROCESS_CLEANER", "CLEANGS_MODE", "CLEANGS_MIN_VIEWS",
                     "CLEANGS_SKY_CONFIDENCE", "CLEANGS_MAX_REMOVAL_FRACTION",
                     "POSTPROCESS_SPARSE_BBOX_MODE", "POSTPROCESS_SPARSE_BBOX_MARGIN",
                     "TRAIN_SP_SPLATS_PER_IMAGE", "TRAIN_SP_GROWTH_FACTOR", "TRAIN_SP_REFINE_STOP_ITER", "TRAIN_SP_REFINE_STOP_NUM_ITER",
                     "TRAIN_SP_FLOATER_SUPPRESSION", "TRAIN_MASKS", "TRAIN_MASKS_BACKEND", "TRAIN_MASKS_MODE",
                     "TRAIN_MASKS_CONFIDENCE", "TRAIN_MASKS_DILATE", "TRAIN_MASKS_MAX_FRACTION",
                     "NUM_GPUS", "SLURM_GPU_TYPE", "SLURM_MEMORY",
                     "SLURM_TIME_LIMIT"):
            self.assertIn(name, result.stderr)
        self.assertFalse((root / "output").exists())

    def test_defaults_and_optional_empty_values_are_valid(self):
        for mode in ("local", "slurm"):
            result, root = self.execute("validate_config.sh", """
SFM_SP_PAIR_MAX_DISTANCE=""
NUM_CPUS=""
PANORAMAX_IMAGE_MAX=""
NUM_GPUS=""
SFM_SP_MATCHER=bruteforce
TRAIN_SP_SPLATS_PER_IMAGE=""
TRAIN_SP_GROWTH_FACTOR=""
TRAIN_SP_REFINE_STOP_ITER=""
TRAIN_SP_REFINE_STOP_NUM_ITER=""
""", mode)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Paramètres valides", result.stdout)
            self.assertFalse((root / "output").exists())

    def test_documented_example_is_valid_without_installed_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.sh"
            config.write_text(
                f'source "{REPO / "tools/libs/scripts/pipeline_config.sh"}"\n'
                f'load_pipeline_defaults "{REPO}" panoramax\n'
                f'OUTPUT_DIR="{root / "output"}"\n'
                'PANORAMAX_BBOX="652600,6863300,652900,6863500"\n'
            )
            result = subprocess.run(
                ["bash", str(ROOT / "validate_config.sh"), str(config), "slurm"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_example_covers_pipeline_parameters_with_actual_defaults(self):
        values = effective_panoramax_values()
        required = {"OUTPUT_DIR", "PANORAMAX_BBOX", "PANORAMAX_BBOX_EPSG", "SPLIT_NUM_BLOCKS", "TRAIN_NUM_ITER",
                    "TRAIN_SP_CAP_MAX", "TRAIN_SP_PRESET", "SLURM_ENV_SETUP", "SLURM_CONDA_ENV",
                    "SLURM_CONDA_BASE", "MVK_CONFIG_LOG_LEVEL"}
        for script in ("../../scripts/panoramax_to_3dgs.sh", "launch.sh"):
            defaults = re.findall(
                r'^([A-Z][A-Z0-9_]*)="\$\{\1:-([^}]*)\}"$',
                (ROOT / script).read_text(), re.MULTILINE)
            required.update(name for name, _ in defaults)
            for name, default in defaults:
                self.assertIn(name, values)
                self.assertEqual(values[name], "" if "$" in default else default, name)
        self.assertFalse(required - values.keys(), required - values.keys())
        block_defaults = {}
        for name, default in re.findall(
                r'^\s*([A-Z][A-Z0-9_]*)="\$\{\1:-([^}]*)\}"$',
                (ROOT.parents[1] / "tools/libs/scripts" / "run_spirula_blocks.sh").read_text(),
                re.MULTILINE):
            block_defaults.setdefault(name, default)
        for name in ("SPLIT_NUM_BLOCKS", "TRAIN_NUM_ITER", "TRAIN_SP_CAP_MAX", "TRAIN_SP_PRESET"):
            self.assertEqual(values[name], block_defaults[name], name)
        self.assertIn('NUM_CPUS="${NUM_CPUS:-2}"',
                      (ROOT.parents[1] / "scripts/panoramax_to_3dgs.sh").read_text())
        self.assertEqual(values["NUM_CPUS"], "2")

    def test_explicit_empty_labels_never_fall_back_to_sky(self):
        result, _ = self.execute("validate_config.sh", """
SFM_MASKS=on
SFM_MASKS_BACKEND=spirula
SFM_MASKS_SPIRULA_MODEL=/models/sam3.ggml
SFM_MASKS_LABELS=""
TRAIN_MASKS=off
""")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SFM_MASKS_LABELS doit contenir au moins un label", result.stderr)

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
            result, _ = self.execute("validate_config.sh", f'PANORAMAX_BBOX="{bbox}"\n')
            self.assertEqual(result.returncode, 1, bbox)
            self.assertIn("PANORAMAX_BBOX", result.stderr)

    def test_sfm_masks_inheritance_and_validation(self):
        result, _ = self.execute("validate_config.sh", """
SFM_MASKS=on
TRAIN_MASKS_BACKEND=spirula
TRAIN_MASKS_SPIRULA_MODEL=/models/sam3.ggml
SFM_MASKS_LABELS="sky,car,person"
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        result, _ = self.execute("validate_config.sh", """
SFM_MASKS=on
SFM_MASKS_BACKEND=spirula
SFM_MASKS_CONFIDENCE=2
SFM_MASKS_DILATE=-1
SFM_MASKS_MAX_FRACTION=0
""")
        self.assertNotEqual(result.returncode, 0)
        for key in ("SPIRULA_MODEL", "CONFIDENCE", "DILATE", "MAX_FRACTION"):
            self.assertIn(f"SFM_MASKS_{key}", result.stderr)

    def test_sam_license_acceptance_is_explicit(self):
        for value in ("true", "false"):
            result, _ = self.execute("validate_config.sh", f"MASKS_ACCEPT_LICENSE={value}\n")
            self.assertEqual(result.returncode, 0, result.stderr)
        result, _ = self.execute("validate_config.sh", "MASKS_ACCEPT_LICENSE=maybe\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MASKS_ACCEPT_LICENSE", result.stderr)

    def test_spirula_photometric_options_and_backend_guards(self):
        result, _ = self.execute("validate_config.sh", """
SFM_SP_IMAGE_EXPOSURE=-1.5
TRAIN_SP_USE_PPISP=true
TRAIN_SP_PPISP_PARAM_TYPE=no_crf
TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF=false
TRAIN_SP_USE_BILATERAL_GRID=true
TRAIN_SP_BILAGRID_TYPE=ppisp
""")
        self.assertEqual(result.returncode, 0, result.stderr)

        result, root = self.execute("validate_config.sh", """
TRAIN_BACKEND=nerfstudio
TRAIN_SP_USE_PPISP=true
""")
        self.assertEqual(result.returncode, 1)
        self.assertIn("TRAIN_SP_USE_PPISP", result.stderr)
        self.assertFalse((root / "output").exists())

        result, root = self.execute("validate_config.sh", """
SFM_SP_IMAGE_EXPOSURE=auto
SFM_SP_PAIR_MAX_DISTANCE=30
""")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SFM_SP_IMAGE_EXPOSURE", result.stderr)
        self.assertFalse((root / "output").exists())

        result, _ = self.execute("validate_config.sh", "TRAIN_SP_PPISP_PARAM_TYPE=invalid\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("TRAIN_SP_PPISP_PARAM_TYPE", result.stderr)

    def test_training_quality_choices(self):
        for value in ("", "low", "medium", "high", "ultra"):
            result, _ = self.execute("validate_config.sh", f'TRAIN_SP_QUALITY="{value}"\n')
            self.assertEqual(result.returncode, 0, result.stderr)
        for script in ("../../scripts/panoramax_to_3dgs.sh", "launch.sh"):
            result, root = self.execute(script, 'TRAIN_SP_QUALITY="maximum"\n')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TRAIN_SP_QUALITY", result.stderr)
            self.assertFalse((root / "output").exists())

    def test_distraction_robustness_choices(self):
        for value in ("off", "mild", "strong"):
            result, _ = self.execute("validate_config.sh",
                                    f'TRAIN_SP_DISTRACTION_ROBUSTNESS="{value}"\n')
            self.assertEqual(result.returncode, 0, result.stderr)
        for script in ("../../scripts/panoramax_to_3dgs.sh", "launch.sh"):
            result, root = self.execute(script, 'TRAIN_SP_DISTRACTION_ROBUSTNESS="invalid"\n')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TRAIN_SP_DISTRACTION_ROBUSTNESS", result.stderr)
            self.assertFalse((root / "output").exists())

    def test_legacy_names_are_rejected_even_when_empty_or_new_name_exists(self):
        for old, new in (
                ("CAP_MAX", "TRAIN_SP_CAP_MAX"), ("NUM_ITER", "TRAIN_NUM_ITER"),
                ("NUM_BLOCKS", "SPLIT_NUM_BLOCKS"), ("CLEAN_GS", "TRAIN_CLEAN"),
                ("BBOX_LAMBERT93", "PANORAMAX_BBOX"),
                ("PANORAMAX_BBOX_LAMBERT93", "PANORAMAX_BBOX"),
                ("GROWTH_FACTOR", "TRAIN_SP_GROWTH_FACTOR"),
                ("SAM_ACCEPT_LICENSE", "MASKS_ACCEPT_LICENSE")):
            for script in ("validate_config.sh", "../../scripts/panoramax_to_3dgs.sh", "launch.sh"):
                result, root = self.execute(script, f'{old}=""\n')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"ancien paramètre {old} interdit", result.stderr)
                self.assertIn(f"utiliser {new}", result.stderr)
                self.assertFalse((root / "output").exists())

    def test_example_parameters_have_stage_prefixes_or_are_shared(self):
        names = re.findall(r'^(?:export )?([A-Z][A-Z0-9_]*)=',
                           "\n".join(path.read_text() for path in DEFAULT_FILES), re.MULTILINE)
        shared = {"OUTPUT_DIR", "SPIRULA_BIN", "SPIRULA_DEVICE", "PYTHON_BIN",
                  "MVK_CONFIG_LOG_LEVEL", "SS_NN_LOG", "NUM_CPUS", "NUM_GPUS",
                  "PIPELINE_RESUME", "REMOVE_LOCKS", "COLMAP_METHOD", "TRAIN_BACKEND"}
        prefixes = ("PANORAMAX_", "SFM_", "TRAIN_", "SPLIT_", "MASKS_", "HLOC_", "GEOREF_",
                    "MERGE_", "POSTPROCESS_", "EXPORT_", "CLEANGS_", "SLURM_", "NS_", "LIDAR_",
                    "ORIENTATIONS_")
        for name in names:
            self.assertTrue(name in shared or name.startswith(prefixes), name)


if __name__ == "__main__":
    unittest.main()
