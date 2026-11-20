#!/usr/bin/env python3
"""Télécharge une couche vecteur (BD TOPO IGN ou OpenStreetMap) dans un GeoPackage en Lambert-93."""

import argparse
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from pyproj import Transformer
import pyogrio.raw
import shapely
from shapely.geometry import LineString, Polygon, shape

CRS = "EPSG:2154"
WFS_URL = "https://data.geopf.fr/wfs/ows"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "colmap-to-gs-vector"

IGN_LAYERS = {
    "building": "BDTOPO_V3:batiment",
    "road": "BDTOPO_V3:troncon_de_route",
    "water": "BDTOPO_V3:surface_hydrographique",
    "vegetation": "BDTOPO_V3:zone_de_vegetation",
}

OSM_FILTERS = {
    "building": ['way["building"]'],
    "road": ['way["highway"]'],
    "water": ['way["natural"="water"]', 'way["waterway"]'],
    "vegetation": ['way["natural"="wood"]', 'way["landuse"="forest"]'],
}


def parse_bbox(value):
    values = [float(v) for v in value.split(",")]
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise ValueError("Emprise attendue : xmin,ymin,xmax,ymax finis")
    xmin, ymin, xmax, ymax = values
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("Emprise invalide : xmin < xmax et ymin < ymax requis")
    return tuple(values)


def resolve_layer(source, layer):
    """Retourne (clé de sortie, identifiant source). Accepte un nom de couche WFS IGN complet."""
    if source == "ign":
        if layer in IGN_LAYERS:
            return layer, IGN_LAYERS[layer]
        if ":" in layer:
            return re.sub(r"\W+", "_", layer.split(":", 1)[1]).lower(), layer
        raise ValueError(f"Couche IGN inconnue : {layer} (disponibles : {', '.join(IGN_LAYERS)})")
    if layer in OSM_FILTERS:
        return layer, layer
    raise ValueError(f"Couche OSM inconnue : {layer} (disponibles : {', '.join(OSM_FILTERS)})")


def http_request(url, *, data=None, timeout=300):
    body = urlencode(data).encode() if data is not None else None
    request = Request(url, data=body, headers={"Accept": "application/json",
                                              "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_ign_crs(page):
    name = ((page.get("crs") or {}).get("properties") or {}).get("name", "")
    if name and not name.endswith("2154"):
        raise ValueError(f"CRS inattendu renvoyé par le WFS : {name}")


def fetch_ign_features(typename, bbox, *, page_size=1000, fetch=http_request):
    """Pagine explicitement le WFS ; refuse une pagination qui renvoie des entités déjà vues."""
    if page_size < 1:
        raise ValueError("La taille de page doit être >= 1")
    xmin, ymin, xmax, ymax = bbox
    features, seen_ids, start = [], set(), 0
    while True:
        query = urlencode({
            "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
            "TYPENAMES": typename, "SRSNAME": CRS,
            "BBOX": f"{xmin},{ymin},{xmax},{ymax},{CRS}",
            "OUTPUTFORMAT": "application/json",
            "COUNT": page_size, "STARTINDEX": start,
        })
        page = fetch(f"{WFS_URL}?{query}")
        check_ign_crs(page)
        batch = page.get("features", [])
        for feature in batch:
            feature_id = feature.get("id")
            if feature_id is not None:
                if feature_id in seen_ids:
                    raise ValueError(f"Pagination WFS répétée à l'index {start}")
                seen_ids.add(feature_id)
        features.extend(batch)
        if len(batch) < page_size:
            return features
        start += len(batch)


def osm_lat_lon_bbox(bbox):
    """Emprise WGS84 (sud, ouest, nord, est) couvrant le bord du rectangle Lambert-93."""
    xmin, ymin, xmax, ymax = bbox
    edge = np.linspace(0, 1, 11)
    xs = np.concatenate([xmin + (xmax - xmin) * edge, xmin + (xmax - xmin) * edge,
                         xmin + 0 * edge, xmax + 0 * edge])
    ys = np.concatenate([ymin + 0 * edge, ymax + 0 * edge,
                         ymin + (ymax - ymin) * edge, ymin + (ymax - ymin) * edge])
    lons, lats = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(xs, ys)
    return min(lats), min(lons), max(lats), max(lons)


def fetch_osm_elements(layer, bbox, *, overpass_url=OVERPASS_URL, fetch=http_request):
    south, west, north, east = osm_lat_lon_bbox(bbox)
    selectors = "".join(f"{s}({south},{west},{north},{east});" for s in OSM_FILTERS[layer])
    query = f"[out:json][timeout:180];({selectors});out geom;"
    return fetch(overpass_url, data={"data": query}).get("elements", [])


def is_osm_area(tags):
    return ("building" in tags or tags.get("area") == "yes" or "landuse" in tags
            or tags.get("natural") in {"water", "wood"})


def osm_features(elements):
    """Convertit les ways Overpass (`out geom`) en géométries Lambert-93."""
    to_lambert = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    features = []
    for element in elements:
        if element.get("type") != "way":
            continue
        points = [(p["lon"], p["lat"]) for p in element.get("geometry", [])]
        if len(points) < 2:
            continue
        xs, ys = to_lambert.transform([p[0] for p in points], [p[1] for p in points])
        coords = list(zip(xs, ys))
        tags = element.get("tags", {})
        closed = len(coords) >= 4 and coords[0] == coords[-1]
        geometry = Polygon(coords) if closed and is_osm_area(tags) else LineString(coords)
        features.append({"geometry": geometry,
                         "properties": {"osm_id": element["id"], **tags}})
    return features


def ign_features(raw_features):
    return [{"geometry": shape(f["geometry"]), "properties": f.get("properties") or {}}
            for f in raw_features if f.get("geometry")]


def column(values):
    present = [v for v in values if v is not None]
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present)
    if present and numeric:
        if len(present) == len(values) and all(isinstance(v, int) for v in values):
            return np.array(values, dtype=np.int64)
        return np.array([np.nan if v is None else v for v in values], dtype=np.float64)
    return np.array([None if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                     for v in values], dtype=object)


def write_gpkg(path, layer, features):
    path.parent.mkdir(parents=True, exist_ok=True)
    geometries = np.array([shapely.to_wkb(f["geometry"]) for f in features], dtype=object)
    names = list(dict.fromkeys(k for f in features for k in f["properties"]))
    field_data = [column([f["properties"].get(name) for f in features]) for name in names]
    pyogrio.raw.write(str(path), geometries, field_data, names, layer=layer,
                      driver="GPKG", geometry_type="Unknown", crs=CRS)


def fetch_vector(bbox, source, layer, output_dir, *, page_size=1000, overpass_url=OVERPASS_URL):
    key, source_id = resolve_layer(source, layer)
    if source == "ign":
        features = ign_features(fetch_ign_features(source_id, bbox, page_size=page_size))
    else:
        features = osm_features(fetch_osm_elements(source_id, bbox, overpass_url=overpass_url))
    output = Path(output_dir) / f"{source}_{key}.gpkg"
    write_gpkg(output, key, features)
    print(f"{len(features)} entités écrites dans {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True, type=parse_bbox,
                        help="xmin,ymin,xmax,ymax en Lambert-93 (EPSG:2154)")
    parser.add_argument("--source", choices=["ign", "osm"], default="ign")
    parser.add_argument("--layer", default="building",
                        help="building, road, water, vegetation ; pour --source ign, un nom WFS complet est aussi accepté")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--page-size", type=int, default=1000, help="pagination WFS IGN")
    parser.add_argument("--overpass-url", default=OVERPASS_URL)
    args = parser.parse_args()
    fetch_vector(args.bbox, args.source, args.layer, args.output_dir,
                 page_size=args.page_size, overpass_url=args.overpass_url)


if __name__ == "__main__":
    main()
