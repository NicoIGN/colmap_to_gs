#!/usr/bin/env python3
"""Écrit <nomsplat>.metadata.json à côté du splat final : métadonnées utilisateur et géoréférencement."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def load_reference(stats_path):
    if stats_path is None or not Path(stats_path).is_file():
        return {}
    return json.loads(Path(stats_path).read_text(encoding="utf-8"))


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
        frame = ("COLMAP GPS-aligned local ENU frame"
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
                        help="georef_stats.json ; absent ou manquant : pas de géoréférencement")
    args = parser.parse_args()
    metadata = build_metadata(args.splat, args.pipeline, load_reference(args.stats))
    output = args.splat.with_name(f"{args.splat.stem}.metadata.json")
    output.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[metadata] {output}", flush=True)


if __name__ == "__main__":
    main()
