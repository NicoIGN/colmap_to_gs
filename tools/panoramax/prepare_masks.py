#!/usr/bin/env python3
"""Prepare independent SfM/training mask policies and share identical outputs."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from prepare_sfm import main as prepare_images


def enabled(value):
    return value in ("on", "true", "1", "yes")


def policy(prefix):
    env = os.environ
    labels = sorted({" ".join(label.lower().split())
                     for label in env[f"{prefix}_LABELS"].split(",") if label.strip()})
    if not labels:
        raise ValueError(f"{prefix}_LABELS doit contenir au moins un label")
    backend = env[f"{prefix}_BACKEND"]
    if backend not in ("spirula", "segmentation"):
        raise ValueError(f"{prefix}_BACKEND doit être spirula ou segmentation")
    native = backend == "spirula"
    model = env[f"{prefix}_SPIRULA_MODEL" if native else f"{prefix}_MODEL"]
    if not model:
        raise ValueError(f"Modèle {prefix} non défini")
    path = Path(model).expanduser().resolve()
    if not (path.is_file() if native else path.is_dir()):
        raise ValueError(f"Modèle {prefix} introuvable : {path}")
    dilate = env[f"{prefix}_DILATE"]
    if dilate == "auto":
        mode = env.get("TRAIN_MASKS_MODE", "auto") if prefix == "TRAIN_MASKS" else "ignore"
        if mode == "auto":
            mode = "empty" if labels == ["sky"] else "ignore"
        dilate = "0" if mode == "empty" else "0.002"
    settings = {
        "backend": backend, "model": str(path), "labels": labels,
        "confidence": float(env[f"{prefix}_CONFIDENCE"]),
        "dilate": float(dilate), "max_fraction": float(env[f"{prefix}_MAX_FRACTION"]),
        "device": env[f"{prefix}_SPIRULA_DEVICE" if native else f"{prefix}_DEVICE"],
        "python": env["PYTHON_BIN"] if native else env[f"{prefix}_PYTHON"],
    }
    if not (0 <= settings["confidence"] <= 1 and
            0 <= settings["dilate"] < 0.1 and
            0 < settings["max_fraction"] <= 1 and
            all(math.isfinite(settings[key])
                for key in ("confidence", "dilate", "max_fraction"))):
        raise ValueError(f"Seuils {prefix} invalides")
    return settings


def generate(settings, images, output):
    env = os.environ
    command = [
        settings["python"], str(Path(__file__).parents[1] / "segmentation_masks/generate_masks.py"),
        "--backend", settings["backend"], "--model", settings["model"],
        "--images", str(images), "--output", str(output),
        "--labels", ",".join(settings["labels"]),
        "--confidence", str(settings["confidence"]),
        "--dilate-fraction", str(settings["dilate"]),
        "--max-masked-fraction", str(settings["max_fraction"]),
        "--spirula-bin", env["SPIRULA_BIN"],
        "--spirula-device", settings["device"] if settings["backend"] == "spirula" else "auto",
        "--device", settings["device"] if settings["backend"] != "spirula" else "auto",
    ]
    print(f"[masques] {output.name} : {settings['backend']}, {settings['labels']}", flush=True)
    subprocess.run(command, check=True)
    report = json.loads((output / "masks.json").read_text())
    if report.get("status") != "complete":
        raise ValueError(f"Masques incomplets : {output}")
    print(f"[masques] {output.name} : calcul terminé", flush=True)


def prepare(dataset, workspace, check_only=False):
    sfm = policy("SFM_MASKS")
    train = policy("TRAIN_MASKS") if enabled(os.environ["TRAIN_MASKS"]) else None
    # Include source images and checkpoint metadata so a completed SfM is never
    # silently reused after its extraction mask policy or inputs have changed.
    inputs = [(str(p.relative_to(dataset)), p.stat().st_size, p.stat().st_mtime_ns)
              for p in sorted((dataset / "images").rglob("*")) if p.is_file()]
    metadata = [(str(p.relative_to(dataset)), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted((dataset / "metadata").rglob("*.json"))]
    model = Path(sfm["model"])
    model_files = [model] if model.is_file() else sorted(model.rglob("*"))
    weights = [(str(p), p.stat().st_size, p.stat().st_mtime_ns)
               for p in model_files if p.is_file()]
    binary = Path(os.environ["SPIRULA_BIN"]).resolve()
    binary_stamp = (str(binary), binary.stat().st_size, binary.stat().st_mtime_ns)
    signature = hashlib.sha256(json.dumps(
        [sfm, inputs, metadata, weights, binary_stamp,
         hashlib.sha256((Path(__file__).parents[1] /
                         "segmentation_masks/generate_masks.py").read_bytes()).hexdigest()],
        sort_keys=True).encode()).hexdigest()
    marker = workspace / "sfm_mask_policy.json"
    previous = json.loads(marker.read_text()) if marker.is_file() else {}
    if not isinstance(previous, dict):
        raise ValueError(f"Signature de masques invalide : {marker}")
    artifacts = any((workspace / name).exists() for name in ("sparse", "features", "matches.bin"))
    artifacts = artifacts or any(workspace.glob("blocks-*"))
    if (artifacts or previous) and previous.get("signature") != signature:
        raise ValueError(
            "SfM existant/partiel avec une autre politique de masques (ou sans signature). "
            f"Déplacer {workspace} en sauvegarde ou choisir un nouvel OUTPUT_DIR. "
            "CLEAN_GS ne recalcule pas le SfM.")
    images = workspace / "images"
    training = workspace / "train_masks"
    features = workspace / "feature_masks"
    shared = train == sfm
    if check_only:
        if (previous.get("status") != "complete" or
                previous.get("training_settings") != train):
            raise ValueError("Politique de masques non préparée : relancer la préparation")
        for directory in ([features, training] if train else [features]):
            report = directory / "masks.json"
            if not report.is_file() or json.loads(report.read_text()).get("status") != "complete":
                raise ValueError(f"Masques incomplets : {directory}")
        return
    if train is not None and training.is_symlink():
        raise ValueError(f"{training} est un lien symbolique : refuser de modifier sa cible")
    if shared and features.exists() and not features.is_symlink():
        raise ValueError(f"{features} est un dossier réel : choisir un nouveau workspace pour partager les masques")
    if not previous and any((p / "masks.json").exists() for p in (features, training)):
        raise ValueError("Masques préexistants sans signature : choisir un nouveau workspace")
    prepare_images(["--dataset", str(dataset), "--output-dir", str(workspace)])
    record = {"signature": signature, "settings": sfm, "training_settings": train,
              "shared_with_training": shared, "status": "partial"}
    marker.write_text(json.dumps(record, indent=2) + "\n")
    if features.is_symlink():
        preserve = (not shared and previous.get("shared_with_training") and
                    features.resolve() == training and training.is_dir())
        features.unlink()
        if preserve:
            # Keep the masks used for extraction before changing training labels.
            shutil.copytree(training, features)
    if train is not None:
        generate(train, images, training)
    if shared:
        features.symlink_to(training, target_is_directory=True)
        print(f"[masques] Politiques identiques : {features} -> {training}", flush=True)
    else:
        generate(sfm, images, features)
    record["status"] = "complete"
    marker.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate preparation without generating masks")
    args = parser.parse_args()
    try:
        prepare(args.dataset.resolve(), args.workspace.resolve(), check_only=args.check)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"[ERREUR] Préparation des masques : {error}", file=sys.stderr)
        sys.exit(1)
