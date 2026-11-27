#!/usr/bin/env python3
"""Exporte un modèle sparse COLMAP binaire en GeoPackage, sans reprojection."""

import argparse
import math
import os
import struct
from pathlib import Path

from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()


def read_values(stream, fmt):
    """Lecture little-endian avec détection des fichiers tronqués."""
    fmt = "<" + fmt
    size = struct.calcsize(fmt)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(
            f"{stream.name} : fin de fichier inattendue à l'octet "
            f"{stream.tell()}."
        )
    return struct.unpack(fmt, data)


def skip_bytes(stream, size):
    """Saute les observations sans les charger en mémoire."""
    end = stream.tell() + size
    if end > os.fstat(stream.fileno()).st_size:
        raise ValueError(f"{stream.name} : fichier tronqué.")
    stream.seek(end)


def read_name(stream):
    """Les noms COLMAP sont terminés par un octet nul."""
    data = bytearray()
    while True:
        char = stream.read(1)
        if not char:
            raise ValueError(f"{stream.name} : nom d'image tronqué.")
        if char == b"\x00":
            return data.decode("utf-8", errors="replace")
        data.extend(char)


def camera_center(qw, qx, qy, qz, tx, ty, tz):
    """Pose monde -> caméra COLMAP : centre C = -R.T @ t."""
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Quaternion caméra invalide.")

    w, x, y, z = (v / norm for v in (qw, qx, qy, qz))

    r00 = 1 - 2 * (y * y + z * z)
    r01 = 2 * (x * y - w * z)
    r02 = 2 * (x * z + w * y)
    r10 = 2 * (x * y + w * z)
    r11 = 1 - 2 * (x * x + z * z)
    r12 = 2 * (y * z - w * x)
    r20 = 2 * (x * z - w * y)
    r21 = 2 * (y * z + w * x)
    r22 = 1 - 2 * (x * x + y * y)

    return (
        -(r00 * tx + r10 * ty + r20 * tz),
        -(r01 * tx + r11 * ty + r21 * tz),
        -(r02 * tx + r12 * ty + r22 * tz),
    )


def read_images(path):
    with path.open("rb") as stream:
        count, = read_values(stream, "Q")
        for _ in range(count):
            # IMAGE_ID, QW QX QY QZ, TX TY TZ, CAMERA_ID
            record = read_values(stream, "idddddddi")
            image_id = record[0]
            center = camera_center(*record[1:8])
            camera_id = record[8]
            name = read_name(stream)

            # Chaque observation : X(double), Y(double), POINT3D_ID(int64).
            n_points2d, = read_values(stream, "Q")
            skip_bytes(stream, n_points2d * 24)

            yield center, {
                "image_id": image_id,
                "image_name": name,
                "camera_id": camera_id,
            }


def read_points(path):
    with path.open("rb") as stream:
        count, = read_values(stream, "Q")
        for _ in range(count):
            point_id, x, y, z, r, g, b, error = read_values(
                stream, "QdddBBBd"
            )
            track_length, = read_values(stream, "Q")

            # Chaque élément de track : IMAGE_ID(int32), POINT2D_IDX(int32).
            skip_bytes(stream, track_length * 8)

            yield (x, y, z), {
                # Texte pour préserver tout l'intervalle uint64 de COLMAP.
                "point3d_id": str(point_id),
                "red": r,
                "green": g,
                "blue": b,
                "error_px": error,
                "n_obs": track_length,
            }


def write_layer(dataset, name, srs, fields, records):
    layer = dataset.CreateLayer(
        name,
        srs=srs,
        geom_type=ogr.wkbPoint25D,
        options=["SPATIAL_INDEX=YES"],
    )
    for field_name, field_type in fields:
        layer.CreateField(ogr.FieldDefn(field_name, field_type))

    definition = layer.GetLayerDefn()
    count = 0

    # Écriture en flux : pas de chargement du nuage complet en mémoire.
    layer.StartTransaction()
    try:
        for xyz, attributes in records:
            feature = ogr.Feature(definition)
            for key, value in attributes.items():
                feature.SetField(key, value)

            geometry = ogr.Geometry(ogr.wkbPoint25D)
            geometry.AddPoint(*xyz)
            feature.SetGeometry(geometry)
            layer.CreateFeature(feature)
            feature = None

            count += 1
            if count % 10000 == 0:
                layer.CommitTransaction()
                layer.StartTransaction()

        layer.CommitTransaction()
    except Exception:
        layer.RollbackTransaction()
        raise

    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sparse",
        type=Path,
        help="Dossier contenant images.bin et points3D.bin, par ex. sparse/0",
    )
    parser.add_argument("output", type=Path, help="GeoPackage de sortie")
    parser.add_argument(
        "--epsg",
        type=int,
        help="SCR des coordonnées existantes ; aucune reprojection",
    )
    args = parser.parse_args()

    for filename in ("images.bin", "points3D.bin"):
        if not (args.sparse / filename).is_file():
            parser.error(f"Fichier introuvable : {args.sparse / filename}")
    if args.output.exists():
        parser.error(f"La sortie existe déjà : {args.output}")
    if not args.output.parent.is_dir():
        parser.error(f"Dossier de sortie absent : {args.output.parent}")

    srs = None
    if args.epsg is not None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(args.epsg)
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    else:
        print("Attention : SCR non défini ; coordonnées COLMAP conservées.")

    dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(args.output))
    if dataset is None:
        raise RuntimeError(f"Impossible de créer {args.output}")

    try:
        n_images = write_layer(
            dataset,
            "prises_de_vue",
            srs,
            [
                ("image_id", ogr.OFTInteger),
                ("image_name", ogr.OFTString),
                ("camera_id", ogr.OFTInteger),
            ],
            read_images(args.sparse / "images.bin"),
        )

        n_points = write_layer(
            dataset,
            "points_3d",
            srs,
            [
                ("point3d_id", ogr.OFTString),
                ("red", ogr.OFTInteger),
                ("green", ogr.OFTInteger),
                ("blue", ogr.OFTInteger),
                ("error_px", ogr.OFTReal),
                ("n_obs", ogr.OFTInteger64),
            ],
            read_points(args.sparse / "points3D.bin"),
        )
    except Exception:
        print(
            f"Échec : {args.output} peut être incomplet. "
            "Supprime-le avant de relancer."
        )
        raise
    finally:
        dataset = None  # Ferme et finalise le GeoPackage.

    print(f"Créé : {args.output}")
    print(f"  prises_de_vue : {n_images:,} centres caméra")
    print(f"  points_3d     : {n_points:,} points")


if __name__ == "__main__":
    main()
