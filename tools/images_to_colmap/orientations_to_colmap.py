#!/usr/bin/env python3
"""Convert explicit calibrated OpenCV poses and a colored PLY into tracked COLMAP."""

import argparse
import json
from pathlib import Path
import struct
import sys

import numpy as np
from PIL import Image
from plyfile import PlyData
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "preprocess"))
from colmap_split_blocks import (
    CAMERA_MODELS, Camera, ImageRec, write_block_images, write_cameras_bin,
)


def convert(orientations, points_path, images_dir, output):
    data = json.loads(orientations.read_text())
    if data.get("version") != 1 or data.get("pose_convention") != "opencv_world_to_camera":
        raise ValueError("JSON version=1 et pose_convention=opencv_world_to_camera requis.")
    cameras = {}
    models = {name: (key, count) for key, (name, count) in CAMERA_MODELS.items()}
    for item in data["cameras"]:
        camera_id = int(item["id"])
        if item["model"] not in ("PINHOLE", "OPENCV"):
            raise ValueError("Conversion LiDAR : seuls PINHOLE et OPENCV sont supportés.")
        model_id, count = models[item["model"]]
        params = np.asarray(item["params"], dtype=float)
        if (params.shape != (count,) or not np.isfinite(params).all()
                or min(item["width"], item["height"], params[0], params[1]) <= 0
                or camera_id <= 0 or camera_id in cameras):
            raise ValueError(f"Caméra invalide : {camera_id}")
        cameras[camera_id] = Camera(camera_id, model_id, item["width"], item["height"], params)
    vertex = PlyData.read(points_path)["vertex"]
    xyz = np.column_stack([vertex[key] for key in ("x", "y", "z")]).astype(float)
    rgb = np.column_stack([vertex[key] for key in ("red", "green", "blue")])
    if (not len(xyz) or not np.isfinite(xyz).all() or not np.isfinite(rgb).all()
            or np.any(rgb < 0) or np.any(rgb > 255)):
        raise ValueError("PLY vide, coordonnées non finies ou couleurs hors [0,255].")
    poses, observations, names = {}, {}, set()
    for image_id, item in enumerate(data["images"], 1):
        name = item["name"]
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\x00" in name or name in names:
            raise ValueError(f"Nom image invalide ou dupliqué : {name!r}")
        names.add(name)
        camera = cameras[item["camera_id"]]
        with Image.open(images_dir / name) as image:
            if image.size != (camera.width, camera.height):
                raise ValueError(f"Dimensions image/calibration incompatibles : {name}")
        qvec, tvec = np.asarray(item["qvec"], float), np.asarray(item["tvec"], float)
        if (qvec.shape != (4,) or tvec.shape != (3,) or not np.isfinite(qvec).all()
                or not np.isfinite(tvec).all() or not np.isclose(np.linalg.norm(qvec), 1)):
            raise ValueError(f"Pose invalide : {name}")
        rotation = Rotation.from_quat(qvec[[1, 2, 3, 0]]).as_matrix()
        local = xyz @ rotation.T + tvec
        indices = np.flatnonzero(local[:, 2] > 0)
        xy = local[indices, :2] / local[indices, 2:3]
        if camera.model_id == 4:
            k1, k2, p1, p2 = camera.params[4:]
            x, y = xy.T
            r2 = x * x + y * y
            radial = 1 + k1 * r2 + k2 * r2 * r2
            xy = np.column_stack((
                x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x),
                y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y,
            ))
        xy = xy * camera.params[:2] + camera.params[2:4]
        valid = (np.isfinite(xy).all(axis=1) & (xy[:, 0] >= 0) & (xy[:, 0] < camera.width)
                 & (xy[:, 1] >= 0) & (xy[:, 1] < camera.height))
        indices, xy = indices[valid], xy[valid]
        # Keep the nearest projected point per pixel to reduce occluded LiDAR samples.
        order = np.argsort(local[indices, 2])
        pixels = np.floor(xy[order]).astype(np.int64)
        _, first = np.unique(pixels, axis=0, return_index=True)
        keep = order[first]
        observations[image_id] = (indices[keep], xy[keep])
        poses[image_id] = (name, camera.camera_id, qvec, tvec)
    counts = np.zeros(len(xyz), dtype=np.int64)
    for indices, _ in observations.values():
        counts[indices] += 1
    allowed = set((np.flatnonzero(counts >= 2) + 1).tolist())
    if len(poses) < 2 or not allowed:
        raise ValueError("Au moins deux images et des points visibles dans deux vues sont requis.")
    tracks = {point_id: [] for point_id in allowed}
    images = {}
    for image_id, (indices, xy) in observations.items():
        valid = np.array([int(index + 1) in allowed for index in indices])
        pids, xy = indices[valid] + 1, xy[valid]
        if not len(pids):
            raise ValueError(f"Aucun point multi-vues dans {poses[image_id][0]}")
        for index, point_id in enumerate(pids):
            tracks[int(point_id)].append((image_id, index))
        name, camera_id, qvec, tvec = poses[image_id]
        images[image_id] = ImageRec(image_id, qvec, tvec, camera_id, name, xy, pids)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Sortie non vide : {output}")
    output.mkdir(parents=True, exist_ok=True)
    write_cameras_bin(output / "cameras.bin", cameras, set(cameras))
    write_block_images(output, images, set(images), allowed, False, True)
    with (output / "points3D.bin").open("wb") as stream:
        stream.write(struct.pack("<Q", len(allowed)))
        for point_id in sorted(allowed):
            index = point_id - 1
            stream.write(struct.pack("<Q3d3BdQ", point_id, *xyz[index],
                                     *map(int, rgb[index]), 0.0, len(tracks[point_id])))
            for image_id, observation in tracks[point_id]:
                stream.write(struct.pack("<II", image_id, observation))
    (output / "orientation_reference.json").write_text(json.dumps({
        "source": str(orientations.resolve()), "points": str(points_path.resolve()),
        "reference": data.get("reference"),
        "warning": "Observations projetées depuis le LiDAR, pas des matches photogrammétriques ; validation visuelle requise.",
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("orientations", "points", "images", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        convert(args.orientations, args.points, args.images, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"[ERREUR] Conversion orientations/LiDAR : {error}\n")
