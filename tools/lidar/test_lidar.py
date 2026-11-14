import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import laspy
import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial.transform import Rotation

from common import (Scene, class_codes, fit_similarity, load_lidar, register, sh_basis, sh_transform,
                    transform_records, write_scene)
from filter_splats_by_lidar import distance_mask
from merge_splats import BASE_DTYPE, SPLAT_DTYPE


class LidarTests(unittest.TestCase):
    def test_similarity_and_registration(self):
        points = np.random.default_rng(4).normal(size=(500, 3))
        r = Rotation.from_rotvec([0.01, -0.02, 0.01]).as_matrix()
        t = np.array([0.04, -0.03, 0.02])
        target = 1.01 * points @ r.T + t
        s, rotation, translation, report = register(
            points, target, distance=0.5, voxel=0.01, estimate_scale=True)
        self.assertAlmostEqual(s, 1.01, places=6)
        np.testing.assert_allclose(rotation, r, atol=1e-6)
        np.testing.assert_allclose(translation, t, atol=1e-6)
        self.assertLess(report["rmse_after"], 1e-6)
        self.assertGreater(report["rmse_before"], 0.01)

    def test_no_overlap_and_degenerate(self):
        points = np.random.default_rng(2).normal(size=(30, 3))
        with self.assertRaisesRegex(ValueError, "Recouvrement"):
            register(points, points + 100, distance=1, voxel=0.01)
        line = np.column_stack((np.arange(10), np.zeros((10, 2))))
        with self.assertRaisesRegex(ValueError, "dégénérées"):
            fit_similarity(line, line, False)
        with patch("common.fit_similarity", return_value=(1, np.eye(3), np.full(3, 0.001))):
            with self.assertRaisesRegex(ValueError, "non convergé"):
                register(points, points, distance=2, voxel=0.001, iterations=1)

    def test_filter_threshold_and_bbox(self):
        points = np.array([[0.5, 0, 0], [0.50001, 0, 0], [9, 9, 9]])
        keep, tested = distance_mask(
            points, np.array([[0., 0, 0]]), 0.5, np.array([-1, -1, -1, 1, 1, 1]))
        np.testing.assert_array_equal(keep, [True, False, True])
        self.assertEqual(tested, 2)

    def test_sh_rotation_preserves_directional_color(self):
        r = Rotation.from_rotvec([0.2, -0.1, 0.4]).as_matrix()
        directions = np.random.default_rng(0).normal(size=(80, 3))
        directions /= np.linalg.norm(directions, axis=1)[:, None]
        for count in (3, 8, 15):
            coefficients = np.arange(count) / count
            transformed = sh_transform(r, count) @ coefficients
            np.testing.assert_allclose(
                sh_basis(directions)[:, :count] @ transformed,
                sh_basis(directions @ r)[:, :count] @ coefficients, atol=1e-12)

    def test_splat_transform_and_ply_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = np.zeros(2, dtype=SPLAT_DTYPE)
            raw["position"] = [[1, 0, 0], [0, 1, 0]]
            raw["scale"] = [1, 2, 3]
            raw["rotation"] = [255, 128, 128, 128]
            raw["rgba"] = [12, 34, 56, 78]
            source = root / "in.splat"
            raw.tofile(source)
            scene = Scene(source)
            r = Rotation.from_euler("z", 90, degrees=True).as_matrix()
            result = transform_records(scene, scene.raw, 2, r, np.ones(3))
            np.testing.assert_allclose(result["position"], [[1, 3, 1], [-1, 1, 1]], atol=1e-6)
            np.testing.assert_allclose(result["scale"], [[2, 4, 6]]*2)
            np.testing.assert_array_equal(result["rgba"], raw["rgba"])
            q = (result["rotation"].astype(float)-128)/128
            np.testing.assert_allclose(
                Rotation.from_quat(q[:, [1, 2, 3, 0]]).as_matrix(),
                np.repeat(r[None], 2, axis=0), atol=0.02)
            scene.close()

    def test_ply_transformation_preserves_attributes_and_sh(self):
        dtype = np.dtype(BASE_DTYPE.descr + [(f"f_rest_{i}", "<f4") for i in range(9)]
                         + [("custom", "<i4")])
        vertex = np.zeros(3, dtype=dtype)
        vertex["rot_0"] = 1
        vertex["x"] = [0, 1, 2]
        vertex["custom"] = [10, 20, 30]
        vertex["opacity"] = 0.7
        for i in range(9):
            vertex[f"f_rest_{i}"] = i / 10
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "in.ply"
            output = Path(directory) / "out.ply"
            PlyData([PlyElement.describe(vertex, "vertex")]).write(source)
            scene = Scene(source)
            r = Rotation.from_euler("z", 45, degrees=True).as_matrix()
            sh = sh_transform(r, 3)
            write_scene(output, scene,
                        transform=lambda raw: transform_records(scene, raw, 2, r, np.ones(3), sh))
            data = PlyData.read(output)["vertex"].data
            np.testing.assert_array_equal(data["custom"], vertex["custom"])
            np.testing.assert_array_equal(data["opacity"], vertex["opacity"])
            np.testing.assert_allclose(data["scale_0"], np.log(2), atol=1e-7)
            np.testing.assert_allclose(data["x"], 1 + 2 * vertex["x"] * np.cos(np.pi/4), atol=1e-6)
            before = np.array([vertex[f"f_rest_{i}"][0] for i in range(9)]).reshape(3, 3)
            after = np.array([data[f"f_rest_{i}"][0] for i in range(9)]).reshape(3, 3)
            np.testing.assert_allclose(after, before @ sh.T, atol=1e-7)
            scene.close()

    def test_big_endian_ply_subset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vertex = np.zeros(2, dtype=BASE_DTYPE)
            vertex["rot_0"] = 1
            vertex["x"] = [0, 2]
            ply = root / "in.ply"
            PlyData([PlyElement.describe(vertex, "vertex")], byte_order=">").write(ply)
            scene = Scene(ply)
            output = root / "out.ply"
            write_scene(output, scene, np.array([False, True]))
            np.testing.assert_array_equal(PlyData.read(output)["vertex"].data["x"], [2])
            scene.close()

    def test_laz_cli_origin_dryrun_and_rejections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = np.array([650000, 6860000, 100])
            xyz = np.random.default_rng(3).normal(size=(100, 3))
            cloud = laspy.LasData(laspy.LasHeader(point_format=0, version="1.2"))
            cloud.header.scales = [0.0001]*3
            cloud.header.offsets = origin
            cloud.x, cloud.y, cloud.z = (xyz + origin).T
            cloud.classification = np.array([2]*50 + [6]*50, dtype="u1")
            laz = root / "cloud.laz"
            cloud.write(laz)
            selected = load_lidar(laz, origin, classes=class_codes("6"))
            np.testing.assert_allclose(selected, xyz[50:], atol=0.000051)
            with self.assertRaisesRegex(ValueError, "trois points"):
                load_lidar(laz, origin, classes={5})
            with self.assertRaises(ValueError):
                class_codes("256")
            raw = np.zeros(101, dtype=SPLAT_DTYPE)
            raw["position"][:100] = xyz
            raw["position"][-1] = [50, 50, 50]
            raw["scale"] = 0.1
            raw["rotation"] = [255, 128, 128, 128]
            raw["rgba"] = [20, 30, 40, 200]
            source = root / "in.splat"
            raw.tofile(source)
            output = root / "out.splat"
            rejected = root / "rejected.splat"
            command = [sys.executable, str(Path(__file__).with_name("filter_splats_by_lidar.py")),
                       "--lidar", str(laz), "--input", str(source), "--output", str(output),
                       "--origin", ",".join(map(str, origin)), "--max-distance", "0.01",
                       "--rejected-output", str(rejected)]
            result = subprocess.run(command + ["--dry-run"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report_path = output.with_suffix(".splat.json")
            self.assertEqual(json.loads(report_path.read_text())["rejected"], 1)
            self.assertFalse(output.exists())
            report_path.unlink()
            result = subprocess.run(command + ["--dry-run", "--lidar-classes", "2"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text())
            self.assertEqual(report["lidar_classes"], [2])
            self.assertEqual(report["rejected"], 51)
            report_path.unlink()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            np.testing.assert_array_equal(np.fromfile(output, dtype=SPLAT_DTYPE), raw[:100])
            np.testing.assert_array_equal(np.fromfile(rejected, dtype=SPLAT_DTYPE), raw[100:])
            aligned = root / "aligned.splat"
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("align_splats_to_lidar.py")),
                 "--lidar", str(laz), "--input", str(output), "--output", str(aligned),
                 "--origin", ",".join(map(str, origin)), "--max-correspondence-distance", "0.1",
                 "--voxel-size", "0.001", "--lidar-classes", "2,6"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(aligned.with_suffix(".splat.json").read_text())
            self.assertLess(report["rmse_after"], 0.001)


if __name__ == "__main__":
    unittest.main()
