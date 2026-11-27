from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pyogrio
from shapely.geometry import LineString, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "vector"))
import fetch_vector as fv


def ign_feature(index):
    return {"type": "Feature", "id": f"batiment.{index}",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            "properties": {"hauteur": 10.0 + index, "nature": "Indifférencié"}}


class ParseAndResolveTests(unittest.TestCase):
    def test_parse_bbox_rejects_inverted_extent(self):
        with self.assertRaises(ValueError):
            fv.parse_bbox("666701,6859851,666201,6860351")

    def test_ign_alias_and_full_typename_resolve(self):
        self.assertEqual(fv.resolve_layer("ign", "building"), ("building", "BDTOPO_V3:batiment"))
        self.assertEqual(fv.resolve_layer("ign", "BDTOPO_V3:troncon_de_route"),
                         ("troncon_de_route", "BDTOPO_V3:troncon_de_route"))

    def test_osm_rejects_unknown_layer_and_raw_typename(self):
        with self.assertRaises(ValueError):
            fv.resolve_layer("osm", "BDTOPO_V3:batiment")
        with self.assertRaises(ValueError):
            fv.resolve_layer("osm", "unknown")


class IgnPaginationTests(unittest.TestCase):
    def test_pages_until_short_page_with_expected_startindex(self):
        calls = []
        pages = [{"features": [ign_feature(0), ign_feature(1)]},
                 {"features": [ign_feature(2)]}]

        def fetch(url):
            calls.append(parse_qs(urlsplit(url).query))
            return pages[len(calls) - 1]

        features = fv.fetch_ign_features("BDTOPO_V3:batiment", (0, 0, 10, 10),
                                         page_size=2, fetch=fetch)
        self.assertEqual(len(features), 3)
        self.assertEqual([c["STARTINDEX"][0] for c in calls], ["0", "2"])
        self.assertEqual(calls[0]["BBOX"], ["0,0,10,10,EPSG:2154"])
        self.assertEqual(calls[0]["SRSNAME"], ["EPSG:2154"])

    def test_repeated_page_is_refused(self):
        def fetch(url):
            return {"features": [ign_feature(0)]}

        with self.assertRaisesRegex(ValueError, "répétée"):
            fv.fetch_ign_features("BDTOPO_V3:batiment", (0, 0, 10, 10), page_size=1, fetch=fetch)

    def test_wrong_crs_is_refused(self):
        page = {"crs": {"properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}, "features": []}
        with self.assertRaisesRegex(ValueError, "CRS inattendu"):
            fv.fetch_ign_features("BDTOPO_V3:batiment", (0, 0, 10, 10), fetch=lambda url: page)


class OsmTests(unittest.TestCase):
    def test_closed_building_becomes_polygon_and_open_road_a_line(self):
        elements = [
            {"type": "way", "id": 1, "tags": {"building": "yes", "name:fr": "Mairie"},
             "geometry": [{"lat": 48.84, "lon": 2.54}, {"lat": 48.84, "lon": 2.541},
                          {"lat": 48.841, "lon": 2.541}, {"lat": 48.84, "lon": 2.54}]},
            {"type": "way", "id": 2, "tags": {"highway": "residential"},
             "geometry": [{"lat": 48.84, "lon": 2.54}, {"lat": 48.841, "lon": 2.541}]},
            {"type": "node", "id": 3, "lat": 48.84, "lon": 2.54},
            {"type": "way", "id": 4, "tags": {"highway": "path"}, "geometry": []},
        ]
        features = fv.osm_features(elements)
        self.assertEqual(len(features), 2)
        self.assertIsInstance(features[0]["geometry"], Polygon)
        self.assertIsInstance(features[1]["geometry"], LineString)
        self.assertEqual(features[0]["properties"], {"osm_id": 1, "building": "yes", "name:fr": "Mairie"})
        x, y = features[0]["geometry"].exterior.coords[0]
        self.assertGreater(x, 600000)
        self.assertLess(y, 7000000)

    def test_overpass_query_uses_wgs84_bbox_and_layer_filter(self):
        sent = {}

        def fetch(url, data=None):
            sent.update(url=url, data=data)
            return {"elements": []}

        fv.fetch_osm_elements("building", (666201, 6859851, 666701, 6860351),
                              overpass_url="https://example.test/api", fetch=fetch)
        self.assertIn('way["building"]', sent["data"]["data"])
        south, west, north, east = fv.osm_lat_lon_bbox((666201, 6859851, 666701, 6860351))
        self.assertLess(south, north)
        self.assertLess(west, east)
        self.assertIn(f"({south},{west},{north},{east})", sent["data"]["data"])


class WriteGpkgTests(unittest.TestCase):
    def test_round_trip_keeps_crs_count_and_mixed_geometries(self):
        features = [
            {"geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 0)]),
             "properties": {"osm_id": 1, "name:fr": "Mairie", "height": 12.5, "nb": 3}},
            {"geometry": LineString([(0, 0), (5, 5)]),
             "properties": {"osm_id": 2, "name:fr": None, "height": None, "nb": None}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "osm_building.gpkg"
            fv.write_gpkg(path, "building", features)
            info = pyogrio.read_info(path)
        self.assertEqual(info["crs"], "EPSG:2154")
        self.assertEqual(info["features"], 2)
        self.assertEqual(info["layer_name"], "building")
        self.assertEqual(list(info["fields"]), ["osm_id", "name:fr", "height", "nb"])


class FetchVectorTests(unittest.TestCase):
    def test_ign_output_file_is_named_after_source_and_layer(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(fv, "fetch_ign_features", return_value=[ign_feature(0)]):
            output = fv.fetch_vector((0, 0, 10, 10), "ign", "building", tmp)
            self.assertEqual(output.name, "ign_building.gpkg")
            self.assertEqual(pyogrio.read_info(output)["features"], 1)


if __name__ == "__main__":
    unittest.main()
