#!/usr/bin/env python3
"""Produire un fichier de positions métriques `image X Y Z` pour Spirula.

Les images sont associées à leur index (`stream_..._<ID>.jpg` → `<ID>`), puis
à un timestamp lu dans le fichier d'événements. La position est interpolée
linéairement dans le fichier `.pos` (temps, lat, lon, hauteur) et convertie
en repère ENU local (mètres) autour de l'origine choisie.
"""

from __future__ import annotations

import argparse
import bisect
import math
import re
import sys
from pathlib import Path

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


def convert(events_path: Path, pos_path: Path, images: list[str]) -> list[str]:
    events = read_events(events_path)
    rows = read_pos(pos_path)
    origin = (rows[0][1], rows[0][2], rows[0][3])
    lines = []
    for name in images:
        index = image_id(name)
        if index not in events:
            raise ValueError(f"Index {index} absent de {events_path} (image {name})")
        lat, lon, height = interpolate(rows, events[index])
        x, y, z = to_enu(lat, lon, height, origin)
        lines.append(f"{Path(name).name} {x:.4f} {y:.4f} {z:.4f}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", required=True, type=Path, help="Fichier d'événements (index timestamp)")
    parser.add_argument("--pos", required=True, type=Path, help="Fichier .pos (temps lat lon hauteur ...)")
    parser.add_argument("--images-dir", required=True, type=Path, help="Dossier contenant les images")
    parser.add_argument("--output", required=True, type=Path, help="Fichier de sortie")
    args = parser.parse_args(argv)
    if not args.images_dir.is_dir():
        print(f"Erreur : dossier introuvable : {args.images_dir}", file=sys.stderr)
        return 1
    names = sorted(p.name for p in args.images_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
    if not names:
        print(f"Erreur : aucune image dans {args.images_dir}", file=sys.stderr)
        return 1
    try:
        lines = convert(args.events, args.pos, names)
    except (OSError, ValueError) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 1
    args.output.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
