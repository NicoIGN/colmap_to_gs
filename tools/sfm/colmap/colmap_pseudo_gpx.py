#!/usr/bin/env python3
"""Générer une pseudo-trajectoire GPX à partir des centres caméra d'un modèle COLMAP et de son gauge.txt.

Le repère du modèle est ENU, centré sur origin_lat/origin_lon/origin_alt du gauge.txt.
Les sommets sont ordonnés géométriquement (plus proche voisin puis 2-opt, chemin ouvert),
ou par indice de frame `_fXXXXX` du nom d'image avec --order frame.
--tolerance X régularise la trajectoire : Douglas-Peucker garde les sommets nécessaires pour
que la courbe reste à moins de X mètres de la polyligne reliant tous les sommets.
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


def camera_poses(model):
    """Noms, centres et directions de visée (monde) des images, triés par nom."""
    reconstruction = pycolmap.Reconstruction(str(model))
    images = sorted(reconstruction.images.values(), key=lambda image: image.name)
    names = [image.name for image in images]
    centres = np.array([np.asarray(image.projection_center(), dtype=float) for image in images])
    headings = np.array([image.cam_from_world().rotation.matrix().T @ [0.0, 0.0, 1.0] for image in images])
    return names, centres, headings


def directed_costs(points, headings, weight):
    """Coût orienté i -> j : distance, plus pénalité si le déplacement ne suit pas la visée en i et en j."""
    delta = points[None, :, :] - points[:, None, :]
    distance = np.linalg.norm(delta, axis=2)
    unit = delta / np.maximum(distance, 1e-12)[:, :, None]
    alignment = (np.einsum("ijk,ik->ij", unit, headings) + np.einsum("ijk,jk->ij", unit, headings)) / 2
    cost = distance + weight * (1.0 - alignment)
    np.fill_diagonal(cost, np.inf)
    return cost


def path_cost(cost, order):
    return float(cost[order[:-1], order[1:]].sum())


def nearest_neighbour_directed(cost, start):
    n = len(cost)
    visited = np.zeros(n, dtype=bool)
    order = [start]
    visited[start] = True
    for _ in range(n - 1):
        candidates = np.where(visited, np.inf, cost[order[-1]])
        nxt = int(np.argmin(candidates))
        order.append(nxt)
        visited[nxt] = True
    return np.array(order)


def directed_two_opt(cost, order, max_passes=100):
    """2-opt sur coût orienté : inverser un segment change le sens de ses arêtes, évalué via sommes préfixées."""
    order = np.array(order)
    n = len(order)
    for _ in range(max_passes):
        improved = False
        forward = cost[order[:-1], order[1:]]
        backward = cost[order[1:], order[:-1]]
        prefix_f = np.concatenate([[0.0], np.cumsum(forward)])
        prefix_b = np.concatenate([[0.0], np.cumsum(backward)])
        for i in range(-1, n - 1):
            js = np.arange(i + 1, n)
            inner_old = prefix_f[js] - prefix_f[i + 1]
            inner_new = prefix_b[js] - prefix_b[i + 1]
            delta = inner_new - inner_old
            if i >= 0:
                delta = delta + cost[order[i], order[js]] - cost[order[i], order[i + 1]]
            has_next = js + 1 < n
            tail = np.zeros(len(js))
            nxt = order[js[has_next] + 1]
            tail[has_next] = (cost[order[i + 1], nxt] - cost[order[js[has_next]], nxt])
            delta = delta + tail
            k = int(np.argmin(delta))
            if delta[k] < -1e-9:
                j = js[k]
                order[i + 1:j + 1] = order[i + 1:j + 1][::-1]
                improved = True
                break
        if not improved:
            break
    return order


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


def median_nearest_distance(points):
    distance = np.linalg.norm(points[None, :, :] - points[:, None, :], axis=2)
    np.fill_diagonal(distance, np.inf)
    return float(np.median(distance.min(axis=1)))


def candidate_starts(points):
    """Extrémités probables d'une trajectoire : points les plus éloignés du centroïde puis l'un de l'autre."""
    first = int(np.argmax(np.linalg.norm(points - points.mean(axis=0), axis=1)))
    second = int(np.argmax(np.linalg.norm(points - points[first], axis=1)))
    return dict.fromkeys([first, second, 0])


def order_centres(names, centres, headings, method, weight):
    if method == "frame":
        return frame_order(names)
    cost = directed_costs(centres, headings, weight)
    best = None
    for start in candidate_starts(centres):
        order = directed_two_opt(cost, nearest_neighbour_directed(cost, start))
        if best is None or path_cost(cost, order) < path_cost(cost, best):
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


def simplify_polyline(points, tolerance):
    """Douglas-Peucker : garde les sommets nécessaires pour que la courbe reste à moins de `tolerance` de la polyligne d'origine."""
    points = np.asarray(points, dtype=float)
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        segment = points[last] - points[first]
        inner = points[first + 1:last] - points[first]
        length = np.linalg.norm(segment)
        if length > 0:
            along = np.clip(inner @ segment / length ** 2, 0.0, 1.0)
            distances = np.linalg.norm(inner - np.outer(along, segment), axis=1)
        else:
            distances = np.linalg.norm(inner, axis=1)
        k = int(np.argmax(distances))
        if distances[k] > tolerance:
            index = first + 1 + k
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return keep


def build_track(model, method, tolerance=0.0, heading_weight=None):
    origin = read_gauge_origin(model / "gauge.txt")
    names, centres, headings = camera_poses(model)
    if len(centres) < 2:
        raise ValueError("Pas assez de centres caméra pour une trajectoire.")
    if heading_weight is None:
        heading_weight = median_nearest_distance(centres)
    order = order_centres(names, centres, headings, method, heading_weight)
    track = centres[order]
    if tolerance > 0:
        track = track[simplify_polyline(track, tolerance)]
    return enu_to_wgs84(track, origin), path_length(track), len(track)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path,
                        help="Dossier COLMAP (images.bin ou images.txt) contenant gauge.txt.")
    parser.add_argument("--output", type=Path, help="GPX de sortie (défaut : MODEL/pseudo_trajectory.gpx).")
    parser.add_argument("--order", choices=("path", "frame"), default="path",
                        help="path : plus court chemin géométrique (défaut) ; frame : indice _fXXXXX du nom.")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="Régularisation en mètres : la trajectoire simplifiée reste à moins de cette distance "
                             "de la polyligne reliant tous les sommets (0 = pas de régularisation).")
    parser.add_argument("--heading-weight", type=float, default=None,
                        help="Poids (m) de la pénalité quand le déplacement ne suit pas la direction de visée "
                             "(défaut : distance médiane au plus proche voisin).")
    args = parser.parse_args()

    output = args.output or args.model / "pseudo_trajectory.gpx"
    coordinates, length, count = build_track(args.model, args.order, args.tolerance, args.heading_weight)
    write_gpx(output, coordinates, "Pseudo-trajectoire COLMAP")
    print(f"[gpx] {count} sommets, longueur {length:.1f} m -> {output}")


if __name__ == "__main__":
    main()
