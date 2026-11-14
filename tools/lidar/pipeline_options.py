#!/usr/bin/env python3
"""Shared configuration and lifecycle hooks for image, video and Panoramax pipelines."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np

from common import write_report
from fetch_ign_lidar import file_signature, validate_cached_lidar


DEFAULTS = {
    "LIDAR_COLMAP_ALIGN": "false", "LIDAR_COLMAP_INJECT": "false",
    "LIDAR_COLMAP_TERRAIN_CENTER": "", "LIDAR_COLMAP_ORIGIN": "0,0,0",
    "LIDAR_COLMAP_GEOREF": "", "LIDAR_COLMAP_ALREADY_PROJECTED": "false",
    "LIDAR_MARGIN": "20", "LIDAR_PDAL": "pdal", "LIDAR_MAX_TILES": "100",
    "LIDAR_MAX_AREA_KM2": "25",
    "LIDAR_COLMAP_DISTANCE": "2", "LIDAR_COLMAP_VOXEL": "0.2",
    "LIDAR_COLMAP_MIN_OVERLAP": "0.3", "LIDAR_COLMAP_CLASSES": "2,3,4,5,6,67",
    "LIDAR_COLMAP_SCALE_MIN": "0.1", "LIDAR_COLMAP_SCALE_MAX": "10",
    "LIDAR_COLMAP_SCALE_STEPS": "9", "LIDAR_COLMAP_YAW_STEPS": "36",
    "LIDAR_COLMAP_SEARCH_RADIUS": "5", "LIDAR_COLMAP_TRANSLATION_STEP": "5",
    "LIDAR_COLMAP_MAX_TILT": "10", "LIDAR_COLMAP_ACCEPT_AMBIGUOUS": "false",
    "LIDAR_COLMAP_INJECT_VOXEL": "0.2", "LIDAR_COLMAP_INJECT_MAX_POINTS": "50000",
    "LIDAR_COLMAP_INJECT_MIN_SEPARATION": "0.1", "LIDAR_COLMAP_MIN_VIEWS": "2",
    "LIDAR_COLMAP_MIN_VIEW_ANGLE": "2", "LIDAR_COLMAP_DEPTH_TOLERANCE": "0.1",
    "LIDAR_COLMAP_VISIBILITY_CELL": "4", "LIDAR_COLMAP_KEYPOINT_SEPARATION": "2",
    "LIDAR_CLEAN": "false", "LIDAR_CLEAN_MODE": "diagnostic",
    "LIDAR_CLEAN_DISTANCE": "0.5", "LIDAR_CLEAN_SUPPORT_SIGMA": "3",
    "LIDAR_CLEAN_SPACING_FACTOR": "1", "LIDAR_CLEAN_MIN_OPACITY": "0.05",
    "LIDAR_CLEAN_CLASSES": "", "LIDAR_CLEAN_GEOREF": "",
    "LIDAR_CLEAN_ORIGIN": "0,0,0",
}
FLAGS = {"LIDAR_COLMAP_ALIGN", "LIDAR_COLMAP_INJECT",
         "LIDAR_COLMAP_ALREADY_PROJECTED", "LIDAR_COLMAP_ACCEPT_AMBIGUOUS", "LIDAR_CLEAN"}
COLMAP_FLAGS = {
    "TERRAIN_CENTER": "terrain-center", "ORIGIN": "origin", "GEOREF": "georef",
    "DISTANCE": "max-correspondence-distance", "VOXEL": "voxel-size",
    "MIN_OVERLAP": "min-overlap", "CLASSES": "lidar-classes",
    "SCALE_MIN": "scale-min", "SCALE_MAX": "scale-max", "SCALE_STEPS": "scale-steps",
    "YAW_STEPS": "yaw-steps", "SEARCH_RADIUS": "search-radius",
    "TRANSLATION_STEP": "translation-step", "MAX_TILT": "max-tilt",
    "INJECT_VOXEL": "inject-voxel", "INJECT_MAX_POINTS": "inject-max-points",
    "INJECT_MIN_SEPARATION": "inject-min-separation", "MIN_VIEWS": "min-views",
    "MIN_VIEW_ANGLE": "min-view-angle", "DEPTH_TOLERANCE": "depth-tolerance",
    "VISIBILITY_CELL": "visibility-cell", "KEYPOINT_SEPARATION": "keypoint-separation",
}


def settings():
    return {key: os.environ.get(key, default) for key, default in DEFAULTS.items()}


def validate(config):
    import numpy as np
    from common import class_codes, validate_support_opacity, vector

    for key in FLAGS:
        if config[key] not in ("true", "false"):
            raise ValueError(f"{key} attend true ou false")
    if config["LIDAR_COLMAP_INJECT"] == "true" and config["LIDAR_COLMAP_ALIGN"] != "true":
        raise ValueError("LIDAR_COLMAP_INJECT nécessite LIDAR_COLMAP_ALIGN=true")
    if config["LIDAR_CLEAN_MODE"] not in ("diagnostic", "apply"):
        raise ValueError("LIDAR_CLEAN_MODE attend diagnostic ou apply")
    for key in ("LIDAR_COLMAP_ORIGIN", "LIDAR_CLEAN_ORIGIN"):
        vector(config[key])
    if config["LIDAR_COLMAP_TERRAIN_CENTER"]:
        vector(config["LIDAR_COLMAP_TERRAIN_CENTER"])
    validate_support_opacity(float(config["LIDAR_CLEAN_MIN_OPACITY"]))
    for key in ("LIDAR_COLMAP_CLASSES", "LIDAR_CLEAN_CLASSES"):
        if config[key]:
            class_codes(config[key])
    positive = ("LIDAR_MARGIN", "LIDAR_MAX_TILES", "LIDAR_MAX_AREA_KM2",
                "LIDAR_COLMAP_DISTANCE", "LIDAR_COLMAP_VOXEL", "LIDAR_COLMAP_MIN_OVERLAP",
                "LIDAR_COLMAP_SCALE_MIN", "LIDAR_COLMAP_SCALE_MAX",
                "LIDAR_COLMAP_SCALE_STEPS", "LIDAR_COLMAP_YAW_STEPS",
                "LIDAR_COLMAP_TRANSLATION_STEP", "LIDAR_COLMAP_INJECT_VOXEL",
                "LIDAR_COLMAP_INJECT_MAX_POINTS", "LIDAR_COLMAP_MIN_VIEWS",
                "LIDAR_COLMAP_MIN_VIEW_ANGLE", "LIDAR_COLMAP_VISIBILITY_CELL",
                "LIDAR_CLEAN_DISTANCE", "LIDAR_CLEAN_SUPPORT_SIGMA")
    nonnegative = ("LIDAR_COLMAP_SEARCH_RADIUS", "LIDAR_COLMAP_MAX_TILT",
                   "LIDAR_COLMAP_INJECT_MIN_SEPARATION", "LIDAR_COLMAP_DEPTH_TOLERANCE",
                   "LIDAR_COLMAP_KEYPOINT_SEPARATION", "LIDAR_CLEAN_SPACING_FACTOR")
    for key in positive + nonnegative:
        value = float(config[key])
        if not np.isfinite(value) or value < 0 or (key in positive and value == 0):
            raise ValueError(f"{key} invalide")
    for key in ("LIDAR_MAX_TILES", "LIDAR_COLMAP_SCALE_STEPS", "LIDAR_COLMAP_YAW_STEPS",
                "LIDAR_COLMAP_INJECT_MAX_POINTS", "LIDAR_COLMAP_MIN_VIEWS",
                "LIDAR_COLMAP_VISIBILITY_CELL"):
        if not config[key].isdigit():
            raise ValueError(f"{key} attend un entier")
    if float(config["LIDAR_COLMAP_MIN_OVERLAP"]) > 1:
        raise ValueError("LIDAR_COLMAP_MIN_OVERLAP doit être <= 1")
    if float(config["LIDAR_COLMAP_SCALE_MIN"]) > float(config["LIDAR_COLMAP_SCALE_MAX"]):
        raise ValueError("Échelles LiDAR inversées")
    if int(config["LIDAR_COLMAP_MIN_VIEWS"]) < 2:
        raise ValueError("Au moins deux vues requises")
    if float(config["LIDAR_COLMAP_MAX_TILT"]) >= 90:
        raise ValueError("LIDAR_COLMAP_MAX_TILT doit être < 90")
    if float(config["LIDAR_COLMAP_MIN_VIEW_ANGLE"]) >= 180:
        raise ValueError("LIDAR_COLMAP_MIN_VIEW_ANGLE doit être < 180")


def preflight(config):
    validate(config)
    if config["LIDAR_COLMAP_ALIGN"] == "true":
        import pycolmap
        import pyproj
        from PIL import Image
        import laspy
        if not laspy.LazBackend.detect_available():
            raise ValueError("Backend LAZ absent : installer lazrs")
    if config["LIDAR_CLEAN"] == "true":
        import laspy
        import pyproj
    if config["LIDAR_COLMAP_ALIGN"] == "true" or config["LIDAR_CLEAN"] == "true":
        for key in ("LIDAR_COLMAP_GEOREF", "LIDAR_CLEAN_GEOREF"):
            if config[key] and not Path(config[key]).is_file():
                raise ValueError(f"Référence absente : {key}")
        # Reuse of an existing shared extraction does not require PDAL.
        workspace = Path(os.environ.get("LIDAR_WORKSPACE", os.environ.get("OUTPUT_DIR", ".")))
        if not (workspace/"lidar/lidar.laz").is_file() and not shutil.which(config["LIDAR_PDAL"]):
            raise ValueError("LiDAR activé mais PDAL absent et aucun LAZ partagé disponible")


def prepare(config, workspace, model, images, check=False):
    if config["LIDAR_COLMAP_ALIGN"] != "true":
        return
    import pycolmap
    from geographic import reference_metadata

    marker = workspace/"lidar/colmap_inputs.json"
    output = workspace/"colmap_lidar"
    files = [model / f"{name}.{ext}" for name in ("cameras", "images", "points3D", "rigs", "frames")
             for ext in ("bin", "txt") if (model/f"{name}.{ext}").is_file()]
    signatures = {str(p.resolve()): file_signature(p) for p in files}
    _, ref_path = reference_metadata(model,
                                    Path(config["LIDAR_COLMAP_GEOREF"])
                                    if config["LIDAR_COLMAP_GEOREF"] else None)
    if ref_path is not None:
        signatures[str(ref_path.resolve())] = file_signature(ref_path)
    image_signatures = {str(p.relative_to(images)): file_signature(p)
                        for p in sorted(images.rglob("*")) if p.is_file()}
    relevant = {key: value for key, value in config.items()
                if key.startswith("LIDAR_COLMAP_") or key in
                ("LIDAR_MARGIN", "LIDAR_MAX_TILES", "LIDAR_MAX_AREA_KM2", "LIDAR_PDAL")}
    expected = {"settings": relevant, "model": signatures, "images": image_signatures}
    if marker.exists():
        previous = json.loads(marker.read_text())
        if previous.get("inputs") != expected:
            raise ValueError("Paramètres/entrées LiDAR modifiés : choisir un nouveau workspace")
        if previous.get("output_signature") != {
                p.name: file_signature(p) for p in sorted(output.glob("*")) if p.is_file()}:
            raise ValueError("Modèle COLMAP LiDAR altéré ou incomplet")
        if not pycolmap.Reconstruction(output).is_valid():
            raise ValueError("Modèle COLMAP LiDAR incohérent")
        validate_cached_lidar(workspace/"lidar/lidar.laz",
                              np.array(previous["bbox"]))
        return
    if check or output.exists():
        raise ValueError("Recalage LiDAR incomplet : relancer prepare dans un nouveau workspace")
    command = [sys.executable, str(Path(__file__).with_name("colmap_lidar_pipeline.py")),
               "--workspace", str(workspace), "--model", str(model), "--images", str(images),
               "--margin", config["LIDAR_MARGIN"], "--pdal", config["LIDAR_PDAL"],
               "--max-tiles", config["LIDAR_MAX_TILES"],
               "--max-area-km2", config["LIDAR_MAX_AREA_KM2"]]
    for suffix, flag in COLMAP_FLAGS.items():
        value = config["LIDAR_COLMAP_"+suffix]
        if value:
            command.extend(["--"+flag, value])
    for key, flag in (("LIDAR_COLMAP_INJECT", "inject"),
                      ("LIDAR_COLMAP_ALREADY_PROJECTED", "already-projected"),
                      ("LIDAR_COLMAP_ACCEPT_AMBIGUOUS", "accept-ambiguous")):
        if config[key] == "true":
            command.append("--"+flag)
    subprocess.run(command, check=True)
    manifest = json.loads((workspace/"lidar/lidar.laz.json").read_text())
    write_report(marker, {"inputs": expected, "bbox": manifest["bbox"],
                         "output_signature": {p.name: file_signature(p)
                                              for p in sorted(output.glob("*")) if p.is_file()}})


def clean(config, workspace, splat):
    if config["LIDAR_CLEAN"] != "true":
        return
    from geographic import local_laz, reference_metadata
    from common import open_scene, vector
    from lidar_pipeline import scene_bbox, scene_projected_bbox

    output = workspace/"lidar_splat"
    marker = output/"inputs.json"
    shared = workspace/"lidar/lidar.laz"
    references = [Path(config[key]) for key in ("LIDAR_CLEAN_GEOREF", "LIDAR_COLMAP_GEOREF")
                  if config[key]]
    implicit = workspace/"colmap_lidar/georef_stats.json"
    if implicit.is_file():
        references.append(implicit)
    expected = {"splat": file_signature(splat), "settings": config,
                "lidar": file_signature(shared) if shared.exists() else None,
                "references": {str(p): file_signature(p) for p in references}}
    if marker.exists():
        previous = json.loads(marker.read_text())
        if (previous.get("settings") != config or previous.get("lidar") != expected["lidar"]
                or previous.get("references") != expected["references"]
                or expected["splat"] not in
                (previous.get("splat"), previous.get("result"))):
            raise ValueError("Nettoyage LiDAR existant incompatible : nouveau workspace requis")
        if config["LIDAR_CLEAN_MODE"] == "apply":
            cleaned = output/"cleaned.splat"
            if file_signature(cleaned) != previous.get("result"):
                raise ValueError("Splat nettoyé altéré")
            if expected["splat"] != previous["result"]:
                temporary = splat.with_suffix(".lidar.tmp")
                shutil.copy2(cleaned, temporary)
                temporary.replace(splat)
        elif not (output/"cleaned.splat.json").is_file():
            raise ValueError("Rapport de diagnostic LiDAR absent")
        return
    if output.exists():
        raise ValueError("Nettoyage LiDAR partiel : sauvegarder le dossier lidar_splat")
    aligned = config["LIDAR_COLMAP_ALIGN"] == "true"
    georef = config["LIDAR_CLEAN_GEOREF"]
    if aligned and not georef and (workspace/"colmap_lidar/georef_stats.json").is_file():
        georef = str(workspace/"colmap_lidar/georef_stats.json")
    flags = ["--max-distance", config["LIDAR_CLEAN_DISTANCE"],
             "--support-sigma", config["LIDAR_CLEAN_SUPPORT_SIGMA"],
             "--spacing-factor", config["LIDAR_CLEAN_SPACING_FACTOR"],
             "--min-support-opacity", config["LIDAR_CLEAN_MIN_OPACITY"]]
    if config["LIDAR_CLEAN_CLASSES"]:
        flags += ["--lidar-classes", config["LIDAR_CLEAN_CLASSES"]]
    if aligned:
        reference, _ = reference_metadata(splat.parent, Path(georef) if georef else None)
        origin = vector(config["LIDAR_COLMAP_ORIGIN"])
        scene = open_scene(splat)
        try:
            bbox = (scene_projected_bbox(scene, reference, float(config["LIDAR_CLEAN_DISTANCE"]),
                                        float(config["LIDAR_CLEAN_SUPPORT_SIGMA"]))
                    if reference else scene_bbox(scene, origin,
                        float(config["LIDAR_CLEAN_DISTANCE"]),
                        float(config["LIDAR_CLEAN_SUPPORT_SIGMA"])))
        finally:
            scene.close()
        validate_cached_lidar(shared, bbox)
        output.mkdir()
        working = shared
        if reference:
            working = output/"lidar_local.laz"
            local_laz(shared, working, reference)
            origin = np.zeros(3)
        command = [sys.executable, str(Path(__file__).with_name("filter_splats_by_lidar.py")),
                   "--input", str(splat), "--lidar", str(working),
                   "--output", str(output/"cleaned.splat"),
                   "--rejected-output", str(output/"rejected.splat"),
                   "--origin", ",".join(map(str, origin)), *flags]
        if config["LIDAR_CLEAN_MODE"] == "diagnostic":
            command.append("--dry-run")
    else:
        command = [sys.executable, str(Path(__file__).with_name("lidar_pipeline.py")),
                   "--input", str(splat), "--output-dir", str(output), "--crs", "EPSG:2154",
                   "--origin", config["LIDAR_CLEAN_ORIGIN"],
                   "--margin", config["LIDAR_MARGIN"], "--pdal", config["LIDAR_PDAL"],
                   "--max-tiles", config["LIDAR_MAX_TILES"],
                   "--max-area-km2", config["LIDAR_MAX_AREA_KM2"], *flags]
        # The master uses a separate name for its class selection.
        if "--lidar-classes" in command:
            command[command.index("--lidar-classes")] = "--filter-classes"
        if shared.exists():
            command += ["--lidar-file", str(shared)]
        if georef:
            command += ["--georef", georef]
        if config["LIDAR_CLEAN_MODE"] == "diagnostic":
            command.append("--dry-run-clean")
    subprocess.run(command, check=True)
    if config["LIDAR_CLEAN_MODE"] == "apply":
        cleaned = output/"cleaned.splat"
        if not cleaned.is_file() or cleaned.stat().st_size == 0:
            raise ValueError("Nettoyage LiDAR vide : splat final conservé")
        backup = splat.with_suffix(".before_lidar.splat")
        if backup.exists():
            raise ValueError("Sauvegarde pré-LiDAR déjà présente")
        shutil.copy2(splat, backup)
        temporary = splat.with_suffix(".lidar.tmp")
        shutil.copy2(cleaned, temporary)
        temporary.replace(splat)
        expected["result"] = file_signature(splat)
    write_report(marker, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("validate", "preflight", "prepare", "check", "clean"))
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--splat", type=Path)
    args = parser.parse_args()
    config = settings()
    try:
        validate(config)
        if args.phase == "preflight":
            preflight(config)
        elif args.phase in ("prepare", "check"):
            if args.workspace is None or args.model is None or args.images is None:
                raise ValueError("Workspace, modèle et images requis")
            prepare(config, args.workspace, args.model, args.images, args.phase == "check")
            dataset = args.workspace/"colmap_dataset.json"
            if config["LIDAR_COLMAP_ALIGN"] == "true" and dataset.is_file():
                record = json.loads(dataset.read_text())
                record["sparse_model"] = "colmap_lidar"
                record["lidar"] = "lidar/lidar.laz"
                temporary = dataset.with_suffix(".json.lidar.tmp")
                write_report(temporary, record)
                temporary.replace(dataset)
        elif args.phase == "clean":
            if args.workspace is None or args.splat is None:
                raise ValueError("Workspace et splat requis")
            clean(config, args.workspace, args.splat)
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"[ERREUR] Options pipeline LiDAR : {error}\n")


if __name__ == "__main__":
    main()
