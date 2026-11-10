#!/usr/bin/env python3
"""Select local images without flattening names; guard workspace reuse."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from prepare_masks import policies


EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


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
    if marker.is_file():
        if json.loads(marker.read_text()) != record:
            raise ValueError("Images ou paramètres SfM/masques/découpage/GPX modifiés : "
                             "choisir un nouvel OUTPUT_DIR. TRAIN_CLEAN ne recalcule pas le SfM.")
    elif check:
        raise ValueError("Préparation absente : lancer --phase prepare.")
    elif any((output / name).exists() for name in
             ("images", "sfm", "masks", "feature_masks", "train_masks", "blocks", "gs")):
        raise ValueError("Workspace sans inputs.json : choisir un nouvel OUTPUT_DIR.")
    if check:
        if not (output / ".prepare_done").is_file():
            raise ValueError("Préparation incomplète : relancer --phase prepare.")
    else:
        output.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
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
    print(f"[images] {len(images)} images sélectionnées ; récursif={recursive}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.check)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[ERREUR] Sélection des images : {exc}\n")
