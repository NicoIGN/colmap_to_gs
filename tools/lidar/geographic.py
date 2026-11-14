"""Conversions between georeferenced local ENU and projected LiDAR coordinates."""

import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "preprocess"))
from georef_colmap_from_gpx import enu_rotation


def enu_lidar(points, lat_lon_height, crs):
    from pyproj import Transformer

    lat, lon, height = lat_lon_height
    geographic_to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    base = np.array(geographic_to_ecef.transform(lon, lat, height))
    to_geo = Transformer.from_crs(crs, 4979, always_xy=True)
    longitude, latitude, altitude = to_geo.transform(*points.T)
    ecef = np.column_stack(geographic_to_ecef.transform(longitude, latitude, altitude))
    return (ecef-base) @ enu_rotation(lat_lon_height).T


def projected_enu(points, lat_lon_height, crs):
    from pyproj import Transformer

    lat, lon, height = lat_lon_height
    to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    base = np.array(to_ecef.transform(lon, lat, height))
    ecef = points @ enu_rotation(lat_lon_height) + base
    to_geo = Transformer.from_crs(4978, 4979, always_xy=True)
    longitude, latitude, altitude = to_geo.transform(*ecef.T)
    to_projected = Transformer.from_crs(4979, crs, always_xy=True)
    return np.column_stack(to_projected.transform(longitude, latitude, altitude))


def reference_metadata(folder, explicit=None):
    candidates = [explicit] if explicit is not None else [
        folder/"georef_stats.json", folder/"orientation_reference.json"]
    for path in candidates:
        if path is None or not path.is_file():
            continue
        metadata = json.loads(path.read_text())
        reference = metadata.get("output_reference", metadata.get("reference"))
        if not isinstance(reference, dict) or reference.get("type") != "local_ENU":
            raise ValueError(f"Référence ENU manquante ou non prise en charge : {path}")
        if reference.get("units", "meters") != "meters" or reference.get(
                "axes", ["East", "North", "Up"]) != ["East", "North", "Up"]:
            raise ValueError(f"Axes/unités ENU non pris en charge : {path}")
        origin = np.asarray(reference.get("origin_lat_lon_height"), dtype=float)
        if (origin.shape != (3,) or not np.isfinite(origin).all()
                or abs(origin[0]) > 90 or abs(origin[1]) > 180):
            raise ValueError(f"Origine ENU invalide : {path}")
        return reference, path
    if explicit is not None:
        raise ValueError(f"Référence introuvable : {explicit}")
    return None, None


def local_laz(source, destination, reference):
    """Create a local working copy, preserving attributes and the shared global LAZ."""
    import copy
    import laspy

    if destination.exists():
        raise ValueError(f"Copie LiDAR locale déjà présente : {destination}")
    with laspy.open(source) as reader:
        header = copy.deepcopy(reader.header)
        header.offsets = np.zeros(3)
        header.scales = np.array([0.0001]*3)
        header.vlrs = [v for v in header.vlrs if v.user_id != "LASF_Projection"]
        with laspy.open(destination, mode="w", header=header, do_compress=True) as writer:
            for chunk in reader.chunk_iterator(100000):
                xyz = np.column_stack((chunk.x, chunk.y, chunk.z))
                xyz = enu_lidar(xyz, reference["origin_lat_lon_height"], "EPSG:2154")
                local = laspy.ScaleAwarePointRecord.zeros(len(chunk), header=header)
                for name in chunk.point_format.dimension_names:
                    if name not in ("X", "Y", "Z"):
                        local[name] = chunk[name]
                local.x, local.y, local.z = xyz.T
                writer.write_points(local)
