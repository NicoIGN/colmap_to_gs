#!/usr/bin/env python3
"""Filter isolated, distant COLMAP points using per-image angular density."""

import argparse
import json
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

import numpy as np
from scipy.spatial import cKDTree
from tqdm import tqdm


POINT_HEADER = struct.Struct("<QdddBBBdQ")
IMAGE_HEADER = struct.Struct("<I7dI")
OBSERVATION = struct.Struct("<ddq")
ALGORITHM_VERSION = 2


def read_exact(stream, size, context):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"Fichier COLMAP tronqué ({context}) : {stream.name}")
    return data


def read_points(path, *, progress=False):
    positions = []
    colors = []
    point_ids = []
    with path.open("rb") as stream:
        count = struct.unpack("<Q", read_exact(stream, 8, "points3D.bin header"))[0]
        for index in tqdm(range(count), desc="Lecture points 3D", unit="pt",
                          disable=not progress):
            raw = read_exact(stream, POINT_HEADER.size, f"point {index}")
            fields = POINT_HEADER.unpack(raw)
            point_ids.append(fields[0])
            positions.append(fields[1:4])
            colors.append(fields[4:7])
            track_size = fields[-1] * 8
            read_exact(stream, track_size, f"track du point {index}")
        if stream.read(1):
            raise ValueError(f"Données superflues après points3D.bin : {path}")
    positions = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    if len(set(point_ids)) != len(point_ids):
        raise ValueError(f"Identifiant de point COLMAP dupliqué : {path}")
    if any(point_id > np.iinfo(np.int64).max for point_id in point_ids):
        raise ValueError(f"Identifiant de point hors plage COLMAP : {path}")
    if not np.isfinite(positions).all():
        raise ValueError(f"Coordonnées non finies dans points3D.bin : {path}")
    return (
        np.asarray(point_ids, dtype=np.int64),
        positions,
        np.asarray(colors, dtype=np.uint8).reshape(-1, 3),
    )


def quaternion_to_rotation(qvec):
    qw, qx, qy, qz = qvec
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if norm == 0:
        raise ValueError("Quaternion COLMAP nul.")
    qw, qx, qy, qz = (value / norm for value in qvec)
    return np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ])


def read_images(path, point_index, *, progress=False):
    images = []
    with path.open("rb") as stream:
        count = struct.unpack("<Q", read_exact(stream, 8, "images.bin header"))[0]
        for image_number in tqdm(range(count), desc="Lecture images", unit="vue",
                                 disable=not progress):
            header = read_exact(stream, IMAGE_HEADER.size, f"image {image_number}")
            fields = IMAGE_HEADER.unpack(header)
            name = bytearray()
            while True:
                char = read_exact(stream, 1, f"image {image_number} name")
                if char == b"\0":
                    break
                name.extend(char)
            observation_count = struct.unpack(
                "<Q", read_exact(stream, 8, f"image {image_number} observations")
            )[0]
            raw_observations = read_exact(
                stream, observation_count * OBSERVATION.size,
                f"image {image_number} observation records",
            )
            point_ids = np.frombuffer(
                raw_observations, dtype=np.dtype([("xy", "V16"), ("id", "<i8")])
            )["id"].copy()
            indices = np.fromiter(
                (point_index.get(int(pid), -1) for pid in point_ids),
                dtype=np.int64,
                count=len(point_ids),
            )
            rotation = quaternion_to_rotation(fields[1:5])
            center = -rotation.T @ np.asarray(fields[5:8], dtype=np.float64)
            if not np.isfinite(center).all():
                raise ValueError(f"Centre caméra non fini dans {path}.")
            images.append({
                "header": header,
                "name": bytes(name),
                "observations": raw_observations,
                "point_ids": point_ids,
                "indices": indices,
                "center": center,
            })
        if stream.read(1):
            raise ValueError(f"Données superflues après images.bin : {path}")
    return images


def _supported_depth_intervals(
    log_depths,
    min_cluster_points,
    min_cluster_fraction,
    low_density_fraction,
    target,
):
    ordered = np.sort(log_depths)
    count = len(ordered)
    minimum_support = max(
        min_cluster_points, math.ceil(min_cluster_fraction * count)
    )
    if count < minimum_support:
        return 0, minimum_support, [], None
    bandwidth = _depth_bandwidth(log_depths)
    low = float(ordered[0])
    high = float(ordered[-1])
    if high - low <= 1e-12:
        cluster = (low - 3 * bandwidth, high + 3 * bandwidth,
                   count, float(np.mean(ordered)))
        return count, minimum_support, [cluster], cluster

    valley_gap = (
        2 * bandwidth * math.sqrt(-2 * math.log(low_density_fraction))
    )
    gaps = np.diff(ordered)
    cut_after = np.flatnonzero(gaps > valley_gap)

    segments = []
    start = 0
    for cut in cut_after:
        segments.append((start, cut + 1))
        start = cut + 1
    segments.append((start, count))
    clusters = []
    target_density = 0
    target_cluster = None
    target_index = int(np.searchsorted(ordered, target, side="left"))
    for start, stop in segments:
        members = ordered[start:stop]
        if start <= target_index < stop:
            target_density = len(members)
        mean = float(np.mean(members))
        sigma = max(float(np.std(members)), bandwidth)
        cluster = (mean - 3 * sigma, mean + 3 * sigma, len(members), mean)
        clusters.append(cluster)
        if start <= target_index < stop:
            target_cluster = cluster
    return target_density, minimum_support, clusters, target_cluster


def _far_support_threshold(
    target,
    clusters,
    target_cluster,
    minimum_support,
    far_support_rate,
):
    """Require more support for a depth packet as it recedes behind a dense one."""
    foreground = [
        cluster for cluster in clusters
        if cluster is not target_cluster
        and cluster[3] < target_cluster[3]
        and cluster[2] >= minimum_support
    ]
    if not foreground:
        return None
    nearest = max(foreground, key=lambda cluster: cluster[3])
    depth_ratio = math.exp(target - nearest[3])
    if depth_ratio <= 1:
        return minimum_support
    required_fraction = min(1.0, far_support_rate * math.log2(depth_ratio))
    return max(
        minimum_support,
        math.ceil(nearest[2] * required_fraction),
    )


def _depth_bandwidth(log_depths):
    spread = float(np.std(log_depths))
    robust_spread = float(min(
        spread,
        (np.quantile(log_depths, 0.75) - np.quantile(log_depths, 0.25)) / 1.349,
    ))
    return max(1e-3, 1.06 * robust_spread * len(log_depths) ** (-0.2))


_DENSITY_STATE = {}


def _view_votes(image_number, image):
    """Return the evaluable and suspicious point indices of one view."""
    positions = _DENSITY_STATE["positions"]
    params = _DENSITY_STATE["params"]
    min_neighbors = params["min_neighbors"]
    empty = np.empty(0, dtype=np.int64)
    observed = np.unique(image["indices"][image["indices"] >= 0])
    if len(observed) < min_neighbors:
        return image_number, len(observed), empty, empty
    vectors = positions[observed] - image["center"]
    depths = np.linalg.norm(vectors, axis=1)
    valid = np.isfinite(depths) & (depths > 0)
    observed = observed[valid]
    vectors = vectors[valid]
    depths = depths[valid]
    if len(observed) < min_neighbors:
        return image_number, len(observed), empty, empty

    directions = vectors / depths[:, None]
    tree = cKDTree(directions)
    k = min(params["neighbors"], len(observed))
    angular_distances, angular_indices = tree.query(directions, k=k)
    if k == 1:
        angular_distances = angular_distances[:, None]
        angular_indices = angular_indices[:, None]

    evaluable = []
    suspicious = []
    for local_index, point_index in enumerate(observed):
        selected = angular_indices[local_index][
            angular_distances[local_index] <= params["chord_limit"]
        ]
        if len(selected) < min_neighbors:
            continue
        log_depths = np.log(depths[selected])
        target = math.log(depths[local_index])
        density, minimum_support, clusters, target_cluster = _supported_depth_intervals(
            log_depths,
            params["min_cluster_points"],
            params["min_cluster_fraction"],
            params["low_density_fraction"],
            target,
        )
        if target_cluster is None or not any(
            cluster[2] >= minimum_support for cluster in clusters
        ):
            continue

        required_support = _far_support_threshold(
            target, clusters, target_cluster, minimum_support,
            params["far_support_rate"],
        )
        evaluable.append(point_index)
        if required_support is not None and density < required_support:
            suspicious.append(point_index)
    return (
        image_number,
        len(observed),
        np.asarray(evaluable, dtype=np.int64),
        np.asarray(suspicious, dtype=np.int64),
    )


def _view_votes_task(item):
    return _view_votes(*item)


def density_votes(
    positions,
    images,
    *,
    neighbors=128,
    max_angle_deg=5.0,
    min_neighbors=12,
    min_cluster_points=5,
    min_cluster_fraction=0.05,
    low_density_fraction=0.2,
    far_support_rate=0.25,
    workers=1,
    progress=False,
    verbose=False,
):
    """Return per-point suspicious and evaluable image-view vote counts."""
    if neighbors < 2 or min_neighbors < 2 or min_neighbors > neighbors:
        raise ValueError("Voisins requis : 2 <= min_neighbors <= neighbors")
    if not 0 < max_angle_deg <= 180:
        raise ValueError("L'angle maximal doit être dans ]0, 180].")
    if min_cluster_points < 2 or not 0 < min_cluster_fraction <= 1:
        raise ValueError("Seuil de paquet invalide.")
    if not 0 < low_density_fraction < 1:
        raise ValueError("Le seuil de densité doit être dans ]0, 1[.")
    if not 0 < far_support_rate <= 1:
        raise ValueError("Le taux de support distant doit être dans ]0, 1].")
    if workers < 1:
        raise ValueError("Le nombre de workers doit être supérieur ou égal à 1.")

    suspicious_votes = np.zeros(len(positions), dtype=np.uint32)
    evaluable_votes = np.zeros(len(positions), dtype=np.uint32)
    _DENSITY_STATE.clear()
    _DENSITY_STATE.update(
        positions=positions,
        params={
            "neighbors": neighbors,
            "min_neighbors": min_neighbors,
            "min_cluster_points": min_cluster_points,
            "min_cluster_fraction": min_cluster_fraction,
            "low_density_fraction": low_density_fraction,
            "far_support_rate": far_support_rate,
            "chord_limit": 2 * math.sin(math.radians(max_angle_deg) / 2),
        },
    )
    numbered_images = enumerate(images, start=1)
    pool = None
    try:
        if workers > 1:
            pool = multiprocessing.get_context("fork").Pool(workers)
            results = pool.imap(_view_votes_task, numbered_images)
        else:
            results = map(_view_votes_task, numbered_images)
        for image_number, observed_count, evaluable, suspicious in tqdm(
            results, total=len(images), desc="Analyse densité", unit="vue",
            disable=not progress,
        ):
            if verbose:
                tqdm.write(
                    f"[densité] Vue {image_number}/{len(images)} : "
                    f"{observed_count} points observés, {len(evaluable)} évaluables.",
                    file=sys.stderr,
                )
            np.add.at(evaluable_votes, evaluable, 1)
            np.add.at(suspicious_votes, suspicious, 1)
    finally:
        _DENSITY_STATE.clear()
        if pool is not None:
            pool.terminate()

    return suspicious_votes, evaluable_votes


def write_partition(input_dir, output_dir, point_ids, images, mask, report, *, progress):
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".filter-sparse-", dir=output_dir) as temporary:
        temporary = Path(temporary)
        shutil.copyfile(input_dir / "cameras.bin", temporary / "cameras.bin")
        for optional in ("rigs.bin", "frames.bin"):
            if (input_dir / optional).is_file():
                shutil.copyfile(input_dir / optional, temporary / optional)
        with (input_dir / "points3D.bin").open("rb") as source, \
                (temporary / "points3D.bin").open("wb") as target:
            count = struct.unpack("<Q", read_exact(source, 8, "points3D.bin header"))[0]
            target.write(struct.pack("<Q", int(mask.sum())))
            for index in tqdm(range(count), desc=f"Écriture sparse {output_dir.name} : points",
                              unit="pt", disable=not progress):
                header = read_exact(source, POINT_HEADER.size, f"point {index}")
                track = read_exact(source, POINT_HEADER.unpack(header)[-1] * 8,
                                   f"track du point {index}")
                if mask[index]:
                    target.write(header)
                    target.write(track)
        selected_ids = set(map(int, point_ids[mask]))
        with (temporary / "images.bin").open("wb") as target:
            target.write(struct.pack("<Q", len(images)))
            for image in tqdm(images, desc=f"Écriture sparse {output_dir.name} : images",
                              unit="vue", disable=not progress):
                target.write(image["header"])
                target.write(image["name"] + b"\0")
                target.write(struct.pack("<Q", len(image["point_ids"])))
                for offset, raw_id in enumerate(image["point_ids"]):
                    start = offset * OBSERVATION.size
                    target.write(image["observations"][start:start + 16])
                    point_id = int(raw_id)
                    target.write(struct.pack("<q", point_id if point_id in selected_ids else -1))
        (temporary / "density_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        for path in temporary.iterdir():
            os.replace(path, output_dir / path.name)
        for optional in ("rigs.bin", "frames.bin"):
            if not (input_dir / optional).is_file():
                (output_dir / optional).unlink(missing_ok=True)


def filter_sparse_model(
    input_dir,
    output_dir=None,
    *,
    neighbors=128,
    max_angle_deg=5.0,
    min_neighbors=12,
    min_cluster_points=5,
    min_cluster_fraction=0.05,
    low_density_fraction=0.2,
    min_views=2,
    vote_fraction=0.75,
    far_support_rate=0.25,
    workers=1,
    progress=False,
    verbose=False,
):
    input_dir = Path(input_dir).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve() if output_dir is not None else input_dir
    clean_dir = output_dir.with_name(output_dir.name + "_clean")
    rejected_dir = output_dir.with_name(output_dir.name + "_rejected")
    if not 0 <= vote_fraction <= 1 or min_views < 1:
        raise ValueError("Le seuil de vues ou de votes est invalide.")
    if input_dir in (clean_dir, rejected_dir) or any(
        destination in input_dir.parents for destination in (clean_dir, rejected_dir)
    ):
        raise ValueError("Les dossiers de sortie doivent être distincts du sparse d'entrée.")
    required = ("cameras.bin", "images.bin", "points3D.bin")
    missing = [name for name in required if not (input_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Modèle COLMAP incomplet dans {input_dir} : {', '.join(missing)}"
        )
    if verbose:
        tqdm.write(f"[sparse] Lecture : {input_dir}", file=sys.stderr)
    point_ids, positions, _ = read_points(input_dir / "points3D.bin", progress=progress)
    if not len(point_ids):
        raise ValueError("Le modèle COLMAP ne contient aucun point 3D.")
    point_index = {int(pid): index for index, pid in enumerate(point_ids)}
    images = read_images(input_dir / "images.bin", point_index, progress=progress)
    if verbose:
        tqdm.write(
            f"[sparse] {len(point_ids)} points, {len(images)} vues ; "
            f"{neighbors} voisins maximum, cône {max_angle_deg}°, "
            f"{workers} workers, support distant {far_support_rate:.0%} par doublement, "
            f"rejet confirmé par {min_views} vues minimum et {vote_fraction:.0%} des votes.",
            file=sys.stderr,
        )
    suspicious, evaluable = density_votes(
        positions,
        images,
        neighbors=neighbors,
        max_angle_deg=max_angle_deg,
        min_neighbors=min_neighbors,
        min_cluster_points=min_cluster_points,
        min_cluster_fraction=min_cluster_fraction,
        low_density_fraction=low_density_fraction,
        far_support_rate=far_support_rate,
        workers=workers,
        progress=progress,
        verbose=verbose,
    )
    rejected = (
        (evaluable >= min_views)
        & (suspicious >= np.ceil(vote_fraction * evaluable))
        & (suspicious > 0)
    )
    if rejected.all():
        raise ValueError("Le filtre proposerait de supprimer tous les points ; sortie annulée.")

    if verbose:
        tqdm.write(
            f"[sparse] {int(rejected.sum())}/{len(point_ids)} points rejetés "
            f"({rejected.mean():.2%}) ; écriture : {clean_dir} et {rejected_dir}",
            file=sys.stderr,
        )
    report = {
        "algorithm_version": ALGORITHM_VERSION,
        "input": str(input_dir),
        "output_clean": str(clean_dir),
        "output_rejected": str(rejected_dir),
        "points_total": int(len(point_ids)),
        "points_kept": int((~rejected).sum()),
        "points_removed": int(rejected.sum()),
        "points_with_enough_views": int((evaluable >= min_views).sum()),
        "removed_points": [
            {
                "point3D_id": int(point_ids[index]),
                "xyz": positions[index].tolist(),
                "evaluable_views": int(evaluable[index]),
                "suspicious_views": int(suspicious[index]),
            }
            for index in np.flatnonzero(rejected)
        ],
        "parameters": {
            "neighbors": neighbors,
            "max_angle_deg": max_angle_deg,
            "min_neighbors": min_neighbors,
            "min_cluster_points": min_cluster_points,
            "min_cluster_fraction": min_cluster_fraction,
            "low_density_fraction": low_density_fraction,
            "far_support_rate": far_support_rate,
            "min_views": min_views,
            "vote_fraction": vote_fraction,
        },
    }
    for destination, mask in ((clean_dir, ~rejected), (rejected_dir, rejected)):
        write_partition(input_dir, destination, point_ids, images, mask, report, progress=progress)
    if verbose:
        tqdm.write(f"[sparse] Terminé ; rapport : {clean_dir / 'density_report.json'}",
                   file=sys.stderr)
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Filtre les points COLMAP lointains et isolés par densité angulaire."
    )
    parser.add_argument("--version", action="version", version=str(ALGORITHM_VERSION))
    parser.add_argument("--input", type=Path, required=True,
                        help="Sparse COLMAP d'entrée, contenant les trois fichiers .bin.")
    parser.add_argument("--output", type=Path,
                        help="Préfixe XXX des dossiers XXX_clean et XXX_rejected "
                             "(défaut : chemin d'entrée). Aucun PLY produit.")
    parser.add_argument("--neighbors", type=int, default=128,
                        help="Nombre maximal de voisins angulaires (défaut : 128).")
    parser.add_argument("--max-angle-deg", type=float, default=5.0,
                        help="Ouverture du voisinage directionnel en degrés (défaut : 5).")
    parser.add_argument("--min-neighbors", type=int, default=12,
                        help="Voisins minimums pour évaluer une vue (défaut : 12).")
    parser.add_argument("--min-cluster-points", type=int, default=5,
                        help="Support absolu minimum d'un paquet (défaut : 5).")
    parser.add_argument("--min-cluster-fraction", type=float, default=0.05,
                        help="Support relatif minimum d'un paquet (défaut : 0.05).")
    parser.add_argument("--low-density-fraction", type=float, default=0.2,
                        help="Densité maximale relative d'un point isolé (défaut : 0.2).")
    parser.add_argument("--far-support-rate", type=float, default=0.25,
                        help="Hausse du support requis par doublement de profondeur "
                             "(défaut : 0.25).")
    parser.add_argument("--min-views", type=int, default=2,
                        help="Nombre de vues évaluables requis (défaut : 2).")
    parser.add_argument("--vote-fraction", type=float, default=0.75,
                        help="Fraction de vues suspectes requise (défaut : 0.75).")
    parser.add_argument("--workers", type=int, default=1,
                        help="Workers parallèles pour les requêtes angulaires (défaut : 1).")
    parser.add_argument("--verbose", action="store_true",
                        help="Afficher les étapes, paramètres et détails par vue sur stderr.")
    parser.add_argument("--no-progress", action="store_true",
                        help="Désactiver les barres de progression sur stderr.")
    args = parser.parse_args()
    try:
        report = filter_sparse_model(
            args.input,
            args.output,
            neighbors=args.neighbors,
            max_angle_deg=args.max_angle_deg,
            min_neighbors=args.min_neighbors,
            min_cluster_points=args.min_cluster_points,
            min_cluster_fraction=args.min_cluster_fraction,
            low_density_fraction=args.low_density_fraction,
            min_views=args.min_views,
            vote_fraction=args.vote_fraction,
            far_support_rate=args.far_support_rate,
            workers=args.workers,
            progress=not args.no_progress,
            verbose=args.verbose,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
