import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools/libs/python"))
import splat_metadata


class SplatMetadataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.splat = Path(self.tmp.name) / "scene.splat"
        self.splat.write_bytes(b"x" * 64)

    def tearDown(self):
        self.tmp.cleanup()

    def test_georeferenced_metadata_keeps_reference_and_lon_lat_point(self):
        stats = {
            "normalized_ordered_rmse": 0.02,
            "output_reference": {
                "type": "local_ENU",
                "origin_lat_lon_height": [48.5, -0.25, 12.0],
                "origin_projected": {"epsg": 2154},
            },
        }
        metadata = splat_metadata.build_metadata(self.splat, "images_to_3dgs", stats)
        self.assertEqual(metadata["output_reference"], stats["output_reference"])
        self.assertTrue(metadata["georeferenced"])
        self.assertEqual(metadata["crs"], "EPSG:2154")
        self.assertEqual(metadata["features"][0]["geometry"]["coordinates"], [-0.25, 48.5, 12.0])

    def test_metadata_without_reference_has_no_output_reference(self):
        metadata = splat_metadata.build_metadata(self.splat, "panoramax_to_3dgs", {})
        self.assertNotIn("output_reference", metadata)
        self.assertFalse(metadata["georeferenced"])
        self.assertIsNone(metadata["features"][0]["geometry"])

    def test_spirula_gauge_provides_reference_when_no_georef_stats(self):
        gauge = Path(self.tmp.name) / "gauge.txt"
        gauge.write_text("# repère\noriented 1\nmetric 1\norigin_lat 48.5\norigin_lon -0.25\norigin_alt 12\n",
                         encoding="utf-8")
        stats = splat_metadata.load_reference(Path(self.tmp.name) / "georef_stats.json", 2154)
        metadata = splat_metadata.build_metadata(self.splat, "panoramax_to_3dgs", stats)
        self.assertTrue(metadata["georeferenced"])
        self.assertEqual(metadata["output_reference"]["origin_lat_lon_height"], [48.5, -0.25, 12.0])
        self.assertEqual(metadata["crs"], "EPSG:2154")
        self.assertEqual(metadata["features"][0]["geometry"]["coordinates"], [-0.25, 48.5, 12.0])

    def test_main_writes_next_to_splat(self):
        output = self.splat.with_name("scene.metadata.json")
        import sys
        argv = sys.argv
        sys.argv = ["splat_metadata.py", "--splat", str(self.splat), "--pipeline", "test"]
        try:
            splat_metadata.main()
        finally:
            sys.argv = argv
        self.assertEqual(json.loads(output.read_text())["pipeline"], "test")


if __name__ == "__main__":
    unittest.main()
