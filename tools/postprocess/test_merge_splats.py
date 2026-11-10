import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from plyfile import PlyData, PlyElement


SCRIPT = Path(__file__).with_name("merge_splats.py")
SPEC = importlib.util.spec_from_file_location("merge_splats", SCRIPT)
merge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge)


class MergeSplatsTest(unittest.TestCase):
    def test_keys_preserve_voxel_membership_and_order(self):
        rng = np.random.default_rng(42)
        xyz = rng.uniform(-100, 100, (1000, 3))
        xyz = np.concatenate((xyz, xyz[:10]))
        cells = np.floor(xyz / 0.1).astype(np.int64)
        old = cells + (1 << 20)
        packed = (old[:, 0] << 42) | (old[:, 1] << 21) | old[:, 2]
        keys = merge.voxel_keys(xyz, 0.1)
        np.testing.assert_array_equal(np.argsort(keys, kind="stable"),
                                      np.argsort(packed, kind="stable"))
        unique, inverse = np.unique(keys, return_inverse=True)
        np.testing.assert_array_equal(inverse, np.unique(packed, return_inverse=True)[1])
        np.testing.assert_array_equal(np.searchsorted(unique, keys), inverse)

    def test_large_positive_negative_coordinates_do_not_collide(self):
        xyz = np.array([[1e6, 0, 0], [1e6, 0, 0], [-1e6, 0, 0],
                        [0, 1e6, 0], [0, 0, 1e6], [0, 0, -1e6]])
        self.assertEqual(len(np.unique(merge.voxel_keys(xyz, 7.38095e-5))), 5)

    def test_invalid_geometry_and_voxel_are_rejected(self):
        for xyz, voxel in (([[np.inf, 0, 0]], 1),
                           ([[np.nan, 0, 0]], 1),
                           ([[float(2 ** 63), 0, 0]], 1),
                           ([[-float(2 ** 64), 0, 0]], 1),
                           ([[0, 0, 0]], 0),
                           ([[0, 0, 0]], np.nan)):
            with self.subTest(xyz=xyz, voxel=voxel), self.assertRaises(ValueError):
                merge.voxel_keys(np.array(xyz), voxel)

    def test_full_merge_preserves_geometry_and_selects_winners(self):
        for format_ in ("splat", "ply"):
            with self.subTest(format_=format_), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                inputs = []
                for i in range(2):
                    records = np.zeros(3, dtype=merge.SPLAT_DTYPE)
                    records["position"] = [[-10000, 0, 0], [10000, 0, 0],
                                           [0, i * 10000, 0]]
                    records["scale"] = 1e-5
                    records["rotation"] = [128, 128, 128, 255]
                    records["rgba"] = [50 + i * 100, 80, 90, 200]
                    path = root / f"block{i}.{format_}"
                    if format_ == "splat":
                        records.tofile(path)
                    else:
                        raw = root / f"raw{i}.splat"
                        records.tofile(raw)
                        scene = merge.Scene(raw)
                        try:
                            data = scene.records(0, 3)
                        finally:
                            scene.close()
                        PlyData([PlyElement.describe(data, "vertex")]).write(path)
                    inputs.append(str(path))
                output = root / f"merged.{format_}"
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *inputs, "--output", str(output)],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                scene = merge.Scene(output)
                try:
                    self.assertEqual(scene.count, 4)
                    xyz, _ = scene.geometry(slice(None))
                    self.assertEqual(set(map(tuple, xyz)),
                                     {(-10000, 0, 0), (10000, 0, 0),
                                      (0, 0, 0), (0, 10000, 0)})
                    # Equal scores in shared voxels retain the first scene.
                    data = scene.records(0, scene.count)
                    shared = np.abs(data["x"]) == 10000
                    rgb = 0.5 + merge.C0 * data["f_dc_0"][shared]
                    np.testing.assert_allclose(rgb, 50 / 255, atol=1e-6)
                finally:
                    scene.close()


if __name__ == "__main__":
    unittest.main()
