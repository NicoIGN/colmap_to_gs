import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest


SCRIPT = Path(__file__).with_name("run_spirula_blocks.sh").resolve()
REAL_PYTHON = os.environ.get("PYTHON", sys.executable)


class BlockPipelineTest(unittest.TestCase):
    def test_strict_geometry_reaches_native_sfm_and_guards_reuse(self):
        for overrides in ({}, {"SFM_MAX_ERROR": "1.5", "SFM_MIN_INLIERS": "50",
                               "SFM_MIN_TRI_ANGLE": "4", "SFM_BA_LOSS": "cauchy",
                               "SFM_BA_LOSS_PARAM": "1"}):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as temporary:
                root, data, sparse, env, log, execute = self.fixture(temporary)
                shutil.rmtree(data / "sfm-3")
                (data / "sfm-3/images").mkdir(parents=True)
                env.update(PREPARE="false", **overrides)
                result = execute("--prepare-only")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                sfm = next(json.loads(line) for line in log.read_text().splitlines()
                           if json.loads(line)[:2] == ["spirula", "sfm"])
                for flag, key, default in (
                    ("--max-error", "SFM_MAX_ERROR", "2"),
                    ("--min-inliers", "SFM_MIN_INLIERS", "30"),
                    ("--min-tri-angle", "SFM_MIN_TRI_ANGLE", "3"),
                    ("--ba-loss", "SFM_BA_LOSS", "huber"),
                    ("--ba-loss-param", "SFM_BA_LOSS_PARAM", "2"),
                ):
                    self.assertEqual(sfm[sfm.index(flag) + 1], overrides.get(key, default))
                self.assertEqual(sfm[sfm.index("--reuse-matches") + 1], "redo")
                self.assertTrue((data / "sfm-3/sfm_geometry_policy.json").is_file())
                before = log.read_text()
                env["SFM_MIN_TRI_ANGLE"] = "5"
                for args in (("--prepare-only",), ("--block", "0")):
                    refused = execute(*args)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn("Contrôles géométriques SfM modifiés", refused.stderr)
                    self.assertEqual(log.read_text(), before)

    def test_legacy_sfm_warns_that_new_thresholds_are_not_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            result = execute("--prepare-only")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("les nouveaux seuils ne sont PAS appliqués", result.stderr)
            self.assertFalse((data / "sfm-3/sfm_geometry_policy.json").exists())

    def fixture(self, temporary):
            root = Path(temporary)
            data = root / "dataset"
            (data / "images").mkdir(parents=True)
            sparse = data / "sfm-3" / "sparse" / "0"
            images = data / "sfm-3" / "images"
            sparse.mkdir(parents=True)
            images.mkdir()
            for name in ("cameras.bin", "images.bin", "points3D.bin"):
                (sparse / name).write_bytes(b"colmap")
            (images / "img.jpg").write_bytes(b"jpg")

            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "commands.jsonl"
            python_wrapper = fake_bin / "python3"
            python_wrapper.write_text(
                f"#!{sys.executable}\n"
                "import json, os, runpy, sys\n"
                "args = sys.argv[1:]\n"
                "log = os.environ['COMMAND_LOG']\n"
                "if args and args[0].endswith('colmap_split_blocks.py'):\n"
                "  with open(log, 'a') as f: f.write(json.dumps(['split'] + args[1:]) + '\\n')\n"
                "  output = args[args.index('--output') + 1]\n"
                "  count = int(args[args.index('--num-blocks') + 1])\n"
                "  os.makedirs(output, exist_ok=True)\n"
                "  blocks = []\n"
                "  for i in range(count):\n"
                "    name = f'block_{i:03d}'; folder = os.path.join(output, name); os.makedirs(folder)\n"
                "    for file in ('cameras.bin', 'images.bin', 'points3D.bin'): open(os.path.join(folder, file), 'wb').write(b'colmap')\n"
                "    blocks.append({'block_id': i})\n"
                "  json.dump({'blocks': blocks}, open(os.path.join(output, 'manifest.json'), 'w'))\n"
                "elif args and args[0].endswith('generate_masks.py'):\n"
                "  with open(log, 'a') as f: f.write(json.dumps(['masks'] + args[1:]) + '\\n')\n"
                "  output = args[args.index('--output') + 1]; os.makedirs(output, exist_ok=True)\n"
                "  json.dump({'status': 'complete', 'settings': {'labels': args[args.index('--labels') + 1]}}, open(os.path.join(output, 'masks.json'), 'w'))\n"
                "elif args and args[0].endswith('export_ply.py'):\n"
                "  with open(log, 'a') as f: f.write(json.dumps(['export']) + '\\n')\n"
                "  output = args[args.index('--out-ply') + 1]\n"
                "  os.makedirs(os.path.dirname(output), exist_ok=True)\n"
                "  open(output, 'wb').write(b'splat')\n"
                "else:\n"
                "  os.execvp(os.environ['REAL_PYTHON'], [os.environ['REAL_PYTHON'], *args])\n"
            )
            python_wrapper.chmod(0o755)
            spirula = fake_bin / "spirula"
            spirula.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['COMMAND_LOG'], 'a') as f: f.write(json.dumps(['spirula'] + args) + '\\n')\n"
                "if args[0] == 'sfm':\n"
                "  status = int(os.environ.get('TEST_SFM_STATUS', '0'))\n"
                "  if status: sys.exit(status)\n"
                "  out=args[args.index('--output')+1]; sparse=os.path.join(out,'sparse','0'); os.makedirs(sparse,exist_ok=True)\n"
                "  for name in ('cameras.bin','images.bin','points3D.bin'): open(os.path.join(sparse,name),'wb').write(b'colmap')\n"
                "if args[0] == 'train':\n"
                "  prefix = args[args.index('--output-dir-prefix') + 1]\n"
                "  name = args[args.index('--output-dir-name') + 1]\n"
                "  iterations = int(args[args.index('--num-iterations') + 1])\n"
                "  splats = os.path.join(prefix, name)\n"
                "  ckpt = os.path.join(splats, f'step-{iterations:09d}.ckpt')\n"
                "  os.makedirs(ckpt, exist_ok=True)\n"
                "  open(os.path.join(ckpt, 'splat.ply'), 'wb').write(b'ply')\n"
                "  json.dump({'format':'spirula-scene-transform','version':1,'train_from_world':{'matrix_4x4':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}}, open(os.path.join(splats, 'scene_transform.json'), 'w'))\n"
            )
            spirula.chmod(0o755)
            env = dict(
                os.environ,
                DIR=str(data),
                SPIRULA_BIN=str(spirula),
                PYTHON_BIN="python3",
                PATH=f"{fake_bin}:{os.environ['PATH']}",
                REAL_PYTHON=REAL_PYTHON,
                COMMAND_LOG=str(log),
                TRAIN_NUM_ITER="3",
                SPLIT_NUM_BLOCKS="2",
                TRAIN_CAP_MAX="1000",
                TRAIN_GROWTH_FACTOR="1.05",
            )

            def execute(*args):
                return subprocess.run(
                    ["bash", str(SCRIPT), *args], cwd=root, env=env,
                    capture_output=True, text=True, timeout=30,
                )

            return root, data, sparse, env, log, execute

    def test_automatic_growth_in_local_and_array_workers(self):
        for mode in ("local", "array"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root, data, sparse, env, log, execute = self.fixture(temporary)
                env.update(TRAIN_NUM_ITER="20000", TRAIN_CAP_MAX="8000000",
                           TRAIN_REFINE_STOP_ITER="14000", TRAIN_REFINE_STOP_NUM_ITER="4000")
                if mode == "local":
                    env["TRAIN_GROWTH_FACTOR"] = ""
                else:
                    env.pop("TRAIN_GROWTH_FACTOR")
                (data / "sfm-3").rename(data / "sfm-20000")
                if mode == "array":
                    prepared = execute("--prepare-only")
                    self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
                    result = execute("--block", "0")
                else:
                    result = execute()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                commands = [json.loads(line) for line in log.read_text().splitlines()]
                trains = [cmd for cmd in commands if cmd[:2] == ["spirula", "train"]]
                self.assertEqual(len(trains), 2 if mode == "local" else 1)
                for train in trains:
                    factor = float(train[train.index("--growth-factor") + 1])
                    self.assertAlmostEqual(factor, 10 ** (1 / 154), places=11)
                    self.assertAlmostEqual(800000 * factor ** 154, 8000000, delta=0.01)
                    self.assertLess(800000 * factor ** 153, 8000000)
                self.assertIn("154 cycles avant le pas 16000", result.stdout)
                command_file = data / "gs-20000-blocks-2/block_000/train_command.sh"
                self.assertIn("--growth-factor", command_file.read_text())

    def test_worker_growth_estimation_bounds_defaults_and_overrides(self):
        cases = (
            # Every growth step is strictly inside (500, stop).
            ({}, ["--num-iterations", "20000"], 10 ** (1 / 169), 169, False),
            ({"TRAIN_REFINE_STOP_ITER": "18000", "TRAIN_REFINE_STOP_NUM_ITER": "12000"},
             ["--num-iterations", "30000"], 10 ** (1 / 174), 174, False),
            ({"TRAIN_REFINE_STOP_ITER": "14000", "TRAIN_REFINE_STOP_NUM_ITER": "4000"},
             ["--num-iterations", "20000", "--min-init-fraction", "0.2"],
             5 ** (1 / 154), 154, False),
            ({}, ["--num-iterations", "700"], 1.05, 1, True),
            ({}, ["--num-iterations", "300000"], 1.001, 2969, True),
            ({"TRAIN_GROWTH_FACTOR": "1.08"}, ["--num-iterations", "3"], 1.08, None, False),
            ({"TRAIN_GROWTH_FACTOR": "1.01"}, ["--num-iterations", "20000"],
             1.01, None, False),
            ({"TRAIN_GROWTH_FACTOR": "1.01"},
             ["--num-iterations", "20000", "--growth-factor", "1.02"], 1.02, None, False),
        )
        for settings, args, expected, cycles, bounded in cases:
            with self.subTest(settings=settings, args=args), tempfile.TemporaryDirectory() as temporary:
                root, data, sparse, env, log, execute = self.fixture(temporary)
                env["TRAIN_GROWTH_FACTOR"] = ""
                env.update(settings)
                result = subprocess.run(
                    ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                     "--sparse-dir", str(sparse), "--images-dir", str(data / "sfm-3/images"),
                     "--output-dir", str(root / "worker"), *args],
                    env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                train = next(json.loads(line) for line in log.read_text().splitlines()
                             if json.loads(line)[:2] == ["spirula", "train"])
                factor = float(train[train.index("--growth-factor") + 1])
                self.assertTrue(math.isfinite(factor))
                self.assertAlmostEqual(factor, expected, places=11)
                if cycles is not None:
                    self.assertIn(f"{cycles} cycles", result.stdout)
                    self.assertGreaterEqual(factor, 1.001)
                    self.assertLessEqual(factor, 1.05)
                else:
                    self.assertNotIn("estimé", result.stdout)
                self.assertEqual("borné" in result.stderr, bounded)

    def test_impossible_automatic_growth_is_rejected_before_training(self):
        for args in (["--num-iterations", "600"],
                     ["--num-iterations", "20000", "--min-init-fraction", "0"],
                     ["--num-iterations", "20000", "--refine-stop-iter", "500",
                      "--refine-stop-num-iter", "19500"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as temporary:
                root, data, sparse, env, log, execute = self.fixture(temporary)
                env.pop("TRAIN_GROWTH_FACTOR")
                result = subprocess.run(
                    ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                     "--sparse-dir", str(sparse), "--images-dir", str(data / "sfm-3/images"),
                     "--output-dir", str(root / "unused"), *args],
                    env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Estimation TRAIN_GROWTH_FACTOR impossible", result.stderr)
                self.assertFalse(log.exists())
                self.assertFalse((root / "unused").exists())

    def test_automatic_growth_uses_locale_independent_decimal_separator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            real_awk = shutil.which("awk")
            wrapper = root / "bin/awk"
            wrapper.write_text(
                f"#!{sys.executable}\n"
                "import os, subprocess, sys\n"
                f"result = subprocess.run([{real_awk!r}, *sys.argv[1:]], "
                "capture_output=True, text=True)\n"
                "output = result.stdout\n"
                # Model a locale-sensitive awk even on hosts with only C locales.
                "if any('%.12f' in arg for arg in sys.argv) and "
                "os.environ.get('LC_ALL') != 'C':\n"
                "    output = output.replace('.', ',')\n"
                "sys.stdout.write(output)\n"
                "sys.stderr.write(result.stderr)\n"
                "sys.exit(result.returncode)\n"
            )
            wrapper.chmod(0o755)
            env.pop("LC_ALL", None)
            env.update(TRAIN_GROWTH_FACTOR="", LC_NUMERIC="C",
                       TRAIN_REFINE_STOP_ITER="12000", TRAIN_REFINE_STOP_NUM_ITER="3000")
            result = subprocess.run(
                ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                 "--sparse-dir", str(sparse), "--images-dir", str(data / "sfm-3/images"),
                 "--output-dir", str(root / "worker"), "--num-iterations", "15000"],
                env=env,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            train = next(json.loads(line) for line in log.read_text().splitlines()
                         if json.loads(line)[:2] == ["spirula", "train"])
            factor = train[train.index("--growth-factor") + 1]
            self.assertEqual(factor, "1.020403477086")
            self.assertIn("114 cycles avant le pas 12000", result.stdout)

    def test_splits_once_and_trains_each_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            first = execute()
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(cmd[0] == "split" for cmd in commands), 1)
            self.assertEqual(sum(cmd[:2] == ["spirula", "train"] for cmd in commands), 2)
            self.assertTrue(all("--quality" not in cmd for cmd in commands
                                if cmd[:2] == ["spirula", "train"]))
            self.assertTrue(all(cmd[cmd.index("--distraction-robustness") + 1] == "off"
                                for cmd in commands if cmd[:2] == ["spirula", "train"]))
            self.assertTrue((data / "gs-3-blocks-2/block_000/splats/Paris-3-block-000.splat").is_file())
            self.assertTrue((data / "gs-3-blocks-2/block_001/splats/Paris-3-block-001.splat").is_file())

            second = execute()
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(cmd[0] == "split" for cmd in commands), 1)
            self.assertEqual(sum(cmd[:2] == ["spirula", "train"] for cmd in commands), 2)

            for name in ("cameras.bin", "images.bin", "points3D.bin"):
                (sparse / name).unlink()
            env.update(SFM_FEATURES="aliked-n16rot", SFM_MATCHER="bruteforce",
                       SFM_THREADS="6", PREPARE="false")
            prepared = execute("--prepare-only")
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            sfm = next(cmd for cmd in commands if cmd[:2] == ["spirula", "sfm"])
            self.assertEqual(sfm[sfm.index("--matcher") + 1], "bruteforce")
            self.assertEqual(sfm[sfm.index("--features") + 1], "aliked-n16rot")
            self.assertEqual(sfm[sfm.index("--threads") + 1], "6")
            self.assertEqual(sum(cmd[:2] == ["spirula", "train"] for cmd in commands), 2)
            for name in ("cameras.bin", "images.bin", "points3D.bin"):
                (sparse / name).unlink()
            env["TEST_SFM_STATUS"] = "139"
            failed = execute("--prepare-only")
            self.assertEqual(failed.returncode, 139)
            self.assertIn("Segmentation fault dans Spirula", failed.stderr)
            self.assertIn("--matcher bruteforce", failed.stderr)


    def test_training_masks_are_generated_and_passed_to_spirula(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            model = root / "model"
            model.mkdir()
            env.update(TRAIN_MASKS="on", TRAIN_MASKS_MODEL=str(model),
                       TRAIN_MASKS_LABELS="sky", SPLIT_NUM_BLOCKS="1")
            result = execute()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            masks = next(cmd for cmd in commands if cmd[0] == "masks")
            masks_dir = data / "sfm-3" / "train_masks"
            self.assertEqual(masks[masks.index("--output") + 1], str(masks_dir))
            self.assertEqual(masks[masks.index("--dilate-fraction") + 1], "0")
            train = next(cmd for cmd in commands if cmd[:2] == ["spirula", "train"])
            self.assertEqual(train[train.index("--mask-dir") + 1], "masks")
            self.assertEqual(train[train.index("--apply-loss-for-mask") + 1], "1")
            block = data / "gs-3-blocks-1" / "block_000"
            self.assertEqual((block / "masks").resolve(), masks_dir.resolve())
            self.assertIn("mode=empty", (block / "train_masks.txt").read_text())

            again = execute()
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertNotIn("autres masques", again.stderr)

            env.update(TRAIN_MASKS_LABELS="sky,person")
            changed = execute()
            self.assertEqual(changed.returncode, 0, changed.stderr)
            self.assertIn("autres masques", changed.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            masks = [cmd for cmd in commands if cmd[0] == "masks"][-1]
            self.assertEqual(masks[masks.index("--dilate-fraction") + 1], "0.002")

            env.update(TRAIN_MASKS="off")
            off = execute()
            self.assertEqual(off.returncode, 0, off.stderr)
            self.assertIn("autres masques", off.stderr)
            env.update(TRAIN_CLEAN="true")
            clean = execute()
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertFalse((block / "masks").exists() or (block / "masks").is_symlink())
            train = [json.loads(line) for line in log.read_text().splitlines()
                     if json.loads(line)[:2] == ["spirula", "train"]][-1]
            self.assertEqual(train[train.index("--load-masks") + 1], "0")

    def test_training_masks_require_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            env.update(TRAIN_MASKS="on", TRAIN_MASKS_MODEL=str(root / "missing"))
            result = execute()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TRAIN_MASKS_MODEL", result.stderr)

    def test_shared_masks_precede_sfm_and_are_used_by_training(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            shutil.rmtree(sparse.parent)
            jpeg = (b"\xff\xd8\xff\xc0" + (11).to_bytes(2, "big") + b"\x08" +
                    (2160).to_bytes(2, "big") + (4096).to_bytes(2, "big") +
                    b"\x01\x01\x11\x00\xff\xd9")
            (data / "images/img.jpg").write_bytes(jpeg)
            model = root / "sam3.ggml"
            model.write_bytes(b"sam")
            env.update(SFM_MASKS="on", TRAIN_MASKS="on", TRAIN_MASKS_BACKEND="spirula",
                       TRAIN_MASKS_LABELS="sky,person,car", TRAIN_MASKS_MODE="ignore",
                       TRAIN_MASKS_SPIRULA_MODEL=str(model), SPLIT_NUM_BLOCKS="1",
                       MASKS_ACCEPT_LICENSE="true")
            result = execute()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(cmd[0] == "masks" for cmd in commands), 1)
            self.assertIn("--accept-spirula-license", next(cmd for cmd in commands if cmd[0] == "masks"))
            sfm = next(cmd for cmd in commands if cmd[:2] == ["spirula", "sfm"])
            self.assertLess(next(i for i, cmd in enumerate(commands) if cmd[0] == "masks"),
                            commands.index(sfm))
            self.assertIn("--reuse-features", sfm)
            self.assertEqual(sfm[sfm.index("--matcher") + 1], "lightglue")
            features = data / "sfm-3/feature_masks"
            self.assertEqual(sfm[sfm.index("--feature-masks") + 1], str(features))
            self.assertTrue(features.is_symlink())
            self.assertEqual(features.resolve(), data / "sfm-3/train_masks")
            block = execute("--block", "0")
            self.assertEqual(block.returncode, 0, block.stderr + block.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(cmd[0] == "masks" for cmd in commands), 1)
            again = execute("--prepare-only")
            self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
            env["SFM_MASKS_LABELS"] = "sky"
            changed = execute("--prepare-only")
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn("TRAIN_CLEAN", changed.stderr)
            env["SFM_MASKS"] = "off"
            disabled = execute("--prepare-only")
            self.assertNotEqual(disabled.returncode, 0)
            self.assertIn("conserver SFM_MASKS", disabled.stderr)

    def test_native_backend_uses_sam_model_without_transformers_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            model = root / "sam3-q4_0.ggml"
            model.write_bytes(b"sam")
            env.update(TRAIN_MASKS="on", TRAIN_MASKS_BACKEND="spirula",
                       TRAIN_MASKS_SPIRULA_MODEL=str(model),
                       TRAIN_MASKS_SPIRULA_DEVICE="0",
                       TRAIN_MASKS_MODEL="", TRAIN_MASKS_PYTHON="/missing/python",
                       SPLIT_NUM_BLOCKS="1", MASKS_ACCEPT_LICENSE="true")
            result = execute()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            masks = next(cmd for cmd in commands if cmd[0] == "masks")
            self.assertEqual(masks[masks.index("--backend") + 1], "spirula")
            self.assertEqual(masks[masks.index("--model") + 1], str(model))
            self.assertEqual(masks[masks.index("--spirula-device") + 1], "0")
            self.assertIn("--accept-spirula-license", masks)
            train = next(cmd for cmd in commands if cmd[:2] == ["spirula", "train"])
            self.assertEqual(train[train.index("--apply-loss-for-mask") + 1], "1")
            model.unlink()
            failed = execute("--prepare-only")
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("TRAIN_MASKS_SPIRULA_MODEL", failed.stderr)
            env.update(TRAIN_MASKS_BACKEND="bad")
            failed = execute()
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("TRAIN_MASKS_BACKEND", failed.stderr)

    def test_quality_is_forwarded_and_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            env["TRAIN_QUALITY"] = "ultra"
            env["TRAIN_DISTRACTION_ROBUSTNESS"] = "strong"
            result = execute()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            for cmd in commands:
                if cmd[:2] == ["spirula", "train"]:
                    self.assertEqual(cmd[cmd.index("--quality") + 1], "ultra")
                    self.assertEqual(cmd[cmd.index("--distraction-robustness") + 1], "strong")
                    self.assertEqual(cmd[cmd.index("--cap-max") + 1], "1000")
                    self.assertEqual(cmd[cmd.index("--num-iterations") + 1], "3")
            env["TRAIN_QUALITY"] = "invalid"
            result = execute("--prepare-only")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TRAIN_QUALITY", result.stderr)
            result = subprocess.run(
                ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                 "--sparse-dir", str(sparse), "--images-dir", str(data / "sfm-3/images"),
                 "--output-dir", str(root / "unused"), "--quality", "invalid"],
                env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--quality doit", result.stderr)
            self.assertFalse((root / "unused").exists())

    def test_invalid_distraction_robustness_is_rejected_by_workers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            env["TRAIN_DISTRACTION_ROBUSTNESS"] = "invalid"
            result = execute("--prepare-only")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TRAIN_DISTRACTION_ROBUSTNESS", result.stderr)
            result = subprocess.run(
                ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                 "--sparse-dir", str(sparse), "--images-dir", str(data / "sfm-3/images"),
                 "--output-dir", str(root / "unused"),
                 "--distraction-robustness", "invalid"],
                env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--distraction-robustness doit", result.stderr)
            self.assertFalse((root / "unused").exists())


if __name__ == "__main__":
    unittest.main()
