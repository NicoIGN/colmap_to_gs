import sys
import unittest
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/export"))
from clean_ply import bbox_support_mask, splat_half_extent


class CleanPlyBboxTests(unittest.TestCase):
    def test_support_crossing_bbox_margin_is_removed(self):
        lo, hi = np.zeros(3), np.array([10., 10., 10.])
        xyz = np.array([[5., 5., 5.],    # inside
                        [-0.4, 5., 5.],  # centre outside margin 0.5, support ok
                        [-0.6, 5., 5.],  # support crosses margin 0.5
                        [5., 5., 9.9]])  # support (1.0) crosses top
        half = np.array([[1., 1., 1.], [.05, .05, .05], [.05, .05, .05], [1., 1., 1.]])
        removed = bbox_support_mask(xyz, half, lo, hi, margin=0.5)
        self.assertEqual(removed.tolist(), [False, False, True, True])

    def test_half_extent_uses_sigmas_times_exp_scale(self):
        raw = np.zeros(2, dtype=[("x", "f4"), ("y", "f4"), ("z", "f4"),
                                 ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4")])
        raw["scale_0"] = raw["scale_1"] = raw["scale_2"] = np.log([.1, .2])
        ply = PlyData([PlyElement.describe(raw, "vertex")])
        half = splat_half_extent(ply, 3.0)
        np.testing.assert_allclose(half[:, 0], [0.3, 0.6], rtol=1e-6)


if __name__ == "__main__":
    unittest.main()
