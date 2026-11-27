#!/usr/bin/env python3
"""Produire un fichier de positions métriques `image X Y Z` pour Spirula.

Les images sont associées à leur index (`stream_..._<ID>.jpg` → `<ID>`), puis
à un timestamp lu dans le fichier d'événements. La position est interpolée
linéairement dans le fichier `.pos` (temps, lat, lon, hauteur) et convertie
en repère ENU local (mètres) autour de l'origine choisie.

Les positions du `.pos` sont en coordonnées géographiques WGS84 (lat, lon).
Option `--bbox` : ne garde que les images dont la position, projetée dans
`--crs`, est dans l'emprise `xmin,ymin,xmax,ymax`. Option `--links-dir` : crée
des liens symboliques vers les images retenues.
"""

from __future__ import annotations

import argparse
import bisect
import math
import re
import sys
from pathlib import Path

from pyproj import Transformer
from tqdm import tqdm

WGS84_A = 6378137.0
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
IMAGE_ID_RE = re.compile(r"_(\d+)\.(?:jpe?g|png)$", re.IGNORECASE)


def image_id(name: str) -> int:
    match = IMAGE_ID_RE.search(Path(name).name)
    if not match:
        raise ValueError(f"ID introuvable dans le nom d'image : {name}")
    return int(match.group(1))


def read_events(path: Path) -> dict[int, float]:
    events = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            events[int(parts[0])] = float(parts[1])
    return events


def read_pos(path: Path) -> list[tuple[float, float, float, float]]:
    rows = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 4 or line.lstrip().startswith("%"):
            continue
        t, lat, lon, height = (float(v) for v in parts[:4])
        rows.append((t, lat, lon, height))
    rows.sort()
    if not rows:
        raise ValueError(f"Aucune position dans {path}")
    return rows


def interpolate(rows, t: float) -> tuple[float, float, float]:
    times = [r[0] for r in rows]
    if t < times[0] or t > times[-1]:
        raise ValueError(f"Timestamp {t} hors de la trajectoire [{times[0]}, {times[-1]}]")
    k = bisect.bisect_right(times, t)
    if k >= len(rows):
        return rows[-1][1:]
    t0, *p0 = rows[k - 1]
    t1, *p1 = rows[k]
    w = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
    return tuple(a + w * (b - a) for a, b in zip(p0[:3], p1[:3]))


def to_enu(lat: float, lon: float, height: float, origin: tuple[float, float, float]) -> tuple[float, float, float]:
    lat0, lon0, h0 = origin
    lat_r0 = math.radians(lat0)
    east = math.radians(lon - lon0) * WGS84_A * math.cos(lat_r0)
    north = math.radians(lat - lat0) * WGS84_A
    return east, north, height - h0


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        xmin, ymin, xmax, ymax = (float(v) for v in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("format attendu : xmin,ymin,xmax,ymax") from None
    if xmin >= xmax or ymin >= ymax:
        raise argparse.ArgumentTypeError("xmin < xmax et ymin < ymax sont requis")
    return xmin, ymin, xmax, ymax


def convert(
    events_path: Path,
    pos_path: Path,
    images: list[str],
    bbox: tuple[float, float, float, float] | None = None,
    crs: str = "EPSG:2154",
) -> tuple[list[str], list[str], tuple[float, float, float]]:
    """Retourne les lignes `image X Y Z`, les noms d'images retenus et l'origine.

    L'origine est la moyenne des positions retenues, comme Spirula la prend pour les
    EXIF GPS (meanGeodetic) : le gauge.txt se lit alors comme s'il venait des EXIF.
    """
    events = read_events(events_path)
    rows = read_pos(pos_path)
    to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True) if bbox else None
    fixes, kept = [], []
    for name in tqdm(images, desc="Positions", unit="img", file=sys.stderr):
        try:
            index = image_id(name)
            if index not in events:
                raise ValueError(f"index {index} absent de {events_path}")
            fix = interpolate(rows, events[index])
        except ValueError as error:
            tqdm.write(f"Avertissement : image ignorée ({name}) : {error}", file=sys.stderr)
            continue
        if to_crs:
            east_crs, north_crs = to_crs.transform(fix[1], fix[0])
            xmin, ymin, xmax, ymax = bbox
            if not (xmin <= east_crs <= xmax and ymin <= north_crs <= ymax):
                continue
        fixes.append(fix)
        kept.append(name)
    if not fixes:
        raise ValueError("Aucune image n'a de position (ou aucune dans la bbox)")
    origin = tuple(sum(f[k] for f in fixes) / len(fixes) for k in range(3))
    lines = []
    for name, fix in zip(kept, fixes):
        x, y, z = to_enu(*fix, origin)
        lines.append(f"{Path(name).name} {x:.4f} {y:.4f} {z:.4f}")
    print(f"{len(lines)}/{len(images)} images retenues", file=sys.stderr)
    return lines, kept, origin


GAUGE_TOLERANCE_DEG = 1e-8
GAUGE_TOLERANCE_M = 1e-3


def write_gauge(path: Path, origin: tuple[float, float, float]) -> None:
    """Écrit gauge.txt au format de Spirula (writeGauge) pour un recalage par positions.

    `up`/`scale` = `positions` comme Spirula pour --metric-positions ; l'origine et
    `alt_source` sont celles qu'il écrit pour des EXIF GPS (moyenne des fixes).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# What this model's frame means, from spirula sfm.\n"
        "oriented 1\nmetric 1\nup positions\nscale positions\n"
        "# x east, y north of this WGS-84 point, in metres\n"
        f"origin_lat {origin[0]:.9f}\norigin_lon {origin[1]:.9f}\norigin_alt {origin[2]:.3f}\n"
        "alt_source pos\n"
    )


def read_header_origin(path: Path) -> tuple[float, float, float] | None:
    """Origine écrite en en-tête (`# origin_lat …`) du fichier de positions, sinon None."""
    values = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "#" and parts[1].startswith("origin_"):
            values[parts[1]] = float(parts[2])
    if not {"origin_lat", "origin_lon", "origin_alt"} <= values.keys():
        return None
    return values["origin_lat"], values["origin_lon"], values["origin_alt"]


def check_gauge(path: Path, expected: tuple[float, float, float] | None = None) -> list[str]:
    """Retourne les erreurs ; vide si gauge.txt est lisible par Spirula et cohérent avec `expected`."""
    if not path.is_file():
        return [f"fichier introuvable : {path}"]
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        parts = raw.split()
        if len(parts) >= 2 and not raw.lstrip().startswith("#"):
            values[parts[0]] = parts[1]
    errors = []
    for key in ("oriented", "metric"):
        if values.get(key) != "1":
            errors.append(f"{key} doit valoir 1 (actuel : {values.get(key)!r})")
    origin = {}
    for key in ("origin_lat", "origin_lon", "origin_alt"):
        if key not in values:
            errors.append(f"clé absente : {key}")
            continue
        try:
            origin[key] = float(values[key])
        except ValueError:
            errors.append(f"valeur non numérique pour {key} : {values[key]!r}")
    if "origin_lat" in origin and not -90 <= origin["origin_lat"] <= 90:
        errors.append(f"origin_lat hors bornes : {origin['origin_lat']}")
    if "origin_lon" in origin and not -180 <= origin["origin_lon"] <= 180:
        errors.append(f"origin_lon hors bornes : {origin['origin_lon']}")
    if expected and len(origin) == 3:
        if abs(origin["origin_lat"] - expected[0]) > GAUGE_TOLERANCE_DEG or \
           abs(origin["origin_lon"] - expected[1]) > GAUGE_TOLERANCE_DEG:
            errors.append(f"origine lat/lon différente de l'en-tête des positions : {expected[:2]}")
        if abs(origin["origin_alt"] - expected[2]) > GAUGE_TOLERANCE_M:
            errors.append(f"altitude différente de l'en-tête des positions : {expected[2]}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", type=Path, help="Fichier d'événements (index timestamp)")
    parser.add_argument("--pos", type=Path, help="Fichier .pos (temps lat lon hauteur ...)")
    parser.add_argument("--images-dir", type=Path, help="Dossier contenant les images")
    parser.add_argument("--output", type=Path, help="Fichier de sortie")
    parser.add_argument("--bbox", type=parse_bbox, help="Emprise xmin,ymin,xmax,ymax dans --crs")
    parser.add_argument("--crs", default="EPSG:2154", help="CRS de --bbox (défaut : Lambert-93)")
    parser.add_argument("--links-dir", type=Path, help="Dossier de liens symboliques vers les images retenues")
    parser.add_argument("--gauge", type=Path, help="Écrire aussi gauge.txt (origine du .pos)")
    parser.add_argument("--check-gauge", type=Path,
                        help="Valider un gauge.txt existant (avec --pos : cohérence avec l'origine du .pos) et quitter")
    args = parser.parse_args(argv)

    if args.check_gauge:
        expected = read_header_origin(args.output) if args.output and args.output.is_file() else None
        errors = check_gauge(args.check_gauge, expected)
        for error in errors:
            print(f"[ERREUR] gauge.txt : {error}", file=sys.stderr)
        if not errors:
            print(f"[OK] gauge.txt valide : {args.check_gauge}")
        return 1 if errors else 0

    missing = [flag for flag, value in (("--events", args.events), ("--pos", args.pos),
                                        ("--images-dir", args.images_dir), ("--output", args.output))
               if value is None]
    if missing:
        parser.error(f"arguments requis : {', '.join(missing)} (ou --check-gauge)")
    if args.bbox and not args.links_dir:
        parser.error("--links-dir est obligatoire avec --bbox")
    if not args.images_dir.is_dir():
        print(f"Erreur : dossier introuvable : {args.images_dir}", file=sys.stderr)
        return 1
    names = sorted(p.name for p in args.images_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
    if not names:
        print(f"Erreur : aucune image dans {args.images_dir}", file=sys.stderr)
        return 1
    try:
        lines, kept, origin = convert(args.events, args.pos, names, args.bbox, args.crs)
    except (OSError, ValueError) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    header = [f"# origin_lat {origin[0]:.9f}", f"# origin_lon {origin[1]:.9f}",
              f"# origin_alt {origin[2]:.3f}"]
    args.output.write_text("\n".join(header + lines) + "\n")
    if args.gauge:
        write_gauge(args.gauge, origin)
        print(f"[OK] gauge.txt écrit : {args.gauge}", file=sys.stderr)
    if args.links_dir:
        args.links_dir.mkdir(parents=True, exist_ok=True)
        source_dir = args.images_dir.resolve()
        for name in kept:
            link = args.links_dir / name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(source_dir / name)
        print(f"{len(kept)} liens créés dans {args.links_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
