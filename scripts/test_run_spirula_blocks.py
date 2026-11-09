import json
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
            )

            def execute(*args):
                return subprocess.run(
                    ["bash", str(SCRIPT), *args], cwd=root, env=env,
                    capture_output=True, text=True, timeout=30,
                )

            return root, data, sparse, env, log, execute

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
            result = execute()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            for cmd in commands:
                if cmd[:2] == ["spirula", "train"]:
                    self.assertEqual(cmd[cmd.index("--quality") + 1], "ultra")
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


if __name__ == "__main__":
    unittest.main()
