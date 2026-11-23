#!/usr/bin/env python3
"""Écrit <nomsplat>.metadata.json à côté du splat final : métadonnées utilisateur et géoréférencement."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def load_gauge_reference(gauge_path, epsg):
    """Repère ENU de Spirula (gauge.txt), même lecture que le viewer."""
    values = {}
    for raw in Path(gauge_path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition(" ")
            values[key] = value.strip()
    lat, lon = float(values["origin_lat"]), float(values["origin_lon"])
    alt = float(values.get("origin_alt", 0))
    return {
        "type": "local_ENU",
        "units": "m",
        "axes": ["East", "North", "Up"],
        "origin_lat_lon_height": [lat, lon, alt],
        "origin_projected": {"epsg": epsg},
        "source": "gauge.txt",
    }


def load_reference(stats_path, epsg=2154):
    """Lit georef_stats.json ; sinon gauge.txt du même dossier (SfM spirula sans recalage COLMAP)."""
    if stats_path is None:
        return {}
    stats_path = Path(stats_path)
    stats = {}
    if stats_path.is_file():
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
    if "output_reference" not in stats and (stats_path.parent / "gauge.txt").is_file():
        stats = {
            "status": "georeferenced",
            "coordinate_frame": "Spirula metric local ENU frame aligned from Panoramax EXIF GPS",
            "output_reference": load_gauge_reference(stats_path.parent / "gauge.txt", epsg),
        }
    return stats


def build_metadata(splat, pipeline, stats):
    reference = stats.get("output_reference")
    origin = reference.get("origin_lat_lon_height") if reference else None
    projected = reference.get("origin_projected", {}) if reference else {}
    geometry = None
    if origin:
        lat, lon, height = origin
        geometry = {"type": "Point", "coordinates": [lon, lat, height]}
    frame = None
    if reference:
        frame = stats.get("coordinate_frame") or ("COLMAP GPS-aligned local ENU frame"
                                                  if stats.get("status", "georeferenced") == "georeferenced"
                                                  else "COLMAP LiDAR-aligned local ENU frame")
    metadata = {
        "type": "FeatureCollection",
        "name": splat.stem,
        "splat": splat.name,
        "format": splat.suffix.lstrip("."),
        "size_bytes": splat.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pipeline": pipeline,
        "georeferenced": reference is not None,
        "coordinate_frame": frame,
        "crs": (f"EPSG:{projected['epsg']}" if projected.get("epsg") else None),
        "quality": {
            "normalized_ordered_rmse": stats.get("normalized_ordered_rmse"),
            "ambiguous": stats.get("ambiguous"),
            "warnings": stats.get("warnings", []),
        },
        "features": [{
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "name": splat.stem,
                "role": "origine du repère géoréférencé",
            },
        }],
    }
    if reference is not None:
        metadata["output_reference"] = reference
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splat", type=Path, required=True)
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--stats", type=Path, default=None,
                        help="georef_stats.json ; sinon gauge.txt du même dossier ; absents : pas de géoréférencement")
    args = parser.parse_args()
    epsg = int(os.environ.get("GEOREF_EPSG", "2154"))
    metadata = build_metadata(args.splat, args.pipeline, load_reference(args.stats, epsg))
    output = args.splat.with_name(f"{args.splat.stem}.metadata.json")
    output.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[metadata] {output}", flush=True)


if __name__ == "__main__":
    main()
