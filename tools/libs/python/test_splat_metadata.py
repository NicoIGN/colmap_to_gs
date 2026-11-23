import json
import tempfile
import unittest
from pathlib import Path

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
