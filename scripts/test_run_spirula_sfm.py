import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run_spirula_sfm.sh")


class SfmRecoveryTest(unittest.TestCase):
    def test_recovery_requires_new_readable_nonempty_files_and_sigsegv(self):
        for kind, status, previous, expected in (
            ("valid", 139, False, 0),
            ("missing", 139, False, 139),
            ("truncated", 139, False, 139),
            ("empty", 139, False, 139),
            ("valid", 139, True, 139),
            ("valid", 3, False, 3),
            ("valid", 4, False, 4),
        ):
            with self.subTest(kind=kind, status=status, previous=previous):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    images = root / "images"
                    images.mkdir()
                    (images / "a.jpg").write_bytes(b"image")
                    output = root / "sfm"
                    if previous:
                        sparse = output / "sparse" / "0"
                        sparse.mkdir(parents=True)
                        (sparse / "images.bin").write_bytes(b"old")
                    spirula = root / "spirula"
                    spirula.write_text(
                        f"#!{sys.executable}\n"
                        "import pathlib, struct, sys\n"
                        "p = pathlib.Path(sys.argv[sys.argv.index('--output')+1])/'sparse'/'0'\n"
                        "p.mkdir(parents=True, exist_ok=True)\n"
                        "pack = struct.pack\n"
                        "p.joinpath('cameras.bin').write_bytes(pack('<QIiQQ3d',1,1,0,100,100,50,50,50))\n"
                        "p.joinpath('images.bin').write_bytes(pack('<QI7dI',1,1,1,0,0,0,0,0,0,1)+b'a.jpg\\0'+pack('<Q',0))\n"
                        "p.joinpath('points3D.bin').write_bytes(pack('<QQdddBBBdQ',1,1,0,0,1,1,2,3,0.1,0))\n"
                        f"kind = {kind!r}\n"
                        "if kind == 'missing': p.joinpath('points3D.bin').unlink()\n"
                        "if kind == 'truncated': p.joinpath('images.bin').write_bytes(pack('<Q',1))\n"
                        "if kind == 'empty': p.joinpath('points3D.bin').write_bytes(pack('<Q',0))\n"
                        f"sys.exit({status})\n"
                    )
                    spirula.chmod(0o755)
                    result = subprocess.run(
                        ["bash", str(SCRIPT), "--spirula-bin", str(spirula),
                         "--dataset", str(images), "--output-dir", str(output)],
                        env=dict(os.environ, PYTHON_BIN=sys.executable, PREPARE="false"),
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    if expected == 0:
                        self.assertIn("Poursuite avec ce modèle", result.stderr)
                        self.assertIn("1 caméras, 1 images, 1 points", result.stdout)


if __name__ == "__main__":
    unittest.main()
