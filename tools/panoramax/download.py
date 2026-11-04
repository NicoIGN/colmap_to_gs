#!/usr/bin/env python3
"""Télécharger les photos Panoramax prises dans une bbox Lambert-93."""

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from pyproj import Transformer
from pyproj.exceptions import ProjError

USER_AGENT = "colmap_to_gs-panoramax/1.0"


class Progress:
    def __init__(self, total):
        self.total = total
        self.current = 0
        self.enabled = sys.stderr.isatty()

    def update(self, counts):
        if not self.enabled:
            return
        self.current += 1
        self.render(counts)

    def render(self, counts):
        width = 28
        if self.total:
            fraction = min(self.current / self.total, 1)
            filled = int(width * fraction)
            bar = f"[{'#' * filled}{'-' * (width - filled)}] {fraction:6.1%}"
            progress = f"{self.current}/{self.total}"
        else:
            bar = f"[{'#' * (self.current % (width + 1)):<{width}}]"
            progress = f"{self.current} sélectionnées"
        message = (
            f"\r{bar} {progress} | "
            f"{counts['downloaded']} téléchargées, "
            f"{counts['reused']} réutilisées, "
            f"{counts['failed']} erreurs"
        )
        print(message, end="", file=sys.stderr, flush=True)

    def finish(self, counts, capped=False):
        if self.enabled and self.current:
            if self.total and not capped:
                self.total = self.current
                self.render(counts)
            print(file=sys.stderr, flush=True)


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        except (OSError, TypeError, ValueError):
            temporary.unlink()
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def request(url, timeout, consume, body=None, method=None):
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"URL HTTP(S) requise : {url}")
    headers = {"User-Agent": USER_AGENT}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        try:
            with urlopen(
                Request(url, data=data, headers=headers, method=method), timeout=timeout
            ) as response:
                return consume(response)
        except HTTPError as exc:
            if attempt == 2 or (exc.code != 429 and not 500 <= exc.code < 600):
                raise
            print(f"HTTP {exc.code}, nouvelle tentative : {url}", file=sys.stderr)
        except (URLError, TimeoutError, ConnectionError, HTTPException) as exc:
            if attempt == 2:
                raise
            print(f"Erreur réseau ({exc}), nouvelle tentative : {url}", file=sys.stderr)
        time.sleep(2 ** attempt)


def read_json(url, timeout, body=None, method=None):
    return request(url, timeout, lambda response: json.load(response), body, method)


def download_image(url, destination, timeout):
    def consume(response):
        mime = response.headers.get_content_type()
        if mime not in ("image/jpeg", "application/octet-stream"):
            raise ValueError(f"Réponse non JPEG ({mime}) : {url}")
        with destination.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        size = destination.stat().st_size
        expected = response.headers.get("Content-Length")
        if size == 0 or (expected is not None and size != int(expected)):
            raise ValueError(f"Photo vide ou tronquée : {url}")
        with destination.open("rb") as stream:
            if stream.read(2) != b"\xff\xd8":
                raise ValueError(f"Signature JPEG invalide : {url}")
        return size

    return request(url, timeout, consume)


def parse_bbox(text):
    try:
        bbox = tuple(float(part) for part in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Utiliser xmin,ymin,xmax,ymax en mètres.") from exc
    if (
        len(bbox) != 4
        or not all(math.isfinite(value) for value in bbox)
        or bbox[0] >= bbox[2]
        or bbox[1] >= bbox[3]
    ):
        raise argparse.ArgumentTypeError("Bbox finie requise avec xmin < xmax et ymin < ymax.")
    return bbox


def parse_positive_int(text):
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Un entier positif est requis.") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("La valeur doit être supérieure ou égale à 1.")
    return value


def item_in_bbox(feature, bbox, to_l93):
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise ValueError(f"Géométrie Point attendue pour {feature.get('id')}.")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ValueError(f"Coordonnées absentes pour {feature.get('id')}.")
    lon, lat = coordinates[:2]
    if (
        isinstance(lon, bool) or isinstance(lat, bool)
        or not isinstance(lon, (int, float)) or not isinstance(lat, (int, float))
        or not math.isfinite(lon) or not math.isfinite(lat)
        or not -180 <= lon <= 180 or not -90 <= lat <= 90
    ):
        raise ValueError(f"Coordonnées WGS84 invalides pour {feature.get('id')}.")
    x, y = to_l93.transform(lon, lat, errcheck=True)
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def run(args):
    image_max = getattr(args, "image_max", None)
    if image_max is not None and image_max < 1:
        raise ValueError("--image-max doit être supérieur ou égal à 1.")
    output = args.output_dir.expanduser().resolve()
    for folder in ("images", "metadata", "pages"):
        (output / folder).mkdir(parents=True, exist_ok=True)
    to_wgs84 = Transformer.from_crs(2154, 4326, always_xy=True)
    to_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
    xmin, ymin, xmax, ymax = args.bbox
    boundary = []
    for index in range(103):
        x = xmin + (xmax - xmin) * index / 102
        y = ymin + (ymax - ymin) * index / 102
        boundary.extend(((x, ymin), (x, ymax), (xmin, y), (xmax, y)))
    longitudes, latitudes = to_wgs84.transform(
        [point[0] for point in boundary], [point[1] for point in boundary], errcheck=True
    )
    geographic = (min(longitudes), min(latitudes), max(longitudes), max(latitudes))
    if not all(math.isfinite(value) for value in geographic) or geographic[0] >= geographic[2]:
        raise ValueError("La bbox Lambert-93 ne produit pas une bbox WGS84 exploitable.")
    api_limit = image_max or 100
    parameters = {"bbox": ",".join(map(str, geographic)), "limit": api_limit}
    if args.datetime:
        parameters["datetime"] = args.datetime
    url = args.api_url.rstrip("/") + "/search?" + urlencode(parameters)
    atomic_json(output / "request.json", {
        "crs": "EPSG:2154", "bbox": args.bbox, "bbox_wgs84": geographic,
        "api_url": args.api_url, "datetime": args.datetime, "quality": args.quality,
        "selection": "photo_capture_point_inside_bbox",
        "image_max": image_max,
        "api_limit": api_limit,
    })
    counts = {"pages": 0, "selected": 0, "downloaded": 0, "reused": 0, "failed": 0}
    progress = Progress(image_max)
    seen_items, seen_pages = set(), set()
    body, method = None, "GET"
    reached_image_max = False
    with (output / "manifest.jsonl").open("w", encoding="utf-8") as manifest:
        while url:
            page_key = (url, method, json.dumps(body, sort_keys=True))
            if page_key in seen_pages:
                raise ValueError("Boucle détectée dans la pagination Panoramax.")
            seen_pages.add(page_key)
            page = read_json(url, args.timeout, body, method)
            if not isinstance(page, dict) or not isinstance(page.get("features"), list):
                raise ValueError(f"Réponse STAC FeatureCollection invalide : {url}")
            counts["pages"] += 1
            atomic_json(output / "pages" / f"page-{counts['pages']:06d}.json", page)
            for feature in page["features"]:
                if image_max is not None and counts["selected"] >= image_max:
                    break
                if not isinstance(feature, dict) or not feature.get("id"):
                    raise ValueError("Photo STAC sans identifiant.")
                key = str(feature.get("collection", "")) + "/" + str(feature["id"])
                if key in seen_items:
                    continue
                seen_items.add(key)
                if not item_in_bbox(feature, args.bbox, to_l93):
                    continue
                counts["selected"] += 1
                name = hashlib.sha256(key.encode("utf-8")).hexdigest()
                image = output / "images" / f"{name}.jpg"
                metadata = output / "metadata" / f"{name}.json"
                record = {
                    "id": feature["id"], "collection": feature.get("collection"),
                    "image": str(image.relative_to(output)),
                    "metadata": str(metadata.relative_to(output)), "source_page": url,
                }
                temporary = None
                try:
                    asset = feature.get("assets", {}).get(args.quality)
                    if not isinstance(asset, dict) or not asset.get("href"):
                        raise ValueError(f"Asset {args.quality} absent pour {feature['id']}.")
                    asset_url = urljoin(url, asset["href"])
                    previous = {}
                    if metadata.is_file():
                        previous = json.loads(metadata.read_text(encoding="utf-8")).get("download", {})
                    if (
                        image.is_file() and image.stat().st_size > 0
                        and previous.get("url") == asset_url
                        and previous.get("bytes") == image.stat().st_size
                    ):
                        size = image.stat().st_size
                        counts["reused"] += 1
                        record["status"] = "reused"
                    else:
                        fd, temporary_name = tempfile.mkstemp(dir=image.parent, suffix=".part")
                        os.close(fd)
                        temporary = Path(temporary_name)
                        size = download_image(asset_url, temporary, args.timeout)
                        os.replace(temporary, image)
                        counts["downloaded"] += 1
                        record["status"] = "downloaded"
                    atomic_json(metadata, {
                        "feature": feature, "source_page": url,
                        "download": {"url": asset_url, "quality": args.quality, "bytes": size},
                    })
                except (OSError, ValueError, HTTPException) as exc:
                    counts["failed"] += 1
                    record.update(status="failed", error=str(exc))
                    print(f"Erreur pour {feature['id']} : {exc}", file=sys.stderr)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                progress.update(counts)
                manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
                manifest.flush()
            print(
                f"Page {counts['pages']} : {counts['selected']} photos sélectionnées, "
                f"{counts['downloaded']} téléchargées, {counts['reused']} réutilisées, "
                f"{counts['failed']} erreurs", flush=True,
            )
            next_links = [link for link in page.get("links", []) if link.get("rel") == "next"]
            if image_max is not None and counts["selected"] >= image_max:
                reached_image_max = True
                url = None
                continue
            if len(next_links) > 1:
                raise ValueError("Plusieurs liens next : pagination ambiguë.")
            if next_links:
                link = next_links[0]
                url = urljoin(url, link["href"])
                method = link.get("method", "GET").upper()
                if method not in ("GET", "POST") or link.get("merge"):
                    raise ValueError("Pagination STAC non prise en charge (méthode ou merge).")
                body = link.get("body") if method == "POST" else None
            else:
                url = None
    progress.finish(counts, capped=reached_image_max)
    atomic_json(output / "summary.json", counts)
    print(f"Résultats : {output}", flush=True)
    return 1 if counts["failed"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True, type=parse_bbox, help="xmin,ymin,xmax,ymax en EPSG:2154")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--api-url", default="https://explore.panoramax.fr/api", help="Racine API STAC, sans /search")
    parser.add_argument("--datetime", help="Date ou intervalle STAC ISO 8601 (début/fin)")
    parser.add_argument("--quality", choices=("hd", "sd", "thumb"), default="hd")
    parser.add_argument(
        "--image-max",
        type=parse_positive_int,
        help="Nombre maximum de photos à sélectionner et télécharger (toutes pages confondues)",
    )
    parser.add_argument("--timeout", type=float, default=120, help="Délai réseau en secondes")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout doit être fini et positif.")
    try:
        return run(args)
    except (OSError, ValueError, HTTPException, ProjError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
