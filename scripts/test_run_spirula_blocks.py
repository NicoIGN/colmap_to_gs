import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run_spirula_blocks.sh").resolve()
REAL_PYTHON = os.environ.get("PYTHON", sys.executable)


class BlockPipelineTest(unittest.TestCase):
    def test_splits_once_and_trains_each_block(self):
        with tempfile.TemporaryDirectory() as temporary:
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
                NUM_ITER="3",
                NUM_BLOCKS="2",
                CAP_MAX="1000",
            )

            def execute():
                return subprocess.run(
                    ["bash", str(SCRIPT)], cwd=root, env=env,
                    capture_output=True, text=True, timeout=30,
                )

            first = execute()
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(cmd[0] == "split" for cmd in commands), 1)
            self.assertEqual(sum(cmd[:2] == ["spirula", "train"] for cmd in commands), 2)
            self.assertTrue((data / "gs-3-blocks-2/block_000/splats/Paris-3-block-000.splat").is_file())
            self.assertTrue((data / "gs-3-blocks-2/block_001/splats/Paris-3-block-001.splat").is_file())

            second = execute()
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(cmd[0] == "split" for cmd in commands), 1)
            self.assertEqual(sum(cmd[:2] == ["spirula", "train"] for cmd in commands), 2)


if __name__ == "__main__":
    unittest.main()
