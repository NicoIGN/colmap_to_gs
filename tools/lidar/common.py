"""Shared LAS/LAZ and Gaussian geometry for LiDAR postprocessing."""

import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "postprocess"))
from merge_splats import Scene
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs/python"))
from pipeline_report import Reporter


def vector(value):
    result = np.array([float(part) for part in value.split(",")])
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError("Origine attendue : X,Y,Z finis")
    return result


def class_codes(value):
    result = {int(part) for part in value.split(",")}
    if not result or any(code < 0 or code > 255 for code in result):
        raise ValueError("Classes LAS attendues : codes entiers entre 0 et 255")
    return result


def load_lidar(path, origin, stride=1, bbox=None, classes=None):
    import laspy

    if stride < 1:
        raise ValueError("Stride LiDAR attendu >= 1")
    clouds = []
    offset = 0
    with laspy.open(path) as reader:
        for chunk in reader.chunk_iterator(200000):
            points = np.column_stack((chunk.x, chunk.y, chunk.z)) - origin
            if not np.isfinite(points).all():
                raise ValueError("Coordonnées LiDAR non finies")
            if classes is not None:
                points = points[np.isin(np.asarray(chunk.classification), sorted(classes))]
            if bbox is not None:
                points = points[((points >= bbox[:3]) & (points <= bbox[3:])).all(axis=1)]
            start = (-offset) % stride
            offset += len(points)
            clouds.append(points[start::stride])
    points = np.concatenate(clouds) if clouds else np.empty((0, 3))
    if len(points) < 3:
        raise ValueError("Moins de trois points LiDAR sélectionnés")
    return points


def voxel_sample(points, size):
    if not np.isfinite(size) or size <= 0:
        raise ValueError("Taille de voxel attendue finie et > 0")
    _, indices = np.unique(np.floor((points - points.min(axis=0)) / size),
                           axis=0, return_index=True)
    return points[np.sort(indices)]


def fit_similarity(source, target, estimate_scale):
    a, b = source.mean(axis=0), target.mean(axis=0)
    x, y = source - a, target - b
    u, singular, vt = np.linalg.svd(x.T @ y)
    if singular[1] <= singular[0] * 1e-10:
        raise ValueError("Correspondances dégénérées : recalage impossible")
    correction = np.ones(3)
    correction[-1] = np.linalg.det(vt.T @ u.T)
    rotation = vt.T @ np.diag(correction) @ u.T
    scale = float(singular @ correction / np.sum(x * x)) if estimate_scale else 1.0
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Échelle estimée invalide")
    return scale, rotation, b - scale * (rotation @ a)


def register(source, target, *, distance, voxel, iterations=60, trim=0.8,
             min_overlap=0.3, estimate_scale=False):
    if (not np.isfinite(distance) or distance <= 0 or iterations < 1
            or not 0 < trim <= 1 or not 0 < min_overlap <= 1):
        raise ValueError("Distance > 0, itérations >= 1, fractions dans ]0,1] requises")
    source = voxel_sample(source, voxel)
    target = voxel_sample(target, voxel)
    if min(len(source), len(target)) < 6:
        raise ValueError("Au moins six points sous-échantillonnés requis")
    tree = cKDTree(target)
    before = tree.query(source)[0]
    scale, rotation, translation = 1.0, np.eye(3), np.zeros(3)
    converged = False
    for gate in (distance, distance / 2, distance / 4):
        converged = False
        for _ in range(iterations):
            moved = scale * source @ rotation.T + translation
            distances, near = tree.query(moved)
            indices = np.flatnonzero(distances <= gate)
            if len(indices) < max(6, int(np.ceil(min_overlap * len(source)))):
                raise ValueError("Recouvrement insuffisant : vérifier origine et placement initial")
            indices = indices[np.argsort(distances[indices])[:max(6, int(len(indices) * trim))]]
            ds, dr, dt = fit_similarity(moved[indices], target[near[indices]], estimate_scale)
            scale, rotation, translation = ds * scale, dr @ rotation, ds * dr @ translation + dt
            delta = np.linalg.norm(ds * moved[indices] @ dr.T + dt - moved[indices], axis=1).max()
            if delta < max(1e-7, voxel * 1e-4):
                converged = True
                break
    after = tree.query(scale * source @ rotation.T + translation)[0]
    inliers = after <= distance / 4
    if not converged:
        raise ValueError("ICP non convergé : aucune scène recalée écrite")
    if inliers.mean() < min_overlap:
        raise ValueError("Recouvrement final insuffisant")
    before_rmse = float(np.sqrt(np.mean(before[inliers] ** 2)))
    after_rmse = float(np.sqrt(np.mean(after[inliers] ** 2)))
    if after_rmse > before_rmse + max(1e-7, voxel * 1e-4):
        raise ValueError("Recalage dégradant les distances : aucune scène écrite")
    return scale, rotation, translation, {
        "sampled_splats": len(source), "sampled_lidar": len(target),
        "overlap": float(inliers.mean()), "inlier_distance": distance / 4,
        "rmse_before": before_rmse, "rmse_after": after_rmse,
        "converged": converged,
    }


def sh_basis(d):
    x, y, z = d.T
    return np.column_stack((
        -0.4886025119029199*y, 0.4886025119029199*z, -0.4886025119029199*x,
        1.0925484305920792*x*y, -1.0925484305920792*y*z,
        0.31539156525252005*(2*z*z-x*x-y*y), -1.0925484305920792*x*z,
        0.5462742152960396*(x*x-y*y),
        -0.5900435899266435*y*(3*x*x-y*y), 2.890611442640554*x*y*z,
        -0.4570457994644658*y*(4*z*z-x*x-y*y),
        0.3731763325901154*z*(2*z*z-3*x*x-3*y*y),
        -0.4570457994644658*x*(4*z*z-x*x-y*y),
        1.445305721320277*z*(x*x-y*y), -0.5900435899266435*x*(x*x-3*y*y),
    ))


def sh_transform(rotation, count):
    if count not in (3, 8, 15):
        raise ValueError("Rotation SH : seuls les degrés 1 à 3 sont pris en charge")
    i = np.arange(128)
    z = 1 - 2 * (i + 0.5) / len(i)
    phi = i * np.pi * (3 - np.sqrt(5))
    d = np.column_stack((np.sqrt(1-z*z)*np.cos(phi), np.sqrt(1-z*z)*np.sin(phi), z))
    return np.linalg.lstsq(sh_basis(d)[:, :count],
                           sh_basis(d @ rotation)[:, :count], rcond=None)[0]


def transform_records(scene, raw, scale, rotation, translation, sh_matrix=None):
    data = raw.copy()
    if scene.is_splat:
        xyz = data["position"].astype(float)
        q = (data["rotation"].astype(float) - 128) / 128
        data["scale"] *= scale
    else:
        xyz = np.column_stack([data[n] for n in "xyz"]).astype(float)
        q = np.column_stack([data[f"rot_{i}"] for i in range(4)])
        for i in range(3):
            data[f"scale_{i}"] += np.log(scale)
        if sh_matrix is not None:
            count = sh_matrix.shape[0]
            coefficients = np.column_stack([data[f"f_rest_{i}"] for i in range(3*count)])
            coefficients = coefficients.reshape(-1, 3, count) @ sh_matrix.T
            for i, values in enumerate(coefficients.reshape(-1, 3*count).T):
                data[f"f_rest_{i}"] = values
    if not np.isfinite(q).all() or np.any(np.linalg.norm(q, axis=1) < 1e-8):
        raise ValueError("Quaternion invalide")
    updated = (Rotation.from_matrix(rotation) * Rotation.from_quat(q[:, [1, 2, 3, 0]])).as_quat()
    updated = updated[:, [3, 0, 1, 2]]
    xyz = scale * xyz @ rotation.T + translation
    if scene.is_splat:
        data["position"] = xyz
        data["rotation"] = np.clip(np.rint(updated * 128 + 128), 0, 255).astype("u1")
        if not np.isfinite(data["position"]).all() or not np.isfinite(data["scale"]).all():
            raise ValueError("Transformation hors plage du format splat")
    else:
        for i, name in enumerate("xyz"):
            data[name] = xyz[:, i]
            if not np.isfinite(data[name]).all():
                raise ValueError("Transformation hors plage du format PLY")
        for i in range(4):
            data[f"rot_{i}"] = updated[:, i]
    return data


def write_scene(path, scene, keep=None, transform=None):
    if path.exists():
        raise ValueError(f"Sortie déjà présente : {path}")
    if path.suffix.lower() != scene.path.suffix.lower():
        raise ValueError("La sortie doit conserver le format de l'entrée")
    count = scene.count if keep is None else int(keep.sum())
    if scene.is_splat:
        header = b""
    else:
        header = PlyData([PlyElement.describe(np.empty(0, dtype=scene.raw.dtype),
                                             "vertex")], byte_order="<").header
        header = (header.replace("element vertex 0\n", f"element vertex {count}\n")
                  + "\n").encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(header)
            for start in range(0, scene.count, 100000):
                raw = scene.raw[start:start+100000]
                if keep is not None:
                    raw = raw[keep[start:start+100000]]
                if transform is not None and len(raw):
                    raw = transform(raw)
                if not scene.is_splat:
                    raw = raw.astype(raw.dtype.newbyteorder("<"))
                stream.write(raw.tobytes())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def write_report(path, report):
    with path.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")


def open_scene(path):
    scene = Scene(path)
    if scene.count == 0:
        scene.close()
        raise ValueError("Scène vide")
    return scene


def gaussian_geometry(scene, selection):
    """Return centres, standard deviations and local-to-world rotation matrices."""
    raw = scene.raw[selection]
    xyz, _ = scene.geometry(selection)
    if scene.is_splat:
        scales = raw["scale"].astype(float)
        quats = (raw["rotation"].astype(float) - 128) / 128
    else:
        scales = np.exp(np.column_stack([raw[f"scale_{i}"] for i in range(3)]).astype(float))
        quats = np.column_stack([raw[f"rot_{i}"] for i in range(4)])
    if not np.isfinite(scales).all() or np.any(scales <= 0):
        raise ValueError("Échelles gaussiennes invalides")
    if not np.isfinite(quats).all() or np.any(np.linalg.norm(quats, axis=1) < 1e-8):
        raise ValueError("Quaternions gaussiens invalides")
    rotations = Rotation.from_quat(quats[:, [1, 2, 3, 0]]).as_matrix()
    return xyz, scales, rotations


def validate_support_opacity(threshold):
    if threshold is not None and (not np.isfinite(threshold) or not 0 < threshold <= 1):
        raise ValueError("Seuil d'opacité de support attendu fini dans ]0,1]")


def support_radii(scene, selection, sigma, threshold=None):
    """Radius in standard deviations where alpha0 * exp(-r²/2) >= threshold."""
    validate_support_opacity(threshold)
    raw = scene.raw[selection]
    if threshold is None:
        return np.full(len(raw), sigma), np.ones(len(raw), dtype=bool)
    if scene.is_splat:
        alpha = raw["rgba"][:, 3].astype(float) / 255
    else:
        logits = raw["opacity"].astype(float)
        if not np.isfinite(logits).all():
            raise ValueError("Opacités PLY non finies")
        alpha = np.exp(-np.logaddexp(0., -logits))
    exists = alpha >= threshold
    radii = np.zeros(len(raw))
    radii[exists] = np.minimum(sigma, np.sqrt(2 * np.log(alpha[exists] / threshold)))
    return radii, exists
