#!/usr/bin/env python3
"""Reconstruct selected images with native COLMAP or ns-process-data."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/preprocess"))
from colmap_split_blocks import read_images_binary


def run(command):
    print("[COLMAP] " + " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True)


def reconstruct():
    env = os.environ
    output = Path(env["OUTPUT_DIR"])
    sfm = output / "sfm"
    images = output / "images"
    if sfm.exists() and any(sfm.iterdir()):
        raise ValueError(
            "Sortie COLMAP partielle : sauvegarder/déplacer sfm avant de relancer. "
            "La reprise incrémentale n'est disponible qu'avec Spirula."
        )
    if env["COLMAP_METHOD"] == "nerfstudio":
        sys.path.insert(0, str(ROOT / "tools/libs/python"))
        from nerfstudio_runtime import check_runtime
        check_runtime()
        if env["COLMAP_MATCHING"] == "vocab_tree":
            raise ValueError("ns-process-data : utiliser exhaustive ou sequential.")
        run([
            env["NS_PROCESS_DATA_BIN"], "images", "--data", images,
            "--output-dir", sfm, "--sfm-tool", "colmap",
            "--matching-method", env["COLMAP_MATCHING"], "--num-downscales", "0",
            "--skip-image-processing", "--colmap-cmd", env["COLMAP_BIN"],
            "--camera-type", {
                "opencv": "perspective", "pinhole": "pinhole",
                "simple_pinhole": "simple_pinhole", "opencv_fisheye": "fisheye",
            }[env["SFM_CAMERA_MODEL"].lower()],
        ])
        raise_if_renamed(sfm, images)
        sparse = sfm / "colmap/sparse"
    else:
        sfm.mkdir(parents=True, exist_ok=True)
        binary = env["COLMAP_BIN"]
        database = sfm / "database.db"
        gpu = "1" if env["COLMAP_USE_GPU"] == "true" else "0"
        mode = env["SFM_CAMERA_MODE"]
        camera_flags = {
            "single": ["--ImageReader.single_camera", "1"],
            "folder": ["--ImageReader.single_camera_per_folder", "1"],
            "image": ["--ImageReader.single_camera", "0"],
        }[mode]
        command = [
            binary, "feature_extractor", "--database_path", database,
            "--image_path", images, "--ImageReader.camera_model", env["SFM_CAMERA_MODEL"].upper(),
            "--SiftExtraction.use_gpu", gpu,
            "--SiftExtraction.max_image_size", env["SFM_MAX_IMAGE_SIZE"],
            "--SiftExtraction.max_num_features", env["SFM_ALIKED_MAX_FEATURES"],
            "--SiftExtraction.num_threads", env["SFM_THREADS"], *camera_flags,
        ]
        if env["SFM_MASKS"] == "on":
            masks = output / "masks"
            if not masks.is_dir():
                masks = output / "feature_masks"
            command += ["--ImageReader.mask_path", masks]
        run(command)
        if env["COLMAP_MATCHING"] == "vocab_tree":
            raise ValueError("Le matching vocab_tree exige un vocabulaire ; utiliser exhaustive ou sequential.")
        run([
            binary, env["COLMAP_MATCHING"] + "_matcher", "--database_path", database,
            "--SiftMatching.use_gpu", gpu, "--SiftMatching.num_threads", env["SFM_THREADS"],
            "--SiftMatching.max_error", env["SFM_MAX_ERROR"],
            "--SiftMatching.min_num_inliers", env["SFM_MIN_INLIERS"],
        ])
        sparse = sfm / "native_sparse"
        sparse.mkdir()
        run([
            binary, "mapper", "--database_path", database, "--image_path", images,
            "--output_path", sparse, "--Mapper.num_threads", env["SFM_THREADS"],
            "--Mapper.filter_max_reproj_error", env["SFM_MAX_ERROR"],
            "--Mapper.filter_min_tri_angle", env["SFM_MIN_TRI_ANGLE"],
        ])
    models = sorted(p for p in sparse.iterdir()
                    if p.is_dir() and (p / "images.bin").is_file())
    if not models:
        raise ValueError(f"Aucun modèle COLMAP binaire dans {sparse}")
    best = max(models, key=lambda p: len(read_images_binary(p / "images.bin")))
    if len(models) > 1:
        print(f"[AVERTISSEMENT] {len(models)} reconstructions déconnectées ; "
              f"seule la plus grande est retenue : {best}.", flush=True)
    destination = sfm / "sparse/0"
    destination.mkdir(parents=True)
    for path in best.iterdir():
        if path.is_file():
            shutil.copy2(path, destination / path.name)
    print("[COLMAP] Sans Spirula, le GPS EXIF n'aligne pas automatiquement le modèle ; "
          "utiliser un GPX pour le recalage.", flush=True)


def raise_if_renamed(sfm, images):
    """Refuse an incoherent mapping rather than silently train against wrong images."""
    for model in (sfm / "colmap/sparse").glob("*/images.bin"):
        for image in read_images_binary(model).values():
            if not (images / image.name).is_file():
                raise ValueError(
                    "ns-process-data a renommé les images : le modèle ne correspond pas "
                    "à la sélection. Utiliser COLMAP_METHOD=colmap ou spirula."
                )


if __name__ == "__main__":
    try:
        reconstruct()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f"[ERREUR] Reconstruction COLMAP : {error}")
