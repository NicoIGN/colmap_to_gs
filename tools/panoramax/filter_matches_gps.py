#!/usr/bin/env python3
"""Filter Spirula VKMT matches before mapping, preserving unknown-GPS pairs."""

import argparse
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile

from pyproj import Geod


U32 = struct.Struct("<I")
PAIR = struct.Struct("<IIiI")


def read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"matches.bin tronqué à l'octet {stream.tell()}")
    return data


def copy_bytes(source, target, size):
    while size:
        chunk = read_exact(source, min(size, 1024 * 1024))
        if target is not None:
            target.write(chunk)
        size -= len(chunk)


def position(metadata_dir, image_name):
    # Spirula stores relative feature stems, without the JPEG extension.
    name = Path(image_name)
    if name.is_absolute() or ".." in name.parts:
        raise ValueError(f"Nom d'image non relatif : {image_name}")
    path = metadata_dir / f"{image_name}.json"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"Métadonnées Panoramax invalides : {path}")
    feature = data.get("feature")
    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    lon, lat = coordinates[:2]
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) for v in (lon, lat)):
        return None
    return (lon, lat) if -180 <= lon <= 180 and -90 <= lat <= 90 else None


def copy_camera_tail(source, target, version, image_count):
    if version == 2:
        if source.read(1):
            raise ValueError("Octets inattendus après les paires VKMT v2")
        return
    raw = read_exact(source, 4)
    target.write(raw)
    camera_count = U32.unpack(raw)[0]
    for _ in range(camera_count):
        header = read_exact(source, 29)  # id,width,height,model,prior,pixel_scale,np
        parameter_count = U32.unpack(header[-4:])[0]
        if parameter_count > 16:
            raise ValueError("Nombre de paramètres caméra VKMT invalide")
        target.write(header)
        copy_bytes(source, target, parameter_count * 8)
    if camera_count:
        raw = read_exact(source, 4)
        if U32.unpack(raw)[0] != image_count:
            raise ValueError("Affectation des caméras incompatible avec les images")
        target.write(raw)
        copy_bytes(source, target, image_count * 4)
        if version == 4:
            copy_bytes(source, target, camera_count)
    if source.read(1):
        raise ValueError("Octets inattendus après les caméras VKMT")


def filter_matches(input_path, output_path, metadata_dir, max_distance):
    input_path, output_path, metadata_dir = map(Path, (input_path, output_path, metadata_dir))
    if not math.isfinite(max_distance) or max_distance <= 0:
        raise ValueError("Le rayon GPS doit être fini et strictement positif")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("La sortie filtrée doit être distincte du matches.bin original")
    if not metadata_dir.is_dir():
        raise ValueError(f"Métadonnées Panoramax absentes : {metadata_dir}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=output_path.parent, suffix=".part")
    temporary = Path(temporary_name)
    geod = Geod(ellps="WGS84")
    counts = dict(input_pairs=0, kept_pairs=0, removed_pairs=0, kept_without_gps=0,
                  max_distance_m=max_distance, distance="WGS84 horizontal geodesic")
    try:
        with os.fdopen(fd, "wb") as target, input_path.open("rb") as source:
            header = read_exact(source, 12)
            magic, version, image_count = struct.unpack("<4sII", header)
            if magic != b"VKMT" or version not in (2, 3, 4):
                raise ValueError(f"Format matches.bin non supporté : {magic!r}, version {version}")
            # Bound allocations using the minimum possible record size.
            if image_count > input_path.stat().st_size // 8:
                raise ValueError("Nombre d'images VKMT invalide")
            target.write(header)
            positions = []
            for _ in range(image_count):
                raw = read_exact(source, 4)
                name_size = U32.unpack(raw)[0]
                if name_size > 1024 * 1024:
                    raise ValueError("Nom d'image VKMT trop long")
                name = read_exact(source, name_size)
                features = read_exact(source, 4)
                target.write(raw + name + features)
                positions.append(position(metadata_dir, name.decode("utf-8")))
            counts["images"] = image_count
            counts["images_without_gps"] = sum(p is None for p in positions)
            pair_count = U32.unpack(read_exact(source, 4))[0]
            if pair_count > input_path.stat().st_size // PAIR.size:
                raise ValueError("Nombre de paires VKMT invalide ou fichier encore en écriture")
            count_offset = target.tell()
            target.write(U32.pack(0))
            counts["input_pairs"] = pair_count
            for _ in range(pair_count):
                raw = read_exact(source, PAIR.size)
                first, second, _, matches = PAIR.unpack(raw)
                if first >= image_count or second >= image_count:
                    raise ValueError("Indice d'image hors limites dans matches.bin")
                a, b = positions[first], positions[second]
                keep = True
                if a is None or b is None:
                    counts["kept_without_gps"] += 1
                else:
                    _, _, distance = geod.inv(*a, *b)
                    if not math.isfinite(distance):
                        raise ValueError("Distance géodésique non finie")
                    keep = distance < max_distance
                if keep:
                    target.write(raw)
                    counts["kept_pairs"] += 1
                else:
                    counts["removed_pairs"] += 1
                copy_bytes(source, target if keep else None, matches * 8)
            copy_camera_tail(source, target, version, image_count)
            target.seek(count_offset)
            target.write(U32.pack(counts["kept_pairs"]))
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        f"[GPS] Rayon strict < {max_distance:g} m : "
        f"{counts['kept_pairs']}/{pair_count} paires conservées, "
        f"{counts['removed_pairs']} retirées, "
        f"{counts['kept_without_gps']} conservées sans GPS. Aucune paire ajoutée.",
        flush=True,
    )
    if counts["images_without_gps"]:
        print(
            f"[AVERTISSEMENT] {counts['images_without_gps']} image(s) sans GPS exploitable ; "
            "leurs paires candidates ne sont pas filtrées.",
            file=sys.stderr,
        )
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metadata-dir", required=True, type=Path)
    parser.add_argument("--max-distance", required=True, type=float)
    args = parser.parse_args()
    try:
        counts = filter_matches(args.input, args.output, args.metadata_dir, args.max_distance)
        if not counts["kept_pairs"]:
            raise ValueError("Aucune paire après filtrage GPS ; augmenter le rayon ou vérifier les positions")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[ERREUR] Filtrage GPS : {exc}\n")


if __name__ == "__main__":
    main()
