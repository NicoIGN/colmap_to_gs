import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import laspy
import numpy as np
from PIL import Image

import colmap_lidar_pipeline as master
import lidar_pipeline as splat_master
from fetch_ign_lidar import CRS, file_signature, validate_cached_lidar
from geographic import projected_enu
from merge_splats import SPLAT_DTYPE
from test_colmap_lidar import options, reconstruction
from test_ign_pipeline import cloud_file


class ColmapPipelineTests(unittest.TestCase):
    def test_download_reuse_injection_and_final_enu_splat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root/"workspace"
            model, points = reconstruction()
            folder = root/"model"
            folder.mkdir()
            model.write(folder)
            reference = {"type": "local_ENU", "units": "meters",
                         "axes": ["East", "North", "Up"],
                         "origin_lat_lon_height": [48., 2., 100.]}
            (folder/"georef_stats.json").write_text(json.dumps({"output_reference": reference}))
            for i in range(1, 4):
                Image.new("RGB", (200, 200), (120, 130, 140)).save(root/f"{i}.png")
            all_points = np.concatenate((points, [[0, 0, 3], [0.5, 0.5, 3]]))
            projected = projected_enu(all_points, reference["origin_lat_lon_height"], CRS)
            args = options(model=folder, output=None, workspace=workspace, margin=20,
                           pdal="missing-pdal", max_tiles=100, max_area_km2=25,
                           images=root, inject=True, already_projected=False)
            def retrieve(bbox, output, **kwargs):
                output.parent.mkdir(parents=True)
                cloud_file(output, projected, [6]*len(projected))
                output.with_suffix(".laz.json").write_text(json.dumps({
                    "crs": CRS, "bbox": bbox.tolist(), "file_signature": file_signature(output)}))
            with patch.object(master, "fetch_lidar", side_effect=retrieve) as fetch:
                master.run(args)
                self.assertEqual(fetch.call_count, 1)
            report = json.loads((args.output/"lidar_alignment.json").read_text())
            self.assertGreater(report["injection"]["inserted"], 0)
            shared = workspace/"lidar/lidar.laz"
            initial_signature = file_signature(shared)
            args.output = workspace/"colmap_second"
            with patch.object(master, "fetch_lidar", side_effect=AssertionError("download")):
                master.run(args)
            self.assertEqual(file_signature(shared), initial_signature)
            pipeline_report = json.loads((workspace/"lidar/colmap_second.pipeline.json").read_text())
            self.assertTrue(pipeline_report["reused"])
            raw = np.zeros(len(points), dtype=SPLAT_DTYPE)
            raw["position"] = points
            raw["scale"] = 0.01
            raw["rotation"] = [255, 128, 128, 128]
            raw["rgba"] = [100, 110, 120, 200]
            source = workspace/"final.splat"
            raw.tofile(source)
            # Use the master parser with no actual dispatch to obtain default parameters.
            with patch("sys.argv", ["lidar_pipeline.py", "--input", str(source),
                                    "--output-dir", str(workspace/"cleaned"),
                                    "--crs", CRS, "--lidar-file", str(shared),
                                    "--georef", str(args.output/"georef_stats.json"),
                                    "--voxel-size", "0.02"]), \
                    patch.object(splat_master, "run") as capture:
                splat_master.main()
            clean_args = capture.call_args.args[0]
            with patch.object(splat_master, "fetch_lidar", side_effect=AssertionError("download")):
                splat_master.run(clean_args)
            self.assertTrue((workspace/"cleaned/cleaned.splat").exists())
            self.assertEqual(file_signature(shared), initial_signature)
            local = laspy.read(workspace/"cleaned/lidar_local.laz")
            np.testing.assert_allclose(np.column_stack((local.x, local.y, local.z)),
                                       all_points, atol=0.0002)

    def test_cache_corruption_and_insufficient_extent_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"lidar.laz"
            output.write_bytes(b"placeholder")
            report = {"crs": CRS, "bbox": [1, 2, 3, 4],
                      "file_signature": file_signature(output)}
            output.with_suffix(".laz.json").write_text(json.dumps(report))
            validate_cached_lidar(output, np.array([1.1, 2.1, 2.9, 3.9]))
            with self.assertRaisesRegex(ValueError, "couvre"):
                validate_cached_lidar(output, np.array([0., 2, 3, 4]))
            output.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                validate_cached_lidar(output, np.array([1., 2, 3, 4]))

    def test_non_georef_extent_covers_yaw_scale_search(self):
        model, points = reconstruction()
        with tempfile.TemporaryDirectory() as directory:
            args = options(model=Path(directory), terrain_center="650050,6860050,120",
                           scale_max=2, search_radius=5, margin=10)
            bbox = master.extraction_bbox(model, args)
            radius = np.linalg.norm(points-points.mean(axis=0), axis=1).max()*2+15
            np.testing.assert_allclose(bbox, [650050-radius, 6860050-radius,
                                             650050+radius, 6860050+radius])


if __name__ == "__main__":
    unittest.main()
