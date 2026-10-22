#!/usr/bin/env python3
import argparse
import json
import os
import struct
import tempfile
from pathlib import Path

import numpy as np
import torch
from plyfile import PlyData
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from gsplat import export_splats


def log(message):
    print(f"[INFO] {message}", flush=True)


def safe_read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise EOFError(f"Expected {size} bytes, got {len(data)}")
    return data


# ============================================================
# WORLD -> TRAIN TRANSFORM
# ============================================================
def load_transform(path):
    with open(path, "r", encoding="utf-8") as stream:
        data = json.load(stream)

    if not isinstance(data, dict):
        raise ValueError("Transform JSON must contain an object")

    if data.get("format") == "spirula-scene-transform":
        if data.get("version") != 1:
            raise ValueError("Unsupported Spirula transform version")

        tf = data.get("train_from_world")
        if not isinstance(tf, dict):
            raise ValueError("Missing train_from_world object")

        if "matrix_4x4" in tf:
            matrix = np.asarray(tf["matrix_4x4"], dtype=np.float64)
        else:
            rotation = np.asarray(
                tf["rotation"]["matrix_3x3"], dtype=np.float64
            )
            translation = np.asarray(tf["translation"], dtype=np.float64)
            scale = float(tf["scale"])

            if rotation.shape != (3, 3) or translation.shape != (3,):
                raise ValueError("Invalid Spirula rotation/translation shape")
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError("Spirula scale must be finite and positive")

            matrix = np.eye(4, dtype=np.float64)
            matrix[:3, :3] = scale * rotation
            matrix[:3, 3] = translation

        log("Transform: Spirula train_from_world")

    elif "transform" in data:
        matrix = np.asarray(data["transform"], dtype=np.float64)

        # Nerfstudio commonly stores a 3x4 affine matrix.
        if matrix.shape == (3, 4):
            matrix = np.vstack([matrix, [0.0, 0.0, 0.0, 1.0]])

        if matrix.shape != (4, 4):
            raise ValueError("Nerfstudio transform must be 3x4 or 4x4")

        scale = float(data.get("scale", 1.0))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("Nerfstudio scale must be finite and positive")

        # Preserve the convention in the original script:
        # train = scale * (transform_rotation @ world + translation)
        matrix = matrix.copy()
        matrix[:3, :] *= scale

        log("Transform: Nerfstudio dataparser")

    else:
        raise ValueError("Unsupported transform JSON format")

    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("Expected a finite 4x4 transformation matrix")

    if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8):
        raise ValueError("Expected an affine transformation")

    linear = matrix[:3, :3]
    singular_values = np.linalg.svd(linear, compute_uv=False)
    uniform_scale = float(singular_values.mean())

    if uniform_scale <= 0 or not np.allclose(
        singular_values, uniform_scale, rtol=1e-5, atol=1e-10
    ):
        raise ValueError(
            "Only uniform scale + rotation + translation are supported"
        )

    rotation = linear / uniform_scale

    if (
        not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
        or np.linalg.det(rotation) <= 0
    ):
        raise ValueError("Transform must contain a proper rotation, not a reflection")

    log(f"World -> train scale: {uniform_scale:.9g}")
    return matrix, rotation, uniform_scale


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
        log(f"Raw COLMAP points: {count}")

        for index in range(count):
            # ID, XYZ, RGB, reprojection error, track length.
            record = struct.unpack("<QdddBBBdQ", safe_read(stream, 51))
            xyz = record[1:4]
            track_length = record[-1]

            next_position = stream.tell() + track_length * 8
            if next_position > file_size:
                raise EOFError(f"Truncated COLMAP track at point {index}")

            stream.seek(next_position)

            if np.isfinite(xyz).all():
                points.append(xyz)

            if len(points) >= max_points:
                log(f"COLMAP reading capped at {max_points} valid points")
                break

    if not points:
        raise ValueError("No valid COLMAP points")

    points = apply_transform(np.asarray(points, dtype=np.float64), matrix)
    if not np.isfinite(points).all():
        raise ValueError("Non-finite transformed COLMAP coordinates")

    log(f"Valid COLMAP points: {len(points)}")
    return points


def filter_outliers(points, percentile):
    center = np.median(points, axis=0)
    distances = np.linalg.norm(points - center, axis=1)
    threshold = np.percentile(distances, percentile)
    filtered = points[distances <= threshold]

    if len(filtered) < 2:
        raise ValueError("At least two COLMAP points are required after filtering")

    log(f"Outlier filtering: kept {len(filtered)}/{len(points)}")
    return filtered


def estimate_radius(points, factor, seed):
    # Same sampled-neighbor approach as the original script,
    # with a reproducible sample.
    rng = np.random.default_rng(seed)
    size = min(5000, len(points))
    sample = points[rng.choice(len(points), size, replace=False)]

    distances, _ = cKDTree(sample).query(sample, k=2)
    positive = distances[:, 1]
    positive = positive[np.isfinite(positive) & (positive > 0)]

    if len(positive) == 0:
        raise ValueError("Cannot estimate a positive radius; specify --dist")

    return float(np.median(positive) * factor)


# ============================================================
# STANDARD INRIA 3DGS PLY
# ============================================================
def load_gaussians(path):
    vertex = PlyData.read(str(path))["vertex"].data
    names = set(vertex.dtype.names or ())

    def columns(fields):
        missing = set(fields) - names
        if missing:
            raise ValueError(f"Missing PLY properties: {sorted(missing)}")
        return np.stack([vertex[field] for field in fields], axis=1).astype(
            np.float64
        )

    means = columns(["x", "y", "z"])
    if len(means) == 0:
        raise ValueError("Empty Gaussian PLY")

    rest_names = sorted(
        (name for name in names if name.startswith("f_rest_")),
        key=lambda name: int(name.rsplit("_", 1)[1]),
    )

    if rest_names:
        expected = [f"f_rest_{i}" for i in range(len(rest_names))]
        if rest_names != expected or len(rest_names) not in (9, 24, 45):
            raise ValueError("Unsupported SH properties; expected SH degree 1, 2 or 3")
        shN = columns(rest_names).reshape(len(means), 3, -1).transpose(0, 2, 1)
    else:
        shN = np.empty((len(means), 0, 3), dtype=np.float64)

    return {
        "means": means,
        "scales": columns([f"scale_{i}" for i in range(3)]),
        "quats": columns([f"rot_{i}" for i in range(4)]),
        "opacities": columns(["opacity"])[:, 0],
        "sh0": columns([f"f_dc_{i}" for i in range(3)])[:, None, :],
        "shN": shN,
    }


# ============================================================
# REAL SH BASIS — INRIA / 3DGS CONVENTION, DEGREES 0..3
# ============================================================
def sh_basis(directions, degree):
    x, y, z = directions.T
    result = [np.full(len(x), 0.28209479177387814)]

    if degree >= 1:
        c1 = 0.4886025119029199
        result += [-c1 * y, c1 * z, -c1 * x]

    if degree >= 2:
        result += [
            1.0925484305920792 * x * y,
            -1.0925484305920792 * y * z,
            0.31539156525252005 * (2 * z * z - x * x - y * y),
            -1.0925484305920792 * x * z,
            0.5462742152960396 * (x * x - y * y),
        ]

    if degree >= 3:
        result += [
            -0.5900435899266435 * y * (3 * x * x - y * y),
            2.890611442640554 * x * y * z,
            -0.4570457994644658 * y * (4 * z * z - x * x - y * y),
            0.3731763325901154 * z * (2 * z * z - 3 * x * x - 3 * y * y),
            -0.4570457994644658 * x * (4 * z * z - x * x - y * y),
            1.445305721320277 * z * (x * x - y * y),
            -0.5900435899266435 * x * (x * x - 3 * y * y),
        ]

    return np.stack(result, axis=1)


def rotate_sh_to_world(shN, train_from_world_rotation, batch_size):
    if shN.shape[1] == 0:
        return

    degree = {3: 1, 8: 2, 15: 3}[shN.shape[1]]

    # Compute each SH-band rotation matrix from an overdetermined
    # system. For these polynomial bases this reproduces the rotation
    # up to floating-point precision; it does not truncate SH degrees.
    rng = np.random.default_rng(0)
    world_dirs = rng.normal(size=(128, 3))
    world_dirs /= np.linalg.norm(world_dirs, axis=1, keepdims=True)

    train_dirs = world_dirs @ train_from_world_rotation.T
    world_basis = sh_basis(world_dirs, degree)
    train_basis = sh_basis(train_dirs, degree)

    for band in range(1, degree + 1):
        begin, end = band * band, (band + 1) ** 2
        mapping = np.linalg.lstsq(
            world_basis[:, begin:end],
            train_basis[:, begin:end],
            rcond=None,
        )[0]

        # shN excludes the DC coefficient.
        indices = slice(begin - 1, end - 1)
        for start in range(0, len(shN), batch_size):
            block = shN[start:start + batch_size, indices, :]
            shN[start:start + batch_size, indices, :] = np.einsum(
                "ij,njc->nic", mapping, block
            )


# ============================================================
# TRAIN -> WORLD: GEOMETRY + APPEARANCE
# ============================================================
def convert_to_world(splats, matrix, rotation, scale, batch_size):
    inverse = np.linalg.inv(matrix)
    world_rotation = Rotation.from_matrix(rotation.T)

    for start in range(0, len(splats["means"]), batch_size):
        stop = start + batch_size

        splats["means"][start:stop] = apply_transform(
            splats["means"][start:stop], inverse
        )

        # PLY 3DGS quaternions use w,x,y,z; SciPy uses x,y,z,w.
        quats = splats["quats"][start:stop]
        train_rotation = Rotation.from_quat(quats[:, [1, 2, 3, 0]])
        world_quats = (world_rotation * train_rotation).as_quat()
        splats["quats"][start:stop] = world_quats[:, [3, 0, 1, 2]]

    # PLY stores logarithms of Gaussian standard deviations.
    splats["scales"] -= np.log(scale)

    rotate_sh_to_world(splats["shN"], rotation, batch_size)
    log("Converted positions, rotations, scales and SH to world coordinates")


# ============================================================
# EXPORT
# ============================================================
def export_result(splats, output, format_name):
    tensors = {
        name: torch.from_numpy(
            np.array(array, dtype=np.float32, order="C", copy=True)
        )
        for name, array in splats.items()
    }

    if not all(torch.isfinite(value).all().item() for value in tensors.values()):
        raise ValueError("Non-finite values after float32 conversion")

    output.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temporary file, then atomically replace the destination.
    fd, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
    )
    os.close(fd)

    try:
        with torch.no_grad():
            export_splats(
                **tensors,
                format=format_name,
                save_to=temporary,
            )

        if os.path.getsize(temporary) == 0:
            raise RuntimeError("Exporter produced an empty file")

        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Clean 3DGS PLY against COLMAP, convert to world, and export"
    )
    parser.add_argument("--in-ply", required=True)
    parser.add_argument("--points", required=True, help="COLMAP points3D.bin")
    parser.add_argument("--transform", required=True)
    parser.add_argument("--out-ply", required=True, help="Output file path")
    parser.add_argument(
        "--format",
        choices=["ply_compressed", "ply", "splat"],
        default="ply_compressed",
    )
    parser.add_argument("--dist", type=float, default=None)
    parser.add_argument("--radius-factor", type=float, default=3.0)
    parser.add_argument("--outlier-percent", type=float, default=97.0)
    parser.add_argument("--max-points", type=int, default=4_000_000)
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    source = Path(args.in_ply).expanduser().resolve()
    points_path = Path(args.points).expanduser().resolve()
    transform_path = Path(args.transform).expanduser().resolve()
    output = Path(args.out_ply).expanduser().resolve()

    inputs = [source, points_path, transform_path]
    for path in inputs:
        if not path.is_file():
            raise ValueError(f"Input file not found: {path}")
        if output == path or (
            output.exists() and os.path.samefile(output, path)
        ):
            raise ValueError("Output must not overwrite any input file")

    if output.exists() and not args.overwrite:
        raise ValueError("Output already exists; use --overwrite")

    if not 0 < args.outlier_percent <= 100:
        raise ValueError("--outlier-percent must be in (0, 100]")
    if args.max_points < 2 or args.batch_size < 1:
        raise ValueError("--max-points >= 2 and --batch-size >= 1 required")
    if not np.isfinite(args.radius_factor) or args.radius_factor <= 0:
        raise ValueError("--radius-factor must be finite and positive")
    if args.dist is not None and (
        not np.isfinite(args.dist) or args.dist <= 0
    ):
        raise ValueError("--dist must be finite and positive")

    matrix, rotation, scale = load_transform(transform_path)

    # 1. Move COLMAP world points to training coordinates.
    colmap = load_colmap_points(points_path, matrix, args.max_points)
    colmap = filter_outliers(colmap, args.outlier_percent)
    bbox_min = colmap.min(axis=0)
    bbox_max = colmap.max(axis=0)
    log(f"Training-space BBOX: {bbox_min} -> {bbox_max}")

    # 2. Load Gaussian PLY, already in training coordinates.
    splats = load_gaussians(source)
    means = splats["means"]
    count = len(means)

    distance = (
        args.dist
        if args.dist is not None
        else estimate_radius(colmap, args.radius_factor, args.seed)
    )
    log(f"Radius in training units: {distance:.9g}")

    tree = cKDTree(colmap)
    mask = np.zeros(count, dtype=bool)

    # 3. Same AND filter as the original:
    # nearest COLMAP distance < radius AND inside filtered COLMAP BBOX.
    for start in range(0, count, args.batch_size):
        stop = min(start + args.batch_size, count)
        xyz = means[start:stop]

        valid = np.ones(stop - start, dtype=bool)
        for array in splats.values():
            block = array[start:stop].reshape(stop - start, -1)
            valid &= np.isfinite(block).all(axis=1)

        valid &= np.linalg.norm(splats["quats"][start:stop], axis=1) > 1e-12
        valid &= ((xyz >= bbox_min) & (xyz <= bbox_max)).all(axis=1)

        local_indices = np.flatnonzero(valid)
        if len(local_indices):
            distances, _ = tree.query(xyz[local_indices], k=1)
            mask[start + local_indices] = distances < distance

    kept = int(mask.sum())
    log(f"Kept: {kept}/{count}")

    if kept == 0:
        raise ValueError(
            "No Gaussians remain. Check coordinate frames, --dist "
            "and --outlier-percent."
        )

    splats = {name: array[mask].copy() for name, array in splats.items()}
    del means, mask

    # The gsplat compressed exporter removes very low-opacity splats.
    if args.format == "ply_compressed":
        threshold_logit = -np.log(254.0)  # sigmoid(logit) > 1/255
        if not np.any(splats["opacities"] > threshold_logit):
            raise ValueError("All remaining splats are below exporter opacity threshold")

    # 4. Convert the cleaned Gaussians back to world coordinates.
    convert_to_world(
        splats, matrix, rotation, scale, args.batch_size
    )

    # 5. Export standard/compressed PLY or SPLAT.
    log(f"Exporting {args.format}...")
    export_result(splats, output, args.format)

    before = source.stat().st_size / 1024**2
    after = output.stat().st_size / 1024**2
    log(f"Saved in WORLD coordinates: {output}")
    log(f"Size: {before:.2f} MiB -> {after:.2f} MiB")


if __name__ == "__main__":
    main()
