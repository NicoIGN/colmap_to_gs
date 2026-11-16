#!/usr/bin/env python3
"""Retry SfM with one camera per significant model when Spirula splits a sequence.

A phone may switch lens/focal length during a video: Spirula then produces
several models whose shared camera intrinsics disagree. ``plan`` groups images
by model, ``finish`` adopts the new reconstruction under the original image
names and ``recover`` completes an interrupted adoption.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import struct
import sys

EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
NO_SPLIT = 3
DOWNSTREAM = ("georef", "sparse", "sparse_filtered", "sparse_clean", "sparse_rejected",
              "colmap_lidar", "lidar", "blocks", "gs", "gs-nerfstudio", ".prepare_done")


def model_dirs(sparse):
    if not sparse.is_dir():
        return []
    return sorted((path for path in sparse.iterdir()
                   if path.is_dir() and path.name.isdigit()), key=lambda path: int(path.name))


def _read_string(handle):
    data = bytearray()
    while True:
        char = handle.read(1)
        if not char:
            raise ValueError("images.bin tronqué")
        if char == b"\0":
            return data.decode("utf-8")
        data += char


def read_image_names(path):
    names = []
    with path.open("rb") as handle:
        (count,) = struct.unpack("<Q", handle.read(8))
        for _ in range(count):
            handle.read(64)
            names.append(_read_string(handle))
            (points,) = struct.unpack("<Q", handle.read(8))
            handle.seek(points * 24, os.SEEK_CUR)
    return names


def rename_images(source, target, rename):
    with source.open("rb") as reader, target.open("wb") as writer:
        header = reader.read(8)
        (count,) = struct.unpack("<Q", header)
        writer.write(header)
        for _ in range(count):
            writer.write(reader.read(64))
            writer.write(rename(_read_string(reader)).encode("utf-8") + b"\0")
            raw = reader.read(8)
            (points,) = struct.unpack("<Q", raw)
            writer.write(raw + reader.read(points * 24))


def image_names(images):
    return sorted(path.relative_to(images).as_posix() for path in images.rglob("*")
                  if path.suffix.lower() in EXTENSIONS and path.is_file())


def assign_groups(names, models, min_images):
    """Map every image to a significant model, then to its nearest neighbour in name order."""
    significant = [model for model in models if len(model) >= min_images]
    groups = {}
    for index, model in enumerate(significant):
        for name in model:
            groups.setdefault(name, index)
    if len(significant) < 2:
        return significant, {}
    known = [position for position, name in enumerate(names) if name in groups]
    if not known:
        return significant, {}
    result = {}
    cursor = 0
    for position, name in enumerate(names):
        if name in groups:
            result[name] = groups[name]
            continue
        while cursor + 1 < len(known) and known[cursor + 1] < position:
            cursor += 1
        candidates = [known[cursor]]
        if cursor + 1 < len(known):
            candidates.append(known[cursor + 1])
        nearest = min(candidates, key=lambda other: (abs(other - position), other))
        result[name] = groups[names[nearest]]
    return significant, result


def link(target, source):
    if target.is_symlink() and target.resolve() == source.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source)


def plan(output, min_images, masks):
    sfm = output / "sfm"
    work = output / "sfm_cameras"
    if (sfm / "camera_split.json").is_file():
        print("[caméras] SfM par groupe de caméras déjà adopté.", flush=True)
        return NO_SPLIT
    models = [read_image_names(path / "images.bin") for path in model_dirs(sfm / "sparse")]
    names = image_names(output / "images")
    significant, groups = assign_groups(names, models, min_images)
    if not groups:
        print(f"[caméras] {len(models)} modèle(s), {len(significant)} avec au moins "
              f"{min_images} images : pas de nouveau SfM.", flush=True)
        return NO_SPLIT
    counts = [sum(1 for group in groups.values() if group == index)
              for index in range(len(significant))]
    record = {"min_images": min_images, "model_images": [len(model) for model in significant],
              "group_images": counts, "groups": groups}
    plan_path = work / "plan.json"
    if plan_path.is_file() and json.loads(plan_path.read_text()) != record:
        raise ValueError(f"Plan de caméras différent déjà présent : {plan_path}")
    for name, group in groups.items():
        folder = f"cam_{group:02d}"
        link(work / "images" / folder / name, (output / "images" / name).resolve())
        if masks is not None:
            mask = masks / (name + ".png")
            if not mask.is_file():
                raise ValueError(f"Masque attendu absent : {mask}")
            link(work / "masks" / folder / (name + ".png"), mask.resolve())
    work.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    sizes = ", ".join(str(len(model)) for model in significant)
    print(f"[caméras] {len(significant)} modèles significatifs ({sizes} images) : "
          f"nouveau SfM avec une caméra par groupe ({', '.join(map(str, counts))} images).",
          flush=True)
    return 0


def archive_downstream(output):
    export = os.environ.get("EXPORT_SPLAT_NAME", "")
    names = list(DOWNSTREAM)
    if export:
        names += [export, f"{Path(export).stem}.georef.json"]
    sources = [output / name for name in names if (output / name).exists()]
    if not sources:
        return
    if os.environ.get("PIPELINE_RESUME") != "true":
        raise ValueError("Sorties dérivées de l'ancien SfM présentes : PIPELINE_RESUME=true "
                         "pour les archiver ou choisir un nouvel OUTPUT_DIR.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup = output / "camera-split-backups" / stamp
    backup.mkdir(parents=True)
    for source in sources:
        source.rename(backup / source.name)
    print(f"[caméras] Sorties de l'ancien SfM conservées : {backup}", flush=True)


def adopt(output):
    sfm = output / "sfm"
    staged = sfm / "sparse.camera-split.tmp"
    previous = sfm / "sparse-before-camera-split"
    if (sfm / "sparse").exists():
        if previous.exists():
            raise ValueError(f"Ancien SfM déjà conservé : {previous}")
        (sfm / "sparse").rename(previous)
    staged.rename(sfm / "sparse")
    plan_record = json.loads((output / "sfm_cameras/plan.json").read_text())
    record = {key: plan_record[key] for key in ("min_images", "model_images", "group_images")}
    record["previous_sparse"] = previous.name
    record["models"] = [len(read_image_names(path / "images.bin"))
                        for path in model_dirs(sfm / "sparse")]
    (sfm / "camera_split.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def finish(output, min_images):
    sfm = output / "sfm"
    work = output / "sfm_cameras"
    groups = json.loads((work / "plan.json").read_text())["groups"]
    models = model_dirs(work / "sfm/sparse")
    if not models:
        raise ValueError(f"Nouveau SfM absent : {work / 'sfm/sparse'}")
    staged = sfm / "sparse.camera-split.tmp"
    if staged.exists():
        shutil.rmtree(staged)

    def original(name):
        folder, _, relative = name.partition("/")
        if groups.get(relative) is None or folder != f"cam_{groups[relative]:02d}":
            raise ValueError(f"Image inattendue dans le nouveau SfM : {name}")
        return relative

    for model in models:
        target = staged / model.name
        target.mkdir(parents=True)
        for path in model.iterdir():
            if path.name == "images.bin":
                rename_images(path, target / path.name, original)
            elif path.is_file():
                shutil.copy2(path, target / path.name)
    archive_downstream(output)
    record = adopt(output)
    registered = record["models"]
    print(f"[caméras] Nouveau SfM adopté : {len(registered)} modèle(s), "
          f"{registered[0]}/{len(groups)} images dans le modèle 0.", flush=True)
    if sum(1 for count in registered if count >= min_images) > 1:
        print("[AVERTISSEMENT] Plusieurs modèles significatifs subsistent ; seul sfm/sparse/0 "
              "est utilisé. Essayer SFM_SP_PAIRS=exhaustive.", flush=True)


def recover(output):
    sfm = output / "sfm"
    staged = sfm / "sparse.camera-split.tmp"
    previous = sfm / "sparse-before-camera-split"
    if (sfm / "sparse").exists() or not previous.is_dir():
        return
    if staged.is_dir() and (output / "sfm_cameras/plan.json").is_file():
        archive_downstream(output)
        adopt(output)
        print("[reprise] Adoption interrompue du SfM par groupe de caméras terminée.", flush=True)
    else:
        previous.rename(sfm / "sparse")
        print("[reprise] SfM initial restauré avant une nouvelle séparation des caméras.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "finish", "recover"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-images", type=int, default=50)
    parser.add_argument("--masks", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    try:
        if args.command == "plan":
            return plan(output, args.min_images, args.masks)
        if args.command == "finish":
            finish(output, args.min_images)
        else:
            recover(output)
    except (OSError, ValueError, struct.error) as error:
        print(f"[ERREUR] Séparation des caméras : {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
