import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image


SCRIPT = Path(__file__).with_name("generate_masks.py")


class NativeMasksTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.images = self.root / "images"
        (self.images / "sub").mkdir(parents=True)
        self.output = self.root / "masks"
        self.model = self.root / "sam3-q4_0.ggml"
        self.model.write_bytes(b"model")
        Image.new("RGB", (16, 8)).save(self.images / "sub" / "a.jpg")
        rotated = Image.new("RGB", (16, 8))
        exif = rotated.getexif()
        exif[0x0112] = 6
        rotated.save(self.images / "a.jpg", exif=exif)
        self.log = self.root / "calls.jsonl"
        self.binary = self.root / "spirula"
        self.binary.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "from PIL import Image\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['SAM_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
            "if os.environ.get('SAM_FAIL'):\n"
            "    print('SAM failure diagnostic', file=sys.stderr); sys.exit(7)\n"
            "frames = Path(args[args.index('--frames') + 1])\n"
            "out = Path(args[args.index('--out') + 1])\n"
            "for index, source in enumerate(sorted(frames.iterdir())):\n"
            "    with Image.open(source) as image:\n"
            "        if os.environ.get('SAM_MISSING'): continue\n"
            "        mode = 'RGB' if os.environ.get('SAM_RGB') else 'L'\n"
            "        size = (32, 16) if os.environ.get('SAM_SIZE') else image.size\n"
            "        mask = Image.new(mode, size, 255)\n"
            "        mask.paste(0, (0, 0, 4, image.height))\n"
            "        mask.save(out / f'frame_{index:05d}.png')\n"
        )
        self.binary.chmod(0o755)
        self.env = dict(os.environ, SAM_LOG=str(self.log))

    def execute(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--backend", "spirula",
             "--spirula-bin", str(self.binary), "--spirula-device", "0",
             "--model", str(self.model), "--images", str(self.images),
             "--output", str(self.output), *extra],
            env=self.env, text=True, capture_output=True, timeout=30)

    def test_native_arguments_raw_orientation_resume_and_label_change(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertRegex(result.stdout, r"Calcul SAM .*0/2")
        self.assertRegex(result.stdout, r"Calcul SAM .*2/2")
        self.assertRegex(result.stdout, r"Génération des masques .*2/2")
        self.assertNotIn("\r", result.stdout)
        command = json.loads(self.log.read_text())
        self.assertEqual(command[:2], ["sam", "track"])
        for flag, value in (("--mask-mode", "image"), ("--device", "0"),
                            ("--dilate-ratio", "0"), ("--text", "sky")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("--remove-prompted", command)
        self.assertNotIn("--accept-license", command)
        for name in ("a.jpg", "sub/a.jpg"):
            with Image.open(self.output / (name + ".png")) as image:
                mask = np.asarray(image)
                self.assertEqual(mask.shape, (8, 16))
                self.assertTrue((mask[:, :4] == 0).all())
                self.assertTrue((mask[:, 4:] == 255).all())
        report = json.loads((self.output / "masks.json").read_text())
        self.assertEqual(report["settings"]["backend"], "spirula")
        self.assertEqual(report["summary"]["exif_rotated"], 1)
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertIn("0 générés, 2 réutilisés", result.stdout)
        self.assertRegex(result.stdout, r"Génération des masques .*2/2")
        result = self.execute("--labels", "sky,person,car", "--max-masked-fraction", "0.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        command = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual(command[command.index("--text") + 1], "sky; person; car")
        report = json.loads((self.output / "masks.json").read_text())
        self.assertEqual(report["summary"]["overmasked_kept"], 2)
        with Image.open(self.output / "a.jpg.png") as image:
            self.assertTrue((np.asarray(image) == 255).all())

    def test_failed_missing_or_invalid_masks_never_complete(self):
        for flag in ("SAM_FAIL", "SAM_MISSING", "SAM_RGB", "SAM_SIZE"):
            with self.subTest(flag=flag):
                self.env[flag] = "1"
                result = self.execute()
                self.assertNotEqual(result.returncode, 0)
                if flag == "SAM_FAIL":
                    self.assertIn("SAM failure diagnostic", result.stderr)
                report = json.loads((self.output / "masks.json").read_text())
                self.assertEqual(report["status"], "partial")
                self.assertFalse((self.output / "a.jpg.png").exists())
                del self.env[flag]

    def test_explicit_license_is_forwarded_without_changing_cache(self):
        result = self.execute("--accept-spirula-license")
        self.assertEqual(result.returncode, 0, result.stderr)
        command = json.loads(self.log.read_text())
        self.assertEqual(command[command.index("--accept-license") + 1], "sam3=yes")
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_native_progress_is_emitted_before_the_process_finishes(self):
        source = self.binary.read_text()
        source = source.replace("import json, os, sys", "import json, os, sys, time")
        source += (
            "        if index == 0: time.sleep(1.1)\n"
            "print('SAM process finished', flush=True)\n"
        )
        self.binary.write_text(source)
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"Calcul SAM .*1/2")
        self.assertLess(result.stdout.index("1/2", result.stdout.index("Calcul SAM")),
                        result.stdout.index("SAM process finished"))

    def test_diagonal_dilation_and_detection_threshold(self):
        result = self.execute("--confidence", "0.7", "--dilate-fraction", "0.04")
        self.assertEqual(result.returncode, 0, result.stderr)
        command = json.loads(self.log.read_text())
        self.assertEqual(command[command.index("--threshold") + 1], "0.7")
        with Image.open(self.output / "a.jpg.png") as image:
            mask = np.asarray(image)
            # round(0.04 * hypot(16, 8)) = 1: four black columns grow to five.
            self.assertTrue((mask[:, :5] == 0).all())
            self.assertTrue((mask[:, 5:] == 255).all())

    def test_backend_change_regenerates_masks_and_failure_preserves_old_output(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        report_file = self.output / "masks.json"
        report = json.loads(report_file.read_text())
        report["settings"].pop("backend")
        report_file.write_text(json.dumps(report))
        previous = report_file.read_bytes()
        self.env["SAM_FAIL"] = "1"
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report_file.read_bytes(), previous)
        self.assertEqual(list(self.root.glob(".masks.tmp-*")), [])
        del self.env["SAM_FAIL"]
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(report_file.read_text())["settings"]["backend"], "spirula")


if __name__ == "__main__":
    unittest.main()
