import unittest
import json
import tempfile
from pathlib import Path

import numpy as np
import pycolmap
from PIL import Image
from scipy.spatial.transform import Rotation

from tools.georef.georef_gps import horizontal_fit, apply_sim, run, geodetic_to_ecef, enu_rotation
from tools.sfm.resume import validate_sparse

if not hasattr(pycolmap.Reconstruction, "add_camera_with_trivial_rig"):
    raise RuntimeError(
        f"pycolmap {getattr(pycolmap, '__version__', '?')} trop ancien pour ces tests : "
        "installer pycolmap==4.2.1 (wheels macOS 14+ arm64 ; Python >= 3.10)")


class HorizontalGpsTest(unittest.TestCase):
    def test_horizontal_alignment_without_height(self):
        source = np.array([[0, 0, 0], [1, 0, .2], [1, 1, .4], [0, 1, .2]], float)
        rotation = Rotation.from_euler("zyx", [35, 10, -5], degrees=True).as_matrix()
        world_up = rotation.T @ np.array([0, 0, 1])
        target = 7 * source @ rotation.T + [100, 200, 30]
        sim = horizontal_fit(source, target, np.tile(world_up, (len(source), 1)))
        np.testing.assert_allclose(apply_sim(source, sim)[:, :2], target[:, :2], atol=1e-8)
        self.assertAlmostEqual(sim[0], 7)
        self.assertAlmostEqual(np.mean(apply_sim(source, sim)[:, 2]), 0, places=8)

    def test_collinear_trajectory_is_rejected(self):
        source = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], float)
        with self.assertRaisesRegex(ValueError, "rectiligne"):
            horizontal_fit(source, source, np.tile([0, 0, 1.], (3, 1)))

    def test_colmap_model_reprojected_to_enu_from_panoramax_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, metadata, sparse, output = (root / name for name in ("source", "metadata", "sparse", "output"))
            for path in (source, metadata, sparse):
                path.mkdir()
            coordinates = [(2.0, 48.0), (2.0002, 48.0), (2.0002, 48.0002), (2.0, 48.0002)]
            geo = np.array([[lat, lon, 0.] for lon, lat in coordinates])
            origin = geo.mean(axis=0)
            centers = (geodetic_to_ecef(geo[:, 0], geo[:, 1], geo[:, 2]) -
                       geodetic_to_ecef(*origin)) @ enu_rotation(origin).T
            camera = pycolmap.Camera(model="PINHOLE", width=16, height=16,
                                     params=[8, 8, 8, 8], camera_id=1)
            model = pycolmap.Reconstruction()
            model.add_camera_with_trivial_rig(camera)
            image_entries, view_names = [], {}
            camera_rotation = pycolmap.Rotation3d(np.array([[1., 0, 0], [0, 0, -1.], [0, 1., 0]]))
            for index, (lon, lat) in enumerate(coordinates):
                name = f"p{index}.jpg"
                Image.new("RGB", (20, 10)).save(source / name)
                (metadata / f"p{index}.json").write_text(json.dumps({
                    "feature": {"geometry": {"type": "Point", "coordinates": [lon, lat]}}
                }))
                view = f"front/{name}.jpg"
                image_entries.append({"name": name})
                view_names[view] = name
                image = pycolmap.Image(name=view, camera_id=1, image_id=index + 1,
                                       keypoints=np.array([[8., 8.]]))
                pose = pycolmap.Rigid3d(camera_rotation, -camera_rotation.matrix() @ centers[index])
                model.add_image_with_trivial_frame(image, pose)
            model.add_point3D(np.array([1., 1., 1.]), pycolmap.Track([
                pycolmap.TrackElement(i, 0) for i in range(1, 5)]))
            model.write(sparse)
            projection = root / "projection.json"
            projection.write_text(json.dumps({"status": "complete", "inputs": {
                "source": str(source), "images": image_entries, "views": view_names}}))
            run(projection, sparse, output, 2154, metadata)
            result = pycolmap.Reconstruction(output)
            validate_sparse(output)
            for image in result.images.values():
                index = int(Path(image.name).name[1])
                np.testing.assert_allclose(image.projection_center()[:2], centers[index, :2], atol=1e-5)
            stats = json.loads((output / "georef_stats.json").read_text())
            self.assertEqual(stats["mode"], "horizontal")
            self.assertFalse(stats["output_reference"]["altitude_georeferenced"])
            self.assertEqual(stats["output_reference"]["origin_projected"]["epsg"], 2154)
            run(projection, sparse, output, 2154, metadata)
            with self.assertRaisesRegex(ValueError, "incompatible"):
                run(projection, sparse, output, 3857, metadata)
            (output / "gps_positions.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "incomplet"):
                run(projection, sparse, output, 2154, metadata)
            unreferenced = root / "unreferenced"
            run(projection, sparse, unreferenced, 2154, allow_missing=True)
            self.assertEqual(json.loads((unreferenced / "georef_stats.json").read_text())["status"],
                             "unreferenced")
            for index in range(len(coordinates)):
                (metadata / f"p{index}.json").write_text(json.dumps({
                    "feature": {"geometry": {"type": "Point", "coordinates": [2 + index * .0001, 48]}}
                }))
            collinear = root / "collinear"
            run(projection, sparse, collinear, 2154, metadata, allow_missing=True)
            self.assertEqual(json.loads((collinear / "georef_stats.json").read_text())["status"],
                             "unreferenced")
            for index, (lon, lat) in enumerate(coordinates):
                (metadata / f"p{index}.json").write_text(json.dumps({
                    "feature": {"geometry": {"type": "Point", "coordinates": [lon, lat, 50]}}
                }))
            three_dimensional = root / "three-dimensional"
            run(projection, sparse, three_dimensional, 2154, metadata)
            stats = json.loads((three_dimensional / "georef_stats.json").read_text())
            self.assertEqual(stats["mode"], "3d")
            self.assertTrue(stats["output_reference"]["altitude_georeferenced"])
