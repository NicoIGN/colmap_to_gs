import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import laspy
import numpy as np

import fetch_ign_lidar as fetcher
import lidar_pipeline as master
from merge_splats import SPLAT_DTYPE


def feature(index):
    return {"id": f"tile.{index}", "properties": {
        "url_npl": f"https://data.geopf.fr/telechargement/lidar/{index}.copc.laz"}}


def cloud_file(path, xyz, classes):
    header = laspy.LasHeader(point_format=6, version="1.4")
    header.offsets = xyz.min(axis=0)
    header.scales = [0.0001]*3
    cloud = laspy.LasData(header)
    cloud.x, cloud.y, cloud.z = xyz.T
    cloud.classification = np.asarray(classes, dtype="u1")
    cloud.write(path)


class IgnPipelineTests(unittest.TestCase):
    def test_wfs_pagination_bbox_and_chunk_urls(self):
        queries = []
        def fake(url):
            queries.append(parse_qs(urlsplit(url).query))
            start = int(queries[-1]["STARTINDEX"][0])
            return {"type": "FeatureCollection", "numberMatched": 3,
                    "features": [feature(i) for i in range(start, min(start+2, 3))]}
        bbox = np.array([650000, 6860000, 650100, 6860100])
        urls = fetcher.discover_tiles(bbox, page_size=2, fetch=fake)
        self.assertEqual(len(urls), 3)
        self.assertIn("/chunk/telechargement/", urls[0])
        self.assertEqual([q["STARTINDEX"][0] for q in queries], ["0", "2"])
        self.assertEqual(queries[0]["BBOX"], ["650000,6860000,650100,6860100,EPSG:2154"])
        self.assertEqual(queries[0]["TYPENAMES"], [fetcher.LAYER])

    def test_wfs_errors_and_limits(self):
        def repeated(_):
            return {"type": "FeatureCollection", "features": [feature(1)]}
        bbox = np.array([1, 2, 3, 4])
        with self.assertRaisesRegex(ValueError, "répété"):
            fetcher.discover_tiles(bbox, fetch=repeated)
        with self.assertRaisesRegex(ValueError, "Aucune tuile"):
            fetcher.discover_tiles(bbox, fetch=lambda _: {
                "type": "FeatureCollection", "features": []})
        with self.assertRaisesRegex(ValueError, "incomplète"):
            fetcher.discover_tiles(bbox, fetch=lambda _: {
                "type": "FeatureCollection", "features": [], "numberMatched": 10})
        with self.assertRaisesRegex(ValueError, "Trop de tuiles"):
            fetcher.discover_tiles(bbox, max_tiles=1, fetch=lambda _: {
                "type": "FeatureCollection", "features": [feature(1), feature(2)]})
        with self.assertRaisesRegex(ValueError, "invalide"):
            fetcher.discover_tiles(bbox, fetch=lambda _: {})
        with self.assertRaises(ValueError):
            fetcher.copc_url("https://example.org/a.copc.laz")

    def test_pdal_pipeline_exact_crop_merge_and_classes_preserved(self):
        bbox = np.array([650000, 6860000, 650100, 6860100])
        config = fetcher.pdal_pipeline(["https://a", "https://b"], bbox, Path("out.laz"))
        stages = config["pipeline"]
        self.assertEqual(stages[2]["type"], "filters.merge")
        self.assertEqual(stages[2]["inputs"], ["tile_0", "tile_1"])
        self.assertEqual(stages[3]["type"], "filters.crop")
        self.assertEqual(stages[-1]["forward"], "all")
        self.assertNotIn("filters.assign", [s["type"] for s in stages])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.laz"
            def pdal(command, check):
                pipeline = json.loads(Path(command[2]).read_text())["pipeline"]
                destination = Path(pipeline[-1]["filename"])
                xyz = np.array([[650010, 6860010, 1], [650020, 6860020, 2],
                                [650030, 6860030, 3]])
                cloud_file(destination, xyz, [3, 5, 67])
            with patch.object(fetcher.shutil, "which", return_value="/bin/pdal"), \
                    patch.object(fetcher, "discover_tiles", return_value=["https://a"]), \
                    patch.object(fetcher.subprocess, "run", side_effect=pdal):
                report = fetcher.fetch_lidar(bbox, output)
            self.assertEqual(report["points"], 3)
            np.testing.assert_array_equal(laspy.read(output).classification, [3, 5, 67])
            self.assertTrue(output.with_suffix(".laz.json").exists())

    def setup_scene(self, directory):
        root = Path(directory)
        origin = np.array([650000., 6860000., 100.])
        xyz = np.random.default_rng(6).uniform(-2, 2, (120, 3))
        raw = np.zeros(121, dtype=SPLAT_DTYPE)
        raw["position"][:120] = xyz
        raw["position"][-1] = [4, 4, 4]
        raw["scale"] = 0.1
        raw["rotation"] = [255, 128, 128, 128]
        raw["rgba"] = [12, 34, 56, 200]
        source = root / "in.splat"
        raw.tofile(source)
        args = argparse.Namespace(
            pdal="pdal", output_dir=root/"result", input=source,
            origin="650000,6860000,100", crs="EPSG:2154", margin=10,
            max_correspondence_distance=0.2, max_distance=0.02,
            max_area_km2=25, voxel_size=0.001, lidar_stride=1, max_tiles=100,
            min_overlap=0.3, align_classes="2,3,4,5,6,67", filter_classes=None,
            estimate_scale=False, dry_run_clean=False, support_sigma=3, spacing_factor=1)
        def retrieve(bbox, path, **kwargs):
            self.assertLess(bbox[0], origin[0]-10)
            classes = np.tile([2, 3, 4, 5, 6, 67], 20)
            cloud_file(path, xyz + origin, classes)
        return args, retrieve

    def test_master_real_alignment_and_cleanup_with_simulated_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            args, retrieve = self.setup_scene(directory)
            with patch.object(master.shutil, "which", return_value="/bin/pdal"), \
                    patch.object(master, "fetch_lidar", side_effect=retrieve):
                master.run(args)
            state = json.loads((args.output_dir/"pipeline.json").read_text())
            self.assertEqual(state["status"], "complete")
            self.assertEqual(len(state["input_sha256"]), 64)
            kept = np.fromfile(args.output_dir/"cleaned.splat", dtype=SPLAT_DTYPE)
            rejected = np.fromfile(args.output_dir/"rejected.splat", dtype=SPLAT_DTYPE)
            self.assertEqual(len(kept), 120)
            self.assertEqual(len(rejected), 1)
            aligned_report = json.loads((args.output_dir/"aligned.splat.json").read_text())
            self.assertEqual(aligned_report["lidar_classes"], [2, 3, 4, 5, 6, 67])

    def test_master_dryrun_failure_and_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            args, retrieve = self.setup_scene(directory)
            args.dry_run_clean = True
            with patch.object(master.shutil, "which", return_value="/bin/pdal"), \
                    patch.object(master, "fetch_lidar", side_effect=retrieve):
                master.run(args)
            self.assertTrue((args.output_dir/"aligned.splat").exists())
            self.assertFalse((args.output_dir/"cleaned.splat").exists())
            args.output_dir = Path(directory)/"failed"
            with patch.object(master.shutil, "which", return_value="/bin/pdal"), \
                    patch.object(master, "fetch_lidar", side_effect=OSError("WFS indisponible")):
                with self.assertRaises(OSError):
                    master.run(args)
            state = json.loads((args.output_dir/"pipeline.json").read_text())
            self.assertEqual(state["status"], "failed")
            self.assertIn("WFS indisponible", state["error"])
            args.output_dir = Path(directory)/"invalid"
            args.margin = 0.1
            with patch.object(master.shutil, "which", return_value="/bin/pdal"):
                with self.assertRaisesRegex(ValueError, "marge"):
                    master.run(args)
            self.assertFalse(args.output_dir.exists())

    def test_master_default_classes(self):
        with patch("sys.argv", ["lidar_pipeline.py", "--input", "in.splat",
                                "--output-dir", "out", "--crs", "EPSG:2154"]), \
                patch.object(master, "run") as run:
            master.main()
        self.assertEqual(run.call_args.args[0].align_classes, "2,3,4,5,6,67")
        self.assertIsNone(run.call_args.args[0].filter_classes)

    def test_coverage_guard_blocks_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            args, retrieve = self.setup_scene(directory)
            def moved(command, check):
                if command[1].endswith("align_splats_to_lidar.py"):
                    aligned = args.output_dir/"aligned.splat"
                    data = np.fromfile(args.input, dtype=SPLAT_DTYPE)
                    data["position"] += 100
                    data.tofile(aligned)
                    return
                self.fail("Nettoyage lancé malgré sortie hors couverture")
            with patch.object(master.shutil, "which", return_value="/bin/pdal"), \
                    patch.object(master, "fetch_lidar", side_effect=retrieve), \
                    patch.object(master.subprocess, "run", side_effect=moved):
                with self.assertRaisesRegex(ValueError, "hors couverture"):
                    master.run(args)
            self.assertFalse((args.output_dir/"cleaned.splat").exists())
            state = json.loads((args.output_dir/"pipeline.json").read_text())
            self.assertEqual(state["failed_stage"], "aligning")


if __name__ == "__main__":
    unittest.main()
