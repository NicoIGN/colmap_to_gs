#!/usr/bin/env python3
"""Convert a colored LAS/LAZ into PLY without changing its coordinates."""

import argparse
from pathlib import Path
import sys

import numpy as np
def convert(source, output, stride):
    try:
        import laspy
    except ImportError as error:
        raise ValueError("Installer laspy et lazrs pour convertir LAS/LAZ.") from error
    if output.exists():
        raise ValueError(f"Sortie déjà présente : {output}")
    dtype = np.dtype([
        ("x", "<f8"), ("y", "<f8"), ("z", "<f8"),
        ("red", "u1"), ("green", "u1"), ("blue", "u1"),
    ])
    maximum_color = 0
    with laspy.open(source) as reader:
        dimensions = set(reader.header.point_format.dimension_names)
        if not {"red", "green", "blue"} <= dimensions:
            raise ValueError("LAS/LAZ coloré requis (red, green, blue) ; aucune couleur inventée.")
        count = (reader.header.point_count + stride - 1) // stride
        if not count:
            raise ValueError("Nuage vide.")
        for chunk in reader.chunk_iterator(100000):
            maximum_color = max(maximum_color, *(int(chunk[key].max())
                                                for key in ("red", "green", "blue")))
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists():
        raise ValueError(f"Conversion partielle existante à sauvegarder : {temporary}")
    with laspy.open(source) as reader, temporary.open("xb") as stream:
        stream.write((
            "ply\nformat binary_little_endian 1.0\n"
            f"element vertex {count}\nproperty double x\nproperty double y\nproperty double z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
        ).encode("ascii"))
        offset = 0
        for chunk in reader.chunk_iterator(100000):
            start = (-offset) % stride
            offset += len(chunk)
            xyz = np.column_stack((chunk.x, chunk.y, chunk.z))[start::stride]
            rgb = np.column_stack((chunk.red, chunk.green, chunk.blue))[start::stride]
            if not np.isfinite(xyz).all():
                raise ValueError("Coordonnées non finies ; sortie .partial conservée pour contrôle.")
            if maximum_color > 255:
                rgb = np.rint(rgb / 257)
            vertex = np.empty(len(xyz), dtype=dtype)
            for index, field in enumerate(("x", "y", "z")):
                vertex[field] = xyz[:, index]
            for index, field in enumerate(("red", "green", "blue")):
                vertex[field] = rgb[:, index]
            stream.write(vertex.tobytes())
    temporary.replace(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stride", type=int, default=1)
    args = parser.parse_args()
    try:
        if args.stride < 1:
            raise ValueError("--stride doit être >= 1.")
        convert(args.input, args.output, args.stride)
    except (OSError, ValueError) as error:
        sys.exit(f"[ERREUR] Conversion LAS/LAZ : {error}")
