import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pycolmap

from tools.georef.georef_positions import run, read_positions
from tools.sfm.spirula.gps_to_metric_positions import check_gauge

if not hasattr(pycolmap.Reconstruction, "add_camera_with_trivial_rig"):
    raise RuntimeError(
        f"pycolmap {getattr(pycolmap, '__version__', '?')} trop ancien pour ces tests : "
        "installer pycolmap==4.2.1 (wheels macOS 14+ arm64 ; Python >= 3.10)")


def write_sparse(folder, centers):
    camera = pycolmap.Camera(model="PINHOLE", width=16, height=16, params=[8, 8, 8, 8], camera_id=1)
    model = pycolmap.Reconstruction()
    model.add_camera_with_trivial_rig(camera)
    for index, center in enumerate(centers):
        image = pycolmap.Image(name=f"p{index}.jpg", camera_id=1, image_id=index + 1,
                               keypoints=np.array([[8., 8.]]))
        model.add_image_with_trivial_frame(image, pycolmap.Rigid3d(pycolmap.Rotation3d(np.eye(3)), -np.asarray(center)))
    folder.mkdir()
    model.write(str(folder))


class GeorefPositionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.local = np.array([[0, 0, 0], [10, 0, 1], [10, 8, 2], [0, 8, 0.5], [5, 4, 1.5]], float)
        self.target = 2 * self.local + [1000, 2000, 50]
        self.origin = (48.0, 2.0, 100.0)

    def tearDown(self):
        self.tmp.cleanup()

    def write_positions(self, target, path=None, origin=None):
        origin = origin or self.origin
        path = path or self.root / "positions.txt"
        lines = [f"# origin_lat {origin[0]}", f"# origin_lon {origin[1]}", f"# origin_alt {origin[2]}"]
        lines += [f"p{i}.jpg {x} {y} {z}" for i, (x, y, z) in enumerate(target)]
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_sparse_is_aligned_on_positions_and_gauge_is_written(self):
        sparse, output = self.root / "sparse", self.root / "georef"
        write_sparse(sparse, self.local)
        positions = self.write_positions(self.target)
        run(sparse, output, positions, 2154, 20.0)
        aligned = pycolmap.Reconstruction(str(output))
        centers = np.array([aligned.images[i + 1].projection_center() for i in range(len(self.local))])
        np.testing.assert_allclose(centers, self.target, atol=1e-6)
        self.assertEqual(check_gauge(output / "gauge.txt", self.origin), [])
        self.assertFalse(json.loads((output / "georef_stats.json").read_text())["method_gap_alert"])

    def test_tilted_sfm_triggers_method_gap_alert(self):
        sparse, output = self.root / "sparse", self.root / "georef"
        target = np.array([[0, 0, 0], [20, 0, 3], [40, 10, -2], [30, 30, 4], [5, 25, 1], [10, 5, 6]], float)
        target += [1000, 2000, 50]
        angle = np.radians(10)
        tilt = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]])
        local = ((target - [1000, 2000, 50]) / 2.0) @ tilt
        write_sparse(sparse, local)
        positions = self.write_positions(target)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            run(sparse, output, positions, 2154, 100.0)
        stats = json.loads((output / "georef_stats.json").read_text())
        self.assertEqual(stats["alignment"], "sim3")
        self.assertTrue(stats["method_gap_alert"])
        self.assertGreater(stats["vertical_tilt_deg"], 5)
        self.assertIn("[GPS][ALERTE]", printed.getvalue())

    def test_quasi_straight_horizontal_trajectory_uses_known_vertical(self):
        sparse, output = self.root / "sparse", self.root / "georef"
        along = np.arange(12) * 10.0
        target = np.column_stack([along, 0.02 * np.sin(along), 0.01 * np.cos(along)]) + [1000, 2000, 50]
        scale, yaw, offset = 0.5, np.radians(30), np.array([3.0, -7.0, 2.0])
        rotation = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
        local = (target - offset) @ rotation / scale
        write_sparse(sparse, local)
        run(sparse, output, self.write_positions(target), 2154, 20.0)
        aligned = pycolmap.Reconstruction(str(output))
        centers = np.array([aligned.images[i + 1].projection_center() for i in range(len(local))])
        np.testing.assert_allclose(centers, target, atol=1e-6)
        self.assertEqual(json.loads((output / "georef_stats.json").read_text())["alignment"], "vertical")

    def test_inconsistent_positions_are_rejected(self):
        sparse, output = self.root / "sparse", self.root / "georef"
        write_sparse(sparse, self.local)
        bad = self.target.copy()
        bad[0] += [200, 0, 0]
        with self.assertRaisesRegex(ValueError, "Recalage incohérent"):
            run(sparse, output, self.write_positions(bad), 2154, 20.0)
        self.assertFalse((output / "georef_stats.json").exists())

    def test_sparse_image_without_position_is_rejected(self):
        sparse, output = self.root / "sparse", self.root / "georef"
        write_sparse(sparse, self.local)
        partial = self.write_positions(self.target[:4])
        with self.assertRaisesRegex(ValueError, "sans position"):
            run(sparse, output, partial, 2154, 20.0)
        self.assertFalse((output / "georef_stats.json").exists())

    def test_positions_file_without_origin_header_is_rejected(self):
        path = self.root / "positions.txt"
        path.write_text("p0.jpg 0 0 0\n")
        with self.assertRaisesRegex(ValueError, "origin_lat"):
            run(self.root, self.root / "out", path, 2154, 20.0)

    def test_duplicate_or_malformed_lines_are_rejected(self):
        path = self.root / "positions.txt"
        path.write_text("p0.jpg 0 0 0\np0.jpg 1 1 1\n")
        with self.assertRaisesRegex(ValueError, "en double"):
            read_positions(path)
        path.write_text("p0.jpg 0 0\n")
        with self.assertRaisesRegex(ValueError, "attendu"):
            read_positions(path)


if __name__ == "__main__":
    unittest.main()
