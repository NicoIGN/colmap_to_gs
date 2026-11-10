#!/usr/bin/env python3
"""Validate artifacts before explicitly adopting an interrupted workspace."""

import argparse
from pathlib import Path
import sys


def validate_masks(images, output):
    from PIL import Image

    extensions = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
    sources = sorted(
        path for path in images.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    )
    if not sources:
        raise ValueError(f"Aucune image sélectionnée : {images}")
    for source in sources:
        mask_path = output / (source.relative_to(images).as_posix() + ".png")
        if not mask_path.is_file():
            raise ValueError(f"Masque attendu absent : {mask_path}")
        with Image.open(source) as image, Image.open(mask_path) as mask:
            mask.load()
            if mask.format != "PNG" or mask.mode != "L" or mask.size != image.size:
                raise ValueError(
                    f"Masque invalide (PNG monochrome aux dimensions de l'image requis) : {mask_path}"
                )
            histogram = mask.histogram()
            if sum(histogram[1:255]):
                raise ValueError(f"Masque non binaire (0/255 requis) : {mask_path}")
    print(f"[reprise] {len(sources)} masques vérifiés dans {output}.", flush=True)


def validate_sparse(folder):
    for name in ("cameras", "images", "points3D"):
        path = folder / f"{name}.bin"
        if not path.is_file() or path.stat().st_size < 8:
            raise ValueError(f"Fichier absent ou tronqué : {path}")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "preprocess"))
    from colmap_split_blocks import (
        read_cameras_binary, read_images_binary, read_points3d_binary,
    )
    import numpy as np

    cameras = read_cameras_binary(folder / "cameras.bin")
    images = read_images_binary(folder / "images.bin")
    points = read_points3d_binary(folder / "points3D.bin")
    if not cameras or not images or not points:
        raise ValueError(f"Modèle sparse vide : {folder}")
    tracks = {point_id: set(point.track) for point_id, point in points.items()}
    for camera_id, camera in cameras.items():
        if camera.width <= 0 or camera.height <= 0 or not np.isfinite(camera.params).all():
            raise ValueError(f"Caméra invalide : {camera_id}")
    for image_id, image in images.items():
        if image.camera_id not in cameras:
            raise ValueError(f"Caméra absente pour l'image {image_id}")
        if (not np.isfinite(image.qvec).all() or not np.isfinite(image.tvec).all()
                or np.linalg.norm(image.qvec) == 0 or not np.isfinite(image.xys).all()):
            raise ValueError(f"Pose non finie pour l'image {image_id}")
        for index, point_id in enumerate(image.point3D_ids):
            if point_id == -1:
                continue
            point = points.get(int(point_id))
            if point is None or (image_id, index) not in tracks[int(point_id)]:
                raise ValueError(f"Observation incohérente : image {image_id}, point {point_id}")
    for point_id, point in points.items():
        if not np.isfinite(point.xyz).all() or not point.track:
            raise ValueError(f"Point invalide : {point_id}")
        for image_id, index in point.track:
            image = images.get(image_id)
            if (image is None or index >= len(image.point3D_ids)
                    or image.point3D_ids[index] != point_id):
                raise ValueError(f"Track incohérente pour le point {point_id}")
    print(
        f"[reprise] Sparse vérifié : {len(cameras)} caméras, "
        f"{len(images)} images, {len(points)} points dans {folder}.",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sparse", type=Path)
    args = parser.parse_args()
    try:
        validate_sparse(args.sparse)
    except ImportError as error:
        parser.exit(2, f"[ERREUR] Dépendance de validation sparse absente : {error}\n")
    except (OSError, EOFError, ValueError) as error:
        parser.exit(1, f"[reprise] Sparse absent ou incomplet : {error}\n")


if __name__ == "__main__":
    main()
