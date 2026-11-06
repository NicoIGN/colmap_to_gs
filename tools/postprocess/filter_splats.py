#!/usr/bin/env python3
"""Conservative, scale-relative peripheral Gaussian filtering."""

import argparse
import json
import os
from pathlib import Path
import struct
import tempfile

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation


SPLAT_DTYPE = np.dtype([
    ("position", "<f4", (3,)), ("scale", "<f4", (3,)),
    ("rgba", "u1", (4,)), ("rotation", "u1", (4,)),
])


def contextual_mask(means, log_scales, quats, *, size_factor=10.0,
                    neighbors=24, reference_limit=100000, batch_size=20000, seed=0):
    """Return suspicious indices; units cancel in every geometric comparison.

    Quaternions are w,x,y,z. Surface support comes from Gaussian centres,
    not from sparse COLMAP points. No semantic road classification is implied.
    """
    count = len(means)
    if means.shape != (count, 3) or log_scales.shape != (count, 3) or quats.shape != (count, 4):
        raise ValueError("Positions, échelles et rotations de dimensions incompatibles")
    if count == 0:
        raise ValueError("Aucun splat à filtrer")
    if not np.isfinite(size_factor) or size_factor <= 1:
        raise ValueError("Le facteur de taille locale doit être fini et > 1")
    if neighbors < 8 or reference_limit < neighbors + 1 or batch_size < 1:
        raise ValueError("Voisins >= 8, référence >= voisins+1 et lot >= 1 requis")
    for array in (means, log_scales, quats):
        if not np.isfinite(array).all():
            raise ValueError("Attributs non finis : filtrage contextuel impossible")
    if np.any(np.max(np.abs(quats), axis=1) == 0):
        raise ValueError("Quaternion nul : filtrage contextuel impossible")
    rejected = np.zeros(count, dtype=bool)
    report = dict(total=count, large=0, peripheral_large=0, supported_large=0,
                  proposed_rejections=0, size_factor=size_factor, neighbors=neighbors,
                  reference_limit=reference_limit, seed=seed,
                  boundary_fraction=0.9, extent_quantiles=[0.02, 0.98])
    if count <= neighbors:
        report["warning"] = "Trop peu de splats pour estimer un contexte ; aucun rejet géométrique."
        return rejected, report

    rng = np.random.default_rng(seed)
    indices = np.sort(rng.choice(count, min(count, reference_limit), replace=False))
    reference = np.asarray(means[indices], dtype=np.float64)
    origin = np.median(reference, axis=0)
    radius = np.linalg.norm(reference - origin, axis=1)
    extent = float(np.quantile(radius, 0.95))
    if extent <= 0 or not np.isfinite(extent):
        report["warning"] = "Étendue robuste indéterminable ; aucun rejet géométrique."
        return rejected, report
    reference = (reference - origin) / extent
    core = reference[radius <= extent]
    eigenvalues, axes = np.linalg.eigh(np.cov(core.T))
    active = eigenvalues > eigenvalues[-1] * 1e-4
    projected = reference @ axes
    lo, hi = np.quantile(projected, [0.02, 0.98], axis=0)
    half_width = (hi - lo) / 2
    active &= half_width > 0
    if np.count_nonzero(active) < 2:
        report["warning"] = "Dimensions actives indéterminables ; aucun rejet géométrique."
        return rejected, report
    middle = (hi + lo) / 2
    tree = cKDTree(reference)
    sizes = np.max(log_scales[indices], axis=1)

    for start in range(0, count, batch_size):
        stop = min(start + batch_size, count)
        positions = (means[start:stop] - origin) / extent
        distances, near = tree.query(positions, k=neighbors + 1)
        # Exclude the queried Gaussian itself, including within a duplicate cluster.
        distances[indices[near] == np.arange(start, stop)[:, None]] = np.inf
        order = np.argsort(distances, axis=1)[:, :neighbors]
        near = np.take_along_axis(near, order, axis=1)
        local_size = np.median(sizes[near], axis=1)
        large = np.max(log_scales[start:stop], axis=1) > local_size + np.log(size_factor)
        frame = positions @ axes
        peripheral = np.any(
            np.abs(frame[:, active] - middle[active]) >= 0.9 * half_width[active],
            axis=1,
        )
        candidates = np.flatnonzero(large & peripheral)
        report["large"] += int(large.sum())
        report["peripheral_large"] += len(candidates)
        if not len(candidates):
            continue
        cloud = reference[near[candidates]]
        centroid = cloud.mean(axis=1)
        centered = cloud - centroid[:, None, :]
        covariance = np.einsum("nki,nkj->nij", centered, centered) / neighbors
        values, vectors = np.linalg.eigh(covariance)
        normal = vectors[:, :, 0]
        planar = (values[:, 0] <= 0.05 * values[:, 2]) & (values[:, 1] > 0.05 * values[:, 2])
        plane_distance = np.abs(np.einsum(
            "ni,ni->n", positions[candidates] - centroid, normal,
        ))
        on_surface = plane_distance <= 0.25 * np.sqrt(np.maximum(values[:, 1], 0))
        q = quats[start:stop][candidates]
        q = q / np.max(np.abs(q), axis=1, keepdims=True)
        rotation = Rotation.from_quat(q[:, [1, 2, 3, 0]]).as_matrix()
        candidate_scales = log_scales[start:stop][candidates]
        minor = np.argmin(candidate_scales, axis=1)
        splat_normal = rotation[np.arange(len(candidates)), :, minor]
        aligned = np.abs(np.einsum("ni,ni->n", normal, splat_normal)) >= np.cos(np.pi / 6)
        flat = candidate_scales.min(axis=1) - candidate_scales.max(axis=1) <= np.log(0.2)
        supported = planar & on_surface & aligned & flat
        report["supported_large"] += int(supported.sum())
        rejected[start + candidates[~supported]] = True
    report["proposed_rejections"] = int(rejected.sum())
    return rejected, report


def load_sparse_points(path):
    """Read finite XYZ from a COLMAP binary points3D.bin."""
    points = []
    with open(path, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError(f"points3D.bin tronqué : {path}")
        count = struct.unpack("<Q", header)[0]
        record = struct.Struct("<QdddBBBdQ")
        for index in range(count):
            data = stream.read(record.size)
            if len(data) != record.size:
                raise ValueError(f"points3D.bin tronqué au point {index} : {path}")
            values = record.unpack(data)
            following = stream.tell() + values[-1] * 8
            if following > size:
                raise ValueError(f"Track COLMAP tronqué au point {index} : {path}")
            stream.seek(following)
            points.append(values[1:4])
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    return points[np.isfinite(points).all(axis=1)]


def sparse_bbox_mask(means, points, *, quantile=0.005, margin=0.05):
    """Return Gaussians whose centre lies outside the robust sparse-point box.

    The box is oriented on the sparse principal axes and bounded by
    per-axis quantiles; the margin is a fraction of its largest side, so the
    test is invariant to rotation, translation and global scale. Only centres
    are tested, so a large road splat whose centre is inside is kept.
    """
    if not 0 <= quantile < 0.5:
        raise ValueError("Quantile de bbox sparse attendu dans [0,0.5)")
    if not np.isfinite(margin) or margin < 0:
        raise ValueError("Marge relative de bbox sparse attendue finie et >= 0")
    if len(points) < 10:
        raise ValueError("Moins de 10 points sparse valides : bbox impossible")
    origin = np.median(points, axis=0)
    _, axes = np.linalg.eigh(np.cov((points - origin).T))
    projected = (points - origin) @ axes
    lo, hi = np.quantile(projected, [quantile, 1 - quantile], axis=0)
    span = float(np.max(hi - lo))
    if not np.isfinite(span) or span <= 0:
        raise ValueError("Points sparse dégénérés : bbox impossible")
    pad = margin * span
    frame = (means - origin) @ axes
    outside = np.any((frame < lo - pad) | (frame > hi + pad), axis=1)
    corners = np.array([[x, y, z] for x in (lo[0] - pad, hi[0] + pad)
                        for y in (lo[1] - pad, hi[1] + pad)
                        for z in (lo[2] - pad, hi[2] + pad)]) @ axes.T + origin
    report = dict(
        sparse_points=len(points), quantile=quantile, margin_fraction=margin,
        outside_sparse_bbox=int(outside.sum()),
        world_aabb_of_box=[corners.min(axis=0).tolist(), corners.max(axis=0).tolist()],
    )
    return outside, report


def sparse_support_mask(means, scales, quats, points, *, distance_factor=3.0,
                        min_fraction=0.5, batch_size=20000):
    """Sample the central principal-plane patch, not just the splat centre.

    Nine samples span +/- one sigma along the two largest axes. Distances
    are divided by sparse local spacing (eighth neighbour / sqrt(8)).
    No absolute scene-unit threshold or colour/height classification is used.
    """
    if not np.isfinite(distance_factor) or distance_factor <= 0:
        raise ValueError("Facteur de distance sparse attendu fini et > 0")
    if not np.isfinite(min_fraction) or not 0 < min_fraction <= 1:
        raise ValueError("Fraction de support sparse attendue dans ]0,1]")
    if batch_size < 1:
        raise ValueError("Taille de lot attendue >= 1")
    points = np.unique(points, axis=0)
    if len(points) < 10 or not np.isfinite(points).all():
        raise ValueError("Au moins 10 points sparse distincts et finis requis")
    origin = np.median(points, axis=0)
    extent = np.linalg.norm(points - origin, axis=1).max()
    if not np.isfinite(extent) or extent <= 0:
        raise ValueError("Étendue sparse indéterminable")
    points = (points - origin) / extent
    tree = cKDTree(points)
    spacing = tree.query(points, k=9)[0][:, -1] / np.sqrt(8)
    offsets = np.array([(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1)])
    rejected = np.zeros(len(means), dtype=bool)
    centre_only = 0
    for start in range(0, len(means), batch_size):
        stop = min(start + batch_size, len(means))
        q = quats[start:stop]
        q = q / np.max(np.abs(q), axis=1, keepdims=True)
        rotation = Rotation.from_quat(q[:, [1, 2, 3, 0]]).as_matrix()
        axes = np.argsort(scales[start:stop], axis=1)[:, -2:]
        directions = np.take_along_axis(rotation, axes[:, None, :], axis=2)
        lengths = np.take_along_axis(scales[start:stop], axes, axis=1) / extent
        samples = ((means[start:stop] - origin) / extent)[:, None, :] + np.einsum(
            "nij,nj,kj->nki", directions, lengths, offsets,
        )
        distances, near = tree.query(samples.reshape(-1, 3))
        supported = (distances <= distance_factor * spacing[near]).reshape(-1, 9)
        rejected[start:stop] = supported.mean(axis=1) < min_fraction
        centre_only += int((supported[:, 4] & rejected[start:stop]).sum())
    return rejected, dict(
        unsupported=int(rejected.sum()), centre_supported_but_patch_unsupported=centre_only,
        sparse_points=len(points), distance_factor=distance_factor,
        min_fraction=min_fraction, samples=9, patch_sigma=1,
        warning="Filtre expérimental : les surfaces peu représentées dans le sparse peuvent être rejetées.",
    )


def write_records(path, records, keep, batch_size=200000):
    """Atomically stream a selected subset, including an empty diagnostic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            for start in range(0, len(records), batch_size):
                stream.write(records[start:start + batch_size][keep[start:start + batch_size]].tobytes())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=("diagnostic", "apply", "off"), default="diagnostic")
    parser.add_argument("--size-factor", type=float, default=10.0)
    parser.add_argument("--neighbors", type=int, default=24)
    parser.add_argument("--reference-limit", type=int, default=100000)
    parser.add_argument("--batch-size", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-removal-fraction", type=float, default=0.2)
    parser.add_argument("--sparse-points", type=Path,
                        help="COLMAP points3D.bin in the same world frame as the splats")
    parser.add_argument("--sparse-bbox-mode", choices=("apply", "diagnostic", "off"),
                        default="apply")
    parser.add_argument("--sparse-bbox-quantile", type=float, default=0.005)
    parser.add_argument("--sparse-bbox-margin", type=float, default=0.05,
                        help="Margin as a fraction of the largest box side")
    parser.add_argument("--sparse-support-mode", choices=("off", "diagnostic", "apply"),
                        default="off")
    parser.add_argument("--sparse-support-distance-factor", type=float, default=3.0)
    parser.add_argument("--sparse-support-min-fraction", type=float, default=0.5)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        source = args.input.resolve()
        output = args.output.resolve()
        rejected_path = output.with_suffix(".rejected.splat")
        report_path = output.with_suffix(".filter.json")
        outputs = (output, rejected_path, report_path)
        if len(set(outputs)) != 3 or any(
            p == source or (p.exists() and os.path.samefile(p, source)) for p in outputs
        ):
            raise ValueError("Les sorties doivent être distinctes de l'entrée")
        if any(p.exists() for p in outputs) and not args.overwrite:
            raise ValueError("Sortie existante : utiliser --overwrite")
        if not 0 <= args.max_removal_fraction <= 1:
            raise ValueError("Fraction maximale de rejet attendue dans [0,1]")
        if args.batch_size < 1:
            raise ValueError("Taille de lot attendue >= 1")
        size = source.stat().st_size
        if size == 0 or size % SPLAT_DTYPE.itemsize:
            raise ValueError("SPLAT vide ou taille non multiple de 32 octets")
        records = np.memmap(source, dtype=SPLAT_DTYPE, mode="r")
        means = records["position"].astype(np.float64)
        scales = records["scale"].astype(np.float64)
        quats = (records["rotation"].astype(np.float64) - 128) / 128
        if (not np.isfinite(scales).all() or np.any(scales <= 0) or
                not np.isfinite(means).all() or np.any(np.max(np.abs(quats), axis=1) == 0)):
            raise ValueError("Attributs SPLAT invalides ; aucune sortie remplacée")
        count = len(records)
        if args.sparse_support_mode != "off" and not args.sparse_points:
            raise ValueError("--sparse-points requis pour le filtre de support")
        points = None
        if args.sparse_points and (
            args.sparse_bbox_mode != "off" or args.sparse_support_mode != "off"
        ):
            points = load_sparse_points(args.sparse_points.resolve())
        bbox_mode = args.sparse_bbox_mode if args.sparse_points else "off"
        outside = np.zeros(count, dtype=bool)
        bbox_report = None
        if bbox_mode != "off":
            outside, bbox_report = sparse_bbox_mask(
                means, points,
                quantile=args.sparse_bbox_quantile, margin=args.sparse_bbox_margin,
            )
            bbox_report.update(mode=bbox_mode, points=str(args.sparse_points.resolve()))
            if outside.all():
                raise ValueError(
                    "Aucun centre de splat dans la bbox sparse : repère des points "
                    "COLMAP différent de celui des splats ? Aucune sortie remplacée"
                )
        contextual = np.zeros(count, dtype=bool)
        if args.mode == "off":
            report = dict(proposed_rejections=0)
        else:
            inside = np.flatnonzero(~outside)
            subset, report = contextual_mask(
                means[inside], np.log(scales[inside]), quats[inside],
                size_factor=args.size_factor, neighbors=args.neighbors,
                reference_limit=args.reference_limit, batch_size=args.batch_size,
                seed=args.seed,
            )
            contextual[inside[subset]] = True
            report["contextual_input"] = report.pop("total")
        report["contextual_rejections"] = report.pop("proposed_rejections")
        unsupported = np.zeros(count, dtype=bool)
        support_report = dict(mode="off")
        if args.sparse_support_mode != "off":
            inside = np.flatnonzero(~outside)
            subset, support_report = sparse_support_mask(
                means[inside], scales[inside], quats[inside], points,
                distance_factor=args.sparse_support_distance_factor,
                min_fraction=args.sparse_support_min_fraction,
                batch_size=args.batch_size,
            )
            unsupported[inside[subset]] = True
            support_report["mode"] = args.sparse_support_mode
            print("[AVERTISSEMENT] " + support_report["warning"], flush=True)
        rejected = outside | contextual | unsupported
        applied = np.zeros(count, dtype=bool)
        if args.mode == "apply":
            applied |= contextual
        if bbox_mode == "apply":
            applied |= outside
        if args.sparse_support_mode == "apply":
            applied |= unsupported
        report.update(
            total=count, mode=args.mode, input=str(source), output=str(output),
            sparse_bbox=bbox_report or dict(mode="off"),
            sparse_support=support_report,
            proposed_rejections=int(rejected.sum()),
            proposed_fraction=float(rejected.mean()),
            applied_rejections=int(applied.sum()),
        )
        fraction = float(applied.mean())
        print("[filtre] " + json.dumps(report, ensure_ascii=False), flush=True)
        if "warning" in report:
            print("[AVERTISSEMENT] " + report["warning"], flush=True)
        if fraction > args.max_removal_fraction:
            raise ValueError(
                f"Rejet appliqué {fraction:.1%} > limite {args.max_removal_fraction:.1%} ; "
                "aucune sortie remplacée. Examiner d'abord le mode diagnostic."
            )
        if applied.all():
            raise ValueError("Le filtre supprimerait tous les splats ; aucune sortie remplacée")
        write_records(rejected_path, records, rejected)
        write_records(output, records, ~applied)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[ERREUR] Filtre contextuel : {exc}\n")


if __name__ == "__main__":
    main()
