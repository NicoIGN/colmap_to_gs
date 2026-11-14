#!/usr/bin/env python3
"""Filtrer les gaussiennes dont l'ellipsoïde orienté est sans support LiDAR proche."""

import argparse
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from common import (class_codes, gaussian_geometry, load_lidar, open_scene,
                    support_radii, validate_support_opacity, vector, write_report, write_scene)


def ground_points(path, origin):
    """Read original class-2 returns, independently of reference-class filtering."""
    import laspy

    clouds = []
    with laspy.open(path) as reader:
        for chunk in reader.chunk_iterator(100000):
            selected = np.asarray(chunk.classification) == 2
            xyz = np.column_stack((chunk.x, chunk.y, chunk.z))[selected] - origin
            if not np.isfinite(xyz).all():
                raise ValueError("Coordonnées du sol LiDAR non finies")
            if len(xyz):
                clouds.append(xyz)
    return np.concatenate(clouds) if clouds else np.empty((0, 3))


def validate_aerial_options(ground_clearance, cell_size, min_returns):
    if (not np.isfinite(ground_clearance) or ground_clearance < 0
            or not np.isfinite(cell_size) or cell_size <= 0 or min_returns < 1):
        raise ValueError("Protection sol >= 0, cellule > 0 et retours >= 1 requis")


def aerial_guard(scene, keep, lidar, ground, *, sigma=3., min_opacity=None,
                 ground_clearance=2., cell_size=1., min_returns=3, distance=0.5):
    """Only reject unsupported Gaussians wholly above locally observed aerial surfaces."""
    validate_aerial_options(ground_clearance, cell_size, min_returns)
    if (lidar.ndim != 2 or lidar.shape[1] != 3 or not np.isfinite(lidar).all()
            or ground.ndim != 2 or ground.shape[1] != 3 or not np.isfinite(ground).all()):
        raise ValueError("Nuages LiDAR/sol invalides pour la visibilité aérienne")
    lidar = np.unique(lidar, axis=0)
    if not len(lidar):
        raise ValueError("Couverture LiDAR vide")
    base = lidar[:, :2].min(axis=0)
    indices = np.floor((lidar[:, :2]-base)/cell_size).astype(np.int64)
    columns = {}
    for key, z in zip(map(tuple, indices), lidar[:, 2]):
        height, count = columns.get(key, (-np.inf, 0))
        columns[key] = (max(height, float(z)), count+1)
    ground_tree = cKDTree(ground[:, :2]) if len(ground) else None
    stats = dict(aerial_rejected=0, protected_near_ground=0,
                 protected_below_cover=0, protected_unknown=0,
                 protected_no_opaque_support=0, ground_points=len(ground),
                 ground_clearance=ground_clearance, aerial_cell_size=cell_size,
                 aerial_min_returns=min_returns)
    result = keep.copy()
    for start in range(0, scene.count, 10000):
        xyz, scales, rotations = gaussian_geometry(scene, slice(start, start+10000))
        radii, exists = support_radii(scene, slice(start, start+10000), sigma, min_opacity)
        extent = radii[:, None] * np.sqrt(np.sum(
            (rotations * scales[:, None, :])**2, axis=2))
        for i in np.flatnonzero(~keep[start:start+len(xyz)]):
            reason = None
            bottom = xyz[i, 2]-extent[i, 2]
            lower = np.floor((xyz[i, :2]-extent[i, :2]-distance-base)/cell_size).astype(np.int64)
            upper = np.floor((xyz[i, :2]+extent[i, :2]+distance-base)/cell_size).astype(np.int64)
            # Missing coverage or excessively large supports are unknown, never negative evidence.
            count = float(upper[0]-lower[0]+1)*float(upper[1]-lower[1]+1)
            if not exists[i]:
                reason = "protected_no_opaque_support"
            elif ground_tree is None or count > 10000:
                reason = "protected_unknown"
            else:
                for x in range(int(lower[0]), int(upper[0])+1):
                    for y in range(int(lower[1]), int(upper[1])+1):
                        cell = columns.get((x, y))
                        if cell is None or cell[1] < min_returns:
                            reason = "protected_unknown"
                            break
                        center = base + (np.array([x, y])+0.5)*cell_size
                        ground_ids = ground_tree.query_ball_point(center, 2*cell_size)
                        if not ground_ids:
                            reason = "protected_unknown"
                            break
                        if bottom <= ground[ground_ids, 2].max()+ground_clearance:
                            reason = "protected_near_ground"
                            break
                        # Include neighbours to avoid falsely carving below roof/canopy edges.
                        top = max(columns.get((x+dx, y+dy), (-np.inf, 0))[0]
                                  for dx in (-1, 0, 1) for dy in (-1, 0, 1))
                        if bottom <= top+distance:
                            reason = "protected_below_cover"
                            break
                    if reason:
                        break
            if reason:
                result[start+i] = True
                stats[reason] += 1
            else:
                stats["aerial_rejected"] += 1
    stats["visibility_model"] = "vertical_columns_not_acquisition_rays"
    return result, stats


def ellipsoid_distances(local_points, axes):
    """Euclidean distance to a solid ellipsoid, solved by Lagrange bisection."""
    if np.all(axes == 0):
        return np.linalg.norm(local_points, axis=1)
    inside = np.sum((local_points / axes) ** 2, axis=1) <= 1
    result = np.zeros(len(local_points))
    p = np.abs(local_points[~inside])
    if not len(p):
        return result
    squared = axes ** 2
    low = np.zeros(len(p))
    high = np.linalg.norm(p * axes, axis=1)
    for _ in range(60):
        middle = (low + high) / 2
        equation = np.sum((axes * p / (squared + middle[:, None])) ** 2, axis=1)
        low = np.where(equation > 1, middle, low)
        high = np.where(equation > 1, high, middle)
    closest = squared * p / (squared + high[:, None])
    result[~inside] = np.linalg.norm(p - closest, axis=1)
    return result


def support_mask(scene, lidar, distance, *, sigma=3., spacing_factor=1., bbox=None,
                 min_opacity=None):
    if (not np.isfinite(distance) or distance <= 0 or not np.isfinite(sigma) or sigma <= 0
            or not np.isfinite(spacing_factor) or spacing_factor < 0):
        raise ValueError("Distance/sigma finis > 0 et facteur de densité fini >= 0 requis")
    validate_support_opacity(min_opacity)
    lidar = np.unique(lidar, axis=0)
    if len(lidar) < 3 or not np.isfinite(lidar).all():
        raise ValueError("Au moins trois points LiDAR distincts et finis requis")
    tree = cKDTree(lidar)
    keep = np.ones(scene.count, dtype=bool)
    tested = centre_far_kept = below_opacity = 0
    for start in range(0, scene.count, 10000):
        xyz, scales, rotations = gaussian_geometry(scene, slice(start, start+10000))
        nearest_distances, nearest = tree.query(xyz)
        neighbors = min(7, len(lidar))
        spacing = tree.query(lidar[nearest], k=neighbors)[0][:, -1] / np.sqrt(neighbors-1)
        tolerances = distance + spacing_factor * spacing
        radii, exists = support_radii(scene, slice(start, start+10000), sigma, min_opacity)
        axes = radii[:, None] * scales
        inside = (np.ones(len(xyz), dtype=bool) if bbox is None else
                  ((xyz >= bbox[:3]) & (xyz <= bbox[3:])).all(axis=1))
        for i in np.flatnonzero(inside):
            tested += 1
            if not exists[i]:
                keep[start+i] = False
                below_opacity += 1
                continue
            if nearest_distances[i] <= tolerances[i]:
                continue
            candidates = tree.query_ball_point(xyz[i], axes[i].max() + tolerances[i])
            supported = False
            for offset in range(0, len(candidates), 10000):
                points = lidar[candidates[offset:offset+10000]]
                local = (points - xyz[i]) @ rotations[i]
                if np.any(ellipsoid_distances(local, axes[i]) <= tolerances[i] + 1e-10):
                    supported = True
                    break
            keep[start+i] = supported
            centre_far_kept += int(supported and nearest_distances[i] > distance)
    return keep, dict(tested=tested, centre_far_kept=centre_far_kept,
                      criterion="distance_to_oriented_solid_ellipsoid",
                      sigma=sigma, spacing_factor=spacing_factor,
                      min_support_opacity=min_opacity, below_support_opacity=below_opacity,
                      spacing_neighbors=min(7, len(lidar))-1)


def distance_mask(points, lidar, distance, bbox=None):
    if not np.isfinite(distance) or distance <= 0:
        raise ValueError("Distance attendue finie et > 0")
    tree = cKDTree(lidar)
    keep = np.ones(len(points), dtype=bool)
    tested = 0
    for start in range(0, len(points), 100000):
        chunk = points[start:start+100000]
        inside = (np.ones(len(chunk), dtype=bool) if bbox is None else
                  ((chunk >= bbox[:3]) & (chunk <= bbox[3:])).all(axis=1))
        tested += int(inside.sum())
        keep[start:start+len(chunk)][inside] = tree.query(chunk[inside])[0] <= distance
    return keep, tested


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lidar", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", default="0,0,0", help="Origine des splats soustraite au LAZ")
    parser.add_argument("--max-distance", type=float, required=True)
    parser.add_argument("--support-sigma", type=float, default=3)
    parser.add_argument("--min-support-opacity", type=float,
                        help="Opacité locale minimale dans ]0,1] ; défaut : désactivé")
    parser.add_argument("--spacing-factor", type=float, default=1,
                        help="Tolérance additionnelle = facteur * espacement LiDAR local")
    parser.add_argument("--evidence-mode", choices=("aerial", "proximity"), default="aerial")
    parser.add_argument("--ground-clearance", type=float, default=2)
    parser.add_argument("--aerial-cell-size", type=float, default=1)
    parser.add_argument("--aerial-min-returns", type=int, default=3)
    parser.add_argument("--lidar-stride", type=int, default=1)
    parser.add_argument("--lidar-classes", help="Codes LAS de référence ; défaut : tous")
    parser.add_argument("--bbox", help="Zone locale xmin,ymin,zmin,xmax,ymax,zmax ; hors zone conservé")
    parser.add_argument("--rejected-output", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Rapport seul, sans écrire de scène")
    args = parser.parse_args()
    scene = None
    try:
        report_path = args.output.with_suffix(args.output.suffix + ".json")
        destinations = [args.output, report_path]
        if args.rejected_output:
            destinations.append(args.rejected_output)
        if len({p.resolve() for p in destinations}) != len(destinations):
            raise ValueError("Chemins de sortie distincts requis")
        if any(p.exists() for p in destinations):
            raise ValueError("Une sortie existe déjà")
        for path in (args.output, args.rejected_output):
            if path is not None and path.suffix.lower() != args.input.suffix.lower():
                raise ValueError("Les scènes de sortie doivent conserver le format de l'entrée")
        origin = vector(args.origin)
        validate_aerial_options(args.ground_clearance, args.aerial_cell_size, args.aerial_min_returns)
        validate_support_opacity(args.min_support_opacity)
        classes = None if args.lidar_classes is None else class_codes(args.lidar_classes)
        bbox = None
        if args.bbox:
            bbox = np.array([float(v) for v in args.bbox.split(",")])
            if (bbox.shape != (6,) or not np.isfinite(bbox).all()
                    or np.any(bbox[:3] >= bbox[3:])):
                raise ValueError("BBox : six coordonnées finies et min < max requis")
        scene = open_scene(args.input)
        lidar = load_lidar(args.lidar, origin, args.lidar_stride, classes=classes)
        keep, support = support_mask(scene, lidar, args.max_distance,
                                     sigma=args.support_sigma, spacing_factor=args.spacing_factor,
                                     bbox=bbox, min_opacity=args.min_support_opacity)
        if args.evidence_mode == "aerial":
            # Use all classes and original density for covering structures, not the filtered references.
            coverage = load_lidar(args.lidar, origin)
            ground = ground_points(args.lidar, origin)
            keep, aerial = aerial_guard(
                scene, keep, coverage, ground, sigma=args.support_sigma,
                min_opacity=args.min_support_opacity, ground_clearance=args.ground_clearance,
                cell_size=args.aerial_cell_size, min_returns=args.aerial_min_returns,
                distance=args.max_distance)
            support.update(aerial)
            if not len(ground):
                print("[AVERTISSEMENT] Aucun sol classé 2 : les rejets aériens sont "
                      "indéterminés et les splats sans support sont conservés.")
        report = dict(total=scene.count, **support, kept=int(keep.sum()),
                      evidence_mode=args.evidence_mode,
                      rejected=int((~keep).sum()), max_distance=args.max_distance,
                      origin=origin.tolist(), lidar_points=len(lidar),
                      lidar_classes=None if classes is None else sorted(classes),
                      bbox=None if bbox is None else bbox.tolist(), dry_run=args.dry_run,
                      warning="Absence de LiDAR ne prouve pas un artefact ; vérifier couverture et dates.")
        if not args.dry_run:
            write_scene(args.output, scene, keep)
            if args.rejected_output:
                write_scene(args.rejected_output, scene, ~keep)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_report(report_path, report)
        print(f"[lidar] {report['rejected']}/{scene.count} splats proposés au rejet. "
              + report["warning"])
        if classes is not None:
            print("[AVERTISSEMENT] Classes LiDAR restreintes : les objets des autres "
                  "classes peuvent être rejetés ; contrôler le diagnostic avant suppression.")
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"[ERREUR] Filtrage LiDAR : {error}\n")
    finally:
        if scene is not None:
            scene.close()


if __name__ == "__main__":
    main()
