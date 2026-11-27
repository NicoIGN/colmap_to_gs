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
    python positions_to_geojson.py positions.txt --format kmz --images-dir images/
    python positions_to_geojson.py positions.txt --format kml --images-dir images/ \
        --image-url-prefix https://exemple.org/images   (affichage dans Google Maps)

Avec --images-dir, l'image dont le nom (sans extension) égale l'index est affichée dans la bulle.
KMZ : images embarquées (réduites à --image-max-size px) ; Google Earth les affiche, Google Maps
(Mes cartes) ne les affiche qu'à partir d'une URL publique (--image-url-prefix, KML).
"""

import argparse
import io
import json
import math
import os
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from pyproj import CRS, Transformer

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


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


def build_image_index(images_dir):
    """Associe le nom (stem) de chaque image à son fichier, sous-dossiers inclus.

    Un nom numérique est aussi indexé sans zéros de tête (000001 -> 1), comme l'index des positions.
    """
    index = {}
    for path in sorted(images_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            index.setdefault(path.stem, path)
            if path.stem.isdigit():
                index.setdefault(str(int(path.stem)), path)
    return index


def image_bytes(path, max_size):
    """Image source, ou JPEG réduit si max_size > 0 (fichiers KMZ plus légers)."""
    if not max_size:
        return path.read_bytes()
    from PIL import Image
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_size, max_size))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def write_kml(features, output_path, name, fmt, images_dir=None, url_prefix=None, max_size=800):
    """KML (ou KMZ avec images embarquées) : un repère par position, image dans la bulle."""
    image_index = build_image_index(images_dir) if images_dir else {}
    embedded = {}
    placemarks, missing = [], 0
    for feature in features:
        props = feature["properties"]
        description = (f"index {props['index']}<br/>"
                       f"lat {props['latitude']:.7f}, lon {props['longitude']:.7f}<br/>"
                       f"alt WGS84 {props['altitude']:.2f} m")
        if images_dir:
            source = image_index.get(str(props["index"]))
            if source is None:
                missing += 1
            else:
                if url_prefix:
                    src = url_prefix.rstrip("/") + "/" + quote(source.name)
                elif fmt == "kmz":
                    src = f"files/{source.name}"
                    embedded[src] = source
                else:
                    src = os.path.relpath(source.resolve(), output_path.parent.resolve()).replace(os.sep, "/")
                description = f'<img src="{escape(src)}" width="400"/><br/>' + description
        placemarks.append(
            f"<Placemark><name>{escape(str(props['index']))}</name>"
            f"<description><![CDATA[{description}]]></description>"
            f"<Point><altitudeMode>absolute</altitudeMode>"
            f"<coordinates>{props['longitude']:.8f},{props['latitude']:.8f},{props['altitude']:.3f}"
            f"</coordinates></Point></Placemark>")
    kml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
           f"<name>{escape(name)}</name>\n" + "\n".join(placemarks) + "\n</Document>\n</kml>\n")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "kmz":
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("doc.kml", kml)
            for arcname, source in embedded.items():
                archive.writestr(arcname, image_bytes(source, max_size))
    else:
        output_path.write_text(kml, encoding="utf-8")
    if images_dir:
        print(f"[OK] Images associées : {len(placemarks) - missing} ; sans image : {missing}")
    return 0


def convert(input_file, output_file, target_crs="EPSG:2154", fmt="geojson",
            images_dir=None, image_url_prefix=None, image_max_size=800):
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

    if fmt in ("kml", "kmz"):
        print(f"[OK] {len(features)} positions converties ({skipped} ignorées)")
        return write_kml(features, Path(output_file), Path(input_file).stem, fmt,
                         Path(images_dir) if images_dir else None, image_url_prefix, image_max_size)

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
    parser.add_argument("--crs", default="EPSG:2154", help="CRS cible pour GeoJSON (défaut : Lambert-93)")
    parser.add_argument("--format", choices=["geojson", "kml", "kmz"], default="geojson",
                        help="Format de sortie (kml/kmz : WGS84, importable dans Google Maps / Earth)")
    parser.add_argument("--images-dir", help="kml/kmz : dossier des images ; image = fichier dont le nom "
                                             "(sans extension) égale l'index de la position")
    parser.add_argument("--image-url-prefix", help="kml : URL publique du dossier d'images "
                                                   "(requis pour l'affichage dans Google Maps)")
    parser.add_argument("--image-max-size", type=int, default=800,
                        help="kmz : côté max des images embarquées en px (0 = copie d'origine)")
    args = parser.parse_args()
    if args.images_dir and args.format == "geojson":
        parser.error("--images-dir nécessite --format kml ou kmz")
    if args.image_url_prefix and args.format != "kml":
        parser.error("--image-url-prefix nécessite --format kml")

    output = args.output or str(Path(args.input).with_suffix(f".{args.format}"))
    sys.exit(convert(args.input, output, args.crs, args.format, args.images_dir,
                     args.image_url_prefix, args.image_max_size))


if __name__ == "__main__":
    main()
