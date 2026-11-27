#!/usr/bin/env python3
"""Géoréférencer un sparse SfM (quel que soit le backend) sur un fichier de positions métriques.

Le fichier (SFM_SP_METRIC_POSITIONS) contient `image X Y Z` en mètres ENU autour de l'origine
écrite dans son en-tête `# origin_lat/lon/alt`. Sortie : sparse recalé, gauge.txt à l'origine
et georef_stats.json, lisibles comme un géoréférencement GPS.
"""

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np
import pycolmap

sys.path.insert(0, str(Path(__file__).resolve().parent))
from georef_colmap_from_gpx import apply_sim, projection_reference, robust_sim3
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sfm" / "spirula"))
from gps_to_metric_positions import read_header_origin, write_gauge


def read_positions(path):
    """Positions `image X Y Z` ; '#' commente, lignes vides ignorées."""
    positions = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            raise ValueError(f"{path}, ligne {number} : attendu `image X Y Z`")
        try:
            values = [float(v) for v in parts[1:]]
        except ValueError as error:
            raise ValueError(f"{path}, ligne {number} : {error}") from error
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"{path}, ligne {number} : valeur non finie")
        if parts[0] in positions:
            raise ValueError(f"{path}, ligne {number} : {parts[0]} en double")
        positions[parts[0]] = np.array(values)
    return positions


def match_position(image_name, positions):
    """Associe un nom du sparse (éventuellement dans un sous-dossier de faces) à une position."""
    base = image_name.rsplit("/", 1)[-1]
    for candidate in (base, Path(base).stem):
        if candidate in positions:
            return candidate
    return None


# Étalement minimal (relatif au principal) des positions ENU. En dessous, roulis et tangage ne
# sont pas observables : la verticale SfM est alors supposée égale à la verticale ENU.
MIN_SPREAD_RATIO = 0.05


def fit_vertical_sim(local, target):
    """Similitude avec Z SfM = Z ENU : estime échelle, lacet et translation seulement."""
    lxy = local[:, :2] - local[:, :2].mean(axis=0)
    txy = target[:, :2] - target[:, :2].mean(axis=0)
    if not np.any(lxy) or not np.any(txy):
        raise ValueError("Positions confondues : recalage impossible.")
    angle = math.atan2(float(np.sum(lxy[:, 0] * txy[:, 1] - lxy[:, 1] * txy[:, 0])),
                       float(np.sum(lxy * txy)))
    cos, sin = math.cos(angle), math.sin(angle)
    rotation_xy = np.array([[cos, -sin], [sin, cos]])
    scale = float(np.sum((lxy @ rotation_xy.T) * txy) / np.sum(lxy * lxy))
    translation_xy = target[:, :2].mean(axis=0) - scale * rotation_xy @ local[:, :2].mean(axis=0)
    translation_z = float(np.mean(target[:, 2] - scale * local[:, 2]))
    rotation = np.eye(3)
    rotation[:2, :2] = rotation_xy
    return scale, rotation, np.append(translation_xy, translation_z)


def run(sparse, output, positions_path, epsg, max_error):
    origin = read_header_origin(positions_path)
    if origin is None:
        raise ValueError(f"En-tête origin_lat/origin_lon/origin_alt absent : {positions_path}")
    if (output / "georef_stats.json").is_file():
        print(f"[GPS] Géoréférencement existant réutilisé : {output}", flush=True)
        return
    positions = read_positions(positions_path)
    model = pycolmap.Reconstruction(str(sparse))
    centers, missing = {}, []
    for image in model.images.values():
        if not image.has_pose:
            continue
        name = match_position(image.name, positions)
        if name is None:
            missing.append(image.name)
        else:
            centers.setdefault(name, []).append(image.projection_center())
    if missing:
        raise ValueError(f"{len(missing)} image(s) du sparse sans position dans {positions_path.name} "
                         f"(exemples : {', '.join(missing[:3])}). Toutes les images doivent être géoréférencées.")
    names = sorted(centers)
    if len(names) < 3:
        raise ValueError(f"{len(names)} image(s) du sparse trouvée(s) dans les positions (au moins 3 requises).")
    local = np.array([np.median(centers[name], axis=0) for name in names])
    target = np.array([positions[name] for name in names])
    spread = np.linalg.svd(target - target.mean(axis=0), compute_uv=False)
    if spread[0] <= 0:
        raise ValueError("Positions confondues : recalage impossible.")
    if spread[1] < MIN_SPREAD_RATIO * spread[0] or spread[2] < MIN_SPREAD_RATIO * spread[0]:
        alignment = "vertical"
        sim = fit_vertical_sim(local, target)
    else:
        alignment = "sim3"
        for points in (local, target):
            singular = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
            if singular[0] <= 0 or singular[1] < singular[0] * 1e-6:
                raise ValueError("Trajectoire des positions ou du SfM quasi rectiligne : recalage ambigu.")
        sim = robust_sim3(local, target)
    residuals = np.linalg.norm(apply_sim(local, sim) - target, axis=1)
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    maximum = float(residuals.max())
    if not math.isfinite(rmse) or maximum > max_error:
        raise ValueError(f"Recalage incohérent : erreur maximale {maximum:.2f} m (> {max_error} m).")
    model.transform(pycolmap.Sim3d(sim[0], pycolmap.Rotation3d(sim[1]), sim[2]))
    output.mkdir(parents=True, exist_ok=True)
    model.write(str(output))
    write_gauge(output / "gauge.txt", origin)
    ref = {"type": "local_ENU", "units": "meters", "axes": ["East", "North", "Up"],
           "origin_lat_lon_height": list(origin), "origin_projected": projection_reference(list(origin), epsg),
           "altitude_georeferenced": True}
    report = {"status": "georeferenced", "mode": "positions", "alignment": alignment,
              "position_count": len(names), "rmse_m": rmse, "max_error_m": maximum, "output_reference": ref}
    (output / "georef_stats.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"[GPS] Recalage positions ({alignment}) : {len(names)} images, RMS {rmse:.2f} m ; repère ENU local.",
          flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sparse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--positions", type=Path, required=True)
    parser.add_argument("--epsg", type=int, default=2154)
    parser.add_argument("--max-error", type=float, default=20.0, help="Erreur maximale admise (mètres)")
    parser.add_argument("--check", action="store_true", help="Vérifier le géoréférencement existant")
    args = parser.parse_args()
    if args.check:
        stats = args.output / "georef_stats.json"
        if not stats.is_file() or json.loads(stats.read_text()).get("status") != "georeferenced":
            sys.exit("[ERREUR] Géoréférencement par positions absent : lancer la préparation.")
        return
    try:
        run(args.sparse, args.output, args.positions, args.epsg, args.max_error)
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"[ERREUR] Géoréférencement par positions : {error}")


if __name__ == "__main__":
    main()
