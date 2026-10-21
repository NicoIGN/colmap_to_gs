#!/usr/bin/env python3
"""Affiche les propriétés d'une image et convertit son GPS vers un EPSG."""

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess

from PIL import Image
from pyproj import CRS, Transformer


def read_metadata(path):
    tags = [
        "Make", "Model", "LensModel",
        "FocalLength", "FocalLengthIn35mmFormat",
        "GPSLatitude", "GPSLongitude", "GPSAltitude", "GPSMapDatum",
        "ProjectionType", "UsePanoramaViewer",
        "FullPanoWidthPixels", "FullPanoHeightPixels",
        "CroppedAreaImageWidthPixels", "CroppedAreaImageHeightPixels",
    ]

    result = subprocess.run(
        [
            "exiftool", "-j", "-n",
            *[f"-{tag}" for tag in tags],
            str(path.resolve()),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)[0]


def describe_panorama(meta, width, height):
    projection = str(meta.get("ProjectionType", "")).lower()

    if projection == "equirectangular":
        full_w = meta.get("FullPanoWidthPixels")
        full_h = meta.get("FullPanoHeightPixels")
        crop_w = meta.get("CroppedAreaImageWidthPixels", width)
        crop_h = meta.get("CroppedAreaImageHeightPixels", height)

        if full_w and full_h:
            if crop_w == full_w and crop_h == full_h:
                return "Panorama équirectangulaire complet déclaré (métadonnées)."
            return "Panorama équirectangulaire déclaré, mais image recadrée."

        return (
            "Projection équirectangulaire déclarée ; "
            "couverture complète 360° non confirmée."
        )

    if projection:
        return f"Projection déclarée : {projection} ; 360° non confirmé."

    if abs(width / height - 2.0) < 0.02:
        return (
            "360° possible : ratio proche de 2:1, "
            "mais aucune métadonnée ne le confirme."
        )

    return "Indéterminé : aucun indice suffisant d'une image 360°."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Image JPEG, PNG ou TIFF.")
    parser.add_argument(
        "--epsg",
        default="2154",
        help="EPSG cible : 2154 ou EPSG:2154 (défaut : Lambert-93).",
    )
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"Image introuvable : {args.image}")
    if not shutil.which("exiftool"):
        parser.error("ExifTool introuvable dans le PATH.")

    try:
        target = CRS.from_user_input(
            args.epsg if ":" in args.epsg else f"EPSG:{args.epsg}"
        )
        # Conversion horizontale uniquement : pas de hauteur inventée.
        if (
            not (target.is_projected or target.is_geographic)
            or len(target.axis_info) != 2
        ):
            parser.error("Choisis un EPSG horizontal à deux axes.")
    except ValueError as exc:
        parser.error(f"EPSG invalide : {exc}")

    try:
        with Image.open(args.image) as image:
            width, height = image.size
            bands = image.getbands()
            mode = image.mode
            image_format = image.format

        meta = read_metadata(args.image)

        print(f"\nImage       : {args.image}")
        print(f"Format      : {image_format}")
        print(f"Dimensions  : {width} × {height} pixels")
        print(f"Mode        : {mode}")
        print(f"Canaux      : {len(bands)} ({', '.join(bands)})")
        if mode == "P":
            print("              Image indexée : les couleurs sont dans une palette.")

        camera = " ".join(
            str(meta[key]) for key in ("Make", "Model") if meta.get(key)
        )
        print(f"\nAppareil    : {camera or 'Non renseigné'}")
        print(f"Objectif    : {meta.get('LensModel', 'Non renseigné')}")

        focal = meta.get("FocalLength")
        focal35 = meta.get("FocalLengthIn35mmFormat")
        print(f"Focale      : {str(focal) + ' mm' if focal else 'Non renseignée'}")
        if focal35:
            print(f"Équiv. 35 mm : {focal35} mm")

        print(f"Panorama    : {describe_panorama(meta, width, height)}")

        lat = meta.get("GPSLatitude")
        lon = meta.get("GPSLongitude")
        alt = meta.get("GPSAltitude")

        if lat is None or lon is None:
            print("\nGPS         : aucune coordonnée trouvée.")
            return

        lat, lon = float(lat), float(lon)
        if (
            not math.isfinite(lat) or not -90 <= lat <= 90
            or not math.isfinite(lon) or not -180 <= lon <= 180
        ):
            raise ValueError("Coordonnées GPS invalides.")

        print(f"\nLatitude    : {lat:.9f}°")
        print(f"Longitude   : {lon:.9f}°")
        if alt is not None:
            print(f"Altitude GPS: {float(alt):.3f} m (non convertie)")

        datum = meta.get("GPSMapDatum", "Non renseigné")
        print(f"Datum EXIF  : {datum}")
        print("Source      : WGS 84 / EPSG:4326 supposé pour la conversion.")

        # always_xy=True : entrée longitude, latitude ; sortie X, Y.
        transformer = Transformer.from_crs(
            "EPSG:4326", target, always_xy=True
        )
        x, y = transformer.transform(lon, lat, errcheck=True)

        unit = target.axis_info[0].unit_name
        print(f"\nCible       : {target.to_string()} — {target.name}")
        print(f"X           : {x:.6f} {unit}")
        print(f"Y           : {y:.6f} {unit}")
        if target.is_geographic:
            print("Ordre       : X = longitude, Y = latitude")

    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Erreur : {exc}\n")


if __name__ == "__main__":
    main()
