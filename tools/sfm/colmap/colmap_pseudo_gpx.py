#!/usr/bin/env python3
"""Générer une pseudo-trajectoire GPX à partir des centres caméra d'un modèle COLMAP et de son gauge.txt.

Le repère du modèle est ENU, centré sur origin_lat/origin_lon/origin_alt du gauge.txt.
Les sommets sont ordonnés géométriquement (plus proche voisin puis 2-opt, chemin ouvert),
ou par indice de frame `_fXXXXX` du nom d'image avec --order frame.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pycolmap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from georef_colmap_from_gpx import enu_to_wgs84
from compare_sparse_geojson import read_gauge_origin

FRAME_PATTERN = re.compile(r"_f([0-9]+)(?=[^0-9]|$)")
GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def camera_centres(model):
    reconstruction = pycolmap.Reconstruction(str(model))
    images = sorted(reconstruction.images.values(), key=lambda image: image.name)
    names = [image.name for image in images]
    centres = np.array([np.asarray(image.projection_center(), dtype=float) for image in images])
    return names, centres


def frame_order(names):
    frames = []
    for name in names:
        match = FRAME_PATTERN.search(Path(name).name)
        if match is None:
            raise ValueError(f"Pas d'indice de frame dans '{name}' (utiliser --order path).")
        frames.append(int(match.group(1)))
    return np.argsort(frames, kind="stable")


def nearest_neighbour_order(points, start=0):
    n = len(points)
    visited = np.zeros(n, dtype=bool)
    order = [start]
    visited[start] = True
    for _ in range(n - 1):
        distances = np.linalg.norm(points - points[order[-1]], axis=1)
        distances[visited] = np.inf
        nxt = int(np.argmin(distances))
        order.append(nxt)
        visited[nxt] = True
    return np.array(order)


def two_opt(points, order, max_passes=100):
    """Améliore un chemin ouvert en retournant des sous-séquences tant que cela raccourcit le trajet."""
    order = np.array(order)
    n = len(order)
    for _ in range(max_passes):
        improved = False
        # i = -1 retourne le préfixe order[0..j] ; sinon retourne order[i+1..j].
        for i in range(-1, n - 1):
            js = np.arange(i + 1, n)
            b = points[order[i + 1]]
            c = points[order[js]]
            has_next = js + 1 < n
            d = points[order[np.minimum(js + 1, n - 1)]]
            removed = np.where(has_next, np.linalg.norm(c - d, axis=1), 0.0)
            added = np.where(has_next, np.linalg.norm(b - d, axis=1), 0.0)
            if i >= 0:
                a = points[order[i]]
                added = added + np.linalg.norm(a - c, axis=1)
                removed = removed + np.linalg.norm(a - b)
            delta = added - removed
            k = int(np.argmin(delta))
            if delta[k] < -1e-9:
                j = js[k]
                order[i + 1:j + 1] = order[i + 1:j + 1][::-1]
                improved = True
        if not improved:
            break
    return order


def candidate_starts(points):
    """Extrémités probables d'une trajectoire : points les plus éloignés du centroïde puis l'un de l'autre."""
    first = int(np.argmax(np.linalg.norm(points - points.mean(axis=0), axis=1)))
    second = int(np.argmax(np.linalg.norm(points - points[first], axis=1)))
    return dict.fromkeys([first, second, 0])


def order_centres(names, centres, method):
    if method == "frame":
        return frame_order(names)
    best = None
    for start in candidate_starts(centres):
        order = two_opt(centres, nearest_neighbour_order(centres, start))
        if best is None or path_length(centres[order]) < path_length(centres[best]):
            best = order
    return best


def path_length(points):
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def write_gpx(path, coordinates, name):
    ET.register_namespace("", GPX_NAMESPACE)
    root = ET.Element(f"{{{GPX_NAMESPACE}}}gpx", {"version": "1.1", "creator": "colmap_pseudo_gpx"})
    track = ET.SubElement(root, f"{{{GPX_NAMESPACE}}}trk")
    ET.SubElement(track, f"{{{GPX_NAMESPACE}}}name").text = name
    segment = ET.SubElement(track, f"{{{GPX_NAMESPACE}}}trkseg")
    for lon, lat, height in coordinates:
        point = ET.SubElement(segment, f"{{{GPX_NAMESPACE}}}trkpt", {"lat": f"{lat:.10f}", "lon": f"{lon:.10f}"})
        ET.SubElement(point, f"{{{GPX_NAMESPACE}}}ele").text = f"{height:.4f}"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_track(model, gauge, method):
    origin = read_gauge_origin(gauge)
    names, centres = camera_centres(model)
    if len(centres) < 2:
        raise ValueError("Pas assez de centres caméra pour une trajectoire.")
    order = order_centres(names, centres, method)
    track = centres[order]
    return enu_to_wgs84(track, origin), path_length(track), [names[i] for i in order]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path, help="Dossier COLMAP (images.bin ou images.txt).")
    parser.add_argument("--gauge", type=Path, help="gauge.txt (défaut : MODEL/gauge.txt, sinon dossier parent).")
    parser.add_argument("--output", type=Path, help="GPX de sortie (défaut : MODEL/pseudo_trajectory.gpx).")
    parser.add_argument("--order", choices=("path", "frame"), default="path",
                        help="path : plus court chemin géométrique (défaut) ; frame : indice _fXXXXX du nom.")
    args = parser.parse_args()

    gauge = args.gauge or (args.model / "gauge.txt" if (args.model / "gauge.txt").exists() else args.model.parent / "gauge.txt")
    output = args.output or args.model / "pseudo_trajectory.gpx"
    coordinates, length, _ = build_track(args.model, gauge, args.order)
    write_gpx(output, coordinates, "Pseudo-trajectoire COLMAP")
    print(f"[gpx] {len(coordinates)} sommets, longueur {length:.1f} m -> {output}")


if __name__ == "__main__":
    main()
