#!/usr/bin/env python3
"""Select local images without flattening names; guard workspace reuse."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from prepare_masks import policies


EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def compatible_record(previous, current):
    if any(previous.get(key) != current.get(key)
           for key in ("source", "images", "georeference")):
        return False
    old_settings = previous.get("settings")
    if not isinstance(old_settings, dict):
        return False
    return all(
        key in current["settings"] and current["settings"][key] == value
        for key, value in old_settings.items()
    )


def prepare(check=False):
    source = Path(os.environ["SFM_IMAGES_DIR"]).resolve()
    output = Path(os.environ["OUTPUT_DIR"]).resolve()
    recursive = os.environ["SFM_IMAGES_RECURSIVE"] == "true"
    images = []
    for folder, dirs, files in os.walk(source, followlinks=False):
        dirs.sort()
        if not recursive:
            dirs[:] = []
        for name in sorted(files):
            path = Path(folder, name)
            if path.suffix.lower() in EXTENSIONS and path.is_file():
                stat = path.stat()
                images.append([path.relative_to(source).as_posix(), str(path.resolve()),
                               stat.st_size, stat.st_mtime_ns])
    if not images:
        raise ValueError(f"Aucune image sélectionnée dans {source} (récursif={recursive})")
    settings = {key: value for key, value in os.environ.items()
                if key.startswith(("SFM_", "TRAIN_MASKS", "SPLIT_"))}
    settings["mask_policies"] = policies()
    for prefix, mask in settings["mask_policies"].items():
        if mask is None:
            continue
        model = Path(mask["model"])
        files = [model] if model.is_file() else sorted(p for p in model.rglob("*") if p.is_file())
        settings[f"{prefix}_model_stamp"] = [
            [str(path), path.stat().st_size, path.stat().st_mtime_ns] for path in files
        ]
    record = {"source": str(source), "images": images, "settings": settings}
    if os.environ.get("GEOREF_GPX_FILE"):
        gpx = Path(os.environ["GEOREF_GPX_FILE"]).resolve()
        record["georeference"] = {
            "gpx": str(gpx),
            "sha256": hashlib.sha256(gpx.read_bytes()).hexdigest(),
            "view_prefix": os.environ["GEOREF_VIEW_PREFIX"],
            "epsg": os.environ["GEOREF_EPSG"],
        }
    marker = output / "inputs.json"
    resume = os.environ.get("PIPELINE_RESUME") == "true"
    adopted = False
    if marker.is_file():
        previous = json.loads(marker.read_text())
        if not isinstance(previous, dict):
            raise ValueError(f"Signature de workspace invalide : {marker}")
        if previous != record:
            if not resume or check or not compatible_record(previous, record):
                raise ValueError("Images ou paramètres SfM/masques/découpage/GPX modifiés : "
                                 "choisir un nouvel OUTPUT_DIR. TRAIN_CLEAN ne recalcule pas le SfM.")
            adopted = True
    elif check:
        raise ValueError("Préparation absente : lancer --phase prepare.")
    elif any((output / name).exists() for name in
             ("images", "sfm", "masks", "feature_masks", "train_masks", "blocks", "gs")):
        if not resume:
            raise ValueError("Workspace sans inputs.json : choisir un nouvel OUTPUT_DIR.")
        adopted = True
    if adopted and any((output / name).exists() for name in ("georef", "blocks", "gs")):
        raise ValueError(
            "Reprise sans signature actuelle refusée après recalage/découpage/entraînement : "
            "choisir un nouvel OUTPUT_DIR."
        )
    if check:
        if not (output / ".prepare_done").is_file():
            raise ValueError("Préparation incomplète : relancer --phase prepare.")
    if resume and (output / "images").is_dir():
        expected = {relative for relative, _, _, _ in images}
        unexpected = [
            path.relative_to(output / "images").as_posix()
            for path in (output / "images").rglob("*")
            if not path.is_dir()
            and path.relative_to(output / "images").as_posix() not in expected
        ]
        if unexpected:
            raise ValueError(f"Images inattendues dans le workspace : {unexpected[:5]}")
    for relative, resolved, _, _ in images:
        target = output / "images" / relative
        if target.is_symlink():
            if target.resolve() != Path(resolved):
                raise ValueError(f"Lien image incompatible : {target}")
        elif target.exists():
            raise ValueError(f"Image sélectionnée non symbolique : {target}")
        elif check:
            raise ValueError(f"Image sélectionnée absente : {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(resolved)
    if not check:
        output.mkdir(parents=True, exist_ok=True)
        if adopted:
            if marker.exists():
                backup = output / "inputs.before_resume.json"
                if backup.exists():
                    raise ValueError(f"Sauvegarde de signature déjà présente : {backup}")
                shutil.copyfile(marker, backup)
            print(
                "[AVERTISSEMENT] Reprise explicite d'une signature absente/ancienne ; "
                "la provenance des artefacts sans métadonnées n'est pas vérifiable.",
                flush=True,
            )
        temporary = output / ".inputs.json.tmp"
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        temporary.replace(marker)
    print(f"[images] {len(images)} images sélectionnées ; récursif={recursive}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.check)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[ERREUR] Sélection des images : {exc}\n")
