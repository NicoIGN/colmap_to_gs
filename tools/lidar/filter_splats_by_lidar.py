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


GRID_DTYPE = np.dtype([("x", "<i8"), ("y", "<i8")])
SPLAT_BATCH = 10000
MAX_SUPPORT_POINTS = 4096
MAX_BATCH_SUPPORT_POINTS = 50000
MAX_AERIAL_CELLS = 256
MAX_LIDAR_POINTS = 20000000


def grid_keys(indices):
    result = np.empty(len(indices), dtype=GRID_DTYPE)
    result["x"], result["y"] = indices.T
    return result


class HeightGrid:
    """Sparse sorted arrays, with no per-point Python objects or dense world raster."""
    def __init__(self, points, base, cell_size):
        if not len(points):
            self.keys = np.empty(0, dtype=GRID_DTYPE)
            self.heights = np.empty(0)
            self.counts = np.empty(0, dtype=np.int64)
            return
        indices = np.floor((points[:, :2]-base)/cell_size).astype(np.int64)
        keys = grid_keys(indices)
        order = np.argsort(keys)
        keys = keys[order]
        starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
        self.keys = keys[starts]
        self.heights = np.maximum.reduceat(points[order, 2], starts)
        self.counts = np.diff(np.r_[starts, len(points)])

    def query(self, indices):
        positions, valid = self.positions(indices)
        heights = np.full(len(indices), -np.inf)
        counts = np.zeros(len(indices), dtype=np.int64)
        heights[valid] = self.heights[positions[valid]]
        counts[valid] = self.counts[positions[valid]]
        return heights, counts

    def positions(self, indices):
        keys = grid_keys(indices)
        positions = np.searchsorted(self.keys, keys)
        valid = positions < len(self.keys)
        valid[valid] &= self.keys[positions[valid]] == keys[valid]
        return positions, valid


def aerial_surfaces(columns, ground_grid, ground, base, cell_size):
    tops = columns.heights.copy()
    ground_heights = np.full(len(tops), -np.inf)
    ground_tree = cKDTree(ground[:, :2])
    for start in range(0, len(tops), SPLAT_BATCH):
        end = min(start+SPLAT_BATCH, len(tops))
        keys = columns.keys[start:end]
        indices = np.column_stack((keys["x"], keys["y"]))
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                heights, _ = ground_grid.query(indices+[dx, dy])
                ground_heights[start:end] = np.maximum(ground_heights[start:end], heights)
                if abs(dx) <= 1 and abs(dy) <= 1:
                    heights, _ = columns.query(indices+[dx, dy])
                    tops[start:end] = np.maximum(tops[start:end], heights)
        centers = base+(indices+.5)*cell_size
        distances = ground_tree.query(centers, distance_upper_bound=2*cell_size+1e-10)[0]
        # Enlarging the height neighbourhood must not turn missing ground into negative evidence.
        ground_heights[start:end][~np.isfinite(distances)] = -np.inf
    return tops, ground_heights


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
    columns = HeightGrid(lidar, base, cell_size)
    ground_grid = HeightGrid(ground, base, cell_size)
    stats = dict(aerial_rejected=0, protected_near_ground=0,
                 protected_below_cover=0, protected_unknown=0,
                 protected_no_opaque_support=0, ground_points=len(ground),
                 ground_clearance=ground_clearance, aerial_cell_size=cell_size,
                 aerial_min_returns=min_returns, protected_cell_budget=0,
                 max_aerial_cells_per_splat=MAX_AERIAL_CELLS)
    result = keep.copy()
    if not len(ground):
        unknown = int((~keep).sum())
        stats["protected_unknown"] = unknown
        stats["visibility_model"] = "vertical_columns_not_acquisition_rays"
        return np.ones_like(keep), stats
    covering_tops, local_ground = aerial_surfaces(columns, ground_grid, ground, base, cell_size)
    for start in range(0, scene.count, 1000):
        rejected = np.flatnonzero(~keep[start:start+1000])
        if not len(rejected):
            continue
        selection = start+rejected
        xyz, scales, rotations = gaussian_geometry(scene, selection)
        radii, exists = support_radii(scene, selection, sigma, min_opacity)
        extent = radii[:, None] * np.hypot.reduce(rotations * scales[:, None, :], axis=2)
        bottom = xyz[:, 2]-extent[:, 2]
        lower_float = np.floor((xyz[:, :2]-extent[:, :2]-distance-base)/cell_size)
        upper_float = np.floor((xyz[:, :2]+extent[:, :2]+distance-base)/cell_size)
        invalid = (~np.isfinite(lower_float).all(axis=1)
                   | ~np.isfinite(upper_float).all(axis=1)
                   | (np.abs(lower_float) > 1e15).any(axis=1)
                   | (np.abs(upper_float) > 1e15).any(axis=1))
        lower = np.clip(np.nan_to_num(lower_float), -1e15, 1e15).astype(np.int64)
        upper = np.clip(np.nan_to_num(upper_float), -1e15, 1e15).astype(np.int64)
        width = upper-lower+1
        cells = width[:, 0].astype(float)*width[:, 1]
        budget = invalid | (cells > MAX_AERIAL_CELLS)
        stats["protected_cell_budget"] += int((budget & exists).sum())
        reason = np.zeros(len(selection), dtype=np.uint8)
        reason[~exists] = 1
        reason[budget & exists] = 2
        active = np.flatnonzero(reason == 0)
        if len(active):
            sizes = cells[active].astype(np.int64)
            owners = np.repeat(active, sizes)
            offsets = np.arange(len(owners))-np.repeat(np.cumsum(sizes)-sizes, sizes)
            indices = lower[owners] + np.column_stack(
                (offsets//width[owners, 1], offsets % width[owners, 1]))
            positions, valid = columns.positions(indices)
            tops = np.full(len(indices), -np.inf)
            ground_heights = np.full(len(indices), -np.inf)
            tops[valid] = covering_tops[positions[valid]]
            ground_heights[valid] = local_ground[positions[valid]]
            dense = np.zeros(len(indices), dtype=bool)
            dense[valid] = columns.counts[positions[valid]] >= min_returns
            unknown = ~dense | ~np.isfinite(ground_heights)
            near_ground = bottom[owners] <= ground_heights+ground_clearance
            covered = bottom[owners] <= tops+distance
            for code, condition in ((4, covered), (3, near_ground), (2, unknown)):
                reason[np.unique(owners[condition])] = code
        for code, name in ((1, "protected_no_opaque_support"), (2, "protected_unknown"),
                           (3, "protected_near_ground"), (4, "protected_below_cover")):
            stats[name] += int((reason == code).sum())
        result[selection[reason != 0]] = True
        stats["aerial_rejected"] += int((reason == 0).sum())
    stats["visibility_model"] = "vertical_columns_not_acquisition_rays"
    return result, stats


def ellipsoid_distances(local_points, axes):
    """Euclidean distance to a solid ellipsoid, solved by Lagrange bisection."""
    axes = np.broadcast_to(axes, local_points.shape)
    degenerate = np.all(axes == 0, axis=1)
    safe_axes = np.where(degenerate[:, None], 1., axes)
    inside = (np.sum((local_points / safe_axes) ** 2, axis=1) <= 1) & ~degenerate
    result = np.zeros(len(local_points))
    result[degenerate] = np.linalg.norm(local_points[degenerate], axis=1)
    outside = ~inside & ~degenerate
    p = np.abs(local_points[outside])
    axes = axes[outside]
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
    result[outside] = np.linalg.norm(p - closest, axis=1)
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
    tested = centre_far_kept = below_opacity = budget_kept = 0
    for start in range(0, scene.count, SPLAT_BATCH):
        xyz, scales, rotations = gaussian_geometry(scene, slice(start, start+SPLAT_BATCH))
        nearest_distances, nearest = tree.query(xyz)
        neighbors = min(7, len(lidar))
        spacing = (tree.query(lidar[nearest], k=neighbors)[0][:, -1] / np.sqrt(neighbors-1)
                   if spacing_factor else np.zeros(len(xyz)))
        tolerances = distance + spacing_factor * spacing
        radii, exists = support_radii(scene, slice(start, start+SPLAT_BATCH), sigma, min_opacity)
        axes = radii[:, None] * scales
        inside = (np.ones(len(xyz), dtype=bool) if bbox is None else
                  ((xyz >= bbox[:3]) & (xyz <= bbox[3:])).all(axis=1))
        tested += int(inside.sum())
        below_opacity += int((inside & ~exists).sum())
        supported = exists & (nearest_distances <= tolerances)
        possible = inside & exists & ~supported & (
            nearest_distances <= axes.max(axis=1)+tolerances)
        ids = np.flatnonzero(possible)
        local = np.einsum("ni,nij->nj", lidar[nearest[ids]]-xyz[ids], rotations[ids])
        supported[ids] = ellipsoid_distances(local, axes[ids]) <= tolerances[ids]+1e-10
        unresolved = np.flatnonzero(possible & ~supported)
        lengths = tree.query_ball_point(xyz[unresolved],
                                        axes[unresolved].max(axis=1)+tolerances[unresolved],
                                        return_length=True)
        order = np.argsort(lengths, kind="stable")
        affordable = np.zeros(len(lengths), dtype=bool)
        affordable[order] = ((np.cumsum(lengths[order]) <= MAX_BATCH_SUPPORT_POINTS)
                             & (lengths[order] <= MAX_SUPPORT_POINTS))
        heavy = unresolved[~affordable]
        supported[heavy] = True
        budget_kept += len(heavy)
        for i in unresolved[affordable]:
            candidates = tree.query_ball_point(xyz[i], axes[i].max()+tolerances[i])
            found = False
            for offset in range(0, len(candidates), 10000):
                points = lidar[candidates[offset:offset+10000]]
                local = (points - xyz[i]) @ rotations[i]
                if np.any(ellipsoid_distances(local, axes[i]) <= tolerances[i] + 1e-10):
                    found = True
                    break
            # The bounded list is complete, so a failed intersection is valid negative evidence.
            supported[i] = found
        keep[start:start+len(xyz)][inside] = supported[inside]
        centre_far_kept += int((inside & supported & (nearest_distances > distance)).sum())
    return keep, dict(tested=tested, centre_far_kept=centre_far_kept,
                      criterion="distance_to_oriented_solid_ellipsoid",
                      sigma=sigma, spacing_factor=spacing_factor,
                      min_support_opacity=min_opacity, below_support_opacity=below_opacity,
                      protected_support_budget=budget_kept,
                      max_support_points_per_splat=MAX_SUPPORT_POINTS,
                      max_support_points_per_batch=MAX_BATCH_SUPPORT_POINTS,
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
        if args.lidar_stride < 1:
            raise ValueError("Stride LiDAR attendu >= 1")
        coverage = (load_lidar(args.lidar, origin, max_points=MAX_LIDAR_POINTS)
                    if args.evidence_mode == "aerial" else None)
        lidar = (coverage[::args.lidar_stride] if coverage is not None and classes is None
                 else load_lidar(args.lidar, origin, args.lidar_stride,
                                 classes=classes, max_points=MAX_LIDAR_POINTS))
        keep, support = support_mask(scene, lidar, args.max_distance,
                                     sigma=args.support_sigma, spacing_factor=args.spacing_factor,
                                     bbox=bbox, min_opacity=args.min_support_opacity)
        if args.evidence_mode == "aerial":
            # Use all classes and original density for covering structures, not the filtered references.
            ground = ground_points(args.lidar, origin)
            keep, aerial = aerial_guard(
                scene, keep, coverage, ground, sigma=args.support_sigma,
                min_opacity=args.min_support_opacity, ground_clearance=args.ground_clearance,
                cell_size=args.aerial_cell_size, min_returns=args.aerial_min_returns,
                distance=args.max_distance)
            support.update(aerial)
            support["max_lidar_points"] = MAX_LIDAR_POINTS
            if not len(ground):
                print("[AVERTISSEMENT] Aucun sol classé 2 : les rejets aériens sont "
                      "indéterminés et les splats sans support sont conservés.")
        if support.get("protected_cell_budget", 0) or support["protected_support_budget"]:
            print("[AVERTISSEMENT] Supports coûteux conservés sans rejet : "
                  f"{support.get('protected_cell_budget', 0)} emprises dépassent le budget de cellules, "
                  f"{support['protected_support_budget']} dépassent le budget de voisins.")
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
