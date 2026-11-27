import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pyproj import Transformer

from tools.grid.grid_tiles import (
    parse_bbox,
    plan_tiles,
    positions_geojson,
    read_positions,
    select_from_metric,
    select_images,
    tiles_geojson,
    write_tile_metric,
)


class PlanTilesTest(unittest.TestCase):
    def test_tiles_cover_bbox_with_overlap_and_last_tile_aligned(self):
        tiles = plan_tiles((0, 0, 10, 4), tile_size=4, overlap=1, margin=2)
        cores_x = sorted({(t["core"][0], t["core"][2]) for t in tiles})
        self.assertEqual(cores_x, [(0, 4), (3, 7), (6, 10)])
        self.assertEqual({(t["core"][1], t["core"][3]) for t in tiles}, {(0, 4)})
        self.assertEqual(len(tiles), 3)

    def test_tiles_geojson_is_wgs84_polygon_per_core_tile(self):
        tiles = plan_tiles((650000, 6860000, 650008, 6860004), tile_size=4, overlap=0, margin=2)
        collection = tiles_geojson(tiles, 2154)
        self.assertEqual(collection["type"], "FeatureCollection")
        self.assertEqual(len(collection["features"]), len(tiles))
        feature = collection["features"][0]
        self.assertEqual(feature["properties"]["id"], "tile_000_000")
        ring = feature["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        self.assertTrue(-5 < min(lons) < max(lons) < 10)
        self.assertTrue(40 < min(lats) < max(lats) < 60)

    def test_tiles_geojson_can_stay_in_native_crs(self):
        tiles = plan_tiles((650000, 6860000, 650008, 6860004), tile_size=4, overlap=0, margin=2)
        collection = tiles_geojson(tiles, 2154, out_epsg=2154)
        self.assertEqual(collection["crs"]["properties"]["name"], "urn:ogc:def:crs:EPSG::2154")
        ring = collection["features"][0]["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], [650000.0, 6860000.0])
        xs = [p[0] for p in ring]
        self.assertEqual((min(xs), max(xs)), (650000.0, 650004.0))

    def test_positions_geojson_is_in_out_crs_with_tile_membership(self):
        tiles = plan_tiles((650000, 6860000, 650008, 6860004), tile_size=4, overlap=0, margin=2)
        points = {"inside.jpg": (650002.0, 6860002.0), "outside.jpg": (650100.0, 6860100.0)}
        collection = positions_geojson(points, tiles, 2154, out_epsg=2154)
        self.assertEqual(collection["crs"]["properties"]["name"], "urn:ogc:def:crs:EPSG::2154")
        by_name = {f["properties"]["name"]: f for f in collection["features"]}
        self.assertEqual(by_name["inside.jpg"]["geometry"]["coordinates"], [650002.0, 6860002.0])
        self.assertTrue(by_name["inside.jpg"]["properties"]["tiles"])
        self.assertEqual(by_name["outside.jpg"]["properties"]["tiles"], [])

    def test_image_count_is_exported_to_geojson_properties(self):
        tiles = plan_tiles((650000, 6860000, 650008, 6860004), tile_size=4, overlap=0, margin=2)
        tiles[0]["image_count"] = 7
        collection = tiles_geojson(tiles, 2154, out_epsg=2154)
        self.assertEqual(collection["features"][0]["properties"]["image_count"], 7)
        self.assertNotIn("image_count", collection["features"][1]["properties"])

    def test_compute_box_is_core_enlarged_by_margin(self):
        first = plan_tiles((0, 0, 10, 4), tile_size=4, overlap=1, margin=2)[0]
        self.assertEqual(first["core"], [0, 0, 4, 4])
        self.assertEqual(first["compute"], [-2, -2, 6, 6])

    def test_overlap_must_be_smaller_than_tile(self):
        with self.assertRaises(ValueError):
            plan_tiles((0, 0, 10, 10), tile_size=4, overlap=4, margin=0)

    def test_bbox_must_be_ordered(self):
        with self.assertRaises(ValueError):
            parse_bbox("10,0,0,4")


class SelectImagesTest(unittest.TestCase):
    TILES = plan_tiles((651000, 6861000, 653000, 6863000), tile_size=2000, overlap=0, margin=0)

    def _lat_lon_centre(self):
        to_wgs84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
        lon, lat = to_wgs84.transform(652000, 6862000)
        return lat, lon

    def test_image_without_gps_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            Image.new("RGB", (8, 8)).save(Path(folder) / "no_gps.jpg")
            with self.assertRaisesRegex(ValueError, "no_gps.jpg.*aucune position"):
                select_images(folder, 2154, self.TILES)

    def test_external_positions_replace_missing_exif(self):
        lat, lon = self._lat_lon_centre()
        with tempfile.TemporaryDirectory() as folder:
            Image.new("RGB", (8, 8)).save(Path(folder) / "pano_1.jpg")
            selection = select_images(folder, 2154, self.TILES, {"pano_1.jpg": (lat, lon, 0.0)})
            self.assertEqual([p.name for paths in selection.values() for p in paths], ["pano_1.jpg"])

    def test_metric_positions_count_without_images_dir(self):
        from tools.sfm.spirula.gps_to_metric_positions import to_enu
        lat, lon = self._lat_lon_centre()
        origin = (lat + 0.01, lon - 0.01, 0.0)
        east, north, up = to_enu(lat, lon, 0.0, origin)
        with tempfile.TemporaryDirectory() as folder:
            metric = Path(folder) / "metric.txt"
            metric.write_text(f"# origin_lat {origin[0]}\n# origin_lon {origin[1]}\n# origin_alt 0\n"
                              f"pano_1.jpg {east} {north} {up}\npano_far.jpg 1e6 1e6 0\n", encoding="utf-8")
            selection = select_from_metric(None, 2154, self.TILES, metric)
            self.assertEqual([p.name for paths in selection.values() for p in paths], ["pano_1.jpg"])

    def test_metric_positions_select_without_reading_exif(self):
        from tools.sfm.spirula.gps_to_metric_positions import to_enu
        lat, lon = self._lat_lon_centre()
        origin = (lat + 0.01, lon - 0.01, 0.0)
        east, north, up = to_enu(lat, lon, 0.0, origin)
        with tempfile.TemporaryDirectory() as folder:
            Image.new("RGB", (8, 8)).save(Path(folder) / "pano_1.jpg")
            metric = Path(folder) / "metric.txt"
            metric.write_text(f"# origin_lat {origin[0]}\n# origin_lon {origin[1]}\n# origin_alt 0\n"
                              f"pano_1.jpg {east} {north} {up}\n", encoding="utf-8")
            selection = select_from_metric(folder, 2154, self.TILES, metric)
        self.assertEqual([p.name for paths in selection.values() for p in paths], ["pano_1.jpg"])

    def test_positions_file_format_is_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            positions = Path(folder) / "positions.txt"
            positions.write_text("# commentaire\npano_1.jpg 48.85\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ligne 2"):
                read_positions(positions)


class TileMetricPositionsTest(unittest.TestCase):
    def test_each_tile_gets_its_own_metric_file_with_only_its_images(self):
        tiles = [
            {"id": "t0", "core": [0, 0, 500, 500], "compute": [-50, -50, 550, 550]},
            {"id": "t1", "core": [500, 0, 1000, 500], "compute": [450, -50, 1050, 550]},
        ]
        to_wgs84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
        lon_a, lat_a = to_wgs84.transform(100, 100)
        lon_b, lat_b = to_wgs84.transform(900, 100)
        selection = {"t0": [Path("a.jpg")], "t1": [Path("b.jpg")]}
        with tempfile.TemporaryDirectory() as folder:
            positions = {"a.jpg": (lat_a, lon_a, 0.0), "b.jpg": (lat_b, lon_b, 0.0)}
            written = write_tile_metric(selection, tiles, 2154, folder, positions=positions)
            lines = written["t1"].read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("# origin_lat"))
        self.assertEqual([line.split()[0] for line in lines if not line.startswith("#")], ["b.jpg"])


if __name__ == "__main__":
    unittest.main()
