#!/usr/bin/env python3
"""Reproject full equirectangular panoramas to overlapping perspective views."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image


FOV_DEGREES = 110
FACES = ("front", "right", "back", "left", "up", "down")
EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def view_map(width, height, size, face):
    f = size / (2 * math.tan(math.radians(FOV_DEGREES) / 2))
    x, y = np.meshgrid((np.arange(size) + .5 - size / 2) / f,
                       (np.arange(size) + .5 - size / 2) / f)
    one = np.ones_like(x)
    directions = {
        "front": (x, -y, one),
        "right": (one, -y, -x),
        "back": (-x, -y, -one),
        "left": (-one, -y, x),
        "up": (x, one, y),
        "down": (x, -one, -y),
    }
    dx, dy, dz = directions[face]
    lon = np.arctan2(dx, dz)
    lat = np.arctan2(dy, np.hypot(dx, dz))
    # Pixel centres are half-integers in the continuous panorama coordinate system.
    u = np.mod((lon / (2 * np.pi) + .5) * width - .5, width)
    v = np.clip((.5 - lat / np.pi) * height - .5, 0, height - 1)
    return u.astype(np.float32), v.astype(np.float32)


def prepare(source, target, manifest, size, check=False):
    source, target, manifest = map(Path, (source, target, manifest))
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Les dossiers source et destination doivent être distincts et disjoints.")
    if size < 2:
        raise ValueError("La taille des vues doit être supérieure ou égale à 2.")
    inputs = sorted(p for p in source.rglob("*")
                    if p.is_file() and p.suffix.lower() in EXTENSIONS)
    if not inputs:
        raise ValueError(f"Aucun panorama dans {source}")
    files = []
    for path in inputs:
        with Image.open(path) as image:
            width, height = image.size
        if width != 2 * height:
            raise ValueError(f"Panorama non équirectangulaire complet (rapport 2:1 requis) : {path}")
        files.append({"name": path.relative_to(source).as_posix(), "sha256": digest(path),
                      "width": width, "height": height})
    record = {"version": 1, "source": str(source), "size": size,
              "fov_degrees": FOV_DEGREES, "images": files}
    expected = {
        f"{face}/{entry['name']}.jpg": entry["name"]
        for entry in files for face in FACES
    }
    record["views"] = expected
    if manifest.exists():
        old = json.loads(manifest.read_text())
        if old.get("inputs") != record:
            raise ValueError("Panoramas ou paramètres modifiés : choisir un nouveau workspace.")
        if old.get("status") == "complete":
            actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
            if actual != set(expected) or any(
                digest(target / name) != old["view_sha256"].get(name) for name in expected
            ):
                raise ValueError("Vues perspectives manquantes ou modifiées : choisir un nouveau workspace.")
            return
        raise ValueError("Projection interrompue : déplacer le workspace avant de recommencer.")
    if check or (target.exists() and any(target.iterdir())):
        raise ValueError("Projection absente ou destination non vide sans signature.")
    target.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"status": "partial", "inputs": record}, indent=2) + "\n")
    map_shape = None
    maps = None
    hashes = {}
    print(f"[360] Projection de {len(files)} panoramas vers {target} ({len(expected)} vues).", flush=True)
    step = max(1, math.ceil(len(files) / 100))
    for index, entry in enumerate(files, 1):
        if index == 1 or index % step == 0 or index == len(files):
            ratio = index / len(files)
            filled = int(32 * ratio)
            print(f"[360] Projection [{'#' * filled}{'-' * (32 - filled)}] "
                  f"{index}/{len(files)} ({ratio * 100:5.1f}%)", flush=True)
        path = source / entry["name"]
        with Image.open(path) as image:
            rgb = np.asarray(image.convert("RGB"))
        shape = (entry["width"], entry["height"])
        if shape != map_shape:
            maps = {face: view_map(*shape, size, face) for face in FACES}
            map_shape = shape
        # A duplicated column supplies interpolation across the longitude seam.
        wrapped = np.concatenate((rgb, rgb[:, :1]), axis=1)
        for face in FACES:
            name = f"{face}/{entry['name']}.jpg"
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            image = cv2.remap(wrapped, *maps[face], cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
            Image.fromarray(image).save(out, quality=95, subsampling=0)
            hashes[name] = digest(out)
    temporary = manifest.with_name(manifest.name + ".tmp")
    temporary.write_text(json.dumps({"status": "complete", "inputs": record,
                                     "view_sha256": hashes}, indent=2) + "\n")
    temporary.replace(manifest)
    print(f"[360] {len(inputs)} panoramas projetés en {len(expected)} vues ({size}px, {FOV_DEGREES}°).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.source, args.output, args.manifest, args.size, args.check)
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"[ERREUR] Projection 360 : {error}")


if __name__ == "__main__":
    main()
