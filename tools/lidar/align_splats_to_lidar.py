#!/usr/bin/env python3
"""Recalage ICP robuste de gaussiennes approximativement géoréférencées sur LAS/LAZ."""

import argparse
from pathlib import Path

import numpy as np

from common import (class_codes, load_lidar, open_scene, register, sh_transform,
                    transform_records, vector, write_report, write_scene)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lidar", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", default="0,0,0", help="Origine des splats soustraite au LAZ")
    parser.add_argument("--max-correspondence-distance", type=float, required=True)
    parser.add_argument("--voxel-size", type=float, default=0.2)
    parser.add_argument("--lidar-stride", type=int, default=1)
    parser.add_argument("--lidar-classes", help="Codes LAS retenus, séparés par virgules ; défaut : tous")
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--min-overlap", type=float, default=0.3)
    parser.add_argument("--trim-fraction", type=float, default=0.8)
    parser.add_argument("--estimate-scale", action="store_true")
    args = parser.parse_args()
    scene = None
    try:
        report_path = args.output.with_suffix(args.output.suffix + ".json")
        if args.output.exists() or report_path.exists():
            raise ValueError("Sortie ou rapport déjà présent")
        if args.output.suffix.lower() != args.input.suffix.lower():
            raise ValueError("La sortie doit conserver le format de l'entrée")
        origin = vector(args.origin)
        classes = None if args.lidar_classes is None else class_codes(args.lidar_classes)
        scene = open_scene(args.input)
        xyz, _ = scene.geometry(slice(None))
        lidar = load_lidar(args.lidar, origin, args.lidar_stride, classes=classes)
        s, r, t, report = register(
            xyz, lidar, distance=args.max_correspondence_distance, voxel=args.voxel_size,
            iterations=args.iterations, min_overlap=args.min_overlap,
            trim=args.trim_fraction, estimate_scale=args.estimate_scale)
        sh = None
        if not scene.is_splat:
            names = [n for n in scene.raw.dtype.names if n.startswith("f_rest_")]
            if names:
                if len(names) % 3 or set(names) != {f"f_rest_{i}" for i in range(len(names))}:
                    raise ValueError("Schéma SH invalide")
                sh = sh_transform(r, len(names)//3)
        matrix = np.eye(4)
        matrix[:3, :3], matrix[:3, 3] = s*r, t
        report.update(origin=origin.tolist(), lidar_classes=None if classes is None else sorted(classes),
                      scale=s, rotation=r.tolist(),
                      translation=t.tolist(), matrix=matrix.tolist(),
                      convention="xyz_out_local = scale * R @ xyz_in_local + translation")
        write_scene(args.output, scene,
                    transform=lambda raw: transform_records(scene, raw, s, r, t, sh))
        write_report(report_path, report)
        print(f"[lidar] Recalage : RMSE {report['rmse_before']:.4f} -> "
              f"{report['rmse_after']:.4f} ; recouvrement {report['overlap']:.1%}")
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"[ERREUR] Recalage LiDAR : {error}\n")
    finally:
        if scene is not None:
            scene.close()


if __name__ == "__main__":
    main()
