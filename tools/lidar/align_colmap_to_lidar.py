#!/usr/bin/env python3
"""Recaler un modèle COLMAP sur LAS/LAZ et ajouter des tracks LiDAR synthétiques."""

import argparse
from pathlib import Path
import sys

import numpy as np
import pycolmap
from scipy.ndimage import minimum_filter
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

from common import (class_codes, load_lidar, register, vector, voxel_sample,
                    write_report)
from geographic import enu_lidar, enu_rotation, projected_enu, reference_metadata


def exif_initialization(model, images):
    from PIL import Image
    from pyproj import Transformer
    from common import fit_similarity

    local, geographic = [], []
    for image in model.images.values():
        if not image.has_pose:
            continue
        path = (images/image.name).resolve()
        if not path.is_relative_to(images.resolve()):
            raise ValueError("Nom d'image hors dossier")
        with Image.open(path) as raster:
            gps = raster.getexif().get_ifd(0x8825)
        if not all(key in gps for key in (1, 2, 3, 4, 6)):
            continue
        def degrees(values):
            return float(values[0])+float(values[1])/60+float(values[2])/3600
        lat = degrees(gps[2]) * (-1 if gps[1] in ("S", b"S") else 1)
        lon = degrees(gps[4]) * (-1 if gps[3] in ("W", b"W") else 1)
        altitude = float(gps[6]) * (-1 if gps.get(5) in (1, b"\x01") else 1)
        geographic.append([lat, lon, altitude])
        local.append(image.projection_center())
    if len(local) < 3:
        raise ValueError("Au moins trois GPS EXIF avec altitude et caméras non colinéaires "
                         "requis pour retrouver l'ancrage ENU")
    geographic = np.asarray(geographic)
    if (not np.isfinite(geographic).all() or np.any(np.abs(geographic[:, 0]) > 90)
            or np.any(np.abs(geographic[:, 1]) > 180)):
        raise ValueError("Coordonnées GPS EXIF invalides")
    origin = geographic.mean(axis=0)
    ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    target = np.column_stack(ecef.transform(geographic[:, 1], geographic[:, 0], geographic[:, 2]))
    base = np.array(ecef.transform(origin[1], origin[0], origin[2]))
    target = (target-base) @ enu_rotation(origin).T
    s, r, t = fit_similarity(np.asarray(local), target, True)
    errors = np.linalg.norm(s*np.asarray(local) @ r.T+t-target, axis=1)
    if errors.max() > 20:
        raise ValueError("Ancrage GPS EXIF incohérent (>20 m) : fournir la référence du modèle")
    return (s, r, t), {"type": "local_ENU", "units": "meters",
                       "axes": ["East", "North", "Up"],
                       "origin_lat_lon_height": origin.tolist(),
                       "height_note": "GPS EXIF brut ; contrôler le référentiel vertical LiDAR"}


def similarity(scale, rotation, translation):
    return pycolmap.Sim3d(scale, pycolmap.Rotation3d(rotation), translation)


def nearest_score(source, tree, distance):
    distances = tree.query(source)[0]
    overlap = float(np.mean(distances <= distance))
    return float(np.mean(np.minimum(distances, distance) ** 2)), overlap


def search_alignment(points, lidar, args, initial=None):
    """Bounded yaw/scale/translation search, then Sim(3) refinement."""
    sample = voxel_sample(lidar, args.voxel_size)
    tree = cKDTree(sample)
    indices = np.linspace(0, len(points)-1, min(len(points), args.search_points), dtype=int)
    source = points[indices]
    origin = vector(args.origin)
    if args.terrain_center is None:
        starts = [initial if initial is not None else (1., np.eye(3), -origin)]
    else:
        centre = points.mean(axis=0)
        terrain = vector(args.terrain_center) - origin
        scales = np.geomspace(args.scale_min, args.scale_max, args.scale_steps)
        offsets = np.arange(-args.search_radius, args.search_radius + 1e-9,
                            args.translation_step)
        offsets = np.unique(np.append(offsets, 0.))
        count = len(scales) * args.yaw_steps * len(offsets)**3
        if count > args.max_candidates:
            raise ValueError(f"{count} candidats : augmenter le pas/réduire le rayon "
                             "ou augmenter --max-candidates")
        starts = (
            (float(s), r, terrain + np.array([x, y, z]) - s*r @ centre)
            for s in scales
            for yaw in np.linspace(0, 360, args.yaw_steps, endpoint=False)
            for r in [Rotation.from_euler("z", yaw, degrees=True).as_matrix()]
            for x in offsets for y in offsets for z in offsets
        )
    best = []
    for number, (s, r, t) in enumerate(starts):
        score, overlap = nearest_score(s*source @ r.T + t, tree,
                                       args.max_correspondence_distance)
        best.append((score, number, s, r, t, overlap))
        best.sort(key=lambda item: (item[0], item[1]))
        del best[args.refine_candidates:]
        if number and number % 1000 == 0:
            print(f"[lidar] Recherche initiale : {number} candidats évalués.", flush=True)
    failures, solutions = [], []
    for _, number, s, r, t, _ in best:
        initialized = s*source @ r.T + t
        try:
            ds, dr, dt, report = register(
                initialized, sample, distance=args.max_correspondence_distance,
                voxel=args.voxel_size, min_overlap=args.min_overlap, estimate_scale=True)
        except ValueError as error:
            failures.append({"candidate": number, "error": str(error)})
            continue
        total_s, total_r, total_t = ds*s, dr @ r, ds*dr @ t + dt
        tilt = float(np.degrees(np.arccos(np.clip(total_r[2, 2], -1, 1))))
        if args.terrain_center is not None and (
                tilt > args.max_tilt or not args.scale_min <= total_s <= args.scale_max):
            failures.append({"candidate": number, "error": "Inclinaison/échelle hors limites"})
            continue
        if args.terrain_center is not None:
            shift = total_s*total_r @ points.mean(axis=0) + total_t
            if np.any(np.abs(shift-(vector(args.terrain_center)-origin)) >
                      args.search_radius + args.max_correspondence_distance):
                failures.append({"candidate": number, "error": "Centre final hors zone de recherche"})
                continue
        score, overlap = nearest_score(total_s*source @ total_r.T + total_t,
                                       tree, args.max_correspondence_distance/4)
        solutions.append((score, total_s, total_r, total_t, report, tilt, overlap))
    if not solutions:
        raise ValueError("Aucun recalage valide. Diagnostics : " + str(failures))
    solutions.sort(key=lambda item: item[0])
    chosen = solutions[0]
    ambiguous = False
    for other in solutions[1:]:
        discrepancy = np.median(np.linalg.norm(
            chosen[1]*source @ chosen[2].T + chosen[3] -
            (other[1]*source @ other[2].T + other[3]), axis=1))
        if discrepancy > args.voxel_size and other[0] <= chosen[0]*1.1 + 1e-8:
            ambiguous = True
    if ambiguous and not args.accept_ambiguous:
        raise ValueError("Plusieurs recalages distincts de scores proches : préciser la "
                         "recherche ou --accept-ambiguous après contrôle")
    _, s, r, t, report, tilt, overlap = chosen
    report.update(candidate_failures=failures, valid_candidates=len(solutions),
                  ambiguous=ambiguous, tilt_degrees=tilt, final_overlap=overlap)
    return s, r, t, report


def visible_projections(camera, pose, points, blockers, depth_tolerance, cell_size):
    """Conservative LiDAR z-buffer; neighbouring cells also block visibility."""
    camera_points = points @ pose.rotation.matrix().T + pose.translation
    xy = camera.img_from_cam(camera_points)
    valid = (np.isfinite(xy).all(axis=1) & (camera_points[:, 2] > 0)
             & (xy[:, 0] >= 0) & (xy[:, 0] < camera.width)
             & (xy[:, 1] >= 0) & (xy[:, 1] < camera.height))
    columns = int(np.ceil(camera.width/cell_size))
    rows = int(np.ceil(camera.height/cell_size))
    depths = np.full((rows, columns), np.inf)
    for start in range(0, len(blockers), 100000):
        transformed = blockers[start:start+100000] @ pose.rotation.matrix().T + pose.translation
        uv = camera.img_from_cam(transformed)
        inside = (np.isfinite(uv).all(axis=1) & (transformed[:, 2] > 0)
                  & (uv[:, 0] >= 0) & (uv[:, 0] < camera.width)
                  & (uv[:, 1] >= 0) & (uv[:, 1] < camera.height))
        pixels = (uv[inside] / cell_size).astype(int)
        np.minimum.at(depths, (pixels[:, 1], pixels[:, 0]), transformed[inside, 2])
    depths = minimum_filter(depths, size=3, mode="constant", cval=np.inf)
    ids = np.flatnonzero(valid)
    pixels = (xy[ids]/cell_size).astype(int)
    ids = ids[camera_points[ids, 2] <= depths[pixels[:, 1], pixels[:, 0]] + depth_tolerance]
    return ids, xy[ids]


def inject_tracks(model, lidar, args):
    from PIL import Image

    candidates = voxel_sample(lidar, args.inject_voxel)
    if len(candidates) > args.inject_max_points:
        indices = np.linspace(0, len(candidates)-1, args.inject_max_points, dtype=int)
        candidates = candidates[indices]
    sparse = np.array([p.xyz for p in model.points3D.values()])
    candidates = candidates[cKDTree(sparse).query(candidates)[0] > args.inject_min_separation]
    blockers = np.concatenate((lidar, sparse))
    observations = [[] for _ in candidates]
    colors = [[] for _ in candidates]
    centers = {}
    for image in sorted(model.images.values(), key=lambda im: im.image_id):
        if not image.has_pose:
            continue
        camera = model.cameras[image.camera_id]
        path = (args.images / image.name).resolve()
        if not path.is_relative_to(args.images.resolve()):
            raise ValueError(f"Nom d'image hors dossier : {image.name}")
        with Image.open(path) as raster:
            if raster.size != (camera.width, camera.height):
                raise ValueError(f"Dimensions image/calibration incompatibles : {path}")
            rgb = np.asarray(raster.convert("RGB"))
        ids, xy = visible_projections(camera, image.cam_from_world(), candidates, blockers,
                                     args.depth_tolerance, args.visibility_cell)
        if len(image.points2D) and len(ids):
            old_xy = np.array([p.xy for p in image.points2D])
            clear = cKDTree(old_xy).query(xy)[0] > args.keypoint_separation
            ids, xy = ids[clear], xy[clear]
        # At most one synthetic observation per visibility cell and image.
        used = set()
        center = image.projection_center()
        centers[image.image_id] = center
        for index, uv in zip(ids, xy):
            cell = tuple((uv/args.visibility_cell).astype(int))
            if cell in used:
                continue
            used.add(cell)
            observations[index].append((image.image_id, uv.copy()))
            colors[index].append(rgb[int(uv[1]), int(uv[0])])
    inserted = track_observations = 0
    for xyz, views, rgb in zip(candidates, observations, colors):
        if len(views) < args.min_views:
            continue
        rays = np.array([xyz-centers[image_id] for image_id, _ in views])
        lengths = np.linalg.norm(rays, axis=1)
        if np.any(lengths <= 0):
            continue
        rays /= lengths[:, None]
        angle = np.degrees(np.arccos(np.clip(np.min(rays @ rays.T), -1, 1)))
        if angle < args.min_view_angle:
            continue
        elements = []
        for image_id, uv in views:
            image = model.images[image_id]
            index = len(image.points2D)
            image.points2D.append(pycolmap.Point2D(uv))
            elements.append(pycolmap.TrackElement(image_id, index))
        pid = model.add_point3D(xyz, pycolmap.Track(elements),
                               np.rint(np.median(rgb, axis=0)).astype("u1"))
        model.points3D[pid].error = 0.
        inserted += 1
        track_observations += len(elements)
    if not inserted:
        raise ValueError("Aucun point LiDAR visible dans assez de vues : injection refusée")
    return {"inserted": inserted, "synthetic_observations": track_observations,
            "candidates": len(candidates), "min_views": args.min_views,
            "warning": "Tracks synthétiques par projection, sans correspondance de texture "
                       "ni mise à jour database.db ; ne pas interpréter error=0 comme une validation."}


def run(args):
    if args.output.exists():
        raise ValueError("Sortie déjà présente ; choisir un nouveau dossier")
    positive = (args.voxel_size, args.max_correspondence_distance, args.translation_step,
                args.scale_min, args.scale_max, args.inject_voxel, args.visibility_cell)
    if any(not np.isfinite(v) or v <= 0 for v in positive):
        raise ValueError("Tailles, distances et échelles positives et finies requises")
    if (args.scale_min > args.scale_max or not 0 < args.min_overlap <= 1
            or not np.isfinite(args.search_radius) or args.search_radius < 0
            or not np.isfinite(args.max_tilt) or not 0 <= args.max_tilt < 90
            or min(args.yaw_steps, args.scale_steps, args.refine_candidates,
                   args.search_points, args.max_candidates, args.inject_max_points) < 1):
        raise ValueError("Paramètres de recherche invalides")
    if (args.min_views < 2 or not 0 < args.min_view_angle < 180
            or not np.isfinite(args.depth_tolerance) or args.depth_tolerance < 0
            or not np.isfinite(args.inject_min_separation) or args.inject_min_separation < 0
            or not np.isfinite(args.keypoint_separation) or args.keypoint_separation < 0):
        raise ValueError("Paramètres de visibilité invalides (au moins deux vues requises)")
    if args.inject and (args.images is None or not args.images.is_dir()):
        raise ValueError("--images requis pour l'injection de tracks et couleurs")
    origin = vector(args.origin)
    model = pycolmap.Reconstruction(args.model)
    if not model.is_valid():
        raise ValueError("Modèle COLMAP incohérent")
    points = np.array([point.xyz for point in model.points3D.values()])
    if len(points) < 6 or not np.isfinite(points).all():
        raise ValueError("Au moins six points sparse finis requis")
    reference, reference_path = reference_metadata(args.model, args.georef)
    initial = None
    if reference is not None:
        if args.terrain_center is not None or args.origin != "0,0,0":
            raise ValueError("Référence ENU détectée : ne pas fournir --terrain-center/--origin")
        initial = (1., np.eye(3), np.zeros(3))
    elif args.terrain_center is None and args.images is not None and not args.already_projected:
        if args.origin != "0,0,0":
            raise ValueError("Ancrage EXIF : ne pas fournir --origin")
        initial, reference = exif_initialization(model, args.images)
    elif args.terrain_center is None and not args.already_projected:
        raise ValueError("Modèle sans référence : fournir --terrain-center ou --images "
                         "pour l'ancrage EXIF ; --already-projected pour un modèle projeté connu")
    lidar = load_lidar(args.lidar, np.zeros(3) if reference is not None else origin)
    if args.lidar_classes is not None:
        references = load_lidar(args.lidar, np.zeros(3) if reference is not None else origin,
                                classes=class_codes(args.lidar_classes))
    else:
        references = lidar
    if reference is not None:
        lidar = enu_lidar(lidar, reference["origin_lat_lon_height"], args.lidar_crs)
        references = enu_lidar(references, reference["origin_lat_lon_height"], args.lidar_crs)
    s, r, t, report = search_alignment(points, references, args, initial)
    model.transform(similarity(s, r, t))
    injection = inject_tracks(model, lidar, args) if args.inject else None
    if not model.is_valid():
        raise ValueError("Modèle transformé/injecté incohérent : export refusé")
    args.output.mkdir(parents=True)
    model.write(args.output)
    reloaded = pycolmap.Reconstruction(args.output)
    if not reloaded.is_valid() or reloaded.num_points3D() != model.num_points3D():
        raise ValueError("Validation du modèle exporté échouée")
    report.update(scale=s, rotation=r.tolist(), translation=t.tolist(), origin=origin.tolist(),
                  output_reference=reference,
                  reference_source=None if reference_path is None else str(reference_path),
                  terrain_center=args.terrain_center, injection=injection,
                  convention="xyz_out_local = scale * R @ xyz_in + translation",
                  points_before=len(points), points_after=model.num_points3D())
    write_report(args.output/"lidar_alignment.json", report)
    if reference is not None:
        write_report(args.output/"georef_stats.json", {"output_reference": reference,
                                                      "source": "lidar_alignment.json"})
    print(f"[lidar] Modèle COLMAP recalé : {args.output}", flush=True)


def build_parser(*, pipeline=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--lidar", type=Path, required=not pipeline)
    parser.add_argument("--output", type=Path, required=not pipeline)
    parser.add_argument("--origin", default="0,0,0")
    parser.add_argument("--georef", type=Path, help="Rapport ENU ; défaut : georef_stats.json du modèle")
    parser.add_argument("--lidar-crs", default="EPSG:2154")
    parser.add_argument("--already-projected", action="store_true",
                        help="Modèle déjà dans le CRS projeté du LiDAR (sans référence ENU)")
    parser.add_argument("--terrain-center", help="X,Y,Z terrain approximatif du centre sparse")
    parser.add_argument("--max-correspondence-distance", type=float, default=2)
    parser.add_argument("--voxel-size", type=float, default=0.2)
    parser.add_argument("--min-overlap", type=float, default=0.3)
    parser.add_argument("--yaw-steps", type=int, default=36)
    parser.add_argument("--scale-min", type=float, default=0.1)
    parser.add_argument("--scale-max", type=float, default=10)
    parser.add_argument("--scale-steps", type=int, default=9)
    parser.add_argument("--search-radius", type=float, default=5)
    parser.add_argument("--translation-step", type=float, default=5)
    parser.add_argument("--max-tilt", type=float, default=10)
    parser.add_argument("--search-points", type=int, default=2000)
    parser.add_argument("--max-candidates", type=int, default=20000)
    parser.add_argument("--refine-candidates", type=int, default=8)
    parser.add_argument("--accept-ambiguous", action="store_true")
    parser.add_argument("--lidar-classes", default="2,3,4,5,6,67")
    parser.add_argument("--inject", action="store_true")
    parser.add_argument("--images", type=Path)
    parser.add_argument("--inject-voxel", type=float, default=0.2)
    parser.add_argument("--inject-max-points", type=int, default=50000)
    parser.add_argument("--inject-min-separation", type=float, default=0.1)
    parser.add_argument("--min-views", type=int, default=2)
    parser.add_argument("--min-view-angle", type=float, default=2)
    parser.add_argument("--depth-tolerance", type=float, default=0.1)
    parser.add_argument("--visibility-cell", type=int, default=4)
    parser.add_argument("--keypoint-separation", type=float, default=2)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        parser.exit(1, f"[ERREUR] Recalage COLMAP/LiDAR : {error}\n")


if __name__ == "__main__":
    main()
