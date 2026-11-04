import contextlib
import importlib.util
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("panoramax_prepare", Path(__file__).with_name("prepare_sfm.py"))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def fake_jpeg(width, height):
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + bytes(9)
    sof = b"\xff\xc0" + (11).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


class PrepareTest(unittest.TestCase):
    def make_dataset(self, root):
        (root / "images").mkdir()
        (root / "metadata").mkdir()
        for index in range(3):
            (root / "images" / f"a{index}.jpg").write_bytes(fake_jpeg(4096, 2160))
            orientation = {"field_of_view": 95, "sensor_array_dimensions": [4096, 2160]}
            feature = {"feature": {"properties": {"pers:interior_orientation": orientation}}}
            (root / "metadata" / f"a{index}.json").write_text(json.dumps(feature))
        (root / "images" / "pano.jpg").write_bytes(fake_jpeg(5760, 2880))
        (root / "images" / "phone.JPG").write_bytes(fake_jpeg(3072, 4080))
        (root / "manifest.jsonl").write_text("")

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            prepare.main(list(argv))
        return out.getvalue(), err.getvalue()

    def test_selects_majority_size_and_focal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pmx"
            root.mkdir()
            self.make_dataset(root)
            work = Path(tmp) / "pmx" / "sfm"
            out, err = self.run_main("--dataset", str(root), "--output-dir", str(work), "--shell")
            values = dict(line.split("=", 1) for line in out.splitlines())
            self.assertEqual(values["images_dir"], str(work.resolve() / "images"))
            self.assertAlmostEqual(float(values["focal"]), 2048 / math.tan(math.radians(47.5)), places=1)
            self.assertEqual(sorted(p.name for p in (work / "images").iterdir()), ["a0.jpg", "a1.jpg", "a2.jpg"])
            summary = json.loads((work / "selection.json").read_text())
            self.assertEqual(sorted(summary["excluded"]), ["pano.jpg", "phone.JPG"])
            self.assertIn("5760x2880 : 1", err)

            (root / "images" / "a1.jpg").unlink()
            (root / "images" / "a1.jpg").write_bytes(fake_jpeg(4096, 2160))
            self.run_main("--dataset", str(root), "--output-dir", str(work), "--size", "5760x2880")
            self.assertEqual([p.name for p in (work / "images").iterdir()], ["pano.jpg"])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_dataset(root)
            out, _ = self.run_main("--dataset", str(root / "images"), "--output-dir", str(root / "w"), "--shell", "--dry-run")
            self.assertIn("focal=1876.", out)
            self.assertFalse((root / "w").exists())


if __name__ == "__main__":
    unittest.main()
