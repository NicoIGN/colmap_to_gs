"""Fuse a LAZ point cloud with a 3DGS scene (.splat or .ply, same format in and out).

1. Splats whose Z lies below or above the local LiDAR Z band (plus margin) are removed.
2. With --fill-holes, voxels where splats are sparser than LiDAR get one LiDAR-derived splat each.
   Its colour is the mean of nearby kept splats, or grey when none is close enough.
"""

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace
import warnings

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "postprocess"))
from merge_splats import C0, matrix

from common import load_lidar, open_scene, vector, write_report, write_scene
from filter_splats_by_lidar import MAX_LIDAR_POINTS

GREY_RGB = 128
GREY_DC = (GREY_RGB / 255.0 - 0.5) / C0
SPLAT_ROTATION_IDENTITY = np.array([255, 128, 128, 128], dtype=np.uint8)
CHUNK = 100000


def positive(value, name):
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"{name} attendu fini et > 0")


def colour_dc(raw, is_splat):
    if is_splat:
        return (raw["rgba"][:, :3].astype(np.float64) / 255.0 - 0.5) / C0
    return matrix(raw, ("f_dc_0", "f_dc_1", "f_dc_2")).astype(np.float64)


def to_u8(dc):
    return np.clip(np.round((dc * C0 + 0.5) * 255.0), 0, 255).astype(np.uint8)


def progress(label, current, total, *, force=False):
    """Print a bar per chunk, same format as the segmentation mask progress."""
    total = max(total, 1)
    width = 32
    ratio = min(current / total, 1.0)
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    print(f"[fusion] {label:<22} [{bar}] {current}/{total} ({ratio * 100:5.1f}%)", flush=True)


def z_band_mask(xyz, lidar, radius, neighbours, margin, label=None):
    """Return (remove, has_evidence) using the Z range of the nearest LiDAR points in XY."""
    tree = cKDTree(lidar[:, :2])
    remove = np.zeros(len(xyz), dtype=bool)
    evidence = np.zeros(len(xyz), dtype=bool)
    for start in range(0, len(xyz), CHUNK):
        part = xyz[start:start + CHUNK]
        dist, idx = tree.query(part[:, :2], k=neighbours, distance_upper_bound=radius)
        valid = np.isfinite(dist)
        z = lidar[np.minimum(idx, len(lidar) - 1), 2]
        zmin = np.where(valid, z, np.inf).min(axis=1)
        zmax = np.where(valid, z, -np.inf).max(axis=1)
        has = valid.any(axis=1)
        outside = (part[:, 2] < zmin - margin) | (part[:, 2] > zmax + margin)
        end = start + len(part)
        evidence[start:end] = has
        remove[start:end] = has & outside
        if label:
            progress(label, end, len(xyz))
    return remove, evidence


def z_offset_stats(xyz, lidar, radius, neighbours):
    """Diagnostic: splat Z relative to the median LiDAR Z of its XY neighbourhood."""
    tree = cKDTree(lidar[:, :2])
    residual = np.full(len(xyz), np.nan)
    for start in range(0, len(xyz), CHUNK):
        part = xyz[start:start + CHUNK]
        dist, idx = tree.query(part[:, :2], k=neighbours, distance_upper_bound=radius)
        valid = np.isfinite(dist)
        z = np.where(valid, lidar[np.minimum(idx, len(lidar) - 1), 2], np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            residual[start:start + len(part)] = part[:, 2] - np.nanmedian(z, axis=1)

    def pct(values):
        values = values[np.isfinite(values)]
        if not len(values):
            return None
        return {f"p{q}": round(float(np.percentile(values, q)), 3) for q in (5, 50, 95)}

    return dict(splat_z=pct(xyz[:, 2]), lidar_z=pct(lidar[:, 2]),
                residual_to_local_lidar_median=pct(residual))


def voxel_keys(points, lo, size):
    return np.floor((points - lo) / size).astype(np.int64)


def fill_candidates(kept_xyz, lidar, voxel, ratio):
    """Pick one LiDAR point per voxel where kept splats are sparser than LiDAR."""
    lo = np.minimum(kept_xyz.min(axis=0), lidar.min(axis=0))
    ks = voxel_keys(kept_xyz, lo, voxel)
    kl = voxel_keys(lidar, lo, voxel)
    shape = np.maximum(ks.max(axis=0), kl.max(axis=0)) + 1
    s_key = np.ravel_multi_index(ks.T, shape)
    l_key = np.ravel_multi_index(kl.T, shape)
    l_uniq, l_inv, l_cnt = np.unique(l_key, return_inverse=True, return_counts=True)
    s_uniq, s_cnt = np.unique(s_key, return_counts=True)
    s_in = np.zeros(len(l_uniq), dtype=np.int64)
    if len(s_uniq):
        pos = np.minimum(np.searchsorted(s_uniq, l_uniq), len(s_uniq) - 1)
        found = s_uniq[pos] == l_uniq
        s_in = np.where(found, s_cnt[pos], 0)
    deficit = s_in < ratio * l_cnt
    candidates = np.flatnonzero(deficit[l_inv])
    _, first = np.unique(l_key[candidates], return_index=True)
    return candidates[first], int(deficit.sum())


def build_added(dtype, is_splat, xyz, sigma, dc, opacity):
    rows = np.zeros(len(xyz), dtype=dtype)
    if is_splat:
        rows["position"] = xyz
        rows["scale"] = np.repeat(sigma[:, None], 3, axis=1)
        rows["rgba"][:, :3] = to_u8(dc)
        rows["rgba"][:, 3] = int(round(255 * opacity))
        rows["rotation"] = SPLAT_ROTATION_IDENTITY
    else:
        for i, name in enumerate("xyz"):
            rows[name] = xyz[:, i]
        for i in range(3):
            rows[f"f_dc_{i}"] = dc[:, i]
            rows[f"scale_{i}"] = np.log(sigma)
        rows["opacity"] = np.log(opacity / (1 - opacity))
        rows["rot_0"] = 1.0
    return rows


def colour_from_neighbours(kept_xyz, kept_dc, points, neighbours, radius):
    tree = cKDTree(kept_xyz)
    dist, idx = tree.query(points, k=neighbours, distance_upper_bound=radius)
    valid = np.isfinite(dist)
    values = np.where(valid[..., None], kept_dc[np.minimum(idx, len(kept_xyz) - 1)], 0.0)
    count = valid.sum(axis=1)
    mean = values.sum(axis=1) / np.maximum(count, 1)[:, None]
    dc = np.where((count > 0)[:, None], mean, GREY_DC)
    return dc, count > 0


def run(args, scene):
    xyz, _ = scene.geometry(slice(0, scene.count))
    origin = vector(args.origin)
    classes = None
    if args.lidar_classes:
        classes = {int(part) for part in args.lidar_classes.split(",")}
    lidar = load_lidar(args.lidar, origin, args.lidar_stride, classes=classes,
                       max_points=MAX_LIDAR_POINTS)

    remove, evidence = z_band_mask(xyz + np.array([0.0, 0.0, args.z_offset]), lidar,
                                   args.z_radius, args.z_neighbours, args.z_margin,
                                   label="bande Z")
    keep = ~remove
    if not keep.any():
        raise ValueError("Aucun splat conservé après filtrage par la bande Z LiDAR")
    kept_raw = scene.raw[keep]
    kept_xyz = xyz[keep]
    kept_dc = colour_dc(kept_raw, scene.is_splat)

    if args.fill_holes:
        cand, deficit_voxels = fill_candidates(kept_xyz, lidar, args.voxel, args.density_ratio)
        if args.min_splat_distance > 0 and len(cand):
            gap = cKDTree(kept_xyz).query(lidar[cand], k=1)[0]
            cand = cand[gap >= args.min_splat_distance]
    else:
        cand, deficit_voxels = np.zeros(0, dtype=np.int64), 0
    added_xyz = lidar[cand]
    if len(cand):
        nn = cKDTree(lidar).query(added_xyz, k=2)[0][:, 1]
        sigma = np.maximum(args.scale_factor * nn, 1e-4)
        dc, from_splats = colour_from_neighbours(kept_xyz, kept_dc, added_xyz,
                                                 args.colour_neighbours, args.colour_radius)
        added = build_added(scene.raw.dtype, scene.is_splat, added_xyz, sigma, dc, args.opacity)
        combined = np.concatenate([kept_raw, added])
    else:
        from_splats = np.zeros(0, dtype=bool)
        combined = kept_raw

    report = dict(
        input=str(args.input), output=str(args.output), lidar=str(args.lidar),
        origin=origin.tolist(), lidar_points=len(lidar), input_splats=scene.count,
        fill_holes=args.fill_holes,
        removed_outside_z=int(remove.sum()),
        kept_without_lidar_evidence=int((keep & ~evidence).sum()),
        deficit_voxels=deficit_voxels, added_splats=len(cand),
        added_coloured_from_splats=int(from_splats.sum()),
        added_default_grey=int(len(cand) - from_splats.sum()),
        output_splats=len(combined), dry_run=args.dry_run,
        z_diagnostics=z_offset_stats(xyz, lidar, args.z_radius, args.z_neighbours),
        parameters=dict(z_offset=args.z_offset, z_margin=args.z_margin, z_radius=args.z_radius,
                        z_neighbours=args.z_neighbours, voxel=args.voxel,
                        density_ratio=args.density_ratio, scale_factor=args.scale_factor,
                        opacity=args.opacity, colour_radius=args.colour_radius,
                        colour_neighbours=args.colour_neighbours,
                        min_splat_distance=args.min_splat_distance,
                        grey_rgb=GREY_RGB),
        warning="Absence de LiDAR ne prouve pas un artefact ; vérifier couverture et dates.")
    return combined, report


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lidar", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True, help="Scène 3DGS .splat ou .ply")
    parser.add_argument("--output", type=Path, required=True,
                        help="Même extension que --input")
    parser.add_argument("--origin", default="0,0,0", help="Origine soustraite au LAZ")
    parser.add_argument("--lidar-stride", type=int, default=1)
    parser.add_argument("--lidar-classes", help="Codes LAS de référence ; défaut : tous")
    parser.add_argument("--fill-holes", action="store_true",
                        help="Ajouter les splats LiDAR dans les zones moins denses que le LiDAR ; "
                             "défaut : seulement retirer les splats hors bande Z")
    parser.add_argument("--z-offset", type=float, default=0.0,
                        help="Décalage ajouté au Z des splats pour le test de bande seulement (m), "
                             "par ex. pour corriger un datum vertical décalé")
    parser.add_argument("--z-margin", type=float, default=0.5,
                        help="Marge verticale autour de la bande Z LiDAR locale (m)")
    parser.add_argument("--z-radius", type=float, default=5.0,
                        help="Rayon XY de recherche de la bande Z LiDAR (m)")
    parser.add_argument("--z-neighbours", type=int, default=64,
                        help="Nombre de points LiDAR XY pris pour la bande Z")
    parser.add_argument("--voxel", type=float, default=1.0,
                        help="Taille de voxel de comparaison de densité (m)")
    parser.add_argument("--density-ratio", type=float, default=1.0,
                        help="Ajout si splats < ratio * points LiDAR dans le voxel")
    parser.add_argument("--scale-factor", type=float, default=0.5,
                        help="Écart-type des splats ajoutés = facteur * distance LiDAR au plus proche")
    parser.add_argument("--opacity", type=float, default=0.9, help="Opacité des splats ajoutés ]0,1[")
    parser.add_argument("--colour-radius", type=float, default=2.0,
                        help="Rayon de propagation de couleur depuis les splats (m)")
    parser.add_argument("--colour-neighbours", type=int, default=4)
    parser.add_argument("--min-splat-distance", type=float, default=0.0,
                        help="N'ajoute un point LiDAR que s'il est à au moins cette distance (m) "
                             "du splat conservé le plus proche ; 0 désactive")
    parser.add_argument("--dry-run", action="store_true", help="Rapport seul, sans écrire de scène")
    args = parser.parse_args()
    scene = None
    try:
        if args.input.suffix.lower() not in (".splat", ".ply"):
            raise ValueError("Scène attendue en .splat ou .ply")
        if args.output.suffix.lower() != args.input.suffix.lower():
            raise ValueError("La sortie doit conserver le format de l'entrée")
        report_path = args.output.with_suffix(args.output.suffix + ".json")
        if args.output.exists() or report_path.exists():
            raise ValueError("Une sortie existe déjà")
        if args.lidar_stride < 1:
            raise ValueError("Stride LiDAR attendu >= 1")
        positive(args.z_margin, "Marge Z")
        positive(args.z_radius, "Rayon Z")
        positive(args.voxel, "Taille de voxel")
        positive(args.density_ratio, "Ratio de densité")
        positive(args.scale_factor, "Facteur d'échelle")
        positive(args.colour_radius, "Rayon couleur")
        if args.z_neighbours < 2 or args.colour_neighbours < 2:
            raise ValueError("Voisinages attendus >= 2")
        if not 0 < args.opacity < 1:
            raise ValueError("Opacité attendue dans ]0,1[")
        scene = open_scene(args.input)
        combined, report = run(args, scene)
        if not args.dry_run:
            write_scene(args.output, SimpleNamespace(path=scene.path, raw=combined,
                                                     count=len(combined), is_splat=scene.is_splat))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_report(report_path, report)
        print(f"[lidar] fusion : {report['removed_outside_z']} splats retirés hors bande Z, "
              f"{report['added_splats']} splats LiDAR ajoutés "
              f"({report['added_default_grey']} en gris). Rapport : {report_path.name}")
    except (OSError, ValueError, ImportError) as error:
        print(f"[erreur] {error}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        if scene is not None:
            scene.close()


if __name__ == "__main__":
    main()
