import contextlib
import importlib.util
import io
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("panoramax_prepare", Path(__file__).with_name("prepare_panoramax.py"))
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

    def test_multi_cam_keeps_every_size_in_its_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_dataset(root)
            work = root / "w"
            with mock.patch.dict(os.environ, {"PANORAMAX_MULTI_CAM": "true"}):
                out, _ = self.run_main("--dataset", str(root), "--output-dir", str(work), "--shell")
            self.assertIn("focal=", out)
            self.assertNotIn("focal=0", out)
            self.assertEqual(
                sorted(p.relative_to(work / "images").as_posix() for p in (work / "images").rglob("*") if p.is_file()),
                ["3072x4080/phone.JPG", "4096x2160/a0.jpg", "4096x2160/a1.jpg", "4096x2160/a2.jpg", "5760x2880/pano.jpg"],
            )
            summary = json.loads((work / "selection.json").read_text())
            self.assertTrue(summary["multi_cam"])
            self.assertEqual(summary["excluded"], [])
            self.assertEqual(summary["sizes"], {"4096x2160": 3, "5760x2880": 1, "3072x4080": 1})
            self.assertIsNone(summary["focal_px"])
            self.assertAlmostEqual(summary["focals_px"]["4096x2160"], 2048 / math.tan(math.radians(47.5)), places=1)

    def test_multi_cam_drops_sizes_under_one_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            for index in range(200):
                (root / "images" / f"a{index}.jpg").write_bytes(fake_jpeg(4096, 2160))
            (root / "images" / "rare.jpg").write_bytes(fake_jpeg(3072, 4080))
            for index in range(3):
                (root / "images" / f"c{index}.jpg").write_bytes(fake_jpeg(5760, 2880))
            work = root / "w"
            with mock.patch.dict(os.environ, {"PANORAMAX_MULTI_CAM": "true"}):
                self.run_main("--dataset", str(root), "--output-dir", str(work))
            summary = json.loads((work / "selection.json").read_text())
            self.assertEqual(summary["excluded"], ["rare.jpg"])
            self.assertEqual(summary["sizes"], {"4096x2160": 200, "5760x2880": 3})
            self.assertFalse((work / "images" / "3072x4080").exists())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_dataset(root)
            out, _ = self.run_main("--dataset", str(root / "images"), "--output-dir", str(root / "w"), "--shell", "--dry-run")
            self.assertIn("focal=1876.", out)
            self.assertFalse((root / "w").exists())

    def test_equirectangular_selection_sets_camera_model_without_focal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            (root / "metadata").mkdir()
            for index in range(2):
                (root / "images" / f"p{index}.jpg").write_bytes(fake_jpeg(4096, 2048))
                feature = {"feature": {"properties": {"type": "equirectangular", "pers:interior_orientation": {"field_of_view": 95}}}}
                (root / "metadata" / f"p{index}.json").write_text(json.dumps(feature))
            out, _ = self.run_main("--dataset", str(root), "--output-dir", str(root / "w"), "--shell")
            values = dict(line.split("=", 1) for line in out.splitlines())
            self.assertEqual(values["camera_model"], "equirectangular")
            self.assertEqual(values["focal"], "")
            self.assertEqual(json.loads((root / "w" / "selection.json").read_text())["camera_model"], "equirectangular")

    def test_mixed_equirectangular_and_perspective_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            (root / "metadata").mkdir()
            for index, projection in enumerate(["equirectangular", "perspective"]):
                (root / "images" / f"p{index}.jpg").write_bytes(fake_jpeg(4096, 2048))
                feature = {"feature": {"properties": {"type": projection}}}
                (root / "metadata" / f"p{index}.json").write_text(json.dumps(feature))
            with self.assertRaises(SystemExit) as raised:
                self.run_main("--dataset", str(root), "--output-dir", str(root / "w"), "--shell")
            self.assertIn("mélangées", str(raised.exception))

    def test_multi_size_360_does_not_announce_focal_estimation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            (root / "metadata").mkdir()
            sizes = [(4096, 2048)] * 6 + [(5760, 2880)] * 6
            for index, (width, height) in enumerate(sizes):
                (root / "images" / f"p{index}.jpg").write_bytes(fake_jpeg(width, height))
                feature = {"feature": {"properties": {"type": "equirectangular"}}}
                (root / "metadata" / f"p{index}.json").write_text(json.dumps(feature))
            out, err = self.run_main("--dataset", str(root), "--output-dir", str(root / "w"), "--multi-cam", "--shell")
            values = dict(line.split("=", 1) for line in out.splitlines())
            self.assertEqual(values["camera_model"], "equirectangular")
            self.assertEqual(values["focal"], "")
            self.assertNotIn("Spirula estime", err)

    def test_per_collection_layout_ignores_sfm_workspace_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "panoramax"
            collection = dataset / "immergis"
            (collection / "images").mkdir(parents=True)
            (collection / "metadata").mkdir()
            (dataset / "sfm" / "images").mkdir(parents=True)
            for name in ("a0", "a1"):
                (collection / "images" / f"{name}.jpg").write_bytes(fake_jpeg(4096, 2160))
                (collection / "metadata" / f"{name}.json").write_text(json.dumps({"download": {}}))
            (dataset / "sfm" / "images" / "stale.jpg").write_bytes(fake_jpeg(4096, 2160))
            work = Path(tmp) / "work"
            out, _ = self.run_main("--dataset", str(dataset), "--output-dir", str(work), "--shell")
            self.assertEqual(sorted(p.name for p in (work / "images").iterdir()), ["a0.jpg", "a1.jpg"])
            self.assertIn("focal=", out)


if __name__ == "__main__":
    unittest.main()
