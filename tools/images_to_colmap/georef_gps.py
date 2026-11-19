#!/usr/bin/env python3
"""Recaler les vues de panoramas sur leurs positions GPS, en ENU local."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import pycolmap
from PIL import Image
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "preprocess"))
from georef_colmap_from_gpx import (apply_sim, enu_rotation, geodetic_to_ecef,
                                    projection_reference, robust_sim3)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "images_to_splat"))
from prepare_inputs import _gps_coordinate


class InsufficientGeometryError(ValueError):
    pass


def image_gps(path):
    with Image.open(path) as image:
        gps = image.getexif().get_ifd(0x8825)
    lat = _gps_coordinate(gps, 1, 2, "N", "S", 90)
    lon = _gps_coordinate(gps, 3, 4, "E", "W", 180)
    altitude = None
    try:
        value = float(gps[6])
        ref = gps.get(5, 0)
        if isinstance(ref, bytes):
            ref = int.from_bytes(ref, "big")
        if math.isfinite(value) and ref in (0, 1):
            altitude = -value if ref == 1 else value
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return lat, lon, altitude


def positions(projection, metadata_dir=None):
    root = Path(projection["source"])
    result = {}
    for entry in projection["images"]:
        name = entry["name"]
        lat, lon, altitude = image_gps(root / name)
        if metadata_dir:
            path = metadata_dir / (Path(name).stem + ".json")
            if not path.is_file():
                raise ValueError(f"Métadonnées GPS absentes : {path}")
            data = json.loads(path.read_text())
            geometry = data.get("feature", {}).get("geometry", {})
            coordinates = geometry.get("coordinates", [])
            if geometry.get("type") == "Point" and len(coordinates) >= 2:
                lon, lat = coordinates[:2]
                if len(coordinates) >= 3 and coordinates[2] is not None:
                    altitude = coordinates[2]
        if (isinstance(lat, bool) or isinstance(lon, bool) or
                not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or
                not math.isfinite(lat) or not math.isfinite(lon) or
                not -90 <= lat <= 90 or not -180 <= lon <= 180):
            result[name] = None
            continue
        if altitude is not None and (isinstance(altitude, bool) or
                                     not isinstance(altitude, (int, float)) or
                                     not math.isfinite(altitude)):
            raise ValueError(f"Altitude GPS invalide : {name}")
        result[name] = [float(lat), float(lon), None if altitude is None else float(altitude)]
    return result


def horizontal_fit(local, target, ups):
    up = np.mean(ups, axis=0)
    length = np.linalg.norm(up)
    if length < .8:
        raise ValueError("Orientations verticales des panoramas incohérentes : recalage horizontal impossible.")
    up /= length
    if np.min(np.asarray(ups) @ up) < .8:
        raise ValueError("Panoramas non redressés : recalage horizontal impossible.")
    level = Rotation.align_vectors([[0, 0, 1]], [up])[0].as_matrix()
    x = (local @ level.T)[:, :2]
    y = target[:, :2]
    x0, y0 = x.mean(axis=0), y.mean(axis=0)
    xc, yc = x - x0, y - y0
    for points in (xc, yc):
        singular = np.linalg.svd(points, compute_uv=False)
        if singular[0] <= 0 or singular[1] < singular[0] * 1e-6:
            raise InsufficientGeometryError("Trajectoire GPS ou SfM quasi rectiligne : recalage ambigu.")
    u, values, vt = np.linalg.svd(yc.T @ xc)
    signs = np.array([1., np.linalg.det(u @ vt)])
    yaw2 = u @ np.diag(signs) @ vt
    scale = float(np.dot(values, signs) / np.sum(xc * xc))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Échelle GPS invalide.")
    yaw = np.eye(3)
    yaw[:2, :2] = yaw2
    rotation = yaw @ level
    translation = np.r_[y0 - scale * yaw2 @ x0, -scale * np.mean((local @ rotation.T)[:, 2])]
    return scale, rotation, translation


def run(projection_path, sparse, output, epsg, metadata_dir=None, allow_missing=False, check=False):
    projection_file = json.loads(projection_path.read_text())
    if projection_file.get("status") != "complete":
        raise ValueError("Projection incomplète.")
    projection = projection_file["inputs"]
    gps = positions(projection, metadata_dir)
    h = hashlib.sha256()
    h.update(json.dumps(["gps_enu_v1", projection_file, gps, epsg, allow_missing], sort_keys=True).encode())
    for name in ("cameras.bin", "images.bin", "points3D.bin", "rigs.bin", "frames.bin"):
        path = sparse / name
        h.update(name.encode())
        if name in ("rigs.bin", "frames.bin") and not path.is_file():
            continue
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
    signature = h.hexdigest()
    marker = output / ".gps_georef.json"
    outputs = ("cameras.bin", "images.bin", "points3D.bin", "georef_stats.json", "gps_positions.json")

    def file_hash(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def finish():
        names = list(outputs) + [name for name in ("rigs.bin", "frames.bin") if (output / name).is_file()]
        hashes = {name: file_hash(output / name) for name in names}
        temporary = output / ".gps_georef.json.tmp"
        temporary.write_text(json.dumps({"signature": signature, "status": "complete",
                                         "outputs": hashes}) + "\n")
        temporary.replace(marker)

    if marker.is_file():
        existing = json.loads(marker.read_text())
        if existing.get("signature") != signature or existing.get("status") != "complete":
            raise ValueError("Géoréférencement GPS incompatible ou incomplet : choisir un nouveau workspace.")
        hashes = existing.get("outputs", {})
        if (not all(name in hashes for name in outputs) or
                not all((output / name).is_file() and file_hash(output / name) == expected
                        for name, expected in hashes.items())):
            raise ValueError("Modèle géoréférencé incomplet : choisir un nouveau workspace.")
        print(f"[GPS] Géoréférencement existant réutilisé : {output}", flush=True)
        return
    if output.exists() and any(output.iterdir()):
        raise ValueError("Sortie GPS non vide sans signature : choisir un nouveau workspace.")
    if check:
        raise ValueError("Géoréférencement GPS absent : lancer la préparation.")
    model = pycolmap.Reconstruction(sparse)
    centers, up_vectors = {}, {}
    for image in model.images.values():
        if not image.has_pose or image.name not in projection["views"]:
            continue
        original = projection["views"][image.name]
        centers.setdefault(original, []).append(image.projection_center())
        face = image.name.split("/", 1)[0]
        axis = {"front": (0, -1, 0), "right": (0, -1, 0), "back": (0, -1, 0),
                "left": (0, -1, 0), "up": (0, 0, 1), "down": (0, 0, -1)}[face]
        up_vectors.setdefault(original, []).append(image.cam_from_world().rotation.matrix().T @ axis)
    names = sorted(name for name in centers if gps.get(name) is not None)
    output.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"signature": signature, "status": "partial"}) + "\n")
    (output / "gps_positions.json").write_text(json.dumps(gps, indent=2, allow_nan=False) + "\n")
    def unreferenced(reason):
        if not allow_missing:
            raise ValueError(reason)
        print(f"[AVERTISSEMENT] {reason} Modèle conservé dans son repère SfM non géoréférencé.", flush=True)
        model.write(output)
        (output / "georef_stats.json").write_text(json.dumps(
            {"status": "unreferenced", "gps_count": len(names), "reason": reason}, indent=2) + "\n")
        finish()

    if len(names) < 3:
        unreferenced("Moins de trois panoramas reconstruits avec GPS : recalage impossible.")
        return
    geographic = np.array([[*gps[name][:2], gps[name][2] if gps[name][2] is not None else 0]
                           for name in names])
    all_altitudes = all(gps[name][2] is not None for name in names)
    origin = geographic.mean(axis=0)
    longitude = np.radians(geographic[:, 1])
    mean_sine, mean_cosine = np.mean(np.sin(longitude)), np.mean(np.cos(longitude))
    if math.hypot(mean_sine, mean_cosine) < 1e-6:
        raise ValueError("Positions GPS trop dispersées pour définir une origine locale.")
    origin[1] = math.degrees(math.atan2(mean_sine, mean_cosine))
    target = (geodetic_to_ecef(geographic[:, 0], geographic[:, 1], geographic[:, 2]) -
              geodetic_to_ecef(*origin)) @ enu_rotation(origin).T
    local = np.array([np.median(centers[name], axis=0) for name in names])
    if all_altitudes:
        for points in (local, target):
            singular = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
            if singular[0] <= 0 or singular[1] < singular[0] * 1e-6:
                unreferenced("Trajectoire GPS ou SfM quasi rectiligne : recalage ambigu.")
                return
        sim = robust_sim3(local, target)
        mode = "3d"
    else:
        ups = np.array([np.mean(up_vectors[name], axis=0) for name in names])
        ups /= np.linalg.norm(ups, axis=1)[:, None]
        try:
            sim = horizontal_fit(local, target, ups)
        except InsufficientGeometryError as error:
            unreferenced(str(error))
            return
        mode = "horizontal"
        print("[AVERTISSEMENT] Altitudes GPS incomplètes : hauteur locale non géoréférencée ; "
              "orientation verticale estimée en supposant les panoramas redressés.", flush=True)
    residuals = np.linalg.norm((apply_sim(local, sim) - target)[:, :2 if mode == "horizontal" else 3], axis=1)
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    maximum = float(residuals.max())
    if not math.isfinite(rmse) or not math.isfinite(maximum) or maximum > 20:
        raise ValueError(f"Recalage GPS incohérent : erreur maximale {maximum:.2f} m (>20 m).")
    model.transform(pycolmap.Sim3d(sim[0], pycolmap.Rotation3d(sim[1]), sim[2]))
    model.write(output)
    projected = projection_reference(origin, epsg)
    projected["height_note"] = ("Hauteurs GPS brutes supposées ellipsoïdales WGS84 ; pas de correction géoïde."
                                if all_altitudes else "Altitude inconnue : origine verticale arbitraire.")
    ref = {"type": "local_ENU", "units": "meters", "axes": ["East", "North", "Up"],
           "origin_lat_lon_height": origin.tolist(), "origin_projected": projected,
           "altitude_georeferenced": all_altitudes}
    report = {"status": "georeferenced", "mode": mode, "gps_count": len(names),
              "rmse_m": rmse, "max_error_m": maximum,
              "output_reference": ref}
    (output / "georef_stats.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    finish()
    print(f"[GPS] Recalage {mode} : {len(names)} panoramas, RMS {rmse:.2f} m ; repère ENU local.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--sparse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epsg", type=int, default=2154)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        run(args.projection, args.sparse, args.output, args.epsg, args.metadata,
            args.allow_missing, args.check)
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"[ERREUR] Géoréférencement GPS : {error}")


if __name__ == "__main__":
    main()
