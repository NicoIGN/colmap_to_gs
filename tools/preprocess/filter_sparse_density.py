#!/usr/bin/env python3
"""Filter isolated, distant COLMAP points using per-image angular density."""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile

import numpy as np
from scipy.spatial import cKDTree


POINT_HEADER = struct.Struct("<QdddBBBdQ")
IMAGE_HEADER = struct.Struct("<I7dI")
OBSERVATION = struct.Struct("<ddq")


def read_exact(stream, size, context):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"Fichier COLMAP tronqué ({context}) : {stream.name}")
    return data


def read_points(path):
    positions = []
    colors = []
    point_ids = []
    with path.open("rb") as stream:
        count = struct.unpack("<Q", read_exact(stream, 8, "points3D.bin header"))[0]
        for index in range(count):
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


def read_images(path, point_index):
    images = []
    with path.open("rb") as stream:
        count = struct.unpack("<Q", read_exact(stream, 8, "images.bin header"))[0]
        for image_number in range(count):
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
        return [], 0, minimum_support
    bandwidth = _depth_bandwidth(log_depths)
    low = float(ordered[0])
    high = float(ordered[-1])
    if high - low <= 1e-12:
        return [(low - 3 * bandwidth, high + 3 * bandwidth)], count, minimum_support

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
    intervals = []
    target_density = 0
    target_index = int(np.searchsorted(ordered, target, side="left"))
    for start, stop in segments:
        members = ordered[start:stop]
        if start <= target_index < stop:
            target_density = len(members)
        if len(members) < minimum_support:
            continue
        mean = float(np.mean(members))
        sigma = max(float(np.std(members)), bandwidth)
        intervals.append((mean - 3 * sigma, mean + 3 * sigma))
    return intervals, target_density, minimum_support


def _depth_bandwidth(log_depths):
    spread = float(np.std(log_depths))
    robust_spread = float(min(
        spread,
        (np.quantile(log_depths, 0.75) - np.quantile(log_depths, 0.25)) / 1.349,
    ))
    return max(1e-3, 1.06 * robust_spread * len(log_depths) ** (-0.2))


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

    suspicious_votes = np.zeros(len(positions), dtype=np.uint32)
    evaluable_votes = np.zeros(len(positions), dtype=np.uint32)
    chord_limit = 2 * math.sin(math.radians(max_angle_deg) / 2)

    for image in images:
        observed = np.unique(image["indices"][image["indices"] >= 0])
        if len(observed) < min_neighbors:
            continue
        vectors = positions[observed] - image["center"]
        depths = np.linalg.norm(vectors, axis=1)
        valid = np.isfinite(depths) & (depths > 0)
        observed = observed[valid]
        vectors = vectors[valid]
        depths = depths[valid]
        if len(observed) < min_neighbors:
            continue

        directions = vectors / depths[:, None]
        tree = cKDTree(directions)
        k = min(neighbors, len(observed))
        angular_distances, angular_indices = tree.query(directions, k=k)
        if k == 1:
            angular_distances = angular_distances[:, None]
            angular_indices = angular_indices[:, None]

        for local_index, point_index in enumerate(observed):
            selected = angular_indices[local_index][
                angular_distances[local_index] <= chord_limit
            ]
            if len(selected) < min_neighbors:
                continue
            local_depths = depths[selected]
            log_depths = np.log(local_depths)
            target = math.log(depths[local_index])
            intervals, density, minimum_support = _supported_depth_intervals(
                log_depths,
                min_cluster_points,
                min_cluster_fraction,
                low_density_fraction,
                target,
            )
            if not intervals:
                continue

            if any(lower <= target <= upper for lower, upper in intervals):
                evaluable_votes[point_index] += 1
                continue

            evaluable_votes[point_index] += 1
            is_farther_than_packets = target > max(upper for _, upper in intervals)
            if (
                is_farther_than_packets
                and density < minimum_support
            ):
                suspicious_votes[point_index] += 1

    return suspicious_votes, evaluable_votes


def filter_sparse_model(
    input_dir,
    output_dir,
    *,
    neighbors=128,
    max_angle_deg=5.0,
    min_neighbors=12,
    min_cluster_points=5,
    min_cluster_fraction=0.05,
    low_density_fraction=0.2,
    min_views=2,
    vote_fraction=0.75,
):
    input_dir = Path(input_dir).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    if not 0 <= vote_fraction <= 1 or min_views < 1:
        raise ValueError("Le seuil de vues ou de votes est invalide.")
    if input_dir == output_dir:
        raise ValueError("Le dossier de sortie doit être différent du sparse d'entrée.")
    required = ("cameras.bin", "images.bin", "points3D.bin")
    missing = [name for name in required if not (input_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Modèle COLMAP incomplet dans {input_dir} : {', '.join(missing)}"
        )
    point_ids, positions, colors = read_points(input_dir / "points3D.bin")
    if not len(point_ids):
        raise ValueError("Le modèle COLMAP ne contient aucun point 3D.")
    point_index = {int(pid): index for index, pid in enumerate(point_ids)}
    images = read_images(input_dir / "images.bin", point_index)
    suspicious, evaluable = density_votes(
        positions,
        images,
        neighbors=neighbors,
        max_angle_deg=max_angle_deg,
        min_neighbors=min_neighbors,
        min_cluster_points=min_cluster_points,
        min_cluster_fraction=min_cluster_fraction,
        low_density_fraction=low_density_fraction,
    )
    rejected = (
        (evaluable >= min_views)
        & (suspicious >= np.ceil(vote_fraction * evaluable))
        & (suspicious > 0)
    )
    if rejected.all():
        raise ValueError("Le filtre proposerait de supprimer tous les points ; sortie annulée.")

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
            target.write(struct.pack("<Q", int((~rejected).sum())))
            for index in range(count):
                raw_header = read_exact(source, POINT_HEADER.size, f"point {index}")
                track_size = POINT_HEADER.unpack(raw_header)[-1] * 8
                raw_track = read_exact(source, track_size, f"track du point {index}")
                if not rejected[index]:
                    target.write(raw_header)
                    target.write(raw_track)
        kept_ids = set(map(int, point_ids[~rejected]))
        with (temporary / "images.bin").open("wb") as target:
            target.write(struct.pack("<Q", len(images)))
            for image in images:
                target.write(image["header"])
                target.write(image["name"] + b"\0")
                target.write(struct.pack("<Q", len(image["point_ids"])))
                records = image["observations"]
                for offset, raw_id in enumerate(image["point_ids"]):
                    start = offset * OBSERVATION.size
                    target.write(records[start:start + 16])
                    point_id = int(raw_id)
                    target.write(struct.pack(
                        "<q", point_id if point_id in kept_ids else -1
                    ))

        report = {
            "input": str(input_dir),
            "output": str(output_dir),
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
                "min_views": min_views,
                "vote_fraction": vote_fraction,
            },
        }
        (temporary / "density_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for name, mask in (
            ("points3D_kept.ply", ~rejected),
            ("points3D_removed.ply", rejected),
        ):
            with (temporary / name).open("w", encoding="ascii") as ply:
                ply.write(
                    "ply\nformat ascii 1.0\n"
                    f"element vertex {int(mask.sum())}\n"
                    "property double x\nproperty double y\nproperty double z\n"
                    "property uchar red\nproperty uchar green\nproperty uchar blue\n"
                    "end_header\n"
                )
                for (x, y, z), (red, green, blue) in zip(
                    positions[mask], colors[mask]
                ):
                    ply.write(
                        f"{x:.17g} {y:.17g} {z:.17g} "
                        f"{red} {green} {blue}\n"
                    )

        filenames = [
            "cameras.bin", "images.bin", "points3D.bin",
            "density_report.json", "points3D_kept.ply", "points3D_removed.ply",
        ]
        filenames.extend(
            name for name in ("rigs.bin", "frames.bin")
            if (temporary / name).is_file()
        )
        for name in filenames:
            os.replace(temporary / name, output_dir / name)
        for optional in ("rigs.bin", "frames.bin"):
            if not (input_dir / optional).is_file():
                (output_dir / optional).unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Filtre les points COLMAP lointains et isolés par densité angulaire."
    )
    parser.add_argument("--input", type=Path, required=True,
                        help="Sparse COLMAP d'entrée, contenant les trois fichiers .bin.")
    parser.add_argument("--output", type=Path, required=True,
                        help="Nouveau sparse COLMAP de sortie, sans modifier l'entrée.")
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
    parser.add_argument("--min-views", type=int, default=2,
                        help="Nombre de vues évaluables requis (défaut : 2).")
    parser.add_argument("--vote-fraction", type=float, default=0.75,
                        help="Fraction de vues suspectes requise (défaut : 0.75).")
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
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
