#!/usr/bin/env python3
"""Select local images without flattening names; guard workspace reuse."""

import argparse
import hashlib
from itertools import islice
import json
import math
import os
from pathlib import Path
import shutil
import sys

from PIL import Image

from prepare_masks import policies


EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
GPS_IFD = 0x8825


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gps_coordinate(gps, ref_tag, value_tag, positive_ref, negative_ref, limit):
    reference = gps.get(ref_tag)
    if isinstance(reference, bytes):
        reference = reference.decode("ascii", errors="ignore")
    if reference not in (positive_ref, negative_ref):
        return None
    values = gps.get(value_tag)
    if not isinstance(values, (tuple, list)) or len(values) != 3:
        return None
    try:
        degrees, minutes, seconds = (float(value) for value in values)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if (not all(math.isfinite(value) for value in (degrees, minutes, seconds))
            or degrees < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60
            or degrees > limit or (degrees == limit and (minutes or seconds))):
        return None
    coordinate = degrees + minutes / 60 + seconds / 3600
    return -coordinate if reference == negative_ref else coordinate


def gps_exif_metadata(images, gpx_configured):
    if gpx_configured:
        return {"status": "superseded_by_gpx"}

    coordinates = []
    altitude_images = 0
    unreadable_images = 0
    for relative, resolved, _, _ in images:
        try:
            with Image.open(resolved) as image:
                gps = image.getexif().get_ifd(GPS_IFD)
        except (OSError, ValueError):
            unreadable_images += 1
            continue
        latitude = _gps_coordinate(gps, 1, 2, "N", "S", 90)
        longitude = _gps_coordinate(gps, 3, 4, "E", "W", 180)
        if latitude is None or longitude is None:
            continue
        altitude = None
        try:
            altitude_value = float(gps[6])
            if math.isfinite(altitude_value):
                altitude_ref = gps.get(5, 0)
                if isinstance(altitude_ref, bytes):
                    altitude_ref = int.from_bytes(altitude_ref, "big")
                altitude = -altitude_value if altitude_ref == 1 else altitude_value
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
        if altitude is not None:
            altitude_images += 1
        coordinates.append([
            relative, format(latitude, ".12g"), format(longitude, ".12g"),
            None if altitude is None else format(altitude, ".12g"),
        ])

    tagged_images = len(coordinates)
    metric_gps = "none"
    if tagged_images >= 2:
        metric_gps = "full" if altitude_images == tagged_images else "horizontal"
    digest = hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": "available" if tagged_images >= 2 else "insufficient",
        "geotagged_images": tagged_images,
        "altitude_images": altitude_images,
        "metric_gps": metric_gps,
        "coordinates_sha256": digest,
        "unreadable_images": unreadable_images,
    }


def record_differences(previous, current, path=""):
    """Yield bounded, field-level diagnostics without changing reuse decisions."""
    if (path == "images" and isinstance(previous, list) and isinstance(current, list)
            and all(isinstance(image, list) and len(image) == 4
                    and isinstance(image[0], str) for image in previous + current)):
        fields = ("resolved", "size", "mtime_ns")
        previous = {image[0]: dict(zip(fields, image[1:])) for image in previous}
        current = {image[0]: dict(zip(fields, image[1:])) for image in current}
    if isinstance(previous, dict) and isinstance(current, dict):
        for key in sorted(previous.keys() | current.keys()):
            name = f"{path}.{key}" if path else key
            if key not in previous:
                yield f"{name} : ajouté dans la signature actuelle"
            elif key not in current:
                yield f"{name} : absent de la signature actuelle"
            else:
                yield from record_differences(previous[key], current[key], name)
    elif previous != current:
        old = repr(previous)
        new = repr(current)
        yield f"{path} : enregistré={old[:200]} ; actuel={new[:200]}"


def compatible_record(previous, current):
    if any(previous.get(key) != current.get(key)
           for key in ("source", "images", "georeference", "gps_exif", "colmap_policy")):
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
    # Keep old unfiltered signatures compatible without adopting new provenance.
    if settings.get("SFM_FILTER_DENSITY") == "false":
        settings.pop("SFM_FILTER_DENSITY")
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
    record["gps_exif"] = gps_exif_metadata(
        images, bool(os.environ.get("GEOREF_GPX_FILE"))
    )
    method = os.environ.get("COLMAP_METHOD", "spirula")
    if method != "spirula":
        record["colmap_policy"] = {
            "method": method,
            "matching": os.environ["COLMAP_MATCHING"],
            "gpu": os.environ["COLMAP_USE_GPU"],
        }
        for key in ("ORIENTATIONS_JSON", "LIDAR_PLY"):
            if method == "orientations":
                path = Path(os.environ[key]).resolve()
                record["colmap_policy"][key] = {
                    "path": str(path),
                    "sha256": file_sha256(path),
                }
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
                differences = list(islice(record_differences(previous, record), 13))
                details = "\n".join(f"  - {item}" for item in differences[:12])
                if len(differences) > 12:
                    details += "\n  - Autres différences non affichées."
                raise ValueError("Images ou paramètres SfM/masques/découpage/GPX modifiés : "
                                 "choisir un nouvel OUTPUT_DIR. TRAIN_CLEAN ne recalcule pas le SfM."
                                 f"\nSignature comparée : {marker}"
                                 f"\nPIPELINE_RESUME={'true' if resume else 'false'}"
                                 f" ; contrôle seul={'true' if check else 'false'}"
                                 f"\nDifférences enregistrées / actuelles :\n{details}")
            adopted = True
    elif check:
        raise ValueError("Préparation absente : lancer --phase prepare.")
    elif any((output / name).exists() for name in
             ("images", "sfm", "masks", "feature_masks", "train_masks",
              "sparse_filtered", "sparse_clean", "sparse_rejected", "blocks", "gs", "gs-nerfstudio")):
        if not resume:
            raise ValueError("Workspace sans inputs.json : choisir un nouvel OUTPUT_DIR.")
        adopted = True
    if adopted and any((output / name).exists()
                       for name in ("georef", "sparse_filtered", "sparse_clean",
                                    "sparse_rejected", "blocks", "gs", "gs-nerfstudio")):
        raise ValueError(
            "Reprise sans signature actuelle refusée après recalage/découpage/entraînement "
            "ou filtrage sparse : "
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
    if record["gps_exif"].get("unreadable_images"):
        print(
            "[AVERTISSEMENT] GPS EXIF non lisible pour "
            f"{record['gps_exif']['unreadable_images']} image(s).",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.check)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[ERREUR] Sélection des images : {exc}\n")
