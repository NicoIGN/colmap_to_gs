#!/usr/bin/env python3
"""Convert a colored LAS/LAZ into PLY, optionally cropped and shifted to a local origin."""

import argparse
from pathlib import Path
import sys

import numpy as np


def _inside(chunk, bbox):
    if bbox is None:
        return slice(None)
    xmin, ymin, xmax, ymax = bbox
    x, y = np.asarray(chunk.x), np.asarray(chunk.y)
    return (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)


def convert(source, output, stride, origin=None, bbox=None):
    """origin (x,y,z) is subtracted from coordinates; bbox (xmin,ymin,xmax,ymax) is applied before stride."""
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
        kept = 0
        for chunk in reader.chunk_iterator(100000):
            inside = _inside(chunk, bbox)
            selected = np.column_stack((chunk.red, chunk.green, chunk.blue))[inside]
            kept += len(selected)
            if len(selected):
                maximum_color = max(maximum_color, int(selected.max()))
        count = (kept + stride - 1) // stride
        if not count:
            raise ValueError("Nuage vide" + (" dans la bbox." if bbox is not None else "."))
    shift = np.zeros(3) if origin is None else np.asarray(origin, dtype=float)
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
            inside = _inside(chunk, bbox)
            xyz = np.column_stack((chunk.x, chunk.y, chunk.z))[inside]
            rgb = np.column_stack((chunk.red, chunk.green, chunk.blue))[inside]
            start = (-offset) % stride
            offset += len(xyz)
            xyz = xyz[start::stride] - shift
            rgb = rgb[start::stride]
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
    parser.add_argument("--origin", help="X,Y,Z soustrait aux coordonnées")
    parser.add_argument("--bbox", help="xmin,ymin,xmax,ymax appliquée avant --stride")
    args = parser.parse_args()
    try:
        if args.stride < 1:
            raise ValueError("--stride doit être >= 1.")
        origin = bbox = None
        if args.origin:
            origin = [float(value) for value in args.origin.split(",")]
            if len(origin) != 3:
                raise ValueError("--origin attend X,Y,Z")
        if args.bbox:
            bbox = [float(value) for value in args.bbox.split(",")]
            if len(bbox) != 4 or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                raise ValueError("--bbox attend xmin,ymin,xmax,ymax")
        convert(args.input, args.output, args.stride, origin, bbox)
    except (OSError, ValueError) as error:
        sys.exit(f"[ERREUR] Conversion LAS/LAZ : {error}")
