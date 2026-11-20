import contextlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from argparse import Namespace
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from urllib.error import HTTPError

from pyproj import Transformer

spec = importlib.util.spec_from_file_location("panoramax_download", Path(__file__).with_name("download.py"))
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


def collection_files(output, kind, pattern):
    return sorted(output.glob(f"*/{kind}/{pattern}"))


# JPEG minimal avec SOF0 : 4 pixels de large, 3 de haut.
PHOTO = b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x03\x00\x04\x01\x01\x11\x00\xff\xd9"


class PictureTypeTest(unittest.TestCase):
    def test_360_detected_from_projection_metadata_without_type(self):
        gpano = {"properties": {"exif": {"Xmp.GPano.ProjectionType": "equirectangular"}}}
        fov = {"properties": {"pers:interior_orientation": {"field_of_view": 360}}}
        self.assertEqual(download.picture_type_of(gpano), "equirectangular")
        self.assertEqual(download.picture_type_of(fov), "equirectangular")

    def test_explicit_type_wins_and_missing_stays_none(self):
        explicit = {"properties": {"type": "flat", "exif": {"Xmp.GPano.ProjectionType": "equirectangular"}}}
        self.assertEqual(download.picture_type_of(explicit), "flat")
        self.assertIsNone(download.picture_type_of({"properties": {}}))


class DownloadTest(unittest.TestCase):
    def test_jpeg_and_generic_binary_content_types(self):
        photo = PHOTO
        for mime in ("image/jpeg", "application/octet-stream", "binary/octet-stream"):
            for payload in (photo, b"<html>not a JPEG</html>"):
                with self.subTest(mime=mime, valid=payload == photo):
                    response = io.BytesIO(payload)
                    response.headers = Message()
                    response.headers["Content-Type"] = mime
                    response.headers["Content-Length"] = str(len(payload))
                    with tempfile.TemporaryDirectory() as temporary:
                        destination = Path(temporary) / "photo.part"
                        with patch.object(download, "urlopen", return_value=response):
                            if payload == photo:
                                size = download.download_image(
                                    "http://localhost/photo.jpg", destination, 1
                                )
                                self.assertEqual(size, len(photo))
                                self.assertEqual(destination.read_bytes(), photo)
                            else:
                                with self.assertRaisesRegex(ValueError, "Signature JPEG invalide"):
                                    download.download_image(
                                        "http://localhost/photo.jpg", destination, 1
                                    )

    def test_terminal_progress_bar_shows_counts_and_finishes(self):
        stream = io.StringIO()
        stream.isatty = lambda: True
        counts = {"downloaded": 1, "reused": 2, "failed": 0}
        with patch.object(download.sys, "stderr", stream):
            progress = download.Progress(4)
            for _ in range(3):
                progress.update(counts)
            progress.finish(counts)
        output = stream.getvalue()
        self.assertIn("3/3", output)
        self.assertIn("1 téléchargées", output)
        self.assertIn("2 réutilisées", output)
        self.assertTrue(output.endswith("\n"))

    def test_paginated_selection_resume_and_failure(self):
        requests = []
        photo = PHOTO
        lon, lat = Transformer.from_crs(2154, 4326, always_xy=True).transform(651500, 6861500)

        def feature(identifier, coordinates, assets=True):
            return {
                "type": "Feature", "id": identifier, "collection": "sequence",
                "geometry": {"type": "Point", "coordinates": coordinates},
                "properties": {"datetime": "2026-01-01T12:00:00Z"},
                "assets": {"hd": {"href": "/photo.jpg", "type": "image/jpeg"}} if assets else {},
            }

        first = feature("first", [lon, lat])
        second = feature("second", [lon, lat])
        pages = [
            {"features": [first, feature("outside", [0, 0])], "links": [{"rel": "next", "href": "?page=2"}]},
            {"features": [first, second], "links": []},
        ]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                is_image = self.path == "/photo.jpg"
                payload = photo if is_image else json.dumps(
                    pages[1 if self.path.endswith("page=2") else 0]
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg" if is_image else "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as temporary:
                    output = Path(temporary)
                    args = Namespace(
                        bbox=(651000, 6861000, 652000, 6862000), output_dir=output,
                        bbox_epsg=2154,
                        api_url=f"http://127.0.0.1:{server.server_port}/api",
                        datetime="2026-01-01T00:00:00Z/2026-12-31T23:59:59Z",
                        quality="hd", timeout=5,
                    )
                    errors = io.StringIO()
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                        self.assertEqual(download.run(args), 0)
                        summary = json.loads((output / "summary.json").read_text())
                        self.assertEqual(summary, {
                            "pages": 2, "available": 2, "available_is_exact": True,
                            "selected": 2, "downloaded": 2, "reused": 0, "failed": 0,
                        })
                        self.assertEqual(len(collection_files(output, "images", "*.jpg")), 2)
                        self.assertEqual(len(list((output / "pages").glob("*.json"))), 2)
                        for image in collection_files(output, "images", "*.jpg"):
                            self.assertEqual(image.read_bytes(), photo)
                        self.assertIn("bbox=", requests[0])
                        self.assertIn("datetime=", requests[0])
                        request_metadata = json.loads((output / "request.json").read_text())
                        self.assertEqual(request_metadata["bbox_epsg"], 2154)
                        self.assertEqual(request_metadata["bbox_crs"], "EPSG:2154")
                        self.assertEqual(download.run(args), 0)
                        self.assertEqual(requests.count("/photo.jpg"), 2)
                        self.assertEqual(json.loads((output / "summary.json").read_text())["reused"], 2)
                        pages[1]["features"].append(feature("missing-asset", [lon, lat], False))
                        self.assertEqual(download.run(args), 0)
                        self.assertEqual(json.loads((output / "summary.json").read_text())["failed"], 1)
                        self.assertIn("[AVERTISSEMENT] Photo missing-asset ignorée", errors.getvalue())
                        self.assertNotIn("[ERREUR]", errors.getvalue())
                        entries = [json.loads(line) for line in (output / "manifest.jsonl").read_text().splitlines()]
                        self.assertEqual(entries[-1]["status"], "failed")
                        self.assertEqual(collection_files(output, "images", "*.part"), [])
                        collection_files(output, "images", "*.jpg")[0].unlink()
                        photo = b"invalid-jpeg"
                        self.assertEqual(download.run(args), 0)
                        self.assertEqual(json.loads((output / "summary.json").read_text())["failed"], 2)
                        self.assertEqual(collection_files(output, "images", "*.part"), [])
                        for image in collection_files(output, "images", "*.jpg"):
                            image.unlink()
                        self.assertEqual(download.run(args), 1)
                        self.assertEqual(json.loads((output / "summary.json").read_text())["failed"], 3)
                        self.assertIn("[ERREUR] Aucune photo utilisable", errors.getvalue())
            finally:
                server.shutdown()
                thread.join()

    def test_invalid_bbox(self):
        for value in ("0,0,0,1", "nan,0,1,1", "0,0,1", "bad"):
            with self.assertRaises(download.argparse.ArgumentTypeError):
                download.parse_bbox(value)

    def test_crs_accepts_only_epsg_prefix(self):
        self.assertEqual(download.parse_crs("EPSG:2154"), 2154)
        for value in ("2154", "EPSG:", "EPSG:0", "proj:2154"):
            with self.assertRaises(download.argparse.ArgumentTypeError):
                download.parse_crs(value)

    def test_image_max_stops_pagination_at_total_selection_limit(self):
        requests = []
        photo = PHOTO
        lon, lat = Transformer.from_crs(2154, 4326, always_xy=True).transform(651500, 6861500)

        def feature(identifier):
            return {
                "type": "Feature", "id": identifier, "collection": "sequence",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "assets": {"hd": {"href": "/photo.jpg", "type": "image/jpeg"}},
            }

        pages = [
            {"features": [feature("first"), feature("second"), feature("third")], "links": []},
        ]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                is_image = self.path == "/photo.jpg"
                payload = photo if is_image else json.dumps(
                    pages[1 if self.path.endswith("page=2") else 0]
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg" if is_image else "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as temporary:
                    output = Path(temporary)
                    args = Namespace(
                        bbox=(651000, 6861000, 652000, 6862000),
                        bbox_epsg=2154,
                        output_dir=output,
                        api_url=f"http://127.0.0.1:{server.server_port}/api",
                        datetime=None, quality="hd", timeout=5, image_max=2,
                    )
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(download.run(args), 0)
                    summary = json.loads((output / "summary.json").read_text())
                    self.assertEqual(summary["available"], 3)
                    self.assertEqual(summary["available_is_exact"], True)
                    self.assertEqual(summary["selected"], 2)
                    self.assertEqual(summary["downloaded"], 2)
                    self.assertEqual(summary["pages"], 1)
                    self.assertEqual(len(collection_files(output, "images", "*.jpg")), 2)
                    from urllib.parse import parse_qs, urlsplit
                    search_query = parse_qs(urlsplit(requests[0]).query)
                    self.assertEqual(search_query["limit"], [str(download.SEARCH_LIMIT)])
                    request_metadata = json.loads((output / "request.json").read_text())
                    self.assertEqual(request_metadata["image_max"], 2)
                    self.assertEqual(request_metadata["bbox_epsg"], 2154)
                    self.assertEqual(request_metadata["api_limit"], download.SEARCH_LIMIT)
                    self.assertEqual(summary["available"], 3)
                    self.assertEqual(download.parse_positive_int("400"), 400)
                    for invalid in ("0", "-1", "1.5", "invalid"):
                        with self.assertRaises(download.argparse.ArgumentTypeError):
                            download.parse_positive_int(invalid)
            finally:
                server.shutdown()
                thread.join()

    def test_capped_search_splits_bbox_until_all_photos_are_found(self):
        points = {"a": (651100, 6861100), "b": (651900, 6861900), "c": (651500, 6861500)}
        lonlat = {key: Transformer.from_crs(2154, 4326, always_xy=True).transform(*xy)
                  for key, xy in points.items()}

        def fake_read_json(url, timeout, body=None, method="GET"):
            query = parse_qs(urlsplit(url).query)
            minx, miny, maxx, maxy = map(float, query["bbox"][0].split(","))
            limit = int(query["limit"][0])
            inside = [key for key, (lon, lat) in lonlat.items()
                      if minx <= lon <= maxx and miny <= lat <= maxy]
            features = [{"type": "Feature", "id": key, "collection": "sequence",
                         "geometry": {"type": "Point", "coordinates": list(lonlat[key])}}
                        for key in inside[:limit]]
            return {"type": "FeatureCollection", "features": features, "links": []}

        args = Namespace(
            bbox=(651000, 6861000, 652000, 6862000), bbox_epsg=2154,
            output_dir=Path(tempfile.mkdtemp()), api_url="http://localhost/api",
            datetime=None, quality="hd", timeout=5, image_max=None, dry_run=True,
        )
        stdout = io.StringIO()
        with patch.object(download, "SEARCH_LIMIT", 2), \
                patch.object(download, "read_json", side_effect=fake_read_json), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(download.run(args), 0)
        self.assertIn("Photos dans la bbox EPSG:2154 : 3\n", stdout.getvalue())

    def test_picture_type_filters_candidates(self):
        photo = PHOTO
        lon, lat = Transformer.from_crs(2154, 4326, always_xy=True).transform(651500, 6861500)

        def feature(identifier, picture_type):
            return {
                "type": "Feature", "id": identifier, "collection": "sequence",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"type": picture_type},
                "assets": {"hd": {"href": "/photo.jpg", "type": "image/jpeg"}},
            }

        page = {"features": [
            feature("flat-photo", "flat"), feature("sphere-photo", "equirectangular"),
        ], "links": []}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                is_image = self.path == "/photo.jpg"
                payload = photo if is_image else json.dumps(page).encode()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg" if is_image else "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for picture_type, expected_available in (("all", 2), ("normal", 1), ("360", 1)):
                    with self.subTest(picture_type=picture_type), tempfile.TemporaryDirectory() as temporary:
                        output = Path(temporary)
                        args = Namespace(
                            bbox=(651000, 6861000, 652000, 6862000), bbox_epsg=2154,
                            output_dir=output, api_url=f"http://127.0.0.1:{server.server_port}/api",
                            datetime=None, quality="hd", timeout=5, image_max=None,
                            picture_type=picture_type,
                        )
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            self.assertEqual(download.run(args), 0)
                        summary = json.loads((output / "summary.json").read_text())
                        self.assertEqual(summary["available"], expected_available)
                        self.assertEqual(summary["downloaded"], expected_available)
                        request_metadata = json.loads((output / "request.json").read_text())
                        self.assertEqual(
                            request_metadata["picture_type"], download.PICTURE_TYPES[picture_type]
                        )
            finally:
                server.shutdown()
                thread.join()

    def test_provider_matches_item_providers_case_insensitively(self):
        feature = {"id": "p", "properties": {"providers": [
            {"name": "IGN", "roles": ["producer"]}, {"name": "Commune X", "roles": ["host"]},
        ]}}
        self.assertTrue(download.item_matches_provider(feature, "ign"))
        self.assertTrue(download.item_matches_provider(feature, "Commune X"))
        self.assertFalse(download.item_matches_provider(feature, "IG"))
        self.assertFalse(download.item_matches_provider({"id": "none", "properties": {}}, "IGN"))

    def test_collection_filters_and_id_folders(self):
        feature = {"id": "p", "collection": "uuid-1", "properties": {
            "geovisio:producer": "Immergis", "exif": "{\"Exif.Image.Software\": \"H19.03.02.02.00\"}",
        }}
        self.assertTrue(download.item_matches_filters(feature, [("geovisio:producer", ["immergis", "x"])]))
        self.assertFalse(download.item_matches_filters(feature, [("geovisio:producer", ["overflorian"])]))
        self.assertTrue(download.item_matches_filters(feature, [("Exif.Image.Software", ["h19.03.02.02.00"])]))
        self.assertTrue(download.item_matches_filters(feature, [("collection", ["UUID-1"])]))
        self.assertFalse(download.item_matches_filters(feature, [
            ("geovisio:producer", ["immergis"]), ("Exif.Image.Software", ["VID2JPG"]),
        ]))
        self.assertEqual(download.parse_filter("geovisio:producer=immergis, overflorian ,"),
                         ("geovisio:producer", ["immergis", "overflorian"]))
        with self.assertRaises(Exception):
            download.parse_filter("sans-egal")
        self.assertEqual(download.collection_folder(feature), "uuid-1")

    def test_place_by_size_splits_only_mixed_collections(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            located = []
            for identifier, collection, size in [
                ("a", "mixed", "4x3"), ("b", "mixed", "8x6"), ("c", "single", "4x3"), ("d", "single", "4x3"),
            ]:
                image = output / collection / "images" / f"{identifier}.jpg"
                metadata = output / collection / "metadata" / f"{identifier}.json"
                image.parent.mkdir(parents=True, exist_ok=True)
                metadata.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"x")
                metadata.write_text("{}", encoding="utf-8")
                feature = {"id": identifier, "collection": collection, "properties": {}}
                record = {"status": "downloaded", "size": size, "collection": collection,
                          "image": str(image.relative_to(output)), "metadata": str(metadata.relative_to(output))}
                located.append((feature, record))
            self.assertTrue(download.place_by_size(located, output))
            self.assertEqual(
                sorted(path.relative_to(output).as_posix() for path in output.glob("*/images/*.jpg")),
                ["mixed-4x3/images/a.jpg", "mixed-8x6/images/b.jpg",
                 "single/images/c.jpg", "single/images/d.jpg"],
            )
            self.assertEqual(located[1][1]["metadata"], "mixed-8x6/metadata/b.json")
            self.assertTrue((output / "mixed-8x6" / "metadata" / "b.json").is_file())

    def test_positions_geojson_in_bbox_crs_with_attributes(self):
        transformer = Transformer.from_crs(4326, 2154, always_xy=True)
        feature = {"id": "abc", "collection": "c1",
                   "geometry": {"type": "Point", "coordinates": [2.3522, 48.8566]},
                   "properties": {"providers": [{"name": "Ville"}], "exif": {"Make": "X"}}}
        record = {"id": "abc", "collection": "c1", "image": "images/a.jpg",
                  "metadata": "metadata/a.json", "source_page": "u", "status": "downloaded"}
        geojson = download.positions_geojson([(feature, record)], 2154, transformer)
        point = geojson["features"][0]
        self.assertEqual(geojson["crs"]["properties"]["name"], "urn:ogc:def:crs:EPSG::2154")
        self.assertAlmostEqual(point["geometry"]["coordinates"][0], 652469.02, places=1)
        self.assertEqual(point["properties"]["lon"], 2.3522)
        self.assertEqual(point["properties"]["providers"], "Ville")
        self.assertEqual(point["properties"]["image"], "images/a.jpg")

    def test_invalid_geometry(self):
        transformer = Transformer.from_crs(4326, 2154, always_xy=True)
        with self.assertRaises(ValueError):
            download.item_in_bbox({"id": "invalid", "geometry": None}, (0, 0, 1, 1), transformer)

    def test_bbox_in_geographic_epsg(self):
        lon, lat = Transformer.from_crs(2154, 4326, always_xy=True).transform(
            651500, 6861500
        )
        feature = {"id": "inside", "geometry": {"type": "Point", "coordinates": [lon, lat]}}
        to_bbox_crs = Transformer.from_crs(4326, 4326, always_xy=True)
        self.assertTrue(
            download.item_in_bbox(
                feature, (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01),
                to_bbox_crs,
            )
        )

    def test_run_transforms_bbox_and_selects_in_input_epsg(self):
        lon, lat = Transformer.from_crs(2154, 4326, always_xy=True).transform(
            651500, 6861500
        )
        feature = {
            "type": "Feature", "id": "inside", "collection": "sequence",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "assets": {"hd": {"href": "https://example.test/photo.jpg"}},
        }
        bbox = (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            args = Namespace(
                bbox=bbox, bbox_epsg=4326, output_dir=output,
                api_url="https://example.test/api", datetime=None,
                quality="hd", timeout=5, image_max=None,
            )

            def save_image(url, destination, timeout):
                destination.write_bytes(PHOTO)

            with patch.object(
                download, "read_json",
                return_value={"features": [feature], "links": []},
            ) as read_json, patch.object(
                download, "download_image", side_effect=save_image
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(download.run(args), 0)

            query_bbox = tuple(
                map(float, parse_qs(urlsplit(read_json.call_args.args[0]).query)["bbox"][0].split(","))
            )
            for actual, expected in zip(query_bbox, bbox):
                self.assertAlmostEqual(actual, expected)
            request_metadata = json.loads((output / "request.json").read_text())
            self.assertEqual(request_metadata["bbox_epsg"], 4326)
            self.assertEqual(request_metadata["bbox_crs"], "EPSG:4326")
            self.assertEqual(json.loads((output / "summary.json").read_text())["selected"], 1)

    def test_dry_run_writes_only_request_summary_and_geojson(self):
        lon, lat = Transformer.from_crs(2154, 4326, always_xy=True).transform(651500, 6861500)
        feature = {
            "type": "Feature", "id": "inside", "collection": "sequence",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "assets": {"hd": {"href": "https://example.test/photo.jpg"}},
        }
        bbox = (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            args = Namespace(
                bbox=bbox, bbox_epsg=4326, output_dir=output,
                api_url="https://example.test/api", datetime=None,
                quality="hd", timeout=5, image_max=None, dry_run=True,
            )
            with patch.object(
                download, "read_json",
                return_value={"features": [feature], "links": []},
            ), patch.object(download, "download_image") as download_image, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(download.run(args), 0)

            download_image.assert_not_called()
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["positions.geojson", "request.json", "summary.json"],
            )
            self.assertEqual(json.loads((output / "summary.json").read_text())["selected"], 1)
            geojson = json.loads((output / "positions.geojson").read_text())
            self.assertEqual(len(geojson["features"]), 1)

    def test_http_retry_limit(self):
        error = HTTPError("http://localhost/api/search", 429, "Too many requests", {}, None)
        with patch.object(download, "urlopen", side_effect=error) as opening:
            with patch.object(download.time, "sleep") as sleeping:
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(HTTPError):
                        download.read_json("http://localhost/api/search", 1)
                self.assertEqual(opening.call_count, 3)
                self.assertEqual(sleeping.call_count, 2)


if __name__ == "__main__":
    unittest.main()
