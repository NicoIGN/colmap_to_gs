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
    def test_hloc_panorama_prepare_georeferences_before_split_and_checks_reuse(self):
        try:
            import pycolmap
            import numpy as np
            from PIL import Image
            from tools.preprocess.georef_colmap_from_gpx import geodetic_to_ecef, enu_rotation
        except ImportError as exc:
            self.skipTest(f"Dépendance HLOC indisponible : {exc}")
        with tempfile.TemporaryDirectory() as temporary:
            root, data, _, env, log, execute = self.fixture(temporary)
            shutil.rmtree(data / "sfm")
            (source / "images/img.jpg").unlink(missing_ok=True)
            (source / "metadata").mkdir()
            coordinates = [(2., 48.), (2.0002, 48.), (2.0002, 48.0002), (2., 48.0002)]
            geo = np.array([[lat, lon, 0.] for lon, lat in coordinates])
            origin = geo.mean(axis=0)
            centers = (geodetic_to_ecef(geo[:, 0], geo[:, 1], geo[:, 2]) -
                       geodetic_to_ecef(*origin)) @ enu_rotation(origin).T
            model = pycolmap.Reconstruction()
            model.add_camera_with_trivial_rig(pycolmap.Camera(
                model="PINHOLE", width=16, height=16, params=[8, 8, 8, 8], camera_id=1))
            rotation = pycolmap.Rotation3d(np.array([[1., 0, 0], [0, 0, -1.], [0, 1., 0]]))
            for index, (lon, lat) in enumerate(coordinates):
                name = f"img{index}.jpg"
                Image.new("RGB", (40, 20)).save(source / "images" / name)
                (source / "metadata" / f"img{index}.json").write_text(json.dumps({
                    "feature": {"geometry": {"type": "Point", "coordinates": [lon, lat]},
                                "properties": {"type": "equirectangular"}}
                }))
                image = pycolmap.Image(name=f"front/{name}.jpg", camera_id=1, image_id=index + 1)
                model.add_image_with_trivial_frame(
                    image, pycolmap.Rigid3d(rotation, -rotation.matrix() @ centers[index]))
            raw = root / "raw-model"
            raw.mkdir()
            model.write(raw)
            wrapper = root / "bin/python3"
            stub = (
                "if args and args[0].endswith('hloc_reconstruct.py'):\n"
                "  import pathlib, shutil\n"
                "  out=pathlib.Path(args[args.index('--output')+1]); out.mkdir(parents=True)\n"
                "  for name in ('cameras.bin','images.bin','points3D.bin'):\n"
                "    shutil.copyfile(pathlib.Path(os.environ['RAW_MODEL'])/name,out/name)\n"
                "  with open(log,'a') as f: f.write(json.dumps(['hloc']+args[1:])+'\\n')\n"
                "  sys.exit(0)\n"
            )
            wrapper.write_text(wrapper.read_text().replace(
                "log = os.environ['COMMAND_LOG']\n", "log = os.environ['COMMAND_LOG']\n" + stub))
            env.update(COLMAP_METHOD="hloc", HLOC_CAMERA_MODEL="EQUIRECTANGULAR",
                       SFM_EQUIRECTANGULAR_VIEW_SIZE="16", RAW_MODEL=str(raw),
                       SFM_MASKS="off", TRAIN_MASKS="off")
            result = execute("--prepare-only")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            stats = json.loads((data / "sfm/sparse/0/georef_stats.json").read_text())
            self.assertEqual(stats["mode"], "horizontal")
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertIn("hloc", [command[0] for command in commands])
            self.assertNotIn(["spirula", "sfm"], [command[:2] for command in commands])
            self.assertEqual(len(list((data / "images").rglob("*.jpg"))), 24)
            env["HLOC_MATCHER"] = "other"
            refused = execute("--block", "0")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("Configuration HLOC modifiée", refused.stderr)

    def test_lidar_recalage_precedes_partition_and_block_jobs_check_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            wrapper = root/"bin/python3"
            stub = (
                "if args and args[0].endswith('pipeline_options.py') and args[1] in ('prepare','check'):\n"
                "  from pathlib import Path\n"
                "  workspace=Path(args[args.index('--workspace')+1])\n"
                "  assert os.environ['LIDAR_COLMAP_INJECT']=='true'\n"
                "  with open(log,'a') as f: f.write(json.dumps(['lidar',args[1]])+'\\n')\n"
                "  if args[1]=='prepare':\n"
                "    target=workspace/'colmap_lidar'; target.mkdir(exist_ok=True)\n"
                "    for name in ('cameras.bin','images.bin','points3D.bin'): (target/name).write_bytes(b'colmap')\n"
                "    (workspace/'lidar').mkdir(exist_ok=True)\n"
                "    (workspace/'lidar/colmap_inputs.json').write_text('{}')\n"
                "  sys.exit(0)\n"
            )
            wrapper.write_text(wrapper.read_text().replace(
                "log = os.environ['COMMAND_LOG']\n", "log = os.environ['COMMAND_LOG']\n"+stub))
            env.update(LIDAR_COLMAP_ALIGN="true", LIDAR_COLMAP_INJECT="true")
            result = execute("--prepare-only")
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            lidar_index = next(i for i, c in enumerate(commands) if c[:2] == ["lidar", "prepare"])
            split_index = next(i for i, c in enumerate(commands) if c[0] == "split")
            self.assertLess(lidar_index, split_index)
            split = commands[split_index]
            self.assertEqual(split[split.index("--input")+1], str(data/"panoramax"/"colmap_lidar"))
            result = execute("--block", "0")
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertIn(["lidar", "check"], commands)

    def test_spirula_analysis_exposure_reaches_sfm_and_training_options_reach_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            shutil.rmtree(data / "sfm")
            (data / "images").mkdir(exist_ok=True)
            source = data / "panoramax"
            (source / "images/img.jpg").write_bytes(
                b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x01\x00\x01" + b"\x00" * 12)
            env.update(
                SFM_SP_IMAGE_EXPOSURE="-1.5",
                TRAIN_SP_USE_PPISP="true",
                TRAIN_SP_PPISP_PARAM_TYPE="no_crf",
                TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF="true",
                TRAIN_SP_USE_BILATERAL_GRID="false",
                TRAIN_SP_BILAGRID_TYPE="ppisp",
            )
            prepared = execute("--prepare-only")
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            sfm = next(cmd for cmd in commands if cmd[:2] == ["spirula", "sfm"])
            self.assertEqual(sfm[sfm.index("--image-exposure") + 1], "-1.5")

            trained = execute()
            self.assertEqual(trained.returncode, 0, trained.stdout + trained.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            for train in (cmd for cmd in commands if cmd[:2] == ["spirula", "train"]):
                for flag, value in (
                    ("--use-ppisp", "1"),
                    ("--ppisp-param-type", "no_crf"),
                    ("--ppisp-exposure-from-exif", "1"),
                    ("--use-bilateral-grid", "0"),
                    ("--bilagrid-type", "ppisp"),
                ):
                    self.assertEqual(train[train.index(flag) + 1], value)

    def test_strict_geometry_reaches_native_sfm_and_guards_reuse(self):
        for overrides in ({}, {"SFM_MAX_ERROR": "1.5", "SFM_MIN_INLIERS": "50",
                               "SFM_MIN_TRI_ANGLE": "4", "SFM_SP_BA_LOSS": "cauchy",
                               "SFM_SP_BA_LOSS_PARAM": "1"}):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as temporary:
                root, data, sparse, env, log, execute = self.fixture(temporary)
                shutil.rmtree(data / "sfm")
                (data / "images").mkdir(exist_ok=True)
                env.update(PREPARE="false", **overrides)
                result = execute("--prepare-only")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                sfm = next(json.loads(line) for line in log.read_text().splitlines()
                           if json.loads(line)[:2] == ["spirula", "sfm"])
                for flag, key, default in (
                    ("--max-error", "SFM_MAX_ERROR", "2"),
                    ("--min-inliers", "SFM_MIN_INLIERS", "30"),
                    ("--min-tri-angle", "SFM_MIN_TRI_ANGLE", "6"),
                    ("--ba-loss", "SFM_SP_BA_LOSS", "huber"),
                    ("--ba-loss-param", "SFM_SP_BA_LOSS_PARAM", "2"),
                ):
                    self.assertEqual(sfm[sfm.index(flag) + 1], overrides.get(key, default))
                self.assertEqual(sfm[sfm.index("--reuse-matches") + 1], "redo")
                self.assertTrue((data / "sfm/sfm_geometry_policy.json").is_file())
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
            self.assertFalse((data / "sfm/sfm_geometry_policy.json").exists())

    def fixture(self, temporary):
            root = Path(temporary)
            data = root / "output"
            source = data / "panoramax"
            (source / "images").mkdir(parents=True)
            sparse = data / "sfm" / "sparse" / "0"
            images = data / "images"
            sparse.mkdir(parents=True)
            images.mkdir()
            for name in ("cameras.bin", "images.bin", "points3D.bin"):
                (sparse / name).write_bytes(b"colmap")
            from PIL import Image
            Image.new("RGB", (8, 8)).save(images / "img.jpg")

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
                "  from PIL import Image\n"
                "  source = args[args.index('--images') + 1]\n"
                "  for image in os.listdir(source): Image.new('L', Image.open(os.path.join(source, image)).size).save(os.path.join(output, image + '.png'))\n"
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
                "  chkpt = os.path.join(prefix, name)\n"
                "  os.makedirs(chkpt, exist_ok=True)\n"
                "  for step in (100, 200):\n"
                "    d = os.path.join(chkpt, 'step-%09d.ckpt' % step); os.makedirs(d, exist_ok=True)\n"
                "    open(os.path.join(d, 'splat.ply'), 'wb').write(b'ply')\n"
                "    open(os.path.join(d, 'state.tar'), 'wb').write(b'tar')\n"
                "  json.dump({'format':'spirula-scene-transform','version':1,'train_from_world':{'matrix_4x4':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}}, open(os.path.join(chkpt, 'scene_transform.json'), 'w'))\n"
                "  print('Entraînement terminé', flush=True)\n"
            )
            spirula.chmod(0o755)
            env = dict(
                os.environ,
                DIR=str(source),
                OUTPUT_DIR=str(data),
                SPIRULA_BIN=str(spirula),
                PYTHON_BIN="python3",
                PATH=f"{fake_bin}:{os.environ['PATH']}",
                REAL_PYTHON=REAL_PYTHON,
                COMMAND_LOG=str(log),
                TRAIN_NUM_ITER="3",
                SPLIT_NUM_BLOCKS="2",
                TRAIN_SP_CAP_MAX="1000",
                TRAIN_SP_GROWTH_FACTOR="1.05",
            )

            def execute(*args):
                return subprocess.run(
                    ["bash", str(SCRIPT), *args], cwd=root, env=env,
                    capture_output=True, text=True, timeout=30,
                )

            return root, data, sparse, env, log, execute

    def test_final_splat_comes_from_latest_step_and_is_published_to_splats(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            result = execute()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PLY final", result.stdout + result.stderr)
            self.assertTrue(list(root.rglob("block_*.splat")), result.stdout + result.stderr)
            splats = sorted(root.rglob("chkpt_step-000000200.ply"))
            self.assertEqual(len(splats), 2, result.stdout + result.stderr)
            self.assertTrue(all(p.parent.name == "splats" for p in splats))
            self.assertFalse(list(root.rglob("step-000000100.ckpt")))

    def test_finished_training_is_not_retrained_when_resumed_before_done_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            first = execute()
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            for done in root.rglob(".train_done"):
                done.unlink()
            for export_done in root.rglob(".export_done"):
                export_done.unlink()
            trains_before = log.read_text().count('"train"')
            env["PIPELINE_RESUME"] = "true"
            second = execute()
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(log.read_text().count('"train"'), trains_before, second.stdout + second.stderr)
            self.assertEqual(len(list(root.rglob(".train_done"))), 2)
            self.assertFalse(list(root.rglob(".train_finalize.lock")))

    def test_automatic_growth_in_local_and_array_workers(self):
        for mode in ("local", "array"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root, data, sparse, env, log, execute = self.fixture(temporary)
                env.update(TRAIN_NUM_ITER="20000", TRAIN_SP_CAP_MAX="8000000",
                           TRAIN_SP_REFINE_STOP_ITER="14000", TRAIN_SP_REFINE_STOP_NUM_ITER="4000")
                if mode == "local":
                    env["TRAIN_SP_GROWTH_FACTOR"] = ""
                else:
                    env.pop("TRAIN_SP_GROWTH_FACTOR")
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
                command_file = data / "panoramax/gs-20000-blocks-2/block_000/train_command.sh"
                self.assertIn("--growth-factor", command_file.read_text())

    def test_worker_growth_estimation_bounds_defaults_and_overrides(self):
        cases = (
            # Every growth step is strictly inside (500, stop).
            ({}, ["--num-iterations", "20000"], 10 ** (1 / 169), 169, False),
            ({"TRAIN_SP_REFINE_STOP_ITER": "18000", "TRAIN_SP_REFINE_STOP_NUM_ITER": "12000"},
             ["--num-iterations", "30000"], 10 ** (1 / 174), 174, False),
            ({"TRAIN_SP_REFINE_STOP_ITER": "14000", "TRAIN_SP_REFINE_STOP_NUM_ITER": "4000"},
             ["--num-iterations", "20000", "--min-init-fraction", "0.2"],
             5 ** (1 / 154), 154, False),
            ({}, ["--num-iterations", "700"], 1.05, 1, True),
            ({}, ["--num-iterations", "300000"], 1.001, 2969, True),
            ({"TRAIN_SP_GROWTH_FACTOR": "1.08"}, ["--num-iterations", "3"], 1.08, None, False),
            ({"TRAIN_SP_GROWTH_FACTOR": "1.01"}, ["--num-iterations", "20000"],
             1.01, None, False),
            ({"TRAIN_SP_GROWTH_FACTOR": "1.01"},
             ["--num-iterations", "20000", "--growth-factor", "1.02"], 1.02, None, False),
        )
        for settings, args, expected, cycles, bounded in cases:
            with self.subTest(settings=settings, args=args), tempfile.TemporaryDirectory() as temporary:
                root, data, sparse, env, log, execute = self.fixture(temporary)
                env["TRAIN_SP_GROWTH_FACTOR"] = ""
                env.update(settings)
                result = subprocess.run(
                    ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                     "--sparse-dir", str(sparse), "--images-dir", str(data / "images"),
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
                env.pop("TRAIN_SP_GROWTH_FACTOR")
                result = subprocess.run(
                    ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                     "--sparse-dir", str(sparse), "--images-dir", str(data / "images"),
                     "--output-dir", str(root / "unused"), *args],
                    env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Estimation TRAIN_SP_GROWTH_FACTOR impossible", result.stderr)
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
            env.update(TRAIN_SP_GROWTH_FACTOR="", LC_NUMERIC="C",
                       TRAIN_SP_REFINE_STOP_ITER="12000", TRAIN_SP_REFINE_STOP_NUM_ITER="3000")
            result = subprocess.run(
                ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                 "--sparse-dir", str(sparse), "--images-dir", str(data / "images"),
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
            self.assertTrue((data / "panoramax/gs-3-blocks-2/block_000/splats/block_000.splat").is_file())
            self.assertTrue((data / "panoramax/gs-3-blocks-2/block_001/splats/block_001.splat").is_file())

            second = execute()
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(cmd[0] == "split" for cmd in commands), 1)
            self.assertEqual(sum(cmd[:2] == ["spirula", "train"] for cmd in commands), 2)

            for name in ("cameras.bin", "images.bin", "points3D.bin"):
                (sparse / name).unlink()
            env.update(SFM_SP_FEATURES="aliked-n16rot", SFM_SP_MATCHER="bruteforce",
                       NUM_CPUS="6", PREPARE="false")
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
            masks_dir = data / "train_masks"
            self.assertEqual(masks[masks.index("--output") + 1], str(masks_dir))
            self.assertEqual(masks[masks.index("--dilate-fraction") + 1], "0")
            train = next(cmd for cmd in commands if cmd[:2] == ["spirula", "train"])
            self.assertEqual(train[train.index("--mask-dir") + 1], "masks")
            self.assertEqual(train[train.index("--apply-loss-for-mask") + 1], "1")
            block = data / "panoramax" / "gs-3-blocks-1" / "block_000"
            self.assertEqual((block / "masks").resolve(), masks_dir.resolve())
            self.assertIn("mode=empty", (block / "train_masks.txt").read_text())

            again = execute()
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertNotIn("autres masques", again.stderr)

            env.update(TRAIN_MASKS_LABELS="sky,person", TRAIN_MASKS_MODE="auto")
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
            source = data / "panoramax"
            shutil.rmtree(sparse.parent)
            from PIL import Image
            Image.new("RGB", (4096, 2160)).save(source / "images/img.jpg")
            Image.new("RGB", (4096, 2160)).save(data / "images/img.jpg")
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
            features = data / "feature_masks"
            self.assertEqual(sfm[sfm.index("--feature-masks") + 1], str(features))
            self.assertTrue(features.is_symlink())
            self.assertEqual(features.resolve(), data / "train_masks")
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

    def test_external_masks_are_adopted_without_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            shutil.rmtree(sparse.parent)
            jpeg = (b"\xff\xd8\xff\xc0" + (11).to_bytes(2, "big") + b"\x08" +
                    (2160).to_bytes(2, "big") + (4096).to_bytes(2, "big") +
                    b"\x01\x01\x11\x00\xff\xd9")
            (data / "panoramax/images/img.jpg").write_bytes(jpeg)
            (data / "images/img.jpg").write_bytes(jpeg)
            external = root / "external_masks"
            external.mkdir()
            (external / "masks.json").write_text(json.dumps({"status": "complete", "settings": {}}))
            from PIL import Image
            Image.new("L", (4096, 2160)).save(external / "img.jpg.png")
            model = root / "sam3.ggml"
            model.write_bytes(b"sam")
            env.update(SFM_MASKS="on", TRAIN_MASKS="on", TRAIN_MASKS_BACKEND="spirula",
                       TRAIN_MASKS_LABELS="sky,person,car", TRAIN_MASKS_MODE="ignore",
                       TRAIN_MASKS_SPIRULA_MODEL=str(model), SPLIT_NUM_BLOCKS="1",
                       MASKS_ACCEPT_LICENSE="true", SFM_MASKS_DIR=str(external),
                       TRAIN_MASKS_DIR=str(external))
            result = execute("--prepare-only")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            self.assertEqual(sum(cmd[0] == "masks" for cmd in commands), 0)
            self.assertIn("copiés et signés (sans génération)", result.stdout + result.stderr)

    def test_native_backend_uses_sam_model_without_transformers_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            model = root / "sam3-q4_0.ggml"
            model.write_bytes(b"sam")
            env.update(TRAIN_MASKS="on", TRAIN_MASKS_BACKEND="spirula",
                       TRAIN_MASKS_SPIRULA_MODEL=str(model),
                       SPIRULA_DEVICE="0",
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

    def test_spirula_photometric_options_are_forwarded_and_guard_checkpoint_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sparse = root / "sparse"
            images = root / "images"
            sparse.mkdir()
            images.mkdir()
            for name in ("cameras.bin", "images.bin", "points3D.bin"):
                (sparse / name).write_bytes(b"colmap")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "commands.jsonl"
            spirula = fake_bin / "spirula"
            spirula.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['COMMAND_LOG'], 'a') as f: "
                "f.write(json.dumps(['spirula'] + args) + '\\n')\n"
                "if args[0] == 'train':\n"
                "  import pathlib\n"
                "  prefix = args[args.index('--output-dir-prefix') + 1]\n"
                "  iterations = int(args[args.index('--num-iterations') + 1])\n"
                "  chkpt = pathlib.Path(prefix) / 'chkpt'\n"
                "  chkpt.mkdir(parents=True, exist_ok=True)\n"
                "  for step in (100, 200):\n"
                "    (chkpt / ('chkpt_step-%09d.ply' % step)).write_bytes(b'ply')\n"
                "    (chkpt / ('chkpt_step-%09d.tar' % step)).write_bytes(b'tar')\n"
                "  (chkpt / 'scene_transform.json').write_text('{}')\n"
            )
            spirula.chmod(0o755)
            python = fake_bin / "python"
            python.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "if sys.argv[1].endswith('export_ply.py'):\n"
                "  args=sys.argv[2:]; out=args[args.index('--out-ply')+1]\n"
                "  open(out, 'wb').write(b'splat')\n"
                "else: os.execvp(os.environ['REAL_PYTHON'], [os.environ['REAL_PYTHON'], *sys.argv[1:]])\n"
            )
            python.chmod(0o755)
            env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}",
                       REAL_PYTHON=REAL_PYTHON, COMMAND_LOG=str(log),
                       TRAIN_SP_GROWTH_FACTOR="1.05", TRAIN_SP_CAP_MAX="1000",
                       TRAIN_SP_USE_PPISP="true",
                       TRAIN_SP_PPISP_PARAM_TYPE="no_crf",
                       TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF="true",
                       TRAIN_SP_USE_BILATERAL_GRID="false",
                       TRAIN_SP_BILAGRID_TYPE="ppisp")
            output = root / "output"
            command = [
                "bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                "--spirula-bin", str(spirula), "--python-bin", str(python),
                "--sparse-dir", str(sparse), "--images-dir", str(images),
                "--output-dir", str(output), "--num-iterations", "3",
            ]
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            train = next(json.loads(line) for line in log.read_text().splitlines()
                         if json.loads(line)[:2] == ["spirula", "train"])
            for flag, value in (
                ("--use-ppisp", "1"),
                ("--ppisp-param-type", "no_crf"),
                ("--ppisp-exposure-from-exif", "1"),
                ("--use-bilateral-grid", "0"),
                ("--bilagrid-type", "ppisp"),
            ):
                self.assertEqual(train[train.index(flag) + 1], value)
            self.assertIn("ppisp_param_type=no_crf", (output / "train_sp_photometric.txt").read_text())

            env["TRAIN_SP_PPISP_PARAM_TYPE"] = "no_crf_no_vig"
            reused = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(reused.returncode, 0, reused.stdout + reused.stderr)
            self.assertIn("d'autres paramètres photométriques", reused.stderr)
            self.assertEqual(sum(json.loads(line)[:2] == ["spirula", "train"]
                                 for line in log.read_text().splitlines()), 1)

    def test_quality_is_forwarded_and_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            env["TRAIN_SP_QUALITY"] = "ultra"
            env["TRAIN_SP_DISTRACTION_ROBUSTNESS"] = "strong"
            result = execute()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            for cmd in commands:
                if cmd[:2] == ["spirula", "train"]:
                    self.assertEqual(cmd[cmd.index("--quality") + 1], "ultra")
                    self.assertEqual(cmd[cmd.index("--distraction-robustness") + 1], "strong")
                    self.assertEqual(cmd[cmd.index("--cap-max") + 1], "1000")
                    self.assertEqual(cmd[cmd.index("--num-iterations") + 1], "3")
            env["TRAIN_SP_QUALITY"] = "invalid"
            result = execute("--prepare-only")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TRAIN_SP_QUALITY", result.stderr)
            result = subprocess.run(
                ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                 "--sparse-dir", str(sparse), "--images-dir", str(data / "images"),
                 "--output-dir", str(root / "unused"), "--quality", "invalid"],
                env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--quality doit", result.stderr)
            self.assertFalse((root / "unused").exists())

    def test_steps_per_save_is_forwarded_and_keeps_only_latest_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            env["TRAIN_SP_STEPS_PER_SAVE"] = "500"
            result = execute()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            trains = [cmd for cmd in commands if cmd[:2] == ["spirula", "train"]]
            self.assertTrue(trains)
            for cmd in trains:
                self.assertEqual(cmd[cmd.index("--steps-per-save") + 1], "500")
                self.assertEqual(cmd[cmd.index("--save-only-latest-checkpoint") + 1], "0")
            leftovers = sorted(p.name for p in root.rglob("step-*.ckpt"))
            self.assertTrue(leftovers)
            self.assertTrue(all("000000200" in name for name in leftovers), leftovers)
            env["TRAIN_SP_STEPS_PER_SAVE"] = "abc"
            result = execute("--prepare-only")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TRAIN_SP_STEPS_PER_SAVE", result.stderr)

    def test_invalid_distraction_robustness_is_rejected_by_workers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, data, sparse, env, log, execute = self.fixture(temporary)
            env["TRAIN_SP_DISTRACTION_ROBUSTNESS"] = "invalid"
            result = execute("--prepare-only")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TRAIN_SP_DISTRACTION_ROBUSTNESS", result.stderr)
            result = subprocess.run(
                ["bash", str(SCRIPT.with_name("run_spirula_block.sh")),
                 "--sparse-dir", str(sparse), "--images-dir", str(data / "images"),
                 "--output-dir", str(root / "unused"),
                 "--distraction-robustness", "invalid"],
                env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--distraction-robustness doit", result.stderr)
            self.assertFalse((root / "unused").exists())


class SingleBlockMergeTest(unittest.TestCase):
    def test_single_splat_is_copied_and_converted_formats_still_use_merge(self):
        helper = SCRIPT.with_name("pipeline_postprocess.sh")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "block.splat"
            source.write_bytes(b"splat-data")
            command = (
                f"repo_root={str(root)!r}; source {str(helper)!r}; "
                f"merge_pipeline_splats {str(root / 'out.splat')!r} 1.0 {str(source)!r}"
            )
            env = dict(os.environ, PYTHON_BIN="/bin/false")
            result = subprocess.run(["bash", "-c", command], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "out.splat").read_bytes(), b"splat-data")

            converted = command.replace("out.splat", "out.ply")
            result = subprocess.run(["bash", "-c", converted], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "out.ply").exists())


if __name__ == "__main__":
    unittest.main()
