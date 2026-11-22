#!/usr/bin/env python3
"""Comparer les centres caméra d'un sparse COLMAP aux positions d'un GeoJSON, dans le repère ENU du gauge.txt."""

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pycolmap
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from georef_colmap_from_gpx import enu_rotation, geodetic_to_ecef


def read_gauge_origin(path):
    values = {}
    for raw in Path(path).read_text().splitlines():
        parts = raw.split()
        if len(parts) >= 2 and not raw.startswith("#"):
            values[parts[0]] = parts[1]
    return float(values["origin_lat"]), float(values["origin_lon"]), float(values.get("origin_alt", 0.0))


def sparse_centres(model):
    reconstruction = pycolmap.Reconstruction(str(model))
    return {
        Path(image.name).stem: np.asarray(image.projection_center(), dtype=float)
        for image in reconstruction.images.values()
    }


def geojson_enu(path, epsg, origin):
    data = json.loads(Path(path).read_text())
    to_wgs84 = Transformer.from_crs(epsg, 4326, always_xy=True)
    lat0, lon0, h0 = origin
    origin_ecef = geodetic_to_ecef(lat0, lon0, h0)
    rotation = enu_rotation((lat0, lon0))
    centres = {}
    for feature in data["features"]:
        props = feature.get("properties") or {}
        coords = feature["geometry"]["coordinates"]
        height = coords[2] if len(coords) > 2 and coords[2] is not None else h0
        lon, lat = to_wgs84.transform(coords[0], coords[1])
        enu = rotation @ (geodetic_to_ecef(lat, lon, height) - origin_ecef)
        name = props.get("image") or props.get("id")
        if name:
            centres[Path(str(name)).stem] = enu
    return centres


def similarity_2d(source, target):
    """Similitude (échelle, rotation, translation) telle que target ≈ s R source + t."""
    mu_s, mu_t = source.mean(axis=0), target.mean(axis=0)
    s0, t0 = source - mu_s, target - mu_t
    n = len(source)
    u, d, vt = np.linalg.svd(t0.T @ s0 / n)
    sign = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sign[1, 1] = -1
    rotation = u @ sign @ vt
    scale = np.trace(np.diag(d) @ sign) * n / (s0 ** 2).sum()
    translation = mu_t - scale * rotation @ mu_s
    return scale, rotation, translation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sparse", required=True, type=Path, help="Dossier sparse COLMAP (images.bin ou .txt).")
    parser.add_argument("--geojson", required=True, type=Path, help="positions.geojson du téléchargement.")
    parser.add_argument("--gauge", required=True, type=Path, help="gauge.txt du sparse.")
    parser.add_argument("--epsg", type=int, default=2154, help="CRS du GeoJSON (défaut : 2154).")
    args = parser.parse_args()

    origin = read_gauge_origin(args.gauge)
    sparse = sparse_centres(args.sparse)
    geo = geojson_enu(args.geojson, args.epsg, origin)
    names = sorted(set(sparse) & set(geo))
    if len(names) < 3:
        raise SystemExit(f"Seulement {len(names)} images communes entre le sparse et le GeoJSON (minimum 3).")

    src = np.array([sparse[name] for name in names])
    dst = np.array([geo[name] for name in names])
    offset = (src - dst)[:, :2]
    z_offset = (src - dst)[:, 2]
    dist = np.linalg.norm(offset, axis=1)

    print(f"Images communes : {len(names)}")
    print(f"Décalage planimétrique sparse - GeoJSON (m) : médiane E {np.median(offset[:, 0]):.3f}, "
          f"N {np.median(offset[:, 1]):.3f} ; norme médiane {np.median(dist):.3f}, max {dist.max():.3f}")
    print(f"Décalage Z sparse - GeoJSON (m) : médiane {np.median(z_offset):.3f} "
          "(attention : Z GeoJSON = EXIF au-dessus du géoïde, Z gauge = ellipsoïdal)")

    scale, rotation, translation = similarity_2d(src[:, :2], dst[:, :2])
    angle = np.degrees(np.arctan2(rotation[1, 0], rotation[0, 0]))
    fitted = (scale * (rotation @ src[:, :2].T)).T + translation
    residual = np.linalg.norm(fitted - dst[:, :2], axis=1)
    print(f"Similitude 2D sparse -> GeoJSON : échelle {scale:.6f}, rotation {angle:.4f}°, "
          f"translation E {translation[0]:.3f}, N {translation[1]:.3f} m")
    print(f"Résidu après similitude : RMS {np.sqrt((residual ** 2).mean()):.3f} m, max {residual.max():.3f} m")


if __name__ == "__main__":
    main()
