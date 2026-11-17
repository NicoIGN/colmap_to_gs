#!/usr/bin/env python3
"""Recaler les points 3D d'un modèle COLMAP sur le sol et les façades d'un LAZ LiDAR aérien.

Seuls les points 3D SfM et les surfaces LiDAR sont utilisés (ni trajectoire, ni axes de rue).
Les toits sont écartés : la classe murs ne garde que les surfaces verticales.
Le recalage est un ICP Sim(3) point-à-plan sur les normales LiDAR ; un diagnostic de
contrainte vérifie que chaque degré de liberté est contraint avant export.
"""

import argparse
from pathlib import Path

import numpy as np
import pycolmap
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

from align_colmap_to_lidar import similarity
from common import load_lidar, write_report
from geographic import enu_lidar, reference_metadata

DOF_NAMES = ("tx", "ty", "tz", "rx", "ry", "rz", "scale")
PLANARITY_RATIO = 0.1
MIN_VOXEL_POINTS = 8


def point_normals(points, voxel):
    """Normale et planéité par voxel (PCA) ; retourne (normales par point, planaire par point)."""
    keys, inverse = np.unique(np.floor(points / voxel).astype(np.int64), axis=0,
                              return_inverse=True)
    inverse = inverse.ravel()
    count = np.bincount(inverse, minlength=len(keys)).astype(float)
    mean = np.column_stack([np.bincount(inverse, points[:, i], len(keys)) for i in range(3)])
    mean /= count[:, None]
    centered = points - mean[inverse]
    outer = np.einsum("ni,nj->nij", centered, centered).reshape(len(points), 9)
    covariance = np.column_stack([np.bincount(inverse, outer[:, k], len(keys))
                                  for k in range(9)]).reshape(-1, 3, 3) / count[:, None, None]
    values, vectors = np.linalg.eigh(covariance)
    planar = (count >= MIN_VOXEL_POINTS) & (values[:, 0] <= PLANARITY_RATIO * values[:, 1])
    return vectors[inverse, :, 0], planar[inverse]


def surface_targets(points, voxel, keep):
    """Garde les points planaires dont la composante verticale de la normale vérifie keep."""
    if len(points) == 0:
        return points, points
    normals, planar = point_normals(points, voxel)
    mask = planar & keep(np.abs(normals[:, 2]))
    return points[mask], normals[mask]


def constraint_report(moved, targets, normals, distance):
    """Matrice point-à-plan (7 DOF) au point courant ; valeurs propres normalisées."""
    distances, near = cKDTree(targets).query(moved)
    keep = distances <= distance / 2
    if keep.sum() < 6:
        raise ValueError("Moins de six appariements SfM-LiDAR pour le diagnostic")
    p = moved[keep]
    n = normals[near[keep]]
    rows = np.column_stack((n, np.cross(p, n), np.einsum("ij,ij->i", n, p)))
    hessian = rows.T @ rows
    scaling = np.sqrt(np.diag(hessian))
    if (scaling <= 0).any():
        scaling = np.where(scaling > 0, scaling, 1.0)
    values, vectors = np.linalg.eigh(hessian / np.outer(scaling, scaling))
    relative = values / max(values[-1], 1e-300)
    weakest = vectors[:, 0] / scaling
    weakest /= np.abs(weakest).max()
    return {
        "pairs": int(keep.sum()),
        "support_ratio": float(keep.mean()),
        "relative_eigenvalues": relative.tolist(),
        "weakest_direction": {name: float(value) for name, value in zip(DOF_NAMES, weakest)},
        "relative_weakest": float(relative[0]),
    }


def point_to_plane_icp(source, targets, normals, *, distance, iterations, min_overlap):
    """ICP Sim(3) point-à-plan ; gates décroissants. Retourne (scale, rotation, translation, rapport)."""
    tree = cKDTree(targets)
    required = max(6, int(np.ceil(min_overlap * len(source))))

    def residuals(moved):
        dist, near = tree.query(moved)
        return dist, near, np.einsum("ij,ij->i", normals[near], moved - targets[near])

    scale, rotation, translation = 1.0, np.eye(3), np.zeros(3)
    first = residuals(source)[2]
    for gate in (distance, distance / 2, distance / 4):
        for _ in range(iterations):
            moved = scale * source @ rotation.T + translation
            dist, near, residual = residuals(moved)
            keep = dist <= gate
            if keep.sum() < required:
                raise ValueError("Recouvrement insuffisant : vérifier origine et placement initial")
            p, n = moved[keep], normals[near[keep]]
            jacobian = np.column_stack((n, np.cross(p, n), np.einsum("ij,ij->i", n, p)))
            delta = np.linalg.lstsq(jacobian, -residual[keep], rcond=None)[0]
            rot = Rotation.from_rotvec(delta[3:6]).as_matrix()
            scale, rotation, translation = (
                (1 + delta[6]) * scale, rot @ rotation,
                (1 + delta[6]) * rot @ translation + delta[:3])
            if np.abs(delta).max() < 1e-8:
                break
    moved = scale * source @ rotation.T + translation
    dist, near, residual = residuals(moved)
    inliers = dist <= distance / 4
    if inliers.sum() < required:
        raise ValueError("Recouvrement final insuffisant")
    before = float(np.sqrt(np.mean(first[inliers] ** 2)))
    after = float(np.sqrt(np.mean(residual[inliers] ** 2)))
    if after > before + 1e-9:
        raise ValueError("Recalage dégradant les distances : aucune scène écrite")
    return scale, rotation, translation, {
        "pairs": int(inliers.sum()), "support_ratio": float(inliers.mean()),
        "plane_rmse_before": before, "plane_rmse_after": after}


def align_points(sfm, ground, walls, *, normal_voxel, distance, iterations, min_overlap,
                 min_ground_nz, max_wall_nz, min_constraint):
    """Retourne (scale, rotation, translation, rapport) ; refuse les directions non contraintes."""
    ground_targets, ground_normals = surface_targets(
        ground, normal_voxel, lambda nz: nz >= min_ground_nz)
    wall_targets, wall_normals = surface_targets(walls, normal_voxel, lambda nz: nz <= max_wall_nz)
    targets = np.vstack((ground_targets, wall_targets))
    normals = np.vstack((ground_normals, wall_normals))
    scale, rotation, translation, icp = point_to_plane_icp(
        sfm, targets, normals, distance=distance, iterations=iterations, min_overlap=min_overlap)
    moved = scale * sfm @ rotation.T + translation
    constraints = constraint_report(moved, targets, normals, distance)
    if constraints["relative_weakest"] < min_constraint:
        raise ValueError(
            "Recalage non contraint dans la direction "
            f"{max(constraints['weakest_direction'], key=lambda k: abs(constraints['weakest_direction'][k]))} "
            f"(valeur propre relative {constraints['relative_weakest']:.2e} < {min_constraint}) : "
            "ajouter des façades visibles ou une autre emprise LiDAR")
    report = {"icp": icp, "constraints": constraints,
              "targets": {"ground": len(ground_targets), "walls": len(wall_targets)}}
    return scale, rotation, translation, report


def crop(points, low, high):
    return points[((points >= low) & (points <= high)).all(axis=1)]


def run(args):
    if args.output.exists():
        raise ValueError("Sortie déjà présente ; choisir un nouveau dossier")
    positive = (args.normal_voxel, args.max_correspondence_distance,
                args.margin, args.max_points)
    if any(not np.isfinite(v) or v <= 0 for v in positive):
        raise ValueError("Tailles, distances et marges positives et finies requises")
    if (not 0 < args.min_overlap <= 1 or args.iterations < 1
            or not 0 <= args.max_wall_nz < 1 or not 0 < args.min_ground_nz <= 1
            or not 0 < args.min_constraint < 1):
        raise ValueError("Paramètres de filtrage ou de contrainte invalides")
    model = pycolmap.Reconstruction(args.model)
    if not model.is_valid():
        raise ValueError("Modèle COLMAP incohérent")
    sfm = np.array([point.xyz for point in model.points3D.values()])
    if len(sfm) < 6 or not np.isfinite(sfm).all():
        raise ValueError("Au moins six points sparse finis requis")
    reference, reference_path = reference_metadata(args.model, args.georef)
    if reference is None:
        raise ValueError("Référence ENU requise (georef_stats.json du modèle ou --georef)")
    lat_lon_height = reference["origin_lat_lon_height"]
    low, high = sfm.min(axis=0) - args.margin, sfm.max(axis=0) + args.margin
    origin = np.zeros(3)
    ground = crop(enu_lidar(load_lidar(args.lidar, origin, classes={args.ground_class},
                                       max_points=args.max_points), lat_lon_height, args.lidar_crs),
                  low, high)
    walls = crop(enu_lidar(load_lidar(args.lidar, origin, classes={args.wall_class},
                                      max_points=args.max_points), lat_lon_height, args.lidar_crs),
                 low, high)
    s, r, t, report = align_points(
        sfm, ground, walls, normal_voxel=args.normal_voxel,
        distance=args.max_correspondence_distance, iterations=args.iterations,
        min_overlap=args.min_overlap, min_ground_nz=args.min_ground_nz,
        max_wall_nz=args.max_wall_nz, min_constraint=args.min_constraint)
    model.transform(similarity(s, r, t))
    if not model.is_valid():
        raise ValueError("Modèle transformé incohérent : export refusé")
    args.output.mkdir(parents=True)
    model.write(args.output)
    reloaded = pycolmap.Reconstruction(args.output)
    if not reloaded.is_valid() or reloaded.num_points3D() != model.num_points3D():
        raise ValueError("Validation du modèle exporté échouée")
    report.update(scale=s, rotation=r.tolist(), translation=t.tolist(),
                  convention="xyz_out_local = scale * R @ xyz_in + translation",
                  output_reference=reference,
                  reference_source=None if reference_path is None else str(reference_path),
                  points_before=len(sfm), points_after=model.num_points3D(),
                  method="icp_sim3_point_to_point_with_point_to_plane_constraints")
    write_report(args.output/"lidar_alignment_sfm.json", report)
    write_report(args.output/"georef_stats.json", {"output_reference": reference,
                                                  "source": "lidar_alignment_sfm.json"})
    print(f"[lidar] Points SfM recalés : {args.output}", flush=True)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--lidar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--georef", type=Path, help="Rapport ENU ; défaut : georef_stats.json du modèle")
    parser.add_argument("--lidar-crs", default="EPSG:2154")
    parser.add_argument("--ground-class", type=int, default=2)
    parser.add_argument("--wall-class", type=int, default=6)
    parser.add_argument("--margin", type=float, default=10, help="Marge autour de l'emprise SfM, mètres")
    parser.add_argument("--max-points", type=int, default=10_000_000,
                        help="Limite de points LiDAR lus par classe (RAM)")
    parser.add_argument("--normal-voxel", type=float, default=1.0,
                        help="Taille de voxel pour estimer les normales LiDAR")
    parser.add_argument("--max-wall-nz", type=float, default=0.5,
                        help="Murs : |n_z| maximal (écarte toits et pentes)")
    parser.add_argument("--min-ground-nz", type=float, default=0.8,
                        help="Sol : |n_z| minimal")
    parser.add_argument("--max-correspondence-distance", type=float, default=5)
    parser.add_argument("--min-overlap", type=float, default=0.3)
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--min-constraint", type=float, default=1e-3,
                        help="Valeur propre relative minimale de la contrainte point-à-plan")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        parser.exit(1, f"[ERREUR] Recalage points SfM/LiDAR : {error}\n")


if __name__ == "__main__":
    main()
