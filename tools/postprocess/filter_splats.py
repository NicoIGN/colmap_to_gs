#!/usr/bin/env python3
"""Conservative, scale-relative peripheral Gaussian filtering."""

import argparse
import json
import os
from pathlib import Path
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
        if args.mode == "off":
            rejected = np.zeros(len(records), dtype=bool)
            report = dict(total=len(records), proposed_rejections=0)
        else:
            rejected, report = contextual_mask(
                means, np.log(scales), quats, size_factor=args.size_factor,
                neighbors=args.neighbors, reference_limit=args.reference_limit,
                batch_size=args.batch_size, seed=args.seed,
            )
        report.update(mode=args.mode, input=str(source), output=str(output))
        fraction = float(rejected.mean())
        report["proposed_fraction"] = fraction
        report["applied_rejections"] = int(rejected.sum()) if args.mode == "apply" else 0
        print("[filtre] " + json.dumps(report, ensure_ascii=False), flush=True)
        if "warning" in report:
            print("[AVERTISSEMENT] " + report["warning"], flush=True)
        if args.mode == "apply" and fraction > args.max_removal_fraction:
            raise ValueError(
                f"Rejet proposé {fraction:.1%} > limite {args.max_removal_fraction:.1%} ; "
                "aucune sortie remplacée. Examiner d'abord le mode diagnostic."
            )
        if args.mode == "apply" and rejected.all():
            raise ValueError("Le filtre supprimerait tous les splats ; aucune sortie remplacée")
        write_records(rejected_path, records, rejected)
        keep = ~rejected if args.mode == "apply" else np.ones(len(records), dtype=bool)
        write_records(output, records, keep)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[ERREUR] Filtre contextuel : {exc}\n")


if __name__ == "__main__":
    main()
