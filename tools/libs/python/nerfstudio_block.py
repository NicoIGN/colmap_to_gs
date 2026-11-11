#!/usr/bin/env python3
"""Train one Splatfacto block and export it back to the original COLMAP frame."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from plyfile import PlyData, PlyElement

from nerfstudio_runtime import check_runtime

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/preprocess"))
from colmap_split_blocks import read_points3d_binary


def run(command):
    print("[Nerfstudio] " + " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True)


def prepare_dataset(sparse, images, masks, dataset):
    dataset.mkdir(parents=True, exist_ok=True)
    link = dataset / "images"
    if not link.exists():
        link.symlink_to(images, target_is_directory=True)
    points = read_points3d_binary(sparse / "points3D.bin")
    if not points:
        raise ValueError("Le bloc ne contient aucun point pour initialiser Splatfacto.")
    vertex = np.empty(len(points), dtype=[
        ("x", "<f8"), ("y", "<f8"), ("z", "<f8"),
        ("red", "u1"), ("green", "u1"), ("blue", "u1"),
    ])
    for row, point in enumerate(points.values()):
        vertex[row] = (*point.xyz, *point.rgb)
    PlyData([PlyElement.describe(vertex, "vertex")]).write(dataset / "sparse_pc.ply")
    run([
        sys.executable, Path(__file__).with_name("colmap_to_transforms.py"),
        "--colmap-model", sparse, "--images-dir", images,
        "--output", dataset / "transforms.json", "--image-prefix", "images",
        "--ply-file-path", "sparse_pc.ply",
    ])
    if masks is not None:
        transforms_path = dataset / "transforms.json"
        data = json.loads(transforms_path.read_text())
        from PIL import Image
        mask_link = dataset / "masks"
        if not mask_link.exists():
            mask_link.symlink_to(masks, target_is_directory=True)
        for frame in data["frames"]:
            name = Path(frame["file_path"]).relative_to("images").as_posix()
            mask_path = masks / (name + ".png")
            with Image.open(images / name) as image, Image.open(mask_path) as mask:
                mask.load()
                if mask.mode != "L" or mask.size != image.size:
                    raise ValueError(f"Masque incompatible : {mask_path}")
                histogram = mask.histogram()
                if sum(histogram[1:255]):
                    raise ValueError(f"Masque non binaire : {mask_path}")
            frame["mask_path"] = "masks/" + name + ".png"
        transforms_path.write_text(json.dumps(data, indent=2) + "\n")


def train(args):
    check_runtime()
    sparse, images, output = args.sparse_dir.resolve(), args.images_dir.resolve(), args.output_dir.resolve()
    masks = args.masks_dir.resolve() if args.masks_dir else None
    iterations = (args.num_iterations if args.num_iterations is not None
                  else int(os.environ.get("TRAIN_NUM_ITER", "20000")))
    factor = int(os.environ.get("NS_DOWNSCALE_FACTOR", "1"))
    if iterations < 1 or factor < 1:
        raise ValueError("Itérations et NS_DOWNSCALE_FACTOR doivent être >= 1.")
    mask_mode = args.mask_mode or os.environ.get("TRAIN_MASKS_MODE", "ignore")
    if masks is not None and mask_mode != "ignore":
        raise ValueError("Nerfstudio exige le mode de masques ignore, pas empty/auto.")
    name = args.name or "block.splat"
    if Path(name).name != name or not name.endswith(".splat"):
        raise ValueError("--name doit être un nom de fichier .splat.")
    output.mkdir(parents=True, exist_ok=True)
    signature = {
        "backend": "nerfstudio", "iterations": iterations, "downscale": factor,
        "images": str(images), "masks": str(masks) if masks else None,
        "sparse": {
            key: hashlib.sha256((sparse / key).read_bytes()).hexdigest()
            for key in ("cameras.bin", "images.bin", "points3D.bin")
        },
        "mask_policy": (masks / "masks.json").read_text() if masks else None,
        "ns_train": os.environ.get("NS_TRAIN_BIN", "ns-train"),
        "ns_export": os.environ.get("NS_EXPORT_BIN", "ns-export"),
        "name": name,
    }
    marker = output / "nerfstudio_inputs.json"
    clean = args.clean or os.environ.get("TRAIN_CLEAN", "false") == "true"
    if clean:
        for folder in ("dataset", "train", "splats", "ns-export"):
            target = output / folder
            if target.is_symlink():
                raise ValueError(f"Nettoyage refusé sur un lien : {target}")
            if target.exists():
                shutil.rmtree(target)
        for file in (".train_done", ".export_done"):
            (output / file).unlink(missing_ok=True)
    elif marker.exists() and json.loads(marker.read_text()) != signature:
        raise ValueError("Paramètres Nerfstudio modifiés : nouveau dossier ou TRAIN_CLEAN=true.")
    elif not marker.exists() and any((output / folder).exists() for folder in ("train", "splats", "dataset")):
        raise ValueError("Sortie sans signature Nerfstudio : nouveau dossier requis.")
    marker.write_text(json.dumps(signature, indent=2) + "\n")
    splats = output / "splats"
    final = splats / name
    if (output / ".export_done").is_file() and final.is_file() and final.stat().st_size:
        if not (splats / "scene_transform.json").is_file():
            raise ValueError("Export marqué terminé sans sa transformation ; relancer l'export après contrôle.")
        print(f"[Nerfstudio] Export réutilisé : {final}")
        return
    train_root = output / "train"
    config = train_root / "block/splatfacto/run/config.yml"
    if not (output / ".train_done").exists():
        if train_root.exists():
            raise ValueError("Entraînement Nerfstudio partiel : sauvegarder puis TRAIN_CLEAN=true.")
        prepare_dataset(sparse, images, masks, output / "dataset")
        print(
            "[Nerfstudio] Les paramètres Spirula de cap, qualité, croissance, "
            "flottants et distractions ne sont pas transposés. Splatfacto utilise "
            "ses réglages propres ; TRAIN_CAP_MAX n'est pas une limite garantie.",
            flush=True,
        )
        run([
            signature["ns_train"], "splatfacto", "--machine.device-type", "cuda",
            "--max-num-iterations", str(iterations), "--output-dir", train_root,
            "--experiment-name", "block", "--timestamp", "run", "--vis", "tensorboard",
            "--viewer.quit-on-train-completion", "True",
            "--pipeline.model.camera-optimizer.mode", "off",
            "nerfstudio-data", "--data", output / "dataset", "--downscale-factor", str(factor),
            "--orientation-method", "none", "--center-method", "none",
            "--auto-scale-poses", "False", "--eval-mode", "all",
        ])
        if not config.is_file() or not (config.parent / "dataparser_transforms.json").is_file():
            raise ValueError("Entraînement sans config.yml ou dataparser_transforms.json.")
        (output / ".train_done").touch()
    run([signature["ns_export"], "gaussian-splat", "--load-config", config,
         "--output-dir", output / "ns-export"])
    source = output / "ns-export/splat.ply"
    if not source.is_file():
        raise ValueError(f"Export Nerfstudio absent : {source}")
    splats.mkdir(exist_ok=True)
    transform = splats / "scene_transform.json"
    shutil.copy2(config.parent / "dataparser_transforms.json", transform)
    run([
        sys.executable, ROOT / "tools/postprocess/export_ply.py",
        "--in-ply", source, "--points", sparse / "points3D.bin",
        "--transform", transform, "--out-ply", final, "--format", "splat",
        "--filter-mode", "off", "--overwrite",
    ])
    if not final.is_file() or not final.stat().st_size:
        raise ValueError("Export .splat absent ou vide.")
    (output / ".export_done").touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("sparse-dir", "images-dir", "output-dir"):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--masks-dir", type=Path)
    parser.add_argument("--mask-mode")
    parser.add_argument("--name")
    parser.add_argument("--num-iterations", type=int)
    parser.add_argument("--clean", action="store_true")
    for option in ("spirula-bin", "python-bin", "export-filter-mode", "train-preset", "cap-max",
                   "quality", "distraction-robustness", "growth-factor", "floater-suppression",
                   "refine-stop-iter", "refine-stop-num-iter"):
        parser.add_argument("--" + option)
    args = parser.parse_args()
    try:
        train(args)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"[ERREUR] Bloc Nerfstudio : {error}\n")


if __name__ == "__main__":
    main()
