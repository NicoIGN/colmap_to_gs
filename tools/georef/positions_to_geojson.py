#!/usr/bin/env python3
"""
Convertit un fichier de positions GPS (WGS84) en GeoJSON projeté.

Format d'entrée (une position par ligne, séparateurs espaces) :
    index latitude longitude altitude [param1 ... paramN]

Les lignes vides et celles commençant par '#' sont ignorées.
L'altitude est reprise telle quelle en Z : c'est une hauteur ellipsoïdale WGS84,
pas une altitude NGF-IGN69 (orthométrique).

Usage :
    python positions_to_geojson.py positions.txt -o positions.geojson
    python positions_to_geojson.py positions.txt --crs EPSG:2154
"""

import argparse
import json
import math
import sys
from pathlib import Path

from pyproj import CRS, Transformer


def parse_line(line):
    """Retourne (index, lat, lon, alt, extra) ou lève ValueError."""
    values = [float(v) for v in line.split()]
    if len(values) < 4:
        raise ValueError("moins de 4 champs")
    if not all(math.isfinite(v) for v in values):
        raise ValueError("valeur non finie")
    index, latitude, longitude, altitude = values[:4]
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("coordonnées WGS84 hors bornes")
    index = int(index) if index.is_integer() else index
    return index, latitude, longitude, altitude, values[4:]


def convert(input_file, output_file, target_crs="EPSG:2154"):
    crs = CRS.from_user_input(target_crs)
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    features = []
    skipped = 0
    with open(input_file, "r", encoding="utf-8") as f:
        for line_number, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                index, latitude, longitude, altitude, extra = parse_line(line)
            except ValueError as exc:
                print(f"[WARNING] Ligne {line_number} ignorée ({exc}) : {line}", file=sys.stderr)
                skipped += 1
                continue

            x, y = transformer.transform(longitude, latitude)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [x, y, altitude]},
                "properties": {
                    "index": index,
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude": altitude,
                    "extra": extra,
                },
            })

    if not features:
        print(f"[ERREUR] Aucune position valide dans {input_file}", file=sys.stderr)
        return 1

    geojson = {
        "type": "FeatureCollection",
        "name": Path(input_file).stem,
        "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:{crs.to_authority()[0]}::{crs.to_authority()[1]}"}},
        "features": features,
    }

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2, ensure_ascii=False)

    xs = [ft["geometry"]["coordinates"][0] for ft in features]
    ys = [ft["geometry"]["coordinates"][1] for ft in features]
    alts = [ft["properties"]["altitude"] for ft in features]
    print(f"[OK] {len(features)} positions converties ({skipped} ignorées)")
    print(f"[OK] Emprise {crs.to_string()} : X [{min(xs):.2f}, {max(xs):.2f}] "
          f"Y [{min(ys):.2f}, {max(ys):.2f}]")
    print(f"[OK] Altitude WGS84 : [{min(alts):.2f}, {max(alts):.2f}] m")
    print(f"[OK] Fichier créé : {output_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Conversion GPS WGS84 vers GeoJSON projeté")
    parser.add_argument("input", help="Fichier de positions")
    parser.add_argument("-o", "--output", default=None, help="Fichier GeoJSON de sortie")
    parser.add_argument("--crs", default="EPSG:2154", help="CRS cible (défaut : Lambert-93)")
    args = parser.parse_args()

    output = args.output or str(Path(args.input).with_suffix(".geojson"))
    sys.exit(convert(args.input, output, args.crs))


if __name__ == "__main__":
    main()
