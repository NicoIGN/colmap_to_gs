#!/usr/bin/env python3
"""Filtrer les centres de gaussiennes éloignés du LiDAR dans un repère métrique commun."""

import argparse
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from common import class_codes, load_lidar, open_scene, vector, write_report, write_scene


def distance_mask(points, lidar, distance, bbox=None):
    if not np.isfinite(distance) or distance <= 0:
        raise ValueError("Distance attendue finie et > 0")
    tree = cKDTree(lidar)
    keep = np.ones(len(points), dtype=bool)
    tested = 0
    for start in range(0, len(points), 100000):
        chunk = points[start:start+100000]
        inside = (np.ones(len(chunk), dtype=bool) if bbox is None else
                  ((chunk >= bbox[:3]) & (chunk <= bbox[3:])).all(axis=1))
        tested += int(inside.sum())
        keep[start:start+len(chunk)][inside] = tree.query(chunk[inside])[0] <= distance
    return keep, tested


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lidar", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", default="0,0,0", help="Origine des splats soustraite au LAZ")
    parser.add_argument("--max-distance", type=float, required=True)
    parser.add_argument("--lidar-stride", type=int, default=1)
    parser.add_argument("--lidar-classes", help="Codes LAS de référence ; défaut : tous")
    parser.add_argument("--bbox", help="Zone locale xmin,ymin,zmin,xmax,ymax,zmax ; hors zone conservé")
    parser.add_argument("--rejected-output", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Rapport seul, sans écrire de scène")
    args = parser.parse_args()
    scene = None
    try:
        report_path = args.output.with_suffix(args.output.suffix + ".json")
        destinations = [args.output, report_path]
        if args.rejected_output:
            destinations.append(args.rejected_output)
        if len({p.resolve() for p in destinations}) != len(destinations):
            raise ValueError("Chemins de sortie distincts requis")
        if any(p.exists() for p in destinations):
            raise ValueError("Une sortie existe déjà")
        for path in (args.output, args.rejected_output):
            if path is not None and path.suffix.lower() != args.input.suffix.lower():
                raise ValueError("Les scènes de sortie doivent conserver le format de l'entrée")
        origin = vector(args.origin)
        classes = None if args.lidar_classes is None else class_codes(args.lidar_classes)
        bbox = None
        if args.bbox:
            bbox = np.array([float(v) for v in args.bbox.split(",")])
            if (bbox.shape != (6,) or not np.isfinite(bbox).all()
                    or np.any(bbox[:3] >= bbox[3:])):
                raise ValueError("BBox : six coordonnées finies et min < max requis")
        scene = open_scene(args.input)
        xyz, _ = scene.geometry(slice(None))
        lidar = load_lidar(args.lidar, origin, args.lidar_stride, classes=classes)
        keep, tested = distance_mask(xyz, lidar, args.max_distance, bbox)
        report = dict(total=scene.count, tested=tested, kept=int(keep.sum()),
                      rejected=int((~keep).sum()), max_distance=args.max_distance,
                      origin=origin.tolist(), lidar_points=len(lidar),
                      lidar_classes=None if classes is None else sorted(classes),
                      bbox=None if bbox is None else bbox.tolist(), dry_run=args.dry_run,
                      warning="Absence de LiDAR ne prouve pas un artefact ; vérifier couverture et dates.")
        if not args.dry_run:
            write_scene(args.output, scene, keep)
            if args.rejected_output:
                write_scene(args.rejected_output, scene, ~keep)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_report(report_path, report)
        print(f"[lidar] {report['rejected']}/{scene.count} splats proposés au rejet. "
              + report["warning"])
        if classes is not None:
            print("[AVERTISSEMENT] Classes LiDAR restreintes : les objets des autres "
                  "classes peuvent être rejetés ; contrôler le diagnostic avant suppression.")
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"[ERREUR] Filtrage LiDAR : {error}\n")
    finally:
        if scene is not None:
            scene.close()


if __name__ == "__main__":
    main()
