#!/usr/bin/env python3
import argparse
import json
import os
import struct
import tempfile
from pathlib import Path

import numpy as np
from plyfile import PlyData
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation


def log(message):
    print(f"[INFO] {message}", flush=True)


def safe_read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise EOFError(
            f"Fichier tronqué : attendu {size} octets, obtenu {len(data)}"
        )
    return data


# ============================================================
# TRANSFORMATION WORLD -> TRAIN
# ============================================================
def load_transform(path):
    with open(path, "r", encoding="utf-8") as stream:
        data = json.load(stream)

    if not isinstance(data, dict):
        raise ValueError("Le JSON de transformation doit être un objet")

    if data.get("format") == "spirula-scene-transform":
        if data.get("version") != 1:
            raise ValueError("Version de transformation Spirula non supportée")

        tf = data.get("train_from_world")
        if not isinstance(tf, dict):
            raise ValueError("Objet train_from_world absent")

        if "matrix_4x4" in tf:
            matrix = np.asarray(tf["matrix_4x4"], dtype=np.float64)
        else:
            rotation = np.asarray(
                tf["rotation"]["matrix_3x3"], dtype=np.float64
            )
            translation = np.asarray(tf["translation"], dtype=np.float64)
            scale = float(tf["scale"])

            if rotation.shape != (3, 3) or translation.shape != (3,):
                raise ValueError("Rotation ou translation Spirula invalide")
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError("Échelle Spirula invalide")

            matrix = np.eye(4, dtype=np.float64)
            matrix[:3, :3] = scale * rotation
            matrix[:3, 3] = translation

        log("Transformation : Spirula train_from_world")

    elif "transform" in data:
        matrix = np.asarray(data["transform"], dtype=np.float64)

        if matrix.shape == (3, 4):
            matrix = np.vstack([matrix, [0, 0, 0, 1]])

        if matrix.shape != (4, 4):
            raise ValueError("La transformation doit être 3x4 ou 4x4")

        scale = float(data.get("scale", 1.0))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("Échelle Nerfstudio invalide")

        # Convention du script initial :
        # train = scale * (R @ world + translation).
        matrix = matrix.copy()
        matrix[:3, :] *= scale

        log("Transformation : Nerfstudio dataparser")

    else:
        raise ValueError("Format de transformation JSON non supporté")

    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("La transformation doit être une matrice 4x4 finie")

    if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8):
        raise ValueError("La matrice doit être affine")

    linear = matrix[:3, :3]
    singular_values = np.linalg.svd(linear, compute_uv=False)
    scale = float(singular_values.mean())

    if scale <= 0:
        raise ValueError("Échelle nulle")

    if not np.allclose(
        singular_values / scale, np.ones(3), rtol=1e-5, atol=1e-7
    ):
        raise ValueError(
            "Échelle non uniforme ou cisaillement : non supporté"
        )

    rotation = linear / scale

    if (
        not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
        or np.linalg.det(rotation) <= 0
    ):
        raise ValueError("Rotation invalide ou réflexion non supportée")

    log(f"Échelle monde -> entraînement : {scale:.9g}")
    return matrix, rotation, scale


def apply_transform(points, matrix):
    return points @ matrix[:3, :3].T + matrix[:3, 3]


# ============================================================
# COLMAP points3D.bin
# ============================================================
def load_colmap_points(path, matrix, max_points):
    points = []

    with open(path, "rb") as stream:
        file_size = os.fstat(stream.fileno()).st_size
        count = struct.unpack("<Q", safe_read(stream, 8))[0]
        log(f"Points COLMAP bruts : {count}")

        for index in range(count):
            # point3D_id, XYZ, RGB, error, track_length.
            record = struct.unpack("<QdddBBBdQ", safe_read(stream, 51))
            xyz = record[1:4]
            track_length = record[-1]

            next_position = stream.tell() + track_length * 8
            if next_position > file_size:
                raise EOFError(f"Track COLMAP tronqué au point {index}")

            stream.seek(next_position)

            if np.isfinite(xyz).all():
                points.append(xyz)

            if len(points) >= max_points:
                log(f"Lecture limitée à {max_points} points valides")
                break

    if not points:
        raise ValueError("Aucun point COLMAP valide")

    points = apply_transform(np.asarray(points, dtype=np.float64), matrix)
    if not np.isfinite(points).all():
        raise ValueError("Coordonnées COLMAP transformées non finies")

    return points


def filter_outliers(points, percentile):
    center = np.median(points, axis=0)
    distances = np.linalg.norm(points - center, axis=1)
    threshold = np.percentile(distances, percentile)
    filtered = points[distances <= threshold]

    if len(filtered) < 2:
        raise ValueError("Moins de deux points COLMAP après filtrage")

    log(f"Filtrage COLMAP : {len(filtered)}/{len(points)} conservés")
    return filtered


def estimate_radius(points, factor, seed):
    rng = np.random.default_rng(seed)
    size = min(5000, len(points))
    sample = points[rng.choice(len(points), size, replace=False)]

    distances, _ = cKDTree(sample).query(sample, k=2)
    positive = distances[:, 1]
    positive = positive[np.isfinite(positive) & (positive > 0)]

    if len(positive) == 0:
        raise ValueError("Rayon automatique indéterminable : fournir --dist")

    return float(np.median(positive) * factor)


# ============================================================
# PLY 3DGS STANDARD
# ============================================================
def load_gaussians(path):
    vertex = PlyData.read(str(path))["vertex"].data
    names = set(vertex.dtype.names or ())

    def columns(fields):
        missing = set(fields) - names
        if missing:
            raise ValueError(
                "PLY 3DGS standard non compressé requis. "
                f"Propriétés manquantes : {sorted(missing)}"
            )
        return np.stack([vertex[name] for name in fields], axis=1).astype(
            np.float64
        )

    splats = {
        "means": columns(["x", "y", "z"]),
        "scales": columns([f"scale_{i}" for i in range(3)]),
        "quats": columns([f"rot_{i}" for i in range(4)]),
        "opacity": columns(["opacity"])[:, 0],
        "dc": columns([f"f_dc_{i}" for i in range(3)]),
    }

    if len(vertex) == 0:
        raise ValueError("PLY vide")

    log(f"Gaussians chargés : {len(vertex)}")
    log("Export SPLAT : les harmoniques supérieures ne sont pas conservées")
    return splats


def clean_gaussians(splats, colmap, distance, batch_size):
    """Garde les centres des Gaussians dans la BBOX COLMAP + marge.

    distance : marge absolue dans le repère d'entraînement,
               calculée par estimate_radius() ou fournie avec --dist.
    """
    if not np.isfinite(distance) or distance <= 0:
        raise ValueError("La marge BBOX doit être finie et positive")

    bbox_min = colmap.min(axis=0)
    bbox_max = colmap.max(axis=0)

    expanded_min = bbox_min - distance
    expanded_max = bbox_max + distance

    log(f"BBOX COLMAP : {bbox_min} -> {bbox_max}")
    log(f"Marge BBOX : {distance:.9g}")
    log(f"BBOX élargie : {expanded_min} -> {expanded_max}")
    log("Filtrage par BBOX uniquement, sans proximité aux points sparse")

    count = len(splats["means"])
    mask = np.zeros(count, dtype=bool)

    for start in range(0, count, batch_size):
        stop = min(start + batch_size, count)
        xyz = splats["means"][start:stop]

        # Exclure les attributs invalides avant l'export.
        valid = np.ones(stop - start, dtype=bool)
        for array in splats.values():
            block = array[start:stop].reshape(stop - start, -1)
            valid &= np.isfinite(block).all(axis=1)

        valid &= (
            np.linalg.norm(splats["quats"][start:stop], axis=1) > 1e-12
        )

        inside_bbox = (
            (xyz >= expanded_min) & (xyz <= expanded_max)
        ).all(axis=1)

        mask[start:stop] = valid & inside_bbox

    kept = int(mask.sum())
    log(f"Gaussians conservés : {kept}/{count}")

    if kept == 0:
        raise ValueError(
            "Aucun Gaussian conservé : vérifier les repères "
            "ou augmenter --radius-factor / --dist"
        )

    return {name: array[mask] for name, array in splats.items()}


# ============================================================
# EXPORT SPLAT BINAIRE — 32 OCTETS PAR GAUSSIAN
# ============================================================
def write_world_splat(splats, output, matrix, rotation, scale, batch_size):
    inverse = np.linalg.inv(matrix)
    world_from_train_rotation = Rotation.from_matrix(rotation.T)

    record_dtype = np.dtype([
        ("position", "<f4", (3,)),
        ("scale", "<f4", (3,)),
        ("rgba", "u1", (4,)),
        ("rotation", "u1", (4,)),
    ])
    assert record_dtype.itemsize == 32

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
    )

    count = len(splats["means"])

    try:
        with os.fdopen(fd, "wb") as stream:
            for start in range(0, count, batch_size):
                stop = min(start + batch_size, count)
                records = np.empty(stop - start, dtype=record_dtype)

                world_positions = apply_transform(
                    splats["means"][start:stop], inverse
                )

                with np.errstate(over="ignore", under="ignore"):
                    world_scales = np.exp(
                        splats["scales"][start:stop] - np.log(scale)
                    )

                quats = splats["quats"][start:stop]
                train_rotations = Rotation.from_quat(
                    quats[:, [1, 2, 3, 0]]
                )
                world_quats = (
                    world_from_train_rotation * train_rotations
                ).as_quat()[:, [3, 0, 1, 2]]

                with np.errstate(over="ignore", under="ignore"):
                    records["position"] = world_positions
                    records["scale"] = world_scales

                if not np.isfinite(records["position"]).all():
                    raise ValueError("Positions monde non représentables en float32")
                if (
                    not np.isfinite(records["scale"]).all()
                    or np.any(records["scale"] <= 0)
                ):
                    raise ValueError("Échelles monde invalides en float32")

                colors = 0.5 + 0.28209479177387814 * splats["dc"][start:stop]
                logits = splats["opacity"][start:stop]
                exp_negative_abs = np.exp(-np.abs(logits))
                alpha = np.where(
                    logits >= 0,
                    1.0 / (1.0 + exp_negative_abs),
                    exp_negative_abs / (1.0 + exp_negative_abs),
                )

                records["rgba"][:, :3] = np.clip(
                    colors * 255, 0, 255
                ).astype(np.uint8)
                records["rgba"][:, 3] = np.clip(
                    alpha * 255, 0, 255
                ).astype(np.uint8)

                records["rotation"] = np.clip(
                    world_quats * 128 + 128, 0, 255
                ).astype(np.uint8)

                stream.write(records.tobytes())

        expected_size = count * 32
        if os.path.getsize(temporary) != expected_size:
            raise RuntimeError("Taille SPLAT produite incorrecte")

        os.replace(temporary, output)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Nettoyage PLY 3DGS avec COLMAP, retour au repère monde "
            "et export SPLAT binaire"
        )
    )
    parser.add_argument("--in-ply", required=True)
    parser.add_argument("--points", required=True, help="COLMAP points3D.bin")
    parser.add_argument("--transform", required=True, help="scene_transform.json")
    parser.add_argument("--out-ply", required=True, help="Sortie .splat")
    parser.add_argument("--format", choices=["splat"], default="splat")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dist", type=float, default=None)
    parser.add_argument("--outlier-percent", type=float, default=97.0)
    parser.add_argument("--radius-factor", type=float, default=3.0)
    parser.add_argument("--max-points", type=int, default=4_000_000)
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    source = Path(args.in_ply).expanduser().resolve()
    points_path = Path(args.points).expanduser().resolve()
    transform_path = Path(args.transform).expanduser().resolve()
    output = Path(args.out_ply).expanduser().resolve()

    for path in (source, points_path, transform_path):
        if not path.is_file():
            parser.error(f"Fichier introuvable : {path}")
        if output == path or (
            output.exists() and os.path.samefile(output, path)
        ):
            parser.error("La sortie ne doit écraser aucun fichier d'entrée")

    if output.suffix.lower() != ".splat":
        parser.error("La sortie doit avoir l'extension .splat")
    if output.exists() and not args.overwrite:
        parser.error("La sortie existe déjà : utiliser --overwrite")
    if not 0 < args.outlier_percent <= 100:
        parser.error("--outlier-percent doit être dans ]0, 100]")
    if args.max_points < 2 or args.batch_size < 1:
        parser.error("--max-points >= 2 et --batch-size >= 1 requis")
    if not np.isfinite(args.radius_factor) or args.radius_factor <= 0:
        parser.error("--radius-factor doit être fini et positif")
    if args.dist is not None and (
        not np.isfinite(args.dist) or args.dist <= 0
    ):
        parser.error("--dist doit être fini et positif")

    matrix, rotation, scale = load_transform(transform_path)

    colmap = load_colmap_points(points_path, matrix, args.max_points)
    colmap = filter_outliers(colmap, args.outlier_percent)

    distance = (
        args.dist
        if args.dist is not None
        else estimate_radius(colmap, args.radius_factor, args.seed)
    )
    log(f"Rayon dans le repère d'entraînement : {distance:.9g}")

    splats = load_gaussians(source)
    splats = clean_gaussians(splats, colmap, distance, args.batch_size)

    log("Transformation vers le monde et écriture SPLAT...")
    write_world_splat(
        splats, output, matrix, rotation, scale, args.batch_size
    )

    log(f"Export terminé : {output}")
    log(f"Repère : monde COLMAP ; Gaussians : {len(splats['means'])}")
    log(
        f"Taille : {source.stat().st_size / 1024**2:.2f} MiB"
        f" -> {output.stat().st_size / 1024**2:.2f} MiB"
    )


if __name__ == "__main__":
    main()
