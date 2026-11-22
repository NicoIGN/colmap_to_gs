#!/usr/bin/env python3
"""Validate artifacts before explicitly adopting an interrupted workspace."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tarfile


def prepare_partition(output, *, check=False):
    """Keep global reconstruction intact when only downstream partitioning changes."""
    manifest = output / "blocks/manifest.json"
    count = int(os.environ["SPLIT_NUM_BLOCKS"])
    overlap = float(os.environ["SPLIT_OVERLAP_MAX_FRACTION"])
    if manifest.is_file():
        try:
            record = json.loads(manifest.read_text())
        except json.JSONDecodeError:
            print(f"[reprise] Manifeste de découpage interrompu : {manifest}", flush=True)
            record = {"blocks": [], "parameters": {}}
        parameters = record.get("parameters", {})
        previous_overlap = parameters.get("overlap_max_fraction")
        if previous_overlap is None:
            legacy = output / "inputs.before_resume.json"
            if legacy.is_file():
                previous_overlap = json.loads(legacy.read_text()).get(
                    "settings", {}).get("SPLIT_OVERLAP_MAX_FRACTION")
        complete = all(
            (output / "blocks" / f"block_{index:03d}" / f"{name}.bin").is_file()
            and (output / "blocks" / f"block_{index:03d}" / f"{name}.bin").stat().st_size
            for index in range(count) for name in ("cameras", "images", "points3D")
        )
        if (len(record["blocks"]) == count and previous_overlap is not None
                and float(previous_overlap) == overlap and complete):
            return
    elif not any((output / name).exists() for name in ("blocks", "gs", "gs-nerfstudio")):
        if not check:
            return
    if check:
        raise ValueError("Découpage absent ou modifié : relancer --phase prepare avant l'entraînement/fusion.")
    locks = list(output.glob(".train-block-*.lock")) + list(output.glob(".merge-*.lock"))
    for name in ("gs", "gs-nerfstudio"):
        locks.extend(path for path in (output / name).rglob(".training_lock") if path.is_dir())
    if locks:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs/python"))
        from pipeline_lock import stage_lock
        for lock in locks:
            stage_lock(lock, os.getpid(), os.environ.get("PIPELINE_RESUME") == "true",
                       workspace=output, ignore=os.environ.get("REMOVE_LOCKS") == "true")
            stage_lock(lock, os.getpid(), False, release=True)
    export = Path(os.environ["EXPORT_SPLAT_NAME"])
    if export.name != str(export):
        raise ValueError("EXPORT_SPLAT_NAME doit être un simple nom de fichier.")
    names = ("blocks", "gs", "gs-nerfstudio", ".prepare_done",
             export.name, f"{export.stem}.georef.json")
    sources = [output / name for name in names if (output / name).exists()]
    if sources:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        backup = output / "partition-backups" / stamp
        backup.mkdir(parents=True)
        for source in sources:
            source.rename(backup / source.name)
        print(f"[découpage] Anciennes sorties conservées : {backup}", flush=True)
    print("[découpage] Nouveau découpage ; SfM global, géoréférencement et masques conservés.", flush=True)


def resumable_checkpoint(splats):
    if not (splats / "config.json").is_file():
        return ""
    json.loads((splats / "config.json").read_text())
    for checkpoint in sorted(splats.glob("step-*.ckpt"), reverse=True):
        state = checkpoint / "state.tar"
        if not state.is_file():
            continue
        try:
            with tarfile.open(state) as archive:
                manifest = archive.extractfile("state.json")
                if manifest is None:
                    raise ValueError("state.json absent")
                record = json.load(manifest)
                names = archive.getnames()
                if record.get("full_resume", 1) and any(
                        name in names for name in ("world.means.npy", "world.opacities.npy")):
                    return str(checkpoint)
        except (tarfile.TarError, OSError, ValueError, KeyError) as error:
            print(f"[reprise] Checkpoint incomplet ignoré : {checkpoint} ({error})", file=sys.stderr)
    return ""


def validate_sparse(folder, images_dir=None):
    for name in ("cameras", "images", "points3D"):
        path = folder / f"{name}.bin"
        if not path.is_file() or path.stat().st_size < 8:
            raise ValueError(f"Fichier absent ou tronqué : {path}")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "preprocess"))
    from colmap_split_blocks import (
        read_cameras_binary, read_images_binary, read_points3d_binary,
    )
    import numpy as np

    cameras = read_cameras_binary(folder / "cameras.bin")
    print(f"[sparse] Lecture cameras.bin : {len(cameras)} caméras", flush=True)
    images = read_images_binary(folder / "images.bin")
    print(f"[sparse] Lecture images.bin : {len(images)} images", flush=True)
    points = read_points3d_binary(folder / "points3D.bin")
    print(f"[sparse] Lecture points3D.bin : {len(points)} points", flush=True)
    if not cameras or not images or not points:
        raise ValueError(f"Modèle sparse vide : {folder}")
    tracks = {point_id: set(point.track) for point_id, point in points.items()}
    for camera_id, camera in cameras.items():
        if camera.width <= 0 or camera.height <= 0 or not np.isfinite(camera.params).all():
            raise ValueError(f"Caméra invalide : {camera_id}")
    root = images_dir.resolve() if images_dir is not None else None
    for image_id, image in images.items():
        if root is not None:
            relative = Path(image.name)
            path = (root / relative).resolve()
            if (relative.is_absolute() or ".." in relative.parts
                    or not path.is_relative_to(root) or not path.is_file()):
                raise ValueError(f"Image du modèle absente ou hors du dataset : {image.name}")
        if image.camera_id not in cameras:
            raise ValueError(f"Caméra absente pour l'image {image_id}")
        if (not np.isfinite(image.qvec).all() or not np.isfinite(image.tvec).all()
                or np.linalg.norm(image.qvec) == 0 or not np.isfinite(image.xys).all()):
            raise ValueError(f"Pose non finie pour l'image {image_id}")
        for index, point_id in enumerate(image.point3D_ids):
            if point_id == -1:
                continue
            point = points.get(int(point_id))
            if point is None or (image_id, index) not in tracks[int(point_id)]:
                raise ValueError(f"Observation incohérente : image {image_id}, point {point_id}")
    for point_id, point in points.items():
        if not np.isfinite(point.xyz).all() or not point.track:
            raise ValueError(f"Point invalide : {point_id}")
        for image_id, index in point.track:
            image = images.get(image_id)
            if (image is None or index >= len(image.point3D_ids)
                    or image.point3D_ids[index] != point_id):
                raise ValueError(f"Track incohérente pour le point {point_id}")
    print(
        f"[reprise] Sparse vérifié : {len(cameras)} caméras, "
        f"{len(images)} images, {len(points)} points dans {folder}.",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sparse", type=Path)
    parser.add_argument("--images", type=Path, help="Vérifier aussi les fichiers images du modèle")
    parser.add_argument("--partition", choices=("prepare", "check"),
                        help="Contrôler le découpage aval indépendamment du SfM")
    parser.add_argument("--training-checkpoint", action="store_true")
    parser.add_argument("--archive-sparse", action="store_true")
    args = parser.parse_args()
    if args.training_checkpoint:
        try:
            print(resumable_checkpoint(args.sparse))
        except (OSError, ValueError) as error:
            parser.exit(1, f"[ERREUR] Checkpoint : {error}\n")
        return
    if args.archive_sparse:
        if args.sparse.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            backup = args.sparse.parent / f"{args.sparse.name}-interrupted-{stamp}"
            args.sparse.rename(backup)
            print(f"[reprise] Sparse incomplet conservé : {backup}", flush=True)
        return
    if args.partition:
        try:
            prepare_partition(args.sparse, check=args.partition == "check")
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.exit(1, f"[ERREUR] Découpage COLMAP : {error}\n")
        return
    try:
        validate_sparse(args.sparse, args.images)
    except ImportError as error:
        parser.exit(2, f"[ERREUR] Dépendance de validation sparse absente : {error}\n")
    except (OSError, EOFError, ValueError) as error:
        parser.exit(1, f"[reprise] Sparse absent ou incomplet : {error}\n")


if __name__ == "__main__":
    main()
