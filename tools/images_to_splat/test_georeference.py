import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np


SOURCE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "gpx_georeference", SOURCE.parent / "preprocess/georef_colmap_from_gpx.py")
gpx_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gpx_tool)


class RealGpxGeoreferenceTest(unittest.TestCase):
    def test_real_alignment_transforms_colmap_and_exports_enu_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            track = root / "source.gpx"
            t = np.linspace(0, 1, 30)
            lat = 48.87 + 0.002 * t + 0.0004 * np.sin(t * 7)
            lon = 2.34 + 0.003 * t + 0.0005 * np.sin(t * 11)
            height = 50 + 10 * np.sin(t * 5)
            track.write_text(
                '<gpx><trk><trkseg>' +
                "".join(f'<trkpt lat="{a}" lon="{b}"><ele>{h}</ele></trkpt>'
                        for a, b, h in zip(lat, lon, height)) +
                '</trkseg></trk></gpx>')
            expected = gpx_tool.read_gpx(track)["segments"][0]
            angle = 0.7
            rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                                 [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
            centers = (expected - [100, 200, 30]) @ rotation / 2.5
            sparse = root / "sfm/sparse/0"
            sparse.mkdir(parents=True)
            # One PINHOLE camera; images have identity world-to-camera rotation.
            (sparse / "cameras.bin").write_bytes(
                struct.pack("<QiiQQdddd", 1, 1, 1, 640, 480, 500, 500, 320, 240))
            with (sparse / "images.bin").open("wb") as stream:
                stream.write(struct.pack("<Q", len(centers)))
                for index, center in enumerate(centers):
                    stream.write(struct.pack("<i7di", index + 1, 1, 0, 0, 0,
                                             *(-center), 1))
                    stream.write(f"h00/paris_f{index:06d}.jpg".encode() + b"\0")
                    stream.write(struct.pack("<Q", 0))
            point = centers[10] + [1, 2, 3]
            (sparse / "points3D.bin").write_bytes(
                struct.pack("<Q", 1) +
                struct.pack("<QdddBBBdQ", 1, *point, 255, 0, 0, 0.1, 0))
            env = dict(os.environ, OUTPUT_DIR=str(root), PYTHON_BIN=sys.executable,
                       GEOREF_GPX_FILE=str(track), GEOREF_VIEW_PREFIX="h00",
                       GEOREF_EPSG="2154", GEOREF_ALLOW_UNCERTAIN="true",
                       EXPORT_SPLAT_NAME="scene.splat", PYTHONDONTWRITEBYTECODE="1")

            def execute(phase):
                result = subprocess.run(
                    [sys.executable, str(SOURCE / "georeference.py"), phase],
                    env=env, capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            execute("prepare")
            execute("check")
            execute("export")
            stats = json.loads((root / "scene.georef.json").read_text())
            self.assertEqual(stats["output_reference"]["axes"], ["East", "North", "Up"])
            self.assertEqual(stats["output_reference"]["units"], "meters")
            self.assertAlmostEqual(stats["output_reference"]["origin_lat_lon_height"][0],
                                   float(lat[0]))
            self.assertFalse((root / "georef/georef_trajectories.gpkg").exists())
            self.assertEqual((root / "georef/source.gpx").read_bytes(), track.read_bytes())
            self.assertEqual((sparse / "points3D.bin").read_bytes()[16:40],
                             struct.pack("<ddd", *point))
            transform = stats["transform"]
            transformed = (transform["scale"] * centers @ np.array(transform["R"]).T +
                           np.array(transform["t"]))
            self.assertLess(float(np.sqrt(np.mean(np.sum((transformed - expected) ** 2, axis=1)))), 2)
            data = (root / "georef/0/points3D.bin").read_bytes()
            result_point = np.array(struct.unpack_from("<ddd", data, 16))
            expected_point = (transform["scale"] * np.array(transform["R"]) @ point +
                              np.array(transform["t"]))
            np.testing.assert_allclose(result_point, expected_point, atol=1e-8)
            # The sidecar is directly consumable by the existing 3D Tiles reader.
            converter_spec = importlib.util.spec_from_file_location(
                "splat_convert", SOURCE.parent / "splat_to_3dtiles/convert.py")
            converter = importlib.util.module_from_spec(converter_spec)
            converter_spec.loader.exec_module(converter)
            reference = converter.read_georeference(root / "scene.georef.json")
            self.assertIsNotNone(reference)


if __name__ == "__main__":
    unittest.main()
