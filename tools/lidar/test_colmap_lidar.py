import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import laspy
import numpy as np
import pycolmap
from PIL import Image
from pyproj import Transformer
from scipy.spatial.transform import Rotation

import align_colmap_to_lidar as tool


def reconstruction():
    model = pycolmap.Reconstruction()
    camera = pycolmap.Camera(camera_id=1, model="PINHOLE", width=200, height=200,
                            params=[100, 100, 100, 100])
    model.add_camera_with_trivial_rig(camera)
    for image_id, x in enumerate((-1., 0., 1.), 1):
        image = pycolmap.Image(name=f"{image_id}.png", camera_id=1, image_id=image_id)
        pose = pycolmap.Rigid3d(pycolmap.Rotation3d(), np.array([-x, 0, 0]))
        model.add_image_with_trivial_frame(image, pose)
    points = np.random.default_rng(4).uniform([-1, -1, 4], [1, 1, 6], (60, 3))
    for xyz in points:
        model.add_point3D(xyz, pycolmap.Track(), np.array([10, 20, 30], dtype="u1"))
    return model, points


def options(**kwargs):
    args = dict(origin="0,0,0", terrain_center=None, scale_min=0.8, scale_max=1.2,
                scale_steps=3, search_radius=0, translation_step=5, yaw_steps=12,
                search_points=2000, max_candidates=20000, refine_candidates=8,
                max_tilt=10, min_overlap=0.3, voxel_size=0.02,
                max_correspondence_distance=0.5, accept_ambiguous=False,
                inject_voxel=0.05, inject_max_points=100, inject_min_separation=0.02,
                min_views=2, min_view_angle=2, depth_tolerance=0.1, visibility_cell=4,
                keypoint_separation=2, inject=False, images=None, georef=None,
                already_projected=True, lidar_crs="EPSG:2154", lidar_classes=None)
    args.update(kwargs)
    return argparse.Namespace(**args)


class ColmapLidarTests(unittest.TestCase):
    def test_search_non_georef_yaw_scale_and_vertical(self):
        points = np.random.default_rng(5).normal(size=(80, 3))
        r = Rotation.from_euler("z", 90, degrees=True).as_matrix()
        t = np.array([10., 20, 30])-r @ points.mean(axis=0)
        target = points @ r.T+t
        args = options(terrain_center="10,20,30")
        s, rotation, translation, report = tool.search_alignment(points, target, args)
        self.assertAlmostEqual(s, 1, places=5)
        np.testing.assert_allclose(rotation, r, atol=1e-5)
        np.testing.assert_allclose(translation, t, atol=1e-5)
        self.assertFalse(report["ambiguous"])

    def test_camera_pose_and_projection_invariance(self):
        model, _ = reconstruction()
        point = next(iter(model.points3D.values())).xyz.copy()
        image = model.images[1]
        old = model.cameras[1].img_from_cam(image.cam_from_world()*point)
        center = image.projection_center()
        r = Rotation.from_euler("z", 30, degrees=True).as_matrix()
        t = np.array([4., 5, 6])
        model.transform(tool.similarity(2, r, t))
        new_point = next(iter(model.points3D.values())).xyz
        new = model.cameras[1].img_from_cam(model.images[1].cam_from_world()*new_point)
        np.testing.assert_allclose(new, old, atol=1e-10)
        np.testing.assert_allclose(model.images[1].projection_center(), 2*r @ center+t)

    def test_visibility_rejects_behind_camera_and_occluded(self):
        camera = pycolmap.Camera(model="PINHOLE", width=100, height=100,
                                params=[50, 50, 50, 50])
        points = np.array([[0., 0, 5], [0, 0, 10], [0, 0, -1], [100, 0, 5]])
        ids, _ = tool.visible_projections(camera, pycolmap.Rigid3d(), points,
                                         points, 0.1, 4)
        np.testing.assert_array_equal(ids, [0])

    def test_injection_tracks_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, points = reconstruction()
            original = model.images[1]
            original.points2D.append(pycolmap.Point2D(np.array([10., 10])))
            old_id = model.add_point3D(np.array([-4.5, -4, 5]),
                                      pycolmap.Track([pycolmap.TrackElement(1, 0)]))
            for i in range(1, 4):
                Image.new("RGB", (200, 200), (120, 130, 140)).save(root/f"{i}.png")
            lidar = np.concatenate((points, [[0, 0, 3], [0.5, 0.5, 3]]))
            report = tool.inject_tracks(model, lidar, options(images=root))
            self.assertGreaterEqual(report["inserted"], 1)
            self.assertTrue(model.is_valid())
            self.assertEqual(model.images[1].points2D[0].point3D_id, old_id)
            for pid, point in model.points3D.items():
                for element in point.track.elements:
                    self.assertEqual(model.images[element.image_id].points2D[
                        element.point2D_idx].point3D_id, pid)
            output = root/"out"
            output.mkdir()
            model.write(output)
            reloaded = pycolmap.Reconstruction(output)
            self.assertTrue(reloaded.is_valid())
            self.assertEqual(reloaded.num_points3D(), model.num_points3D())

    def test_exif_anchor_without_initial_coordinate(self):
        local = np.array([[0., 0, 0], [10, 0, 0], [0, 10, 0], [10, 10, 2]])
        origin = np.array([48., 2., 100.])
        ecef = Transformer.from_crs(4979, 4978, always_xy=True)
        base = np.array(ecef.transform(origin[1], origin[0], origin[2]))
        world = local @ tool.enu_rotation(origin)+base
        geo = Transformer.from_crs(4978, 4979, always_xy=True)
        lon, lat, altitude = geo.transform(*world.T)
        def dms(value):
            value = abs(value)
            degrees = int(value)
            minutes = int((value-degrees)*60)
            return [degrees, minutes, ((value-degrees)*60-minutes)*60]
        gps = [{1: "N", 2: dms(a), 3: "E", 4: dms(b), 6: c}
               for a, b, c in zip(lat, lon, altitude)]
        class Raster:
            def __init__(self, index):
                self.index = index
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def getexif(self):
                return SimpleNamespace(get_ifd=lambda _: gps[self.index])
        model = SimpleNamespace(images={
            i: SimpleNamespace(name=f"{i}.png", has_pose=True,
                               projection_center=lambda i=i: local[i]) for i in range(4)})
        with patch("PIL.Image.open", side_effect=lambda path: Raster(int(path.stem))):
            (s, r, t), reference = tool.exif_initialization(model, Path("/tmp/exif"))
        self.assertAlmostEqual(s, 1., places=5)
        np.testing.assert_allclose(r @ r.T, np.eye(3), atol=1e-10)
        self.assertEqual(reference["type"], "local_ENU")
        self.assertLess(np.linalg.norm(t), 10)

    def test_enu_metadata_and_georef_run_without_coordinate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, points = reconstruction()
            folder = root/"model"
            folder.mkdir()
            model.write(folder)
            origin = [48., 2., 100.]
            reference = {"type": "local_ENU", "units": "meters",
                         "axes": ["East", "North", "Up"], "origin_lat_lon_height": origin}
            (folder/"georef_stats.json").write_text(json.dumps({"output_reference": reference}))
            ecef = Transformer.from_crs(4979, 4978, always_xy=True)
            base = np.array(ecef.transform(origin[1], origin[0], origin[2]))
            world = points @ tool.enu_rotation(origin)+base
            geo = Transformer.from_crs(4978, 4979, always_xy=True)
            lon, lat, height = geo.transform(*world.T)
            projection = Transformer.from_crs(4979, 2154, always_xy=True)
            projected = np.column_stack(projection.transform(lon, lat, height))
            header = laspy.LasHeader(point_format=6, version="1.4")
            header.offsets = projected.min(axis=0)
            header.scales = [0.00001]*3
            cloud = laspy.LasData(header)
            cloud.x, cloud.y, cloud.z = projected.T
            source = root/"lidar.laz"
            cloud.write(source)
            output = root/"output"
            args = options(model=folder, lidar=source, output=output, already_projected=False)
            tool.run(args)
            self.assertTrue(pycolmap.Reconstruction(output).is_valid())
            stats = json.loads((output/"lidar_alignment.json").read_text())
            self.assertEqual(stats["output_reference"], reference)
            self.assertIsNone(stats["terrain_center"])
            self.assertLess(stats["rmse_after"], 0.001)
            output2 = root/"output2"
            args.output = output2
            args.terrain_center = "1,2,3"
            with self.assertRaisesRegex(ValueError, "Référence ENU"):
                tool.run(args)


if __name__ == "__main__":
    unittest.main()
