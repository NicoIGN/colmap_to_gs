import contextlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from pyproj import Transformer

spec = importlib.util.spec_from_file_location("panoramax_download", Path(__file__).with_name("download.py"))
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


class DownloadTest(unittest.TestCase):
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
        photo = b"\xff\xd8test-image\xff\xd9"
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
                        api_url=f"http://127.0.0.1:{server.server_port}/api",
                        datetime="2026-01-01T00:00:00Z/2026-12-31T23:59:59Z",
                        quality="hd", timeout=5,
                    )
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(download.run(args), 0)
                        summary = json.loads((output / "summary.json").read_text())
                        self.assertEqual(summary, {
                            "pages": 2, "available": 2, "available_is_exact": True,
                            "selected": 2, "downloaded": 2, "reused": 0, "failed": 0,
                        })
                        self.assertEqual(len(list((output / "images").glob("*.jpg"))), 2)
                        self.assertEqual(len(list((output / "pages").glob("*.json"))), 2)
                        for image in (output / "images").glob("*.jpg"):
                            self.assertEqual(image.read_bytes(), photo)
                        self.assertIn("bbox=", requests[0])
                        self.assertIn("datetime=", requests[0])
                        self.assertEqual(download.run(args), 0)
                        self.assertEqual(requests.count("/photo.jpg"), 2)
                        self.assertEqual(json.loads((output / "summary.json").read_text())["reused"], 2)
                        pages[1]["features"].append(feature("missing-asset", [lon, lat], False))
                        self.assertEqual(download.run(args), 1)
                        self.assertEqual(json.loads((output / "summary.json").read_text())["failed"], 1)
                        entries = [json.loads(line) for line in (output / "manifest.jsonl").read_text().splitlines()]
                        self.assertEqual(entries[-1]["status"], "failed")
                        self.assertEqual(list((output / "images").glob("*.part")), [])
                        next((output / "images").glob("*.jpg")).unlink()
                        photo = b"invalid-jpeg"
                        self.assertEqual(download.run(args), 1)
                        self.assertEqual(json.loads((output / "summary.json").read_text())["failed"], 2)
                        self.assertEqual(list((output / "images").glob("*.part")), [])
            finally:
                server.shutdown()
                thread.join()

    def test_invalid_bbox(self):
        for value in ("0,0,0,1", "nan,0,1,1", "0,0,1", "bad"):
            with self.assertRaises(download.argparse.ArgumentTypeError):
                download.parse_bbox(value)

    def test_image_max_stops_pagination_at_total_selection_limit(self):
        requests = []
        photo = b"\xff\xd8test-image\xff\xd9"
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
                    self.assertEqual(len(list((output / "images").glob("*.jpg"))), 2)
                    from urllib.parse import parse_qs, urlsplit
                    search_query = parse_qs(urlsplit(requests[0]).query)
                    self.assertEqual(search_query["limit"], [str(download.SEARCH_LIMIT)])
                    request_metadata = json.loads((output / "request.json").read_text())
                    self.assertEqual(request_metadata["image_max"], 2)
                    self.assertEqual(request_metadata["api_limit"], download.SEARCH_LIMIT)
                    self.assertEqual(summary["available"], 3)
                    self.assertEqual(download.parse_positive_int("400"), 400)
                    for invalid in ("0", "-1", "1.5", "invalid"):
                        with self.assertRaises(download.argparse.ArgumentTypeError):
                            download.parse_positive_int(invalid)
            finally:
                server.shutdown()
                thread.join()

    def test_invalid_geometry(self):
        transformer = Transformer.from_crs(4326, 2154, always_xy=True)
        with self.assertRaises(ValueError):
            download.item_in_bbox({"id": "invalid", "geometry": None}, (0, 0, 1, 1), transformer)

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
