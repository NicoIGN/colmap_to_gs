#!/usr/bin/env python3
"""Planifie les dalles du mode GRID et sélectionne les images de chaque dalle.

Chaque dalle a une emprise `core` (taille GRID_TILE_SIZE, pas GRID_TILE_SIZE - GRID_OVERLAP) et une
emprise de calcul `compute` = core élargie de GRID_MARGIN. Le calcul utilise `compute`, mais seul le
splat clippé sur `core` est exporté. Les dalles adjacentes se recouvrent de GRID_OVERLAP mètres.

Sous-commandes :
  plan            manifest.json : dalles, emprises core et compute ; tiles.geojson : emprises core en EPSG:4326 (même dossier)
  select-images   liens symboliques des images dont la position (fichier --positions, sinon GPS EXIF) est dans l'emprise compute
"""

import argparse
import json
import math
from pathlib import Path
import sys

from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "georef"))
from georef_gps import image_gps  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sfm" / "spirula"))
from gps_to_metric_positions import read_header_origin, to_enu  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".JPG", ".JPEG"}


def parse_bbox(text):
    values = [float(v) for v in text.split(",")]
    if len(values) != 4:
        raise ValueError("GRID_BBOX doit être xmin,ymin,xmax,ymax")
    xmin, ymin, xmax, ymax = values
    if not (xmin < xmax and ymin < ymax):
        raise ValueError("GRID_BBOX : xmin < xmax et ymin < ymax sont requis")
    return xmin, ymin, xmax, ymax


def _axis_starts(low, high, size, step):
    """Débuts de dalles sur un axe, la dernière dalle se termine exactement sur `high`."""
    span = high - low
    if span <= size:
        return [low]
    count = math.ceil((span - size) / step) + 1
    return [low + i * step for i in range(count)]


def plan_tiles(bbox, tile_size, overlap, margin):
    """Retourne la liste des dalles ; emprises en tuples (xmin, ymin, xmax, ymax) dans le SCR de la bbox."""
    if tile_size <= 0:
        raise ValueError("GRID_TILE_SIZE doit être > 0")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("GRID_OVERLAP doit être >= 0 et < GRID_TILE_SIZE")
    if margin < 0:
        raise ValueError("GRID_MARGIN doit être >= 0")
    xmin, ymin, xmax, ymax = bbox
    step = tile_size - overlap
    xs = _axis_starts(xmin, xmax, tile_size, step)
    ys = _axis_starts(ymin, ymax, tile_size, step)
    tiles = []
    for row, y0 in enumerate(ys):
        for col, x0 in enumerate(xs):
            core = tuple(round(v, 6) for v in (x0, y0, min(x0 + tile_size, xmax), min(y0 + tile_size, ymax)))
            compute = tuple(round(v, 6) for v in (core[0] - margin, core[1] - margin,
                                                  core[2] + margin, core[3] + margin))
            tiles.append({
                "id": f"tile_{row:03d}_{col:03d}",
                "row": row,
                "col": col,
                "core": list(core),
                "compute": list(compute),
            })
    return tiles


def write_manifest(path, bbox, epsg, tile_size, overlap, margin, tiles):
    manifest = {
        "bbox": list(bbox),
        "epsg": epsg,
        "tile_size": tile_size,
        "overlap": overlap,
        "margin": margin,
        "tiles": tiles,
    }
    Path(path).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def tiles_geojson(tiles, epsg, edge_points=11):
    """FeatureCollection GeoJSON (EPSG:4326) des emprises `core` ; bords densifiés pour suivre la reprojection."""
    to_wgs84 = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    features = []
    for tile in tiles:
        x0, y0, x1, y1 = tile["core"]
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        ring = []
        for (ax, ay), (bx, by) in zip(corners, corners[1:]):
            for step in range(edge_points - 1):
                t = step / (edge_points - 1)
                ring.append(to_wgs84.transform(ax + (bx - ax) * t, ay + (by - ay) * t))
        ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {"id": tile["id"], "row": tile["row"], "col": tile["col"],
                           "epsg_native": epsg, "core_native": tile["core"]},
            "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]},
        })
    return {"type": "FeatureCollection", "features": features}


def inside(point, box):
    x, y = point
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def read_positions(path):
    """Positions WGS84 externes : `nom_image lat lon [alt]` ; '#' commente. Retourne {nom: (lat, lon, alt)}."""
    positions = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) < 3:
            raise ValueError(f"{path} ligne {number} : format attendu `nom_image lat lon [alt]`")
        altitude = float(fields[3]) if len(fields) > 3 else 0.0
        positions[fields[0]] = (float(fields[1]), float(fields[2]), altitude)
    return positions


def select_images(images_dir, epsg, tiles, positions=None):
    """Retourne {tile_id: [images]}.

    La position vient du fichier externe `positions` (prioritaire, clé = nom de fichier) ou, à défaut,
    des GPS EXIF. Lève une erreur si une image n'a ni l'un ni l'autre.
    """
    to_projected = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    selection = {tile["id"]: [] for tile in tiles}
    paths = sorted(p for p in Path(images_dir).iterdir() if p.suffix in IMAGE_SUFFIXES)
    if not paths:
        raise ValueError(f"Aucune image JPEG dans {images_dir}")
    step = max(1, len(paths) // 10)
    for index, path in enumerate(paths, 1):
        if index % step == 0 or index == len(paths):
            print(f"[select-images] positions lues : {index}/{len(paths)}", flush=True)
        if positions and path.name in positions:
            lat, lon, _ = positions[path.name]
        else:
            lat, lon, _ = image_gps(path)
        if lat is None or lon is None:
            raise ValueError(f"{path.name} : aucune position (ni GPS EXIF, ni GRID_POSITIONS)")
        point = to_projected.transform(lon, lat)
        for tile in tiles:
            if inside(point, tile["compute"]):
                selection[tile["id"]].append(path)
    return selection


def select_from_metric(images_dir, epsg, tiles, metric_file):
    """Sélection à partir de SFM_SP_METRIC_POSITIONS (ENU en mètres autour de l'origine d'en-tête), sans lire les EXIF.

    Les images absentes du fichier métrique sont ignorées et comptées dans le message.
    """
    origin = read_header_origin(Path(metric_file))
    if origin is None:
        raise ValueError(f"{metric_file} : en-tête `# origin_lat/lon/alt` absent")
    rows = {}
    for raw in Path(metric_file).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, x, y, z = line.split()
        rows[name] = (float(x), float(y), float(z))

    lat0, lon0, h0 = origin
    to_ecef = Transformer.from_crs("EPSG:4326", "EPSG:4978", always_xy=True)
    ox, oy, oz = to_ecef.transform(lon0, lat0, h0)
    phi, lam = math.radians(lat0), math.radians(lon0)
    sp, cp, sl, cl = math.sin(phi), math.cos(phi), math.sin(lam), math.cos(lam)
    to_projected = Transformer.from_crs("EPSG:4978", f"EPSG:{epsg}", always_xy=True)

    selection = {tile["id"]: [] for tile in tiles}
    paths = sorted(p for p in Path(images_dir).iterdir() if p.suffix in IMAGE_SUFFIXES)
    if not paths:
        raise ValueError(f"Aucune image JPEG dans {images_dir}")
    skipped = 0
    step = max(1, len(paths) // 10)
    for index, path in enumerate(paths, 1):
        if index % step == 0 or index == len(paths):
            print(f"[select-images] positions lues : {index}/{len(paths)}", flush=True)
        if path.name not in rows:
            skipped += 1
            continue
        east, north, up = rows[path.name]
        dx = -sl * east - sp * cl * north + cp * cl * up
        dy = cl * east - sp * sl * north + cp * sl * up
        dz = cp * north + sp * up
        x, y, _ = to_projected.transform(ox + dx, oy + dy, oz + dz)
        point = (x, y)
        for tile in tiles:
            if inside(point, tile["compute"]):
                selection[tile["id"]].append(path)
    if skipped:
        print(f"[select-images] {skipped} image(s) absente(s) de SFM_SP_METRIC_POSITIONS, ignorée(s)", flush=True)
    return selection


def write_tile_metric(selection, tiles, epsg, output_dir, positions=None, global_metric=None):
    """Écrit work/<dalle>/metric_positions.txt (SFM_SP_METRIC_POSITIONS) pour chaque dalle non vide.

    - positions externes : repère ENU centré sur le centre de la dalle, convention de
      gps_to_metric_positions (en-tête `# origin_lat/lon/alt`, lignes `image X Y Z`).
    - sinon fichier métrique global : lignes filtrées sur les images de la dalle, repère inchangé.
    Retourne {dalle: chemin}. Sans source de positions, retourne {}.
    """
    if positions is None and global_metric is None:
        return {}
    to_wgs84 = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    global_header, global_rows = [], {}
    if global_metric is not None:
        for raw in Path(global_metric).read_text(encoding="utf-8").splitlines():
            if not raw.strip() or raw.startswith("#"):
                global_header.append(raw)
            else:
                global_rows[raw.split()[0]] = raw
    written = {}
    for tile in tiles:
        names = [path.name for path in selection[tile["id"]]]
        if not names:
            continue
        if positions is not None:
            x0, y0, x1, y1 = tile["core"]
            lon, lat = to_wgs84.transform((x0 + x1) / 2, (y0 + y1) / 2)
            origin = (lat, lon, 0.0)
            lines = [f"# origin_lat {origin[0]:.9f}", f"# origin_lon {origin[1]:.9f}", f"# origin_alt {origin[2]:.3f}"]
            for name in names:
                if name not in positions:
                    raise ValueError(f"{name} : absente de GRID_POSITIONS (requise pour le fichier métrique de la dalle)")
                x, y, z = to_enu(*positions[name], origin)
                lines.append(f"{name} {x:.4f} {y:.4f} {z:.4f}")
        else:
            missing = [name for name in names if name not in global_rows]
            if missing:
                raise ValueError(f"{missing[0]} : absente de SFM_SP_METRIC_POSITIONS")
            lines = global_header + [global_rows[name] for name in names]
        target = Path(output_dir) / tile["id"] / "metric_positions.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written[tile["id"]] = target
    return written


def link_selection(selection, output_dir):
    """Crée output_dir/<tile>/images/ avec des liens symboliques vers les images retenues."""
    for tile_id, paths in selection.items():
        target = Path(output_dir) / tile_id / "images"
        target.mkdir(parents=True, exist_ok=True)
        for path in paths:
            link = target / path.name
            if not link.exists():
                link.symlink_to(path.resolve())
    return {tile_id: len(paths) for tile_id, paths in selection.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Écrire le manifest des dalles")
    plan.add_argument("--bbox", required=True)
    plan.add_argument("--epsg", type=int, required=True)
    plan.add_argument("--tile-size", type=float, required=True)
    plan.add_argument("--overlap", type=float, required=True)
    plan.add_argument("--margin", type=float, required=True)
    plan.add_argument("--output", required=True, type=Path)

    tiles = sub.add_parser("tiles", help="Afficher les dalles (TSV) pour le driver shell")
    tiles.add_argument("--manifest", required=True, type=Path)

    select = sub.add_parser("select-images", help="Lier les images de chaque dalle")
    select.add_argument("--manifest", required=True, type=Path)
    select.add_argument("--images", required=True, type=Path)
    select.add_argument("--output-dir", required=True, type=Path)
    select.add_argument("--positions", type=Path, default=None, help="Positions WGS84 externes (prioritaires sur EXIF)")
    select.add_argument("--global-metric", type=Path, default=None,
                        help="SFM_SP_METRIC_POSITIONS global, filtré par dalle si --positions est absent")

    args = parser.parse_args()
    if args.command == "plan":
        bbox = parse_bbox(args.bbox)
        tiles = plan_tiles(bbox, args.tile_size, args.overlap, args.margin)
        write_manifest(args.output, bbox, args.epsg, args.tile_size, args.overlap, args.margin, tiles)
        geojson_path = args.output.parent / "tiles.geojson"
        geojson_path.write_text(json.dumps(tiles_geojson(tiles, args.epsg), ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[OK] {len(tiles)} dalle(s) : {args.output} ; emprises : {geojson_path}")
    elif args.command == "tiles":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        for tile in manifest["tiles"]:
            fields = [tile["id"], *tile["core"], *tile["compute"]]
            print("\t".join(str(v) for v in fields))
    else:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        print(f"[select-images] {len(manifest['tiles'])} dalle(s), lecture des positions...", flush=True)
        positions = read_positions(args.positions) if args.positions else None
        if positions is None and args.global_metric is not None:
            selection = select_from_metric(args.images, manifest["epsg"], manifest["tiles"], args.global_metric)
        else:
            selection = select_images(args.images, manifest["epsg"], manifest["tiles"], positions)
        print("[select-images] sélection faite, écriture des liens et positions...", flush=True)
        counts = link_selection(selection, args.output_dir)
        write_tile_metric(selection, manifest["tiles"], manifest["epsg"], args.output_dir,
                          positions, args.global_metric)
        for tile_id, count in counts.items():
            print(f"[OK] {tile_id} : {count} image(s)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        sys.exit(f"[ERREUR] {error}")
