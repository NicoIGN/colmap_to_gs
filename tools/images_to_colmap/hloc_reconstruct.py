#!/usr/bin/env python3
"""SfM HLOC (extraction, appariement, triangulation pycolmap) vers un modèle COLMAP binaire."""
import argparse
import shutil
import sys
from pathlib import Path

import h5py
import numpy as np
import pycolmap
from PIL import Image

from hloc import (extract_features, match_features, pairs_from_exhaustive,
                  pairs_from_retrieval, reconstruction)
from hloc.utils.io import list_h5_names

CAMERA_MODES = {"folder": "PER_FOLDER", "single": "SINGLE", "image": "PER_IMAGE"}


def apply_masks(features_path, masks_dir, output_path):
    """Écrit une copie des points en retirant ceux tombant sur un masque 0 (255 = gardé)."""
    kept_total = dropped_total = 0
    with h5py.File(features_path, "r") as src, h5py.File(output_path, "w") as dst:
        for name in list_h5_names(features_path):
            mask_path = masks_dir / f"{name}.png"
            if not mask_path.is_file():
                raise FileNotFoundError(f"Masque SfM manquant : {mask_path}")
            mask = np.asarray(Image.open(mask_path).convert("L"))
            group = src[name]
            keypoints = group["keypoints"][()]
            x = np.clip(np.round(keypoints[:, 0]).astype(int), 0, mask.shape[1] - 1)
            y = np.clip(np.round(keypoints[:, 1]).astype(int), 0, mask.shape[0] - 1)
            keep = mask[y, x] == 255
            kept_total += int(keep.sum())
            dropped_total += int((~keep).sum())
            out = dst.create_group(name)
            for key in group:
                data = group[key][()]
                if key == "descriptors":
                    data = data[:, keep]
                elif data.ndim and data.shape[0] == len(keep):
                    data = data[keep]
                out.create_dataset(key, data=data).attrs.update(group[key].attrs)
    print(f"[HLOC] masques SfM : {kept_total} points gardés, {dropped_total} retirés")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Dossier sparse/0 de sortie")
    parser.add_argument("--work", required=True, type=Path, help="Dossier de travail HLOC")
    parser.add_argument("--feature-masks", type=Path, default=None)
    parser.add_argument("--features", required=True)
    parser.add_argument("--matcher", required=True)
    parser.add_argument("--pairs", required=True, choices=["retrieval", "exhaustive"])
    parser.add_argument("--retrieval", default="netvlad")
    parser.add_argument("--num-neighbors", type=int, default=20)
    parser.add_argument("--camera-model", required=True)
    parser.add_argument("--camera-mode", required=True, choices=list(CAMERA_MODES))
    args = parser.parse_args()

    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    feature_conf = extract_features.confs[args.features]
    matcher_conf = match_features.confs[args.matcher]

    features = extract_features.main(feature_conf, args.images, work)
    if args.feature_masks:
        masked = work / f"{feature_conf['output']}_masked.h5"
        apply_masks(features, args.feature_masks, masked)
        features = masked

    pairs = work / "pairs.txt"
    if args.pairs == "retrieval":
        global_descriptors = extract_features.main(
            extract_features.confs[args.retrieval], args.images, work)
        pairs_from_retrieval.main(global_descriptors, pairs, args.num_neighbors)
    else:
        pairs_from_exhaustive.main(pairs, features=features)

    matches = match_features.main(matcher_conf, pairs, features, export_dir=work)

    camera_mode = pycolmap.CameraMode.__members__[CAMERA_MODES[args.camera_mode]]
    sfm = work / "sfm"
    reconstruction_result = reconstruction.main(
        sfm, args.images, pairs, features, matches,
        camera_mode=camera_mode,
        image_options={"camera_model": args.camera_model},
    )
    if reconstruction_result is None:
        sys.exit("Erreur : HLOC n'a produit aucune reconstruction.")

    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        shutil.copy2(sfm / name, args.output / name)


if __name__ == "__main__":
    main()
