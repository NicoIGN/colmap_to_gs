#!/usr/bin/env python3
"""Prepare independent SfM/training mask policies and share identical outputs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from prepare_sfm import main as prepare_images
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from mask_policies import enabled, generate, policy


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
    generator_hash = hashlib.sha256((Path(__file__).parents[1] /
                                     "segmentation_masks/generate_masks.py").read_bytes()).hexdigest()
    def fingerprint(code_hash):
        return hashlib.sha256(json.dumps(
            [sfm, inputs, metadata, weights, binary_stamp, code_hash],
            sort_keys=True).encode()).hexdigest()

    signature = fingerprint(generator_hash)
    marker = workspace / "sfm_mask_policy.json"
    previous = json.loads(marker.read_text()) if marker.is_file() else {}
    if not isinstance(previous, dict):
        raise ValueError(f"Signature de masques invalide : {marker}")
    artifacts = any((workspace / name).exists() for name in ("sparse", "features", "matches.bin"))
    artifacts = artifacts or any(workspace.glob("blocks-*"))
    compatible = {signature}
    # These generator updates change only consent forwarding and diagnostics.
    if generator_hash in (
            "6f6363b524a637850821662d19792cf2d4397ce644513c327dd3d52aa773c74e",
            "f6eeb39e0d9227aa318d4bc44ac8952432af6bdfca44b733069bd7f6a880b896"):
        compatible.add(fingerprint("5b46a95fa64db889a1d0c16740a4790c1b3e1a7e26e74d2f3145534ad6045379"))
        compatible.add(fingerprint("6f6363b524a637850821662d19792cf2d4397ce644513c327dd3d52aa773c74e"))
    if (artifacts or previous) and previous.get("signature") not in compatible:
        raise ValueError(
            "SfM existant/partiel avec une autre politique de masques (ou sans signature). "
            f"Déplacer {workspace} en sauvegarde ou choisir un nouvel OUTPUT_DIR. "
            "TRAIN_CLEAN ne recalcule pas le SfM.")
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
