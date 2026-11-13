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

try:
    from .filter_splats import contextual_mask
except ImportError:
    from filter_splats import contextual_mask


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


def shape_isolation_mask(means, log_scales, *, max_aspect_ratio, isolation_factor,
                         neighbors=8, batch_size=20000):
    """Flag Gaussians that are both elongated and isolated (needle-like floaters).

    Elongation: largest / smallest semi-axis. Isolation: mean distance to the
    `neighbors` nearest Gaussian centres, relative to the scene median.
    """
    count = len(means)
    rejected = np.zeros(count, dtype=bool)
    report = dict(total=count, elongated=0, isolated=0, proposed_rejections=0,
                  max_aspect_ratio=max_aspect_ratio, isolation_factor=isolation_factor,
                  neighbors=neighbors)
    if count <= neighbors:
        report["warning"] = "Trop peu de splats pour estimer l'isolement ; aucun rejet de forme."
        return rejected, report

    tree = cKDTree(means)
    mean_distance = np.empty(count, dtype=np.float64)
    for start in range(0, count, batch_size):
        stop = min(start + batch_size, count)
        distances, _ = tree.query(means[start:stop], k=neighbors + 1)
        mean_distance[start:stop] = distances[:, 1:].mean(axis=1)
    reference = np.median(mean_distance)
    if not np.isfinite(reference) or reference <= 0:
        report["warning"] = "Distance de référence indéterminable ; aucun rejet de forme."
        return rejected, report

    scales = np.exp(log_scales)
    elongated = scales.max(axis=1) / scales.min(axis=1) > max_aspect_ratio
    isolated = mean_distance > isolation_factor * reference
    rejected = elongated & isolated
    report.update(elongated=int(elongated.sum()), isolated=int(isolated.sum()),
                  proposed_rejections=int(rejected.sum()))
    return rejected, report


def clean_gaussians(
    splats,
    colmap,
    distance,
    batch_size,
    *,
    world_scale,
    max_world_scale=None,
    min_opacity=0.0,
    max_scale_factor=10.0,
    max_removal_fraction=0.2,
    max_aspect_ratio=None,
    isolation_factor=None,
    filter_mode="diagnostic",
    return_rejected=False,
    seed=0,
):
    """Sanitize attributes; optionally diagnose/apply contextual geometry filtering."""
    if not np.isfinite(world_scale) or world_scale <= 0:
        raise ValueError("L'échelle monde -> entraînement est invalide")
    if max_world_scale is not None:
        raise ValueError("--max-world-scale appartient au filtre export_ply_legacy.py")
    if not np.isfinite(min_opacity) or not 0 <= min_opacity < 1:
        raise ValueError("L'opacité minimale doit être dans [0, 1[")
    if not np.isfinite(max_scale_factor) or max_scale_factor <= 1:
        raise ValueError("Le facteur de taille doit être fini et > 1")
    if not np.isfinite(max_removal_fraction) or not 0 <= max_removal_fraction <= 1:
        raise ValueError("La fraction maximale de suppression doit être dans [0, 1]")
    if (max_aspect_ratio is None) != (isolation_factor is None):
        raise ValueError("Filtre de forme : --max-aspect-ratio et --isolation-factor vont ensemble")
    if max_aspect_ratio is not None and (not np.isfinite(max_aspect_ratio) or max_aspect_ratio <= 1):
        raise ValueError("Le rapport d'élongation maximal doit être fini et > 1")
    if isolation_factor is not None and (not np.isfinite(isolation_factor) or isolation_factor <= 0):
        raise ValueError("Le facteur d'isolement doit être fini et > 0")
    if filter_mode not in ("diagnostic", "apply", "off"):
        raise ValueError("Mode de filtre inconnu")
    if batch_size < 1 or len(splats["means"]) == 0:
        raise ValueError("Lot et nombre de Gaussians doivent être positifs")
    count = len(splats["means"])
    mask = np.ones(count, dtype=bool)
    for array in splats.values():
        mask &= np.isfinite(array.reshape(count, -1)).all(axis=1)
    mask &= np.max(np.abs(splats["quats"]), axis=1) > 0
    log(f"Attributs invalides supprimés : {count - int(mask.sum())}/{count}")
    if min_opacity > 0:
        mask &= splats["opacity"] >= np.log(min_opacity) - np.log1p(-min_opacity)
    if not mask.any():
        raise ValueError("Aucun Gaussian valide après contrôle des attributs et de l'opacité")
    valid = {name: array[mask] for name, array in splats.items()}
    rejected = np.zeros(len(valid["means"]), dtype=bool)
    report = dict(total=len(rejected), proposed_rejections=0)
    if filter_mode != "off":
        rejected, report = contextual_mask(
            valid["means"], valid["scales"], valid["quats"],
            size_factor=max_scale_factor, batch_size=min(batch_size, 20000), seed=seed,
        )
    if filter_mode != "off" and max_aspect_ratio is not None:
        shape_rejected, shape_report = shape_isolation_mask(
            valid["means"], valid["scales"],
            max_aspect_ratio=max_aspect_ratio, isolation_factor=isolation_factor,
            batch_size=min(batch_size, 20000),
        )
        rejected |= shape_rejected
        report["shape"] = shape_report
    report.update(mode=filter_mode, applied_rejections=int(rejected.sum()) if filter_mode == "apply" else 0)
    log("Filtre contextuel : " + json.dumps(report, ensure_ascii=False))
    if "warning" in report:
        log("AVERTISSEMENT : " + report["warning"])
    if filter_mode == "apply" and rejected.mean() > max_removal_fraction:
        raise ValueError("Rejets contextuels au-delà de la limite ; utiliser le mode diagnostic")
    if filter_mode == "apply" and rejected.all():
        raise ValueError("Le filtre supprimerait tous les splats")
    keep = ~rejected if filter_mode == "apply" else np.ones(len(rejected), dtype=bool)
    result = {name: array[keep] for name, array in valid.items()}
    if return_rejected:
        return result, {name: array[rejected] for name, array in valid.items()}, report
    return result


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
    parser.add_argument("--points", required=True, help="Référence COLMAP conservée pour compatibilité ; pas de test de proximité")
    parser.add_argument("--transform", required=True, help="scene_transform.json")
    parser.add_argument("--out-ply", required=True, help="Sortie .splat")
    parser.add_argument("--format", choices=["splat"], default="splat")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--filter-mode", choices=("diagnostic", "apply", "off"), default="diagnostic")
    parser.add_argument("--dist", type=float, default=None)
    parser.add_argument("--outlier-percent", type=float, default=97.0)
    parser.add_argument("--radius-factor", type=float, default=3.0)
    parser.add_argument("--max-points", type=int, default=4_000_000)
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-scale-factor", type=float, default=10.0,
                        help="Demi-axe maximal / médiane locale (défaut : 10)")
    parser.add_argument("--max-removal-fraction", type=float, default=0.2,
                        help="Refuser l'application au-delà de cette fraction de rejets contextuels (défaut : 0.2)")
    parser.add_argument("--max-aspect-ratio", type=float, default=None,
                        help=(
                            "Rapport max/min des demi-axes au-delà duquel un splat est allongé "
                            "(ex. 20). Doit être combiné à --isolation-factor. Désactivé par défaut."
                        ))
    parser.add_argument("--isolation-factor", type=float, default=None,
                        help=(
                            "Un splat est isolé si sa distance moyenne aux voisins dépasse ce "
                            "multiple de la médiane de la scène (ex. 5). Combiné à --max-aspect-ratio "
                            "par ET. Désactivé par défaut."
                        ))
    parser.add_argument( "--max-world-scale",type=float,default=None,
        help=(
            "Demi-axe maximal d'un Gaussian dans les unités monde COLMAP. "
            "Un Gaussian est supprimé si un seul axe dépasse ce seuil. "
            "Désactivé par défaut."
        ),
    )

    parser.add_argument( "--min-opacity", type=float,  default=0.0,
        help=(
            "Opacité minimale après sigmoid, dans [0, 1[. "
            "0 désactive ce filtre."
        ),
    )
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
    if args.dist is not None and ( not np.isfinite(args.dist) or args.dist <= 0 ):
        parser.error("--dist doit être fini et positif")
    if args.max_world_scale is not None and ( not np.isfinite(args.max_world_scale) or args.max_world_scale <= 0 ):
        parser.error("--max-world-scale doit être fini et positif")
    if not np.isfinite(args.min_opacity) or not 0 <= args.min_opacity < 1:
        parser.error("--min-opacity doit être dans [0, 1[")
    if not np.isfinite(args.max_scale_factor) or args.max_scale_factor <= 1:
        parser.error("--max-scale-factor doit être fini et > 1")
    if not np.isfinite(args.max_removal_fraction) or not 0 <= args.max_removal_fraction <= 1:
        parser.error("--max-removal-fraction doit être dans [0, 1]")
        
    matrix, rotation, scale = load_transform(transform_path)

    if args.max_world_scale is not None:
        parser.error("--max-world-scale : utiliser export_ply_legacy.py pour le filtre absolu")
    log("Nouveau filtre : pas de suppression BBOX ni de proximité COLMAP ; "
        "--points/--dist/--radius-factor/--outlier-percent/--max-points "
        "n'interviennent plus dans le nettoyage.")
    rejected_path = output.with_suffix(".rejected.splat")
    report_path = output.with_suffix(".filter.json")
    for sidecar in (rejected_path, report_path):
        if sidecar in (source, points_path, transform_path) or (
            sidecar.exists() and any(os.path.samefile(sidecar, p)
                                     for p in (source, points_path, transform_path))
        ):
            parser.error("Une sortie diagnostic écraserait une entrée")
        if sidecar.exists() and not args.overwrite:
            parser.error("Diagnostic existant : utiliser --overwrite")
    splats = load_gaussians(source)
    splats, rejected, report = clean_gaussians(
        splats,
        None,
        None,
        args.batch_size,
        world_scale=scale,
        max_world_scale=args.max_world_scale,
        min_opacity=args.min_opacity,
        max_scale_factor=args.max_scale_factor,
        max_removal_fraction=args.max_removal_fraction,
        max_aspect_ratio=args.max_aspect_ratio,
        isolation_factor=args.isolation_factor,
        filter_mode=args.filter_mode,
        return_rejected=True,
        seed=args.seed,
    )

    log("Transformation vers le monde et écriture SPLAT...")
    write_world_splat(
        splats, output, matrix, rotation, scale, args.batch_size
    )
    write_world_splat(rejected, rejected_path, matrix, rotation, scale, args.batch_size)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    log(f"Export terminé : {output}")
    log(f"Repère : monde COLMAP ; Gaussians : {len(splats['means'])}")
    log(
        f"Taille : {source.stat().st_size / 1024**2:.2f} MiB"
        f" -> {output.stat().st_size / 1024**2:.2f} MiB"
    )


if __name__ == "__main__":
    main()
