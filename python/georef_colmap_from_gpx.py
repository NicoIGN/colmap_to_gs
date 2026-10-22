#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Recalage géométrique de modèles COLMAP sparse sur un sous-parcours GPX.

Aucun FPS, timestamp ou offset temporel.

Algorithme :
1. Construire une trajectoire caméra ordonnée par _fXXXXX.
2. Rééchantillonner par distance parcourue.
3. Rechercher des fenêtres GPX à plusieurs positions et échelles.
4. Classer les fenêtres avec un descripteur invariant par similitude.
5. Raffiner les meilleurs candidats avec une DTW contrainte et une
   estimation robuste de Sim(3).

Sorties :
- Modèles COLMAP transformés, même format et arborescence.
- georef_stats.json par modèle.
- georef_summary.json à la racine.
- georef_trajectories.gpkg à la racine :
    gpx
    prises_de_vue
    gpx_apparie
    correspondances
    sommets

Limitations :
- Recherche multi-départs approximative, sans garantie d'optimum global.
- Le parcours caméra doit correspondre à un sous-parcours GPS continu.
- Les grands détours manquants et les importantes déformations COLMAP
  ne sont pas corrigés par une simple similitude.
- Les fichiers rigs.* et frames.* ne sont pas pris en charge.
- Altitudes GPX utilisées telles quelles, sans correction géoïde.
"""

import argparse
import json
import math
import re
import shutil
import struct
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.ndimage import median_filter
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from tqdm import tqdm


IMAGE_HEADER = struct.Struct("<I7dI")
POINT_HEADER = struct.Struct("<QdddBBBdQ")
FRAME_PATTERN = re.compile(r"_f([0-9]+)(?=[^0-9]|$)")


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def info(message):
    tqdm.write(f"[INFO] {message}")


def warn(message):
    tqdm.write(f"[WARN] {message}")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def read_exact(f, size):
    data = f.read(size)
    if len(data) != size:
        raise EOFError(f"Fichier tronqué : {f.name}")
    return data


def read_u64(f):
    return struct.unpack("<Q", read_exact(f, 8))[0]


def read_name(f):
    result = bytearray()
    while True:
        char = read_exact(f, 1)
        if char == b"\x00":
            return result.decode("utf-8")
        result.extend(char)


def copy_bytes(src, dst, size):
    while size:
        chunk = read_exact(src, min(size, 1024 * 1024))
        dst.write(chunk)
        size -= len(chunk)


def error_statistics(errors):
    errors = np.asarray(errors, dtype=float)
    if not len(errors):
        return None
    return {
        "count": int(len(errors)),
        "mean_m": float(errors.mean()),
        "median_m": float(np.median(errors)),
        "rmse_m": float(np.sqrt(np.mean(errors ** 2))),
        "p90_m": float(np.percentile(errors, 90)),
        "max_m": float(errors.max()),
    }


# ---------------------------------------------------------------------------
# Géométrie
# ---------------------------------------------------------------------------

def quaternion_to_matrix(qvec):
    q = np.array(qvec, dtype=float, copy=True)
    norm = np.linalg.norm(q)
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("Quaternion invalide.")
    q /= norm
    return Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()


def matrix_to_quaternion(matrix):
    q = Rotation.from_matrix(matrix).as_quat()[[3, 0, 1, 2]]
    return -q if q[0] < 0 else q


def clean_polyline(points):
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    if not np.isfinite(points).all():
        raise ValueError("Polyligne non finie.")
    if len(points) < 2:
        raise ValueError("Polyligne trop courte.")
    keep = np.r_[
        True,
        np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-10,
    ]
    points = points[keep]
    if len(points) < 2:
        raise ValueError("Polyligne stationnaire.")
    return points


def polyline_length(points):
    return float(np.linalg.norm(
        np.diff(points, axis=0), axis=1
    ).sum())


def sample_polyline(points, fractions):
    points = clean_polyline(points)
    lengths = np.r_[
        0.0,
        np.cumsum(np.linalg.norm(
            np.diff(points, axis=0), axis=1
        )),
    ]
    positions = np.clip(fractions, 0.0, 1.0) * lengths[-1]
    return np.column_stack([
        np.interp(positions, lengths, points[:, axis])
        for axis in range(3)
    ])


def smooth_polyline(points, window):
    points = clean_polyline(points)
    if window > 1 and len(points) >= window:
        filtered = median_filter(
            points, size=(window, 1), mode="nearest"
        )
        # Conserver les extrémités originales.
        filtered[0] = points[0]
        filtered[-1] = points[-1]
        points = clean_polyline(filtered)
    return points


def apply_sim(points, sim):
    scale, rotation, translation = sim
    return scale * (np.asarray(points) @ rotation.T) + translation


def fit_sim3(X, Y, weights=None):
    X = np.asarray(X, dtype=float)
    Y = np.asarray(Y, dtype=float)

    if X.shape != Y.shape or len(X) < 3:
        raise ValueError("Correspondances Sim(3) invalides.")

    if weights is None:
        weights = np.ones(len(X), dtype=float)
    weights = np.asarray(weights, dtype=float)
    weights = weights / weights.sum()

    mx = np.sum(X * weights[:, None], axis=0)
    my = np.sum(Y * weights[:, None], axis=0)
    Xc, Yc = X - mx, Y - my

    for centered in (Xc, Yc):
        singular = np.linalg.svd(
            centered * np.sqrt(weights[:, None]),
            compute_uv=False,
        )
        if singular[0] <= 0 or singular[1] < singular[0] * 1e-6:
            raise ValueError("Trajectoire quasi rectiligne ou dégénérée.")

    covariance = (Yc * weights[:, None]).T @ Xc
    U, singular, Vt = np.linalg.svd(covariance)
    signs = np.ones(3)
    if np.linalg.det(U @ Vt) < 0:
        signs[-1] = -1

    rotation = U @ np.diag(signs) @ Vt
    variance = np.sum(weights[:, None] * Xc ** 2)
    scale = float(np.dot(singular, signs) / variance)

    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Échelle invalide.")

    translation = my - scale * rotation @ mx
    return scale, rotation, translation


def robust_sim3(X, Y):
    weights = np.ones(len(X))
    for _ in range(8):
        sim = fit_sim3(X, Y, weights)
        errors = np.linalg.norm(apply_sim(X, sim) - Y, axis=1)
        cutoff = max(1e-8, 1.5 * float(np.median(errors)))
        weights = np.minimum(1.0, cutoff / np.maximum(errors, 1e-12))
    return fit_sim3(X, Y, weights)


# ---------------------------------------------------------------------------
# GPX et ENU
# ---------------------------------------------------------------------------

def geodetic_to_ecef(lat, lon, height):
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    height = np.asarray(height, dtype=float)

    a = 6378137.0
    e2 = 6.6943799901413165e-3
    normal = a / np.sqrt(1.0 - e2 * np.sin(lat) ** 2)

    return np.stack([
        (normal + height) * np.cos(lat) * np.cos(lon),
        (normal + height) * np.cos(lat) * np.sin(lon),
        (normal * (1.0 - e2) + height) * np.sin(lat),
    ], axis=-1)


def enu_rotation(origin):
    lat, lon = np.radians(origin[:2])
    return np.array([
        [-np.sin(lon), np.cos(lon), 0.0],
        [
            -np.sin(lat) * np.cos(lon),
            -np.sin(lat) * np.sin(lon),
            np.cos(lat),
        ],
        [
            np.cos(lat) * np.cos(lon),
            np.cos(lat) * np.sin(lon),
            np.sin(lat),
        ],
    ])


def read_gpx(path):
    root = ET.parse(path).getroot()
    raw = []
    missing = 0

    for segment in root.iter():
        if segment.tag.rsplit("}", 1)[-1] != "trkseg":
            continue

        records = []
        for element in segment:
            if element.tag.rsplit("}", 1)[-1] != "trkpt":
                continue

            fields = {
                child.tag.rsplit("}", 1)[-1]:
                    (child.text or "").strip()
                for child in element
            }
            lat = float(element.attrib["lat"])
            lon = float(element.attrib["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Coordonnées GPX invalides.")

            if fields.get("ele"):
                height = float(fields["ele"])
            else:
                height = 0.0
                missing += 1

            if not np.isfinite([lat, lon, height]).all():
                raise ValueError("Coordonnées GPX non finies.")

            records.append((lon, lat, height))

        if len(records) >= 2:
            raw.append(np.asarray(records, dtype=float))

    if not raw:
        raise ValueError("Aucun segment GPX exploitable.")

    lon0, lat0, h0 = raw[0][0]
    origin = [float(lat0), float(lon0), float(h0)]
    origin_ecef = geodetic_to_ecef(lat0, lon0, h0)
    rotation = enu_rotation(origin)

    segments = []
    for coordinates in raw:
        ecef = geodetic_to_ecef(
            coordinates[:, 1],
            coordinates[:, 0],
            coordinates[:, 2],
        )
        segments.append((ecef - origin_ecef) @ rotation.T)

    if missing:
        warn(
            f"{missing} sommets GPS sans altitude : valeur 0 utilisée. "
            "Vérifier la cohérence verticale."
        )

    return {
        "origin": origin,
        "segments": segments,
        "geographic_segments": raw,
        "missing_elevation": missing,
    }


def enu_to_wgs84(points, origin):
    from pyproj import Transformer

    points = np.asarray(points, dtype=float).reshape(-1, 3)
    ecef = (
        points @ enu_rotation(origin)
        + geodetic_to_ecef(*origin)
    )
    transformer = Transformer.from_crs(
        4978, 4979, always_xy=True
    )
    lon, lat, height = transformer.transform(
        ecef[:, 0], ecef[:, 1], ecef[:, 2], errcheck=True
    )
    return np.column_stack((lon, lat, height))


def projection_reference(origin, epsg):
    """
    Décrit le repère ENU local dans un SCR projeté.
    - origin: [lat, lon, h] WGS84 (issu du GPX)
    - epsg: code EPSG projeté (ex: 2154)
    """
    from pyproj import CRS, Transformer

    crs = CRS.from_epsg(epsg)
    if not crs.is_projected:
        raise ValueError(
            f"EPSG:{epsg} n'est pas projeté (il faut des unités en mètres)."
        )

    to_target = Transformer.from_crs(4979, crs, always_xy=True)

    lat, lon, height = origin
    x0, y0, z0 = to_target.transform(lon, lat, height, errcheck=True)

    # Linéarisation locale ENU -> XY projeté (pas de 100 m)
    step = 100.0
    probes = enu_to_wgs84(
        np.array([[step, 0.0, 0.0], [0.0, step, 0.0]]),
        origin
    )
    xs, ys, _ = to_target.transform(
        probes[:, 0], probes[:, 1], probes[:, 2], errcheck=True
    )

    east_axis = np.array([xs[0] - x0, ys[0] - y0]) / step
    north_axis = np.array([xs[1] - x0, ys[1] - y0]) / step
    M = np.column_stack((east_axis, north_axis))

    return {
        "epsg": int(epsg),
        "name": crs.name,
        "origin_x": float(x0),
        "origin_y": float(y0),
        "origin_height": float(z0),
        "local_en_to_xy": {
            "equation": "[X, Y] = [origin_x, origin_y] + M @ [E, N]",
            "M": M.tolist(),
            "note": "Linéarisation locale autour de l'origine.",
        },
        "height_note": (
            "Hauteur GPX brute (pas de correction géoïde)."
        ),
    }
# ---------------------------------------------------------------------------
# Lecture COLMAP
# ---------------------------------------------------------------------------

def model_format(folder):
    for ext in ("bin", "txt"):
        if all(
            (folder / f"{name}.{ext}").is_file()
            for name in ("cameras", "images", "points3D")
        ):
            return ext
    return None


def discover_models(root):
    if model_format(root):
        return [root]
    result = sorted({
        path.parent
        for filename in ("images.bin", "images.txt")
        for path in root.rglob(filename)
        if model_format(path.parent)
    })
    if not result:
        raise ValueError("Aucun modèle COLMAP complet.")
    return result


def iter_text_images(f):
    while True:
        header = f.readline()
        if header == "":
            return
        if not header.strip() or header.lstrip().startswith("#"):
            continue
        tokens = header.strip().split(maxsplit=9)
        if len(tokens) != 10:
            raise ValueError("En-tête images.txt invalide.")
        observations = f.readline()
        if observations == "":
            raise EOFError("Ligne POINTS2D manquante.")
        yield tokens, observations


def load_image_metadata(folder, ext):
    result = []
    path = folder / f"images.{ext}"

    if ext == "bin":
        with path.open("rb") as f:
            count = read_u64(f)
            file_size = path.stat().st_size
            for _ in tqdm(range(count), desc="Lecture poses", unit="image"):
                record = IMAGE_HEADER.unpack(
                    read_exact(f, IMAGE_HEADER.size)
                )
                name = read_name(f)
                observations = read_u64(f)
                skip = observations * 24
                if skip > file_size - f.tell():
                    raise EOFError("Observations binaires tronquées.")
                f.seek(skip, 1)
                result.append({
                    "id": int(record[0]),
                    "qvec": np.array(record[1:5]),
                    "tvec": np.array(record[5:8]),
                    "name": name,
                })
    else:
        with path.open(encoding="utf-8") as f:
            for tokens, _ in tqdm(
                iter_text_images(f), desc="Lecture poses", unit="image"
            ):
                result.append({
                    "id": int(tokens[0]),
                    "qvec": np.array(tokens[1:5], dtype=float),
                    "tvec": np.array(tokens[5:8], dtype=float),
                    "name": tokens[9],
                })

    for image in result:
        image["center"] = (
            -quaternion_to_matrix(image["qvec"]).T @ image["tvec"]
        )
        match = FRAME_PATTERN.search(Path(image["name"]).name)
        image["frame"] = int(match.group(1)) if match else None
        image["folder"] = Path(image["name"]).parent.as_posix()
        if not np.isfinite(image["center"]).all():
            raise ValueError(f"Centre caméra invalide : {image['name']}")

    return result


def select_camera_path(images, prefix, smooth_window):
    groups = {}
    for image in images:
        if prefix is not None and not image["name"].startswith(prefix):
            continue
        if image["frame"] is not None:
            groups.setdefault(image["folder"], []).append(image)

    choices = []
    for folder, records in groups.items():
        frames = {}
        for image in records:
            frames.setdefault(image["frame"], []).append(image["center"])
        if len(frames) >= 6:
            ordered = sorted(frames)
            points = np.array([
                np.median(frames[index], axis=0)
                for index in ordered
            ])
            choices.append((len(ordered), folder, points))

    if not choices:
        raise ValueError(
            "Aucun dossier avec au moins six indices _fXXXXX distincts."
        )

    choices.sort(key=lambda item: (-item[0], item[1]))
    _, folder, points = choices[0]
    info(f"Trajectoire utilisée pour le recalage : {folder}")

    return smooth_polyline(points, smooth_window), {
        "selected_folder": folder,
        "selection": "largest number of distinct frame indices",
        "available_folders": [
            {"folder": item[1], "distinct_frames": item[0]}
            for item in choices
        ],
        "smooth_window": smooth_window,
    }


# ---------------------------------------------------------------------------
# Recherche de sous-courbes
# ---------------------------------------------------------------------------

def shape_descriptor(points):
    """
    Distances inter-points normalisées :
    invariantes par translation, rotation et échelle.
    Une réflexion sera rejetée/évaluée ensuite par Sim(3).
    """
    distances = np.linalg.norm(
        points[:, None, :] - points[None, :, :], axis=2
    )
    norm = float(np.sqrt(np.mean(distances ** 2)))
    if norm <= 1e-12:
        raise ValueError("Descripteur de forme dégénéré.")
    return distances / norm


def window_points(path, start, span, count):
    return sample_polyline(
        path, start + span * np.linspace(0.0, 1.0, count)
    )


def diverse_candidates(candidates, count):
    selected = []
    for candidate in sorted(candidates, key=lambda item: item["score"]):
        duplicate = any(
            candidate["segment"] == other["segment"]
            and candidate["reverse"] == other["reverse"]
            and abs(candidate["start"] - other["start"]) < 0.025
            and abs(math.log(candidate["span"] / other["span"])) < 0.12
            for other in selected
        )
        if not duplicate:
            selected.append(candidate)
        if len(selected) >= count:
            break
    return selected


def search_windows(source, paths, args):
    descriptor_source = sample_polyline(
        source, np.linspace(0.0, 1.0, 24)
    )
    source_descriptor = shape_descriptor(descriptor_source)
    candidates = []

    spans = np.geomspace(
        args.min_gpx_fraction, 1.0, args.scale_steps
    )

    for (segment, reverse), path in tqdm(
        list(paths.items()), desc="Recherche de sous-courbes", unit="sens"
    ):
        for span in spans:
            starts = np.linspace(
                0.0, max(0.0, 1.0 - span),
                1 if span >= 1.0 else args.position_steps,
            )
            for start in starts:
                target = window_points(path, start, span, 24)
                try:
                    descriptor_error = float(np.sqrt(np.mean(
                        (shape_descriptor(target) - source_descriptor) ** 2
                    )))
                    sim = fit_sim3(descriptor_source, target)
                    extent = max(
                        np.linalg.norm(np.ptp(target, axis=0)), 1e-8
                    )
                    fit_error = float(np.sqrt(np.mean(np.sum(
                        (apply_sim(descriptor_source, sim) - target) ** 2,
                        axis=1,
                    )))) / extent
                except (ValueError, np.linalg.LinAlgError):
                    continue

                candidates.append({
                    "segment": segment,
                    "reverse": reverse,
                    "start": float(start),
                    "span": float(span),
                    "score": descriptor_error + fit_error,
                })

    if not candidates:
        raise RuntimeError("Aucune fenêtre GPS non dégénérée.")

    # Raffiner localement position et longueur autour des meilleurs.
    best = diverse_candidates(candidates, args.candidates * 2)

    for level in range(2):
        local = list(best)
        position_step = 1.0 / max(args.position_steps - 1, 1)
        position_step /= 3 ** (level + 1)
        log_step = (
            math.log(1.0 / args.min_gpx_fraction)
            / max(args.scale_steps - 1, 1)
            / (3 ** (level + 1))
        )

        for candidate in best:
            path = paths[(candidate["segment"], candidate["reverse"])]
            for ds in (-1, 0, 1):
                span = float(np.clip(
                    candidate["span"] * math.exp(ds * log_step),
                    args.min_gpx_fraction, 1.0,
                ))
                for dp in (-1, 0, 1):
                    start = float(np.clip(
                        candidate["start"] + dp * position_step,
                        0.0, 1.0 - span,
                    ))
                    target = window_points(path, start, span, 24)
                    try:
                        descriptor_error = float(np.sqrt(np.mean(
                            (
                                shape_descriptor(target)
                                - source_descriptor
                            ) ** 2
                        )))
                        sim = fit_sim3(descriptor_source, target)
                        extent = max(
                            np.linalg.norm(np.ptp(target, axis=0)), 1e-8
                        )
                        fit_error = float(np.sqrt(np.mean(np.sum(
                            (
                                apply_sim(descriptor_source, sim)
                                - target
                            ) ** 2,
                            axis=1,
                        )))) / extent
                    except (ValueError, np.linalg.LinAlgError):
                        continue

                    local.append({
                        **candidate,
                        "start": start,
                        "span": span,
                        "score": descriptor_error + fit_error,
                    })

        best = diverse_candidates(local, args.candidates * 2)

    return diverse_candidates(best, args.candidates)


# ---------------------------------------------------------------------------
# DTW contrainte et raffinement Sim(3)
# ---------------------------------------------------------------------------

def constrained_dtw(X, Y, band_fraction):
    """
    Appariement ordonné à extrémités fixées pour une fenêtre GPS candidate.

    Pas autorisés :
        (1,1), (1,2), (2,1)

    Aucun déplacement purement horizontal/vertical :
    évite les longues séquences appariées à un seul sommet.

    Une bande limite la déformation de progression.
    """
    n, m = len(X), len(Y)
    distances = np.sum(
        (X[:, None, :] - Y[None, :, :]) ** 2,
        axis=2,
    )
    cost = np.full((n, m), np.inf)
    previous = np.zeros((n, m), dtype=np.uint8)
    cost[0, 0] = distances[0, 0]
    band = max(3, int(max(n, m) * band_fraction))

    for i in range(1, n):
        expected = i * (m - 1) / (n - 1)
        lower = max(1, int(expected - band))
        upper = min(m, int(expected + band) + 1)

        for j in range(lower, upper):
            options = [
                cost[i - 1, j - 1] + distances[i, j],
                (
                    cost[i - 1, j - 2]
                    + distances[i, j - 1] + distances[i, j]
                    if j >= 2 else np.inf
                ),
                (
                    cost[i - 2, j - 1]
                    + distances[i - 1, j] + distances[i, j]
                    if i >= 2 else np.inf
                ),
            ]
            step = int(np.argmin(options))
            cost[i, j] = options[step]
            previous[i, j] = step + 1

    if not np.isfinite(cost[-1, -1]):
        raise RuntimeError("Pas d'appariement DTW valide.")

    i, j = n - 1, m - 1
    pairs = [(i, j)]

    while i or j:
        step = previous[i, j]
        if step == 1:
            i, j = i - 1, j - 1
        elif step == 2:
            pairs.append((i, j - 1))
            i, j = i - 1, j - 2
        elif step == 3:
            pairs.append((i - 1, j))
            i, j = i - 2, j - 1
        else:
            raise RuntimeError("Chemin DTW incomplet.")
        pairs.append((i, j))

    pairs.reverse()

    # Une cible moyenne par sommet source : poids source uniforme.
    target_indices = [[] for _ in range(n)]
    for source_index, target_index in pairs:
        target_indices[source_index].append(target_index)

    mapping = np.array([
        np.mean(indices) for indices in target_indices
    ])
    matched = np.column_stack([
        np.interp(mapping, np.arange(m), Y[:, axis])
        for axis in range(3)
    ])
    return matched, mapping


def refine_candidate(source, target, candidate, args):
    sim = fit_sim3(source, target)

    for _ in range(args.refine_iters):
        transformed = apply_sim(source, sim)
        matched, _ = constrained_dtw(
            transformed, target, args.dtw_band
        )
        updated = robust_sim3(source, matched)

        difference = np.sqrt(np.mean(np.sum(
            (apply_sim(source, updated) - transformed) ** 2,
            axis=1,
        )))
        sim = updated
        if difference < 1e-5:
            break

    transformed = apply_sim(source, sim)
    matched, mapping = constrained_dtw(
        transformed, target, args.dtw_band
    )
    errors = np.linalg.norm(transformed - matched, axis=1)

    extent = max(np.linalg.norm(np.ptp(target, axis=0)), 1e-8)

    # Distance symétrique uniquement dans la fenêtre sélectionnée.
    forward = cKDTree(target).query(transformed)[0]
    backward = cKDTree(transformed).query(target)[0]
    symmetric = 0.5 * (
        np.sqrt(np.mean(forward ** 2))
        + np.sqrt(np.mean(backward ** 2))
    ) / extent

    normalized_ordered = (
        np.sqrt(np.mean(errors ** 2)) / extent
    )
    progression = float(np.sqrt(np.mean(
        (
            mapping / (len(target) - 1)
            - np.linspace(0.0, 1.0, len(source))
        ) ** 2
    )))

    length_ratio = (
        sim[0] * polyline_length(source)
        / max(polyline_length(target), 1e-10)
    )

    # Tous les critères de sélection sont sans dimension.
    score = (
        normalized_ordered
        + 0.5 * symmetric
        + 0.10 * progression
        + 0.10 * abs(math.log(max(length_ratio, 1e-12)))
    )

    return {
        **candidate,
        "score": float(score),
        "sim": sim,
        "errors": errors,
        "prediction": transformed,
        "matched": matched,
        "target": target,
        "normalized_ordered_rmse": float(normalized_ordered),
        "normalized_symmetric_distance": float(symmetric),
        "progression_penalty": progression,
        "length_ratio": float(length_ratio),
    }


def fit_model(images, gpx, args):
    camera_path, camera_meta = select_camera_path(
        images, args.view_prefix, args.smooth_window
    )
    source = sample_polyline(
        camera_path, np.linspace(0.0, 1.0, args.samples)
    )

    paths = {}
    for index, segment in enumerate(gpx["segments"]):
        try:
            path = smooth_polyline(segment, args.smooth_window)
        except ValueError:
            continue
        paths[(index, False)] = path
        paths[(index, True)] = path[::-1]

    if not paths:
        raise ValueError("Aucune trajectoire GPS non stationnaire.")

    candidates = search_windows(source, paths, args)
    solutions = []

    for candidate in tqdm(
        candidates, desc="Raffinement DTW / Sim(3)", unit="candidat"
    ):
        path = paths[(candidate["segment"], candidate["reverse"])]
        target = window_points(
            path, candidate["start"], candidate["span"], args.samples
        )
        try:
            solutions.append(
                refine_candidate(source, target, candidate, args)
            )
        except (ValueError, RuntimeError, np.linalg.LinAlgError):
            continue

    if not solutions:
        raise RuntimeError("Aucun raffinement DTW valide.")

    solutions.sort(key=lambda item: item["score"])
    best = solutions[0]
    warnings = []

    if best["span"] <= args.min_gpx_fraction * 1.02:
        warnings.append(
            "La portion retenue atteint la longueur minimale autorisée."
        )

    # Heuristique d'ambiguïté : candidat de score proche dont le placement
    # d'au moins une partie de la trajectoire est sensiblement différent.
    extent = max(
        np.linalg.norm(np.ptp(best["target"], axis=0)), 1e-8
    )
    ambiguous = False
    for alternative in solutions[1:]:
        placement_difference = float(np.sqrt(np.mean(np.sum(
            (
                apply_sim(source, alternative["sim"])
                - best["prediction"]
            ) ** 2,
            axis=1,
        ))))
        if (
            alternative["score"] <= best["score"] * 1.15 + 0.005
            and placement_difference > max(10.0, extent * 0.05)
        ):
            ambiguous = True
            break

    if ambiguous:
        warnings.append(
            "Plusieurs placements différents ont des scores proches : "
            "recalage ambigu."
        )

    if best["normalized_ordered_rmse"] > 0.08:
        warnings.append(
            "Erreur normalisée importante : contrôler le recalage."
        )

    scale, rotation, translation = best["sim"]

    def solution_summary(solution):
        s, r, t = solution["sim"]
        return {
            "score": solution["score"],
            "segment_index": solution["segment"],
            "reversed": solution["reverse"],
            "start_fraction_oriented": solution["start"],
            "fraction_used": solution["span"],
            "errors_m": error_statistics(solution["errors"]),
            "scale": float(s),
            "R": r.tolist(),
            "t": t.tolist(),
        }

    stats = {
        "method": "partial_curve_multiscale_constrained_dtw_sim3",
        "uses_timestamps": False,
        "uses_fps": False,
        "requires_visual_validation": True,
        "ambiguous": ambiguous,
        "camera_polyline": camera_meta,
        "score": best["score"],
        "normalized_ordered_rmse": best["normalized_ordered_rmse"],
        "normalized_symmetric_distance": (
            best["normalized_symmetric_distance"]
        ),
        "progression_penalty": best["progression_penalty"],
        "length_ratio": best["length_ratio"],
        "errors_resampled_polyline": error_statistics(best["errors"]),
        "matched_gpx": {
            "segment_index": best["segment"],
            "reversed": best["reverse"],
            "start_fraction_oriented": best["start"],
            "fraction_used": best["span"],
            "note": "Fractions mesurées sur la polyligne GPS lissée.",
        },
        "transform": {
            "equation": "X_enu = scale * R @ X_colmap + t",
            "scale": float(scale),
            "R": rotation.tolist(),
            "t": translation.tolist(),
        },
        "warnings": warnings,
        "best_solutions": [
            solution_summary(solution) for solution in solutions[:5]
        ],
    }

    for message in warnings:
        warn(message)

    return best["sim"], stats, best


# ---------------------------------------------------------------------------
# Transformation du modèle complet
# ---------------------------------------------------------------------------

def transform_pose(qvec, tvec, sim):
    scale, rotation, translation = sim
    new_rotation = quaternion_to_matrix(qvec) @ rotation.T
    new_translation = (
        scale * np.asarray(tvec) - new_rotation @ translation
    )
    return matrix_to_quaternion(new_rotation), new_translation


def transform_images(source, target, ext, sim):
    if ext == "bin":
        with source.open("rb") as src, target.open("wb") as dst:
            total = read_u64(src)
            dst.write(struct.pack("<Q", total))
            for _ in tqdm(range(total), desc="Écriture poses", unit="image"):
                record = IMAGE_HEADER.unpack(
                    read_exact(src, IMAGE_HEADER.size)
                )
                name = read_name(src)
                qvec, tvec = transform_pose(
                    record[1:5], record[5:8], sim
                )
                dst.write(IMAGE_HEADER.pack(
                    record[0], *qvec, *tvec, record[8]
                ))
                dst.write(name.encode("utf-8") + b"\x00")
                observations = read_u64(src)
                dst.write(struct.pack("<Q", observations))
                copy_bytes(src, dst, observations * 24)
        return total

    count = 0
    with source.open(encoding="utf-8") as src, target.open(
        "w", encoding="utf-8"
    ) as dst:
        dst.write("# COLMAP georeferenced: local ENU, meters\n")
        for tokens, observations in tqdm(
            iter_text_images(src), desc="Écriture poses", unit="image"
        ):
            qvec, tvec = transform_pose(
                np.array(tokens[1:5], dtype=float),
                np.array(tokens[5:8], dtype=float),
                sim,
            )
            pose = " ".join(
                f"{float(value):.17g}" for value in (*qvec, *tvec)
            )
            dst.write(
                f"{tokens[0]} {pose} {tokens[8]} {tokens[9]}\n"
            )
            dst.write(
                observations if observations.endswith("\n")
                else observations + "\n"
            )
            count += 1
    return count


def transform_points(source, target, ext, sim):
    if ext == "bin":
        with source.open("rb") as src, target.open("wb") as dst:
            total = read_u64(src)
            dst.write(struct.pack("<Q", total))
            for _ in tqdm(range(total), desc="Écriture points 3D", unit="pt"):
                record = POINT_HEADER.unpack(
                    read_exact(src, POINT_HEADER.size)
                )
                xyz = apply_sim(np.array(record[1:4]), sim)
                dst.write(POINT_HEADER.pack(
                    record[0], *xyz, *record[4:]
                ))
                copy_bytes(src, dst, record[-1] * 8)
        return total

    count = 0
    with source.open(encoding="utf-8") as src, target.open(
        "w", encoding="utf-8"
    ) as dst:
        dst.write("# COLMAP points: local ENU, meters\n")
        for line in tqdm(src, desc="Écriture points 3D", unit="ligne"):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            tokens = line.split()
            if len(tokens) < 8 or (len(tokens) - 8) % 2:
                raise ValueError("Ligne points3D.txt invalide.")
            xyz = apply_sim(np.array(tokens[1:4], dtype=float), sim)
            dst.write(
                f"{tokens[0]} "
                + " ".join(f"{value:.17g}" for value in xyz)
                + " " + " ".join(tokens[4:]) + "\n"
            )
            count += 1
    return count


# ---------------------------------------------------------------------------
# GeoPackage unique à la racine
# ---------------------------------------------------------------------------

def export_geopackage(path, gpx, results):
    import fiona
    from fiona.crs import CRS

    if path.exists():
        raise FileExistsError(f"GeoPackage déjà présent : {path}")

    crs = CRS.from_epsg(4979)

    def write_layer(name, geometry, properties, features):
        schema = {
            "geometry": geometry,
            "properties": properties,
        }
        with fiona.open(
            str(path),
            mode="w",
            driver="GPKG",
            layer=name,
            schema=schema,
            crs=crs,
            encoding="UTF-8",
        ) as layer:
            for feature in features:
                layer.write(feature)

    def feature(geometry_type, coordinates, properties):
        return {
            "geometry": {
                "type": geometry_type,
                "coordinates": coordinates,
            },
            "properties": properties,
        }

    def gps_lines():
        for index, coordinates in enumerate(gpx["geographic_segments"]):
            yield feature("LineString", coordinates.tolist(), {
                "segment_id": index,
                "vertices": len(coordinates),
            })

    write_layer(
        "gpx",
        "3D LineString",
        {"segment_id": "int", "vertices": "int"},
        gps_lines(),
    )

    def pv_lines():
        for result in results:
            groups = {}
            for image in result["images"]:
                if image["frame"] is not None:
                    groups.setdefault(image["folder"], []).append(image)
            for folder, images in sorted(groups.items()):
                images.sort(key=lambda im: (im["frame"], im["id"]))
                if len(images) < 2:
                    continue
                centers = np.array([im["center"] for im in images])
                coordinates = enu_to_wgs84(
                    apply_sim(centers, result["sim"]), gpx["origin"]
                )
                yield feature("LineString", coordinates.tolist(), {
                    "model": result["model"],
                    "folder": folder,
                    "vertices": len(images),
                })

    write_layer(
        "prises_de_vue",
        "3D LineString",
        {"model": "str", "folder": "str", "vertices": "int"},
        pv_lines(),
    )

    def matched_lines():
        for result in results:
            coordinates = enu_to_wgs84(
                result["fit"]["target"], gpx["origin"]
            )
            yield feature("LineString", coordinates.tolist(), {
                "model": result["model"],
                "segment_id": result["fit"]["segment"],
                "reversed": int(result["fit"]["reverse"]),
            })

    write_layer(
        "gpx_apparie",
        "3D LineString",
        {"model": "str", "segment_id": "int", "reversed": "int"},
        matched_lines(),
    )

    def correspondence_lines():
        for result in results:
            fit = result["fit"]
            source = enu_to_wgs84(
                fit["prediction"], gpx["origin"]
            )
            target = enu_to_wgs84(
                fit["matched"], gpx["origin"]
            )
            for index, (a, b, error) in enumerate(zip(
                source, target, fit["errors"]
            )):
                yield feature("LineString", [a.tolist(), b.tolist()], {
                    "model": result["model"],
                    "sample_id": index,
                    "error_m": float(error),
                })

    write_layer(
        "correspondances",
        "3D LineString",
        {"model": "str", "sample_id": "int", "error_m": "float"},
        correspondence_lines(),
    )

    def vertices():
        # Sommets GPS originaux, y compris les doublons/stationnements.
        for segment, coordinates in enumerate(gpx["geographic_segments"]):
            for index, coordinate in enumerate(coordinates):
                yield feature("Point", coordinate.tolist(), {
                    "source": "GPS",
                    "model": "",
                    "folder": "",
                    "name": "",
                    "image_id": None,
                    "frame": None,
                    "segment_id": segment,
                    "vertex_id": index,
                })

        # Tous les centres PV, y compris ceux sans numéro de frame.
        for result in results:
            images = result["images"]
            if not images:
                continue
            coordinates = enu_to_wgs84(
                apply_sim(
                    np.array([im["center"] for im in images]),
                    result["sim"],
                ),
                gpx["origin"],
            )
            for index, (image, coordinate) in enumerate(zip(
                images, coordinates
            )):
                yield feature("Point", coordinate.tolist(), {
                    "source": "PV",
                    "model": result["model"],
                    "folder": image["folder"],
                    "name": image["name"],
                    "image_id": image["id"],
                    "frame": image["frame"],
                    "segment_id": None,
                    "vertex_id": index,
                })

    write_layer(
        "sommets",
        "3D Point",
        {
            "source": "str",
            "model": "str",
            "folder": "str",
            "name": "str",
            "image_id": "int64",
            "frame": "int64",
            "segment_id": "int",
            "vertex_id": "int",
        },
        vertices(),
    )

    expected = {
        "gpx", "prises_de_vue", "gpx_apparie",
        "correspondances", "sommets",
    }
    available = set(fiona.listlayers(str(path)))
    if not expected.issubset(available):
        raise RuntimeError(f"Couches GeoPackage manquantes : {expected - available}")

    counts = {}
    for name in sorted(expected):
        with fiona.open(str(path), layer=name) as layer:
            counts[name] = len(layer)

    info(f"GeoPackage : {path}")
    return counts


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_model(folder, destination, relative, gpx, args):
    started = time.perf_counter()
    ext = model_format(folder)
    images = load_image_metadata(folder, ext)
    sim, stats, fit = fit_model(images, gpx, args)

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        folder / f"cameras.{ext}",
        destination / f"cameras.{ext}",
    )
    image_count = transform_images(
        folder / f"images.{ext}",
        destination / f"images.{ext}",
        ext, sim,
    )
    point_count = transform_points(
        folder / f"points3D.{ext}",
        destination / f"points3D.{ext}",
        ext, sim,
    )

    stats.update({
        "input_model": str(folder.resolve()),
        "output_model": str(destination.resolve()),
        "images_written": image_count,
        "points3D_written": point_count,
        "output_reference": {
            "type": "local_ENU",
            "units": "meters",
            "axes": ["East", "North", "Up"],
            "origin_lat_lon_height": gpx["origin"],
            "origin_geographic_crs": "EPSG:4979 (WGS84 supposé pour le GPX)",
            "origin_projected": gpx["projected"],
            "height_note": "Altitudes GPX brutes, sans correction géoïde.",
        },
        "geopackage": str(
            (args.output_dir.resolve() / "georef_trajectories.gpkg")
        ),
        "elapsed_seconds": time.perf_counter() - started,
    })
    write_json(destination / "georef_stats.json", stats)

    return {
        "model": str(relative),
        "images": images,
        "sim": sim,
        "fit": fit,
        "stats": stats,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Recalage partiel de courbes COLMAP / GPX, sans temps."
    )
    parser.add_argument("--gpx", type=Path, required=True)
    parser.add_argument("--sparse-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--view-prefix", default=None)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--min-gpx-fraction", type=float, default=0.08)
    parser.add_argument("--scale-steps", type=int, default=24)
    parser.add_argument("--position-steps", type=int, default=50)
    parser.add_argument("--candidates", type=int, default=16)
    parser.add_argument("--refine-iters", type=int, default=8)
    parser.add_argument("--dtw-band", type=float, default=0.20)
    parser.add_argument(
        "--smooth-window", type=int, default=3,
        help="Filtre médian spatial : entier impair ; 1 = désactivé.",
    )
    parser.add_argument(
        "--epsg",
        type=int,
        default=2154,
        help=(
            "EPSG projeté utilisé pour exprimer l'origine du repère local "
            "(défaut: 2154, Lambert-93)."
        ),
    )

    args = parser.parse_args()

    if not args.gpx.is_file() or not args.sparse_dir.is_dir():
        parser.error("Vérifier --gpx et --sparse-dir.")
    if args.samples < 20:
        parser.error("--samples doit être >= 20.")
    if not 0 < args.min_gpx_fraction < 1:
        parser.error("--min-gpx-fraction doit être dans ]0,1[.")
    if not 0 < args.dtw_band <= 1:
        parser.error("--dtw-band doit être dans ]0,1].")
    if args.scale_steps < 2 or args.position_steps < 2:
        parser.error("--scale-steps et --position-steps doivent être >= 2.")
    if args.candidates < 1 or args.refine_iters < 1:
        parser.error("--candidates et --refine-iters doivent être >= 1.")
    if args.smooth_window < 1 or args.smooth_window % 2 == 0:
        parser.error("--smooth-window doit être impair et >= 1.")

    try:
        import fiona
        import pyproj
    except ImportError:
        parser.error("Installer les dépendances : pip install fiona pyproj")


    try:
        pyproj.CRS.from_epsg(args.epsg)
    except Exception as exc:
        parser.error(f"Code EPSG invalide ({args.epsg}) : {exc}")
        
    source = args.sparse_dir.resolve()
    output = args.output_dir.resolve()

    if source == output or source in output.parents or output in source.parents:
        parser.error("Entrée et sortie doivent être distinctes, non imbriquées.")
    if output.exists() and (
        not output.is_dir() or any(output.iterdir())
    ):
        parser.error("Le dossier de sortie doit être vide ou inexistant.")

    models = discover_models(source)
    for model in models:
        for name in ("rigs.bin", "rigs.txt", "frames.bin", "frames.txt"):
            if (model / name).exists():
                parser.error(f"Rigs/frames non pris en charge : {model / name}")

    gpx = read_gpx(args.gpx)
    
    try:
        gpx["projected"] = projection_reference(gpx["origin"], args.epsg)
    except Exception as exc:
        parser.error(f"Impossible de projeter l'origine en EPSG:{args.epsg} : {exc}")
        
    output.mkdir(parents=True, exist_ok=True)

    summary = {
        "input": str(source),
        "output": str(output),
        "method": "partial_curve_multiscale_constrained_dtw_sim3",
        "uses_fps": False,
        "uses_timestamps": False,
        "parameters": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "models": [],
    }
    results = []

    for model in tqdm(models, desc="Modèles", unit="modèle"):
        relative = model.relative_to(source)
        info(f"Modèle : {relative}")

        try:
            result = process_model(
                model, output / relative, relative, gpx, args
            )
            results.append(result)
            stats = result["stats"]
            summary["models"].append({
                "model": str(relative),
                "status": "exported_requires_validation",
                "stats_file": str(
                    (output / relative / "georef_stats.json").resolve()
                ),
                "scale": stats["transform"]["scale"],
                "score": stats["score"],
                "ambiguous": stats["ambiguous"],
                "errors_resampled_polyline": (
                    stats["errors_resampled_polyline"]
                ),
                "warnings": stats["warnings"],
            })
        except Exception as exc:
            warn(f"Échec modèle {relative} : {exc}")
            summary["models"].append({
                "model": str(relative),
                "status": "failed",
                "error": str(exc),
            })

        write_json(output / "georef_summary.json", summary)

    # Un seul GeoPackage, contenant les résultats de tous les modèles.
    gpkg_path = output / "georef_trajectories.gpkg"
    try:
        counts = export_geopackage(gpkg_path, gpx, results)
        summary["geopackage"] = {
            "path": str(gpkg_path),
            "status": "ok",
            "crs": "EPSG:4979",
            "layers": counts,
        }
    except Exception as exc:
        summary["geopackage"] = {
            "path": str(gpkg_path),
            "status": "failed",
            "error": str(exc),
        }
        write_json(output / "georef_summary.json", summary)
        raise

    write_json(output / "georef_summary.json", summary)

    failed = sum(
        model["status"] == "failed" for model in summary["models"]
    )
    info(f"Modèles exportés : {len(results)}/{len(models)}")
    info(f"Rapport : {output / 'georef_summary.json'}")
    info("Contrôler visuellement le résultat : exporté ne signifie pas validé.")

    if failed:
        warn("Les modèles en échec ne figurent pas dans les couches PV.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
