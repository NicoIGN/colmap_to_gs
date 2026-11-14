#!/usr/bin/env python3
"""Retrieve reusable IGN LiDAR, align COLMAP and optionally enrich its tracks."""

from pathlib import Path
import subprocess

import numpy as np
import pycolmap

import align_colmap_to_lidar as aligner
from common import vector, write_report
from fetch_ign_lidar import (CRS, fetch_lidar, file_signature, validate_cached_lidar)


def extraction_bbox(model, args):
    points = np.array([point.xyz for point in model.points3D.values()])
    if len(points) < 6 or not np.isfinite(points).all():
        raise ValueError("Au moins six points sparse finis requis")
    if not np.isfinite(args.margin) or args.margin <= 0:
        raise ValueError("Marge finie > 0 requise")
    if args.margin < args.max_correspondence_distance:
        raise ValueError("Marge inférieure à la distance de recalage")
    reference, _ = aligner.reference_metadata(args.model, args.georef)
    if reference is not None:
        if args.terrain_center is not None or args.origin != "0,0,0":
            raise ValueError("Modèle ENU : ne pas fournir --terrain-center/--origin")
        projected = aligner.projected_enu(points, reference["origin_lat_lon_height"], CRS)
        lower, upper = projected[:, :2].min(axis=0), projected[:, :2].max(axis=0)
    elif args.terrain_center is None and args.images is not None and not args.already_projected:
        if args.origin != "0,0,0":
            raise ValueError("Modèle EXIF : ne pas fournir --origin")
        (scale, rotation, translation), reference = aligner.exif_initialization(model, args.images)
        initialized = scale * points @ rotation.T + translation
        projected = aligner.projected_enu(initialized, reference["origin_lat_lon_height"], CRS)
        lower, upper = projected[:, :2].min(axis=0), projected[:, :2].max(axis=0)
    elif args.terrain_center is not None:
        if (not np.isfinite(args.scale_max) or not np.isfinite(args.scale_min)
                or not 0 < args.scale_min <= args.scale_max
                or not np.isfinite(args.search_radius) or args.search_radius < 0):
            raise ValueError("Échelles/rayon de recherche invalides")
        center = vector(args.terrain_center)
        # Bounding sphere covers any searched yaw and permitted tilt at maximum scale.
        radius = np.linalg.norm(points-points.mean(axis=0), axis=1).max() * args.scale_max
        radius += args.search_radius
        lower, upper = center[:2]-radius, center[:2]+radius
    elif args.already_projected:
        lower, upper = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
    else:
        raise ValueError("Ancrage absent : fournir --terrain-center ou --images GPS EXIF")
    return np.concatenate((lower-args.margin, upper+args.margin))


def run(args):
    if args.lidar_crs != CRS:
        raise ValueError("Récupération IGN limitée à EPSG:2154")
    if args.max_tiles < 1:
        raise ValueError("Nombre maximal de tuiles attendu >= 1")
    if args.output is None:
        args.output = args.workspace / "colmap_lidar"
    if args.output.exists():
        raise ValueError("Sortie COLMAP déjà présente ; choisir un nouveau --output")
    if args.inject and (args.images is None or not args.images.is_dir()):
        raise ValueError("--images requis pour l'enrichissement avec tracks")
    model = pycolmap.Reconstruction(args.model)
    if not model.is_valid():
        raise ValueError("Modèle COLMAP incohérent")
    bbox = extraction_bbox(model, args)
    area = np.prod(bbox[2:]-bbox[:2])/1e6
    if not np.isfinite(args.max_area_km2) or args.max_area_km2 <= 0 or area > args.max_area_km2:
        raise ValueError(f"Emprise {area:.2f} km² trop grande : ajuster recherche ou --max-area-km2")
    if not (0 < bbox[0] < bbox[2] < 1400000 and 6000000 < bbox[1] < bbox[3] < 7300000):
        raise ValueError("Emprise incompatible avec Lambert-93 métropolitain")
    shared = args.workspace / "lidar"
    laz = shared / "lidar.laz"
    manifest = laz.with_suffix(".laz.json")
    reused = laz.exists() or manifest.exists()
    if reused:
        validate_cached_lidar(laz, bbox)
        print(f"[lidar] Réutilisation vérifiée : {laz}", flush=True)
    else:
        fetch_lidar(bbox, laz, pdal=args.pdal, max_tiles=args.max_tiles)
    # No rewriting of the shared extraction: its original extent and hash remain authoritative.
    args.lidar = laz
    state = {"status": "aligning", "lidar": str(laz.resolve()), "reused": reused,
             "bbox": bbox.tolist(), "model": str(args.model.resolve()),
             "output": str(args.output.resolve()),
             "lidar_signature": file_signature(laz),
             "parameters": {key: str(value) if isinstance(value, Path) else value
                            for key, value in vars(args).items()}}
    state_path = shared / (args.output.name + ".pipeline.json")
    if state_path.exists():
        raise ValueError(f"Rapport déjà présent : {state_path}")
    write_report(state_path, state)
    try:
        aligner.run(args)
        state["status"] = "complete"
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.CalledProcessError) as error:
        state.update(status="failed", error=str(error))
        raise
    finally:
        temporary = state_path.with_suffix(".json.tmp")
        write_report(temporary, state)
        temporary.replace(state_path)
    print(f"[lidar] LAZ partagé pour le splat final : {laz}", flush=True)


def main():
    parser = aligner.build_parser(pipeline=True)
    parser.description = __doc__
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--margin", type=float, default=10)
    parser.add_argument("--pdal", default="pdal")
    parser.add_argument("--max-tiles", type=int, default=100)
    parser.add_argument("--max-area-km2", type=float, default=25)
    args = parser.parse_args()
    if args.lidar is not None:
        parser.error("Le maître gère le LAZ dans workspace/lidar ; utiliser le script autonome pour --lidar")
    try:
        run(args)
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"[ERREUR] Pipeline COLMAP/LiDAR : {error}\n")


if __name__ == "__main__":
    main()
