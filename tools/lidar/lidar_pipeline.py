#!/usr/bin/env python3
"""Fetch IGN LiDAR over a georeferenced Gaussian scene, align it, then filter it."""

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np

from common import Reporter, class_codes, gaussian_geometry, open_scene, vector, write_report
from common import validate_support_opacity
from fetch_ign_lidar import CRS, fetch_lidar, validate_cached_lidar
from geographic import local_laz, projected_enu, reference_metadata


def scene_bbox(scene, origin, margin, sigma=3.):
    if not np.isfinite(margin) or margin <= 0:
        raise ValueError("Marge attendue finie et > 0")
    lower, upper = np.full(3, np.inf), np.full(3, -np.inf)
    for start in range(0, scene.count, 100000):
        xyz, scales, rotations = gaussian_geometry(scene, slice(start, start+100000))
        extent = sigma * np.sqrt(np.sum((rotations * scales[:, None, :])**2, axis=2))
        lower = np.minimum(lower, (xyz-extent).min(axis=0))
        upper = np.maximum(upper, (xyz+extent).max(axis=0))
    return np.array([lower[0]+origin[0]-margin, lower[1]+origin[1]-margin,
                     upper[0]+origin[0]+margin, upper[1]+origin[1]+margin])


def scene_projected_bbox(scene, reference, margin, sigma):
    lower, upper = np.full(2, np.inf), np.full(2, -np.inf)
    for start in range(0, scene.count, 10000):
        xyz, scales, rotations = gaussian_geometry(scene, slice(start, start+10000))
        extent = sigma * np.sqrt(np.sum((rotations * scales[:, None, :])**2, axis=2))
        for signs in ((x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)):
            projected = projected_enu(xyz + extent*np.asarray(signs),
                                      reference["origin_lat_lon_height"], CRS)
            lower = np.minimum(lower, projected[:, :2].min(axis=0))
            upper = np.maximum(upper, projected[:, :2].max(axis=0))
    return np.concatenate((lower-margin, upper+margin))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args):
    if args.lidar_file is None and not shutil.which(args.pdal):
        raise ValueError("PDAL introuvable ; installer PDAL avec le lecteur COPC")
    if args.output_dir.exists():
        raise ValueError("Choisir un nouveau --output-dir ; aucune reprise implicite")
    origin = vector(args.origin)
    reference, _ = reference_metadata(args.input.parent, args.georef)
    if reference is not None and args.origin != "0,0,0":
        raise ValueError("Splat ENU : ne pas fournir --origin")
    validate_support_opacity(args.min_support_opacity)
    for value, label in ((args.max_correspondence_distance, "distance de recalage"),
                         (args.max_distance, "distance de nettoyage"),
                         (args.max_area_km2, "surface maximale"), (args.voxel_size, "voxel"),
                         (args.support_sigma, "sigma")):
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{label} attendu fini et > 0")
    if not np.isfinite(args.spacing_factor) or args.spacing_factor < 0:
        raise ValueError("Facteur de densité attendu fini >= 0")
    if args.margin < args.max_correspondence_distance + args.max_distance:
        raise ValueError("La marge doit couvrir distance de recalage + distance de nettoyage")
    if args.lidar_stride < 1 or args.max_tiles < 1 or not 0 < args.min_overlap <= 1:
        raise ValueError("Stride/tuiles >= 1 et recouvrement dans ]0,1] requis")
    for codes in (args.align_classes, args.filter_classes):
        if codes is not None:
            class_codes(codes)
    scene = open_scene(args.input)
    try:
        bbox = (scene_bbox(scene, origin, args.margin, args.support_sigma)
                if reference is None else
                scene_projected_bbox(scene, reference, args.margin, args.support_sigma))
    finally:
        scene.close()
    area = (bbox[2]-bbox[0]) * (bbox[3]-bbox[1]) / 1e6
    if area > args.max_area_km2:
        raise ValueError(f"Emprise trop grande ({area:.2f} km²) : vérifier origine/splats "
                         "ou augmenter --max-area-km2")
    if not (0 < bbox[0] < bbox[2] < 1400000 and 6000000 < bbox[1] < bbox[3] < 7300000):
        raise ValueError("Emprise incompatible avec Lambert-93 métropolitain : vérifier --origin/--crs")
    if args.lidar_file is not None:
        validate_cached_lidar(args.lidar_file, bbox)
    output = args.output_dir
    output.mkdir(parents=True)
    suffix = args.input.suffix.lower()
    laz = args.lidar_file if args.lidar_file is not None else output / "lidar.laz"
    aligned, cleaned = output / ("aligned"+suffix), output / ("cleaned"+suffix)
    rejected = output / ("rejected"+suffix)
    state_path = output / "pipeline.json"
    state = {"status": "fetching", "input": str(args.input.resolve()),
             "input_size": args.input.stat().st_size, "input_sha256": sha256(args.input),
             "crs": args.crs, "origin": origin.tolist(), "bbox": bbox.tolist(),
             "parameters": {key: str(value) if isinstance(value, Path) else value
                            for key, value in vars(args).items()}}
    write_report(state_path, state)
    reporter = Reporter(output, "lidar_splat_pipeline")
    reporter.__enter__()
    def update(status):
        state["status"] = status
        temporary = output / ".pipeline.json.tmp"
        write_report(temporary, state)
        temporary.replace(state_path)
    try:
        reporter.phase("récupération ou réutilisation LiDAR")
        if args.lidar_file is None:
            fetch_lidar(bbox, laz, pdal=args.pdal, max_tiles=args.max_tiles)
        else:
            print(f"[lidar] Réutilisation du LAZ partagé : {laz}", flush=True)
        working_laz = laz
        if reference is not None:
            working_laz = output / "lidar_local.laz"
            local_laz(laz, working_laz, reference)
            write_report(output/"georef_stats.json", {"output_reference": reference})
        directory = Path(__file__).resolve().parent
        shared = ["--lidar", str(working_laz), "--origin", args.origin,
                  "--lidar-stride", str(args.lidar_stride)]
        align = [sys.executable, str(directory / "align_splats_to_lidar.py"), *shared,
                 "--input", str(args.input), "--output", str(aligned),
                 "--max-correspondence-distance", str(args.max_correspondence_distance),
                 "--voxel-size", str(args.voxel_size), "--min-overlap", str(args.min_overlap)]
        if args.estimate_scale:
            align.append("--estimate-scale")
        if args.align_classes is not None:
            align.extend(["--lidar-classes", args.align_classes])
        update("aligning")
        reporter.phase("recalage du splat")
        subprocess.run(align, check=True)
        # A large correction can move points outside the fetched footprint.
        checked = open_scene(aligned)
        try:
            new_bbox = (scene_bbox(checked, origin, args.max_distance, args.support_sigma)
                        if reference is None else scene_projected_bbox(
                            checked, reference, args.max_distance, args.support_sigma))
        finally:
            checked.close()
        if np.any(new_bbox[:2] < bbox[:2]) or np.any(new_bbox[2:] > bbox[2:]):
            raise ValueError("Scène recalée hors couverture extraite : relancer avec davantage de marge")
        clean = [sys.executable, str(directory / "filter_splats_by_lidar.py"), *shared,
                 "--input", str(aligned), "--output", str(cleaned),
                 "--max-distance", str(args.max_distance), "--rejected-output", str(rejected),
                 "--support-sigma", str(args.support_sigma),
                 "--spacing-factor", str(args.spacing_factor)]
        if args.filter_classes is not None:
            clean.extend(["--lidar-classes", args.filter_classes])
        if args.min_support_opacity is not None:
            clean.extend(["--min-support-opacity", str(args.min_support_opacity)])
        if args.dry_run_clean:
            clean.append("--dry-run")
        update("filtering")
        reporter.phase("nettoyage du splat")
        subprocess.run(clean, check=True)
        update("complete")
        print(f"[lidar] Terminé : {output}", flush=True)
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as error:
        state["failed_stage"] = state["status"]
        state["error"] = str(error)
        update("failed")
        raise
    finally:
        reporter.__exit__(*sys.exc_info())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--lidar-file", type=Path, help="LAZ IGN déjà extrait avec son manifeste")
    parser.add_argument("--georef", type=Path, help="Référence ENU du modèle/splat")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--crs", choices=[CRS], required=True)
    parser.add_argument("--origin", default="0,0,0")
    parser.add_argument("--margin", type=float, default=10, help="Marge XY en mètres")
    parser.add_argument("--max-correspondence-distance", type=float, default=2)
    parser.add_argument("--max-distance", type=float, default=0.5)
    parser.add_argument("--support-sigma", type=float, default=3)
    parser.add_argument("--min-support-opacity", type=float, default=0.05,
                        help="Opacité locale minimale du support LiDAR ; défaut : 0.05")
    parser.add_argument("--spacing-factor", type=float, default=1)
    parser.add_argument("--voxel-size", type=float, default=0.2)
    parser.add_argument("--min-overlap", type=float, default=0.3)
    parser.add_argument("--estimate-scale", action="store_true")
    parser.add_argument("--align-classes", default="2,3,4,5,6,67",
                        help="Classes LiDAR pour le recalage ; défaut : sol, végétation, bâti et 67")
    parser.add_argument("--filter-classes", help="Classes LiDAR pour le nettoyage ; défaut : toutes")
    parser.add_argument("--lidar-stride", type=int, default=1)
    parser.add_argument("--pdal", default="pdal")
    parser.add_argument("--max-tiles", type=int, default=100)
    parser.add_argument("--max-area-km2", type=float, default=25)
    parser.add_argument("--dry-run-clean", action="store_true", help="Recalage réel, nettoyage en diagnostic")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"[ERREUR] Pipeline LiDAR : {error}\n")


if __name__ == "__main__":
    main()
