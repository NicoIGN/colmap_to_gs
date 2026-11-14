#!/usr/bin/env python3
"""Extract IGN LiDAR HD COPC points using the Geoplateforme WFS tile index."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import numpy as np

from common import load_lidar, write_report

WFS = "https://data.geopf.fr/wfs"
LAYER = "IGNF_LIDAR-HD_METADONNEE:metadata"
CRS = "EPSG:2154"


def bbox_values(value):
    bbox = np.array([float(v) for v in value.split(",")])
    if (bbox.shape != (4,) or not np.isfinite(bbox).all()
            or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
        raise ValueError("Emprise attendue : xmin,ymin,xmax,ymax finis avec min < max")
    return bbox


def copc_url(value):
    if not isinstance(value, str):
        raise ValueError("Métadonnée WFS sans url_npl")
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname != "data.geopf.fr"
            or parts.username or parts.password
            or not parts.path.lower().endswith(".copc.laz")):
        raise ValueError(f"URL COPC IGN non prise en charge : {value}")
    if parts.path.startswith("/telechargement/"):
        parts = parts._replace(path="/chunk" + parts.path)
    return urlunsplit(parts)


def get_json(url):
    request = Request(url, headers={"Accept": "application/json",
                                   "User-Agent": "colmap-to-gs-lidar"})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def discover_tiles(bbox, *, page_size=100, max_tiles=100, fetch=get_json):
    """Paginate WFS explicitly; reject incomplete/repeated pages instead of guessing."""
    if page_size < 1 or max_tiles < 1:
        raise ValueError("Limites de pagination et de tuiles attendues >= 1")
    urls, seen_ids, start = [], set(), 0
    while True:
        query = urlencode({
            "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
            "TYPENAMES": LAYER, "OUTPUTFORMAT": "application/json", "SRSNAME": CRS,
            "BBOX": ",".join(str(v) for v in bbox) + "," + CRS,
            "COUNT": page_size, "STARTINDEX": start,
            "SORTBY": "coordonnees_nw",
        })
        page = fetch(WFS + "?" + query)
        if not isinstance(page, dict) or page.get("type") != "FeatureCollection":
            raise ValueError("Réponse WFS invalide : FeatureCollection attendue")
        features = page.get("features")
        if not isinstance(features, list):
            raise ValueError("Réponse WFS sans liste de features")
        if not features:
            matched = page.get("numberMatched")
            if matched not in (None, "unknown") and start < int(matched):
                raise ValueError("Pagination WFS incomplète")
            break
        for feature in features:
            if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
                raise ValueError("Feature WFS invalide")
            url = copc_url(feature["properties"].get("url_npl"))
            identity = feature.get("id") or url
            if identity in seen_ids:
                raise ValueError("WFS a répété une feature : pagination non fiable")
            seen_ids.add(identity)
            if url not in urls:
                urls.append(url)
            if len(seen_ids) > max_tiles:
                raise ValueError("Trop de tuiles WFS : réduire l'emprise ou augmenter --max-tiles")
        start += len(features)
        matched = page.get("numberMatched")
        if matched not in (None, "unknown") and start >= int(matched):
            break
    if not urls:
        raise ValueError("Aucune tuile LiDAR HD disponible dans cette emprise")
    return urls


def pdal_pipeline(urls, bbox, output):
    xmin, ymin, xmax, ymax = bbox
    bounds = f"([{xmin},{xmax}],[{ymin},{ymax}])"
    stages = [{"type": "readers.copc", "filename": url, "bounds": bounds,
               "tag": f"tile_{i}"} for i, url in enumerate(urls)]
    stages.extend([
        {"type": "filters.merge", "inputs": [f"tile_{i}" for i in range(len(urls))]},
        {"type": "filters.crop", "bounds": bounds},
        {"type": "writers.las", "filename": str(output), "compression": "laszip",
         "forward": "all", "a_srs": CRS},
    ])
    return {"pipeline": stages}


def fetch_lidar(bbox, output, *, pdal="pdal", max_tiles=100):
    if not shutil.which(pdal):
        raise ValueError("PDAL introuvable ; installer PDAL avec readers.copc et writers.las")
    manifest = output.with_suffix(output.suffix + ".json")
    pipeline_path = output.with_suffix(output.suffix + ".pipeline.json")
    partial = output.with_name(output.stem + ".partial.laz")
    for path in (output, manifest, pipeline_path, partial):
        if path.exists():
            raise ValueError(f"Sortie déjà présente : {path}")
    urls = discover_tiles(bbox, max_tiles=max_tiles)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_report(pipeline_path, pdal_pipeline(urls, bbox, partial))
    print(f"[lidar] Extraction COPC de {len(urls)} tuile(s) via PDAL.", flush=True)
    subprocess.run([pdal, "pipeline", str(pipeline_path)], check=True)
    if not partial.is_file():
        raise ValueError("PDAL n'a pas produit de LAZ")
    # Validation before publishing; a failed extraction keeps its partial for diagnosis.
    points = load_lidar(partial, np.array([bbox[0], bbox[1], 0.]))
    global_xy = points[:, :2] + bbox[:2]
    if (np.any(global_xy < bbox[:2] - 0.01) or np.any(global_xy > bbox[2:] + 0.01)):
        raise ValueError("LAZ hors emprise : extraction PDAL invalide")
    partial.rename(output)
    report = {"wfs": WFS, "layer": LAYER, "crs": CRS,
              "bbox": bbox.tolist(), "copc_urls": urls, "points": len(points),
              "classes_preserved": True}
    write_report(manifest, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True, help="xmin,ymin,xmax,ymax en Lambert-93")
    parser.add_argument("--crs", required=True, choices=[CRS])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdal", default="pdal")
    parser.add_argument("--max-tiles", type=int, default=100)
    args = parser.parse_args()
    try:
        if args.output.suffix.lower() != ".laz":
            raise ValueError("Sortie .laz requise")
        fetch_lidar(bbox_values(args.bbox), args.output, pdal=args.pdal, max_tiles=args.max_tiles)
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"[ERREUR] Récupération LiDAR IGN : {error}\n")


if __name__ == "__main__":
    main()
