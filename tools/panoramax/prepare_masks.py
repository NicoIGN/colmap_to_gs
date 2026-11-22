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

from dataset_layout import image_files, metadata_index
from prepare_sfm import main as prepare_images
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/libs/python"))
from mask_policies import enabled, generate, policy
from mask_preparation import read_report, without_device, write_json


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def external_dir(key):
    value = os.environ.get(key, "").strip()
    return Path(value).expanduser().resolve() if value else None


def copy_external(source, directory):
    # The copy is validated and signed by the caller; it is never regenerated.
    if source is None:
        return
    if not source.is_dir():
        raise ValueError(f"Dossier de masques introuvable : {source}")
    if directory.is_symlink() or directory.exists():
        if directory.resolve() != source:
            raise ValueError(f"{directory} existe déjà et ne provient pas de {source} : le sauvegarder avant de continuer")
        return
    shutil.copytree(source, directory)
    print(f"[masques] Masques externes copiés (sans génération) : {source} -> {directory}", flush=True)


def prepare(dataset, workspace, check_only=False, perspective=False):
    sfm = policy("SFM_MASKS")
    train = policy("TRAIN_MASKS") if enabled(os.environ["TRAIN_MASKS"]) else None
    # Include source images and checkpoint metadata so a completed SfM is never
    # silently reused after its extraction mask policy or inputs have changed.
    # Content hashes keep a re-download of identical photos from invalidating the SfM.
    inputs = [(str(p.relative_to(dataset)), hashlib.sha256(p.read_bytes()).hexdigest())
              for p in image_files(dataset)]
    metadata = [(str(p.relative_to(dataset)), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted(metadata_index(dataset).values())]
    model = Path(sfm["model"])
    model_files = [model] if model.is_file() else sorted(model.rglob("*"))
    weights = [(str(p), p.stat().st_size, p.stat().st_mtime_ns)
               for p in model_files if p.is_file()]
    binary = Path(os.environ["SPIRULA_BIN"]).resolve()
    if perspective and sfm["backend"] != "spirula" and (train is None or train["backend"] != "spirula"):
        binary_stamp = None
    else:
        binary_stamp = (str(binary), binary.stat().st_size, binary.stat().st_mtime_ns)
    if perspective:
        inputs.append(("projection", hashlib.sha256((workspace / "projection.json").read_bytes()).hexdigest()))
    generator_hash = hashlib.sha256((Path(__file__).parents[1] /
                                     "segmentation_masks/generate_masks.py").read_bytes()).hexdigest()
    def fingerprint(code_hash, policy):
        return hashlib.sha256(json.dumps(
            [policy, inputs, metadata, weights, binary_stamp, code_hash],
            sort_keys=True).encode()).hexdigest()

    signature = fingerprint(generator_hash, without_device(sfm))
    marker = workspace / "sfm_mask_policy.json"
    previous = read_report(marker) or {}
    if not isinstance(previous, dict):
        raise ValueError(f"Signature de masques invalide : {marker}")
    artifacts = any((workspace / name).exists() for name in ("sparse", "features", "matches.bin"))
    artifacts = artifacts or any(workspace.glob("blocks-*"))
    compatible = {signature}
    # Markers written before the device was left out of the signature hashed it too.
    previous_settings = previous.get("settings")
    if isinstance(previous_settings, dict) and without_device(previous_settings) == without_device(sfm):
        compatible.add(fingerprint(generator_hash, previous_settings))
    # One-time migration: markers written before inputs were content-hashed carry a
    # stat-based digest that cannot be recomputed. Trust a completed marker without
    # an input digest and with the same mask settings; the new marker is rewritten below.
    if (isinstance(previous_settings, dict) and previous.get("status") == "complete" and
            "input_digest" not in previous and
            without_device(previous_settings) == without_device(sfm)):
        print("[masques] Signature SfM héritée acceptée (migration unique vers SHA-256).", flush=True)
        compatible.add(previous.get("signature"))
    # These generator updates change only consent forwarding, diagnostics, or the
    # speed of dilation (distance transform gives identical masks, commit f039271).
    if generator_hash in (
            "6f6363b524a637850821662d19792cf2d4397ce644513c327dd3d52aa773c74e",
            "f6eeb39e0d9227aa318d4bc44ac8952432af6bdfca44b733069bd7f6a880b896",
            "16bc0e17e25fb01933fbd907941c1ea8b01e48084b857a17b0fffe1748d42437",
            # Verbose-only logging (d2f19e3, 121e786): same masks.
            "145320d66c87b352255eddd7a6d5fd306b659424b6cfb115857d0774cdea8985"):
        if isinstance(previous_settings, dict) and without_device(previous_settings) == without_device(sfm):
            compatible.add(fingerprint("5b46a95fa64db889a1d0c16740a4790c1b3e1a7e26e74d2f3145534ad6045379",
                                       previous_settings))
            compatible.add(fingerprint("6f6363b524a637850821662d19792cf2d4397ce644513c327dd3d52aa773c74e",
                                       previous_settings))
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
                without_device(previous.get("training_settings")) != without_device(train)):
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
    if not perspective:
        prepare_images(["--dataset", str(dataset), "--output-dir", str(workspace)])
    # Validate copied masks before the partial marker exists: a rejected adoption must not
    # leave a marker that would make the next run regenerate the user's masks.
    adoptable = set()
    if not previous:
        train_source = external_dir("TRAIN_MASKS_DIR")
        sfm_source = external_dir("SFM_MASKS_DIR")
        if shared and train_source and sfm_source and train_source != sfm_source:
            raise ValueError("Labels identiques : SFM_MASKS_DIR et TRAIN_MASKS_DIR doivent désigner le même dossier")
        if train is not None:
            copy_external(train_source or (sfm_source if shared else None), training)
        if not shared:
            copy_external(sfm_source, features)
        for name, directory in (("training", training if train is not None else None),
                                ("features", None if shared else features)):
            if directory is None or not (directory / "masks.json").is_file():
                continue
            report = read_report(directory / "masks.json") or {}
            if report.get("status") != "complete":
                raise ValueError(f"Masques incomplets sans signature : {directory}")
            for source in images.rglob("*"):
                if source.is_file() and source.suffix.lower() in IMAGE_SUFFIXES:
                    mask = directory / (source.relative_to(images).as_posix() + ".png")
                    if not mask.is_file():
                        raise ValueError(f"Masque attendu absent : {mask}")
            adoptable.add(name)
    adopted = []
    record = {"signature": signature, "input_digest": "sha256", "settings": sfm, "training_settings": train,
              "shared_with_training": shared, "status": "partial"}
    write_json(marker, record)
    if features.is_symlink():
        preserve = (not shared and previous.get("shared_with_training") and
                    features.resolve() == training and training.is_dir())
        features.unlink()
        if preserve:
            # Keep the masks used for extraction before changing training labels.
            shutil.copytree(training, features)
    def adopt_or_generate(name, settings, output):
        # Masks copied into the workspace were validated above: sign them, do not regenerate.
        if name in adoptable:
            adopted.append(output.name)
            print(f"[masques] Masques existants validés et signés : {output}", flush=True)
            return
        generate(settings, images, output)

    if train is not None:
        adopt_or_generate("training", train, training)
    if shared:
        features.symlink_to(training, target_is_directory=True)
        print(f"[masques] Politiques identiques : {features} -> {training}", flush=True)
    else:
        adopt_or_generate("features", sfm, features)
    record["adopted_masks"] = adopted
    record["status"] = "complete"
    write_json(marker, record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate preparation without generating masks")
    parser.add_argument("--perspective", action="store_true", help="Utiliser les vues déjà projetées")
    args = parser.parse_args()
    try:
        prepare(args.dataset.resolve(), args.workspace.resolve(), check_only=args.check,
                perspective=args.perspective)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"[ERREUR] Préparation des masques : {error}", file=sys.stderr)
        sys.exit(1)
