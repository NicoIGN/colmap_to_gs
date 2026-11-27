import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from tools.sfm.colmap.colmap_pseudo_gpx import (
    GPX_NAMESPACE, frame_order, order_centres, path_length, simplify_polyline, two_opt, write_gpx,
)
from tools.georef.georef_colmap_from_gpx import enu_to_wgs84


class PseudoGpxOrderingTest(unittest.TestCase):
    def test_two_opt_untangles_crossing_path(self):
        square = np.array([[0, 0, 0], [1, 1, 0], [1, 0, 0], [0, 1, 0]], float)
        order = two_opt(square, [0, 1, 2, 3])
        self.assertAlmostEqual(path_length(square[order]), 3.0, places=9)

    def test_shuffled_trajectory_is_recovered_up_to_reversal(self):
        rng = np.random.default_rng(0)
        t = np.linspace(0, 1, 120)
        points = np.column_stack([t * 200, 20 * np.sin(t * 6), np.zeros_like(t)])
        perm = rng.permutation(len(points))
        tangent = np.column_stack([np.full_like(t, 200.0), 120 * np.cos(t * 6), np.zeros_like(t)])
        tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
        names = [f"img_f{i:05d}" for i in range(len(points))]
        order = order_centres(names, points[perm], tangent[perm], "path", weight=1.0)
        recovered = perm[order]
        self.assertGreater(abs(np.corrcoef(np.arange(len(points)), recovered)[0, 1]), 0.999)

    def test_simplification_keeps_curve_within_tolerance(self):
        t = np.linspace(0, 1, 200)
        points = np.column_stack([t * 100, np.sin(t * 3) * 5, np.zeros_like(t)])
        points[50, 1] += 0.05
        keep = simplify_polyline(points, tolerance=1.0)
        self.assertLess(keep.sum(), len(points))
        self.assertTrue(keep[0] and keep[-1])
        kept = points[keep]
        for p in points:
            seg = np.diff(kept, axis=0)
            rel = p - kept[:-1]
            along = np.clip(np.sum(rel * seg, axis=1) / np.sum(seg * seg, axis=1), 0, 1)
            dist = np.linalg.norm(rel - along[:, None] * seg, axis=1).min()
            self.assertLessEqual(dist, 1.0 + 1e-9)

    def test_zero_tolerance_keeps_all_points_shape(self):
        points = np.array([[0, 0, 0], [1, 1, 0], [2, 0, 0]], float)
        self.assertTrue(simplify_polyline(points, tolerance=0.1).all())

    def test_frame_order_uses_frame_index_not_positions(self):
        names = ["x_f00010.jpg", "x_f00002.jpg", "x_f00007.jpg"]
        np.testing.assert_array_equal(frame_order(names), [1, 2, 0])

    def test_frame_order_rejects_names_without_frame(self):
        with self.assertRaisesRegex(ValueError, "frame"):
            frame_order(["plain.jpg"])

    def test_gpx_round_trip_matches_enu_positions(self):
        origin = (48.848305432, 2.393715452, 93.426)
        enu = np.array([[0.0, 0.0, 0.0], [100.0, -50.0, 3.0]])
        coordinates = enu_to_wgs84(enu, origin)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.gpx"
            write_gpx(path, coordinates, "test")
            points = ET.parse(path).getroot().findall(f".//{{{GPX_NAMESPACE}}}trkpt")
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(float(points[1].get("lon")), coordinates[1][0], places=8)
        self.assertAlmostEqual(float(points[1].get("lat")), coordinates[1][1], places=8)
        self.assertAlmostEqual(float(points[1].find(f"{{{GPX_NAMESPACE}}}ele").text), coordinates[1][2], places=3)


if __name__ == "__main__":
    unittest.main()
