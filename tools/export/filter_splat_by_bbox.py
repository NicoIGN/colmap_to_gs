#!/usr/bin/env python3
"""Retire les splats dont le support sort d'une boîte projetée et/ou d'une plage de z.

Le repère des splats est le repère ENU du géoréférencement (gauge.txt ou metadata.json).
La boîte (xmin,ymin,xmax,ymax) et zmin/zmax sont exprimés dans le SCR projeté de ce
géoréférencement (EPSG:2154 par défaut). Le support d'un splat est son ellipsoïde à
--sigma écarts-types, encadré par sa boîte axis-aligned en ENU puis projeté par ses 8 coins.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
from plyfile import PlyData, PlyElement
from pyproj import Transformer
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.libs.python.splat_metadata import build_metadata, load_gauge_reference  # noqa: E402
from tools.export.filter_splats import SPLAT_DTYPE, write_records  # noqa: E402


CORNER_SIGNS = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=np.float64)


def parse_bbox(text):
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError as error:
        raise ValueError(f"BBOX invalide '{text}' : attendu xmin,ymin,xmax,ymax") from error
    if len(values) != 4 or not np.isfinite(values).all():
        raise ValueError(f"BBOX invalide '{text}' : attendu xmin,ymin,xmax,ymax (nombres finis)")
    xmin, ymin, xmax, ymax = values
    if xmin >= xmax or ymin >= ymax:
        raise ValueError(f"BBOX invalide '{text}' : xmin < xmax et ymin < ymax requis")
    return values


def load_georef(path, epsg_override):
    """Retourne (stats, repère, EPSG) : stats est le contenu du metadata.json, ou le repère seul."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Géoréférencement introuvable : {path.name}")
    if path.suffix.lower() == ".json":
        stats = json.loads(path.read_text(encoding="utf-8"))
        ref = stats.get("output_reference")
        if not isinstance(ref, dict):
            raise ValueError(f"{path.name} : bloc output_reference absent")
    else:
        ref = load_gauge_reference(path, epsg_override or 2154)
        stats = {"output_reference": ref}
    projected = ref.get("origin_projected") or {}
    epsg = epsg_override or projected.get("epsg")
    if not epsg:
        raise ValueError(f"{path.name} : EPSG du SCR projeté absent (utiliser --epsg)")
    if ref.get("type") not in ("local_ENU", "projected"):
        raise ValueError(f"{path.name} : type de référence non géré : {ref.get('type')}")
    return stats, ref, int(epsg)


def enu_to_projected_function(ref, epsg):
    """Fonction vectorisée (N,3) ENU -> (N,3) projeté, comme georef.js."""
    if ref["type"] == "projected":
        origin = ref.get("origin_projected", {})
        offset = np.array([
            origin.get("origin_x", origin.get("x")),
            origin.get("origin_y", origin.get("y")),
            origin.get("origin_height", origin.get("h", 0.0)),
        ], dtype=np.float64)
        if not np.isfinite(offset[:2]).all():
            raise ValueError("origin_projected x/y absents pour un repère projeté")
        return lambda points: points + offset

    lat, lon, height = (float(v) for v in ref["origin_lat_lon_height"])
    geodetic_to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    ecef_to_projected = Transformer.from_crs(4978, epsg, always_xy=True)
    origin_ecef = np.array(geodetic_to_ecef.transform(lon, lat, height), dtype=np.float64)
    phi, lam = np.radians(lat), np.radians(lon)
    # Lignes : vecteurs unitaires East, North, Up exprimés en ECEF.
    basis = np.array([
        [-np.sin(lam), np.cos(lam), 0.0],
        [-np.sin(phi) * np.cos(lam), -np.sin(phi) * np.sin(lam), np.cos(phi)],
        [np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)],
    ])

    def convert(points):
        ecef = origin_ecef + points @ basis
        x, y, z = ecef_to_projected.transform(ecef[:, 0], ecef[:, 1], ecef[:, 2])
        return np.column_stack((x, y, z))

    return convert


def read_gaussians(path, fmt):
    """Retourne les enregistrements SPLAT (memmap) ou les sommets PLY 3DGS."""
    if fmt == "splat":
        size = path.stat().st_size
        if size == 0 or size % SPLAT_DTYPE.itemsize:
            raise ValueError("SPLAT vide ou taille non multiple de 32 octets")
        return np.memmap(path, dtype=SPLAT_DTYPE, mode="r")
    vertex = PlyData.read(str(path))["vertex"].data
    names = set(vertex.dtype.names or ())
    required = {"x", "y", "z", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"}
    if required - names:
        raise ValueError(f"PLY 3DGS requis, propriétés manquantes : {sorted(required - names)}")
    if len(vertex) == 0:
        raise ValueError("PLY vide")
    return vertex


def gaussian_batch(data, fmt, start, stop):
    if fmt == "splat":
        chunk = np.asarray(data[start:stop])
        means = chunk["position"].astype(np.float64)
        scales = chunk["scale"].astype(np.float64)
        quats = (chunk["rotation"].astype(np.float64) - 128) / 128
    else:
        chunk = data[start:stop]
        means = np.column_stack([chunk["x"], chunk["y"], chunk["z"]]).astype(np.float64)
        scales = np.exp(np.column_stack([chunk[f"scale_{i}"] for i in range(3)]).astype(np.float64))
        quats = np.column_stack([chunk[f"rot_{i}"] for i in range(4)]).astype(np.float64)
    if not (np.isfinite(means).all() and np.isfinite(scales).all() and np.isfinite(quats).all()):
        raise ValueError("Attributs non finis dans le splat ; aucune sortie écrite")
    if np.any(scales <= 0) or np.any(np.max(np.abs(quats), axis=1) == 0):
        raise ValueError("Échelles nulles ou quaternions nuls ; aucune sortie écrite")
    return means, scales, quats


def inside_mask(means, scales, quats, to_projected, sigma, box, z_range):
    """True pour les splats dont les 8 coins du support projeté restent dans la boîte."""
    rotation = Rotation.from_quat(quats[:, [1, 2, 3, 0]]).as_matrix()
    half = sigma * np.sqrt(((rotation * scales[:, None, :]) ** 2).sum(axis=2))
    corners = means[:, None, :] + CORNER_SIGNS[None, :, :] * half[:, None, :]
    projected = to_projected(corners.reshape(-1, 3)).reshape(len(means), 8, 3)
    low = projected.min(axis=1)
    high = projected.max(axis=1)
    keep = np.ones(len(means), dtype=bool)
    if box is not None:
        xmin, ymin, xmax, ymax = box
        keep &= (low[:, 0] >= xmin) & (high[:, 0] <= xmax)
        keep &= (low[:, 1] >= ymin) & (high[:, 1] <= ymax)
    if z_range is not None:
        zmin, zmax = z_range
        if zmin is not None:
            keep &= low[:, 2] >= zmin
        if zmax is not None:
            keep &= high[:, 2] <= zmax
    return keep


def write_ply(path, vertex, keep):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.close(fd)
        PlyData([PlyElement.describe(vertex[keep], "vertex")]).write(temporary)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def filter_file(args):
    source = args.input.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise ValueError(f"Entrée introuvable : {args.input}")
    if output == source or (output.exists() and os.path.samefile(output, source)):
        raise ValueError("La sortie doit être distincte de l'entrée")
    if output.exists() and not args.overwrite:
        raise ValueError(f"Sortie existante : {args.output} (utiliser --overwrite)")
    if args.bbox is None and args.zmin is None and args.zmax is None:
        raise ValueError("Préciser --bbox, --zmin ou --zmax")
    if args.zmin is not None and args.zmax is not None and args.zmin > args.zmax:
        raise ValueError("zmin doit être <= zmax")
    if not np.isfinite(args.sigma) or args.sigma <= 0:
        raise ValueError("--sigma doit être fini et > 0")
    if args.batch_size < 1:
        raise ValueError("--batch-size doit être >= 1")

    box = parse_bbox(args.bbox) if args.bbox else None
    z_range = (args.zmin, args.zmax) if (args.zmin is not None or args.zmax is not None) else None
    stats, ref, epsg = load_georef(args.georef.resolve(), args.epsg)
    to_projected = enu_to_projected_function(ref, epsg)
    metadata_path = output.with_name(f"{output.stem}.metadata.json")
    if metadata_path.exists() and not args.overwrite:
        raise ValueError(f"Sortie existante : {metadata_path.name} (utiliser --overwrite)")

    data = read_gaussians(source, args.format)
    count = len(data)
    keep = np.zeros(count, dtype=bool)
    for start in range(0, count, args.batch_size):
        stop = min(start + args.batch_size, count)
        means, scales, quats = gaussian_batch(data, args.format, start, stop)
        keep[start:stop] = inside_mask(means, scales, quats, to_projected, args.sigma, box, z_range)

    if args.format == "splat":
        write_records(output, data, keep, batch_size=args.batch_size)
    else:
        write_ply(output, data, keep)

    report = dict(
        input=str(source), output=str(output), format=args.format, epsg=epsg,
        georef_type=ref["type"], bbox=box, zmin=args.zmin, zmax=args.zmax,
        sigma=args.sigma, total=count, kept=int(keep.sum()), removed=int(count - keep.sum()),
    )
    metadata = build_metadata(output, "filter_splat_by_bbox", stats)
    metadata["filter"] = {key: report[key] for key in (
        "bbox", "zmin", "zmax", "sigma", "total", "kept", "removed")}
    metadata["filter"]["source"] = source.name
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["metadata"] = str(metadata_path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--format", choices=("splat", "ply"), default="splat",
                        help="Format de l'entrée et de la sortie (défaut : splat)")
    parser.add_argument("--georef", required=True, type=Path,
                        help="gauge.txt ou metadata.json du même repère que les splats")
    parser.add_argument("--epsg", type=int,
                        help="SCR projeté ; défaut : celui du géoréférencement (2154 pour gauge.txt)")
    parser.add_argument("--bbox", help="xmin,ymin,xmax,ymax dans le SCR projeté")
    parser.add_argument("--zmin", type=float, help="Altitude minimale du support (SCR projeté)")
    parser.add_argument("--zmax", type=float, help="Altitude maximale du support (SCR projeté)")
    parser.add_argument("--sigma", type=float, default=3.0,
                        help="Rayon du support en écarts-types (défaut : 3)")
    parser.add_argument("--batch-size", type=int, default=200000)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        report = filter_file(args)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"[ERREUR] filter_splat_by_bbox : {error}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
