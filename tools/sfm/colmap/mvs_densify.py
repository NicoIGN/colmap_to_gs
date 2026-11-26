#!/usr/bin/env python3
"""Densifie le SfM avec COLMAP MVS (undistort, PatchMatch, fusion) et écrit un modèle sparse/0.

Cameras et poses sont copiées du SfM d'origine. points3D.bin contient les points denses ; chacun
reçoit la track de son point épars le plus proche, pour que le découpage et le filtrage restent valides.
"""
import argparse
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
from plyfile import PlyData
from scipy.spatial import cKDTree


def read_points3d(path):
    """Renvoie les positions et les tracks (N, 2) uint32 des points d'un points3D.bin."""
    data = path.read_bytes()
    (count,) = struct.unpack_from("<Q", data, 0)
    offset = 8
    xyz = np.empty((count, 3))
    tracks = []
    for index in range(count):
        _, x, y, z = struct.unpack_from("<Qddd", data, offset)
        offset += 8 + 24 + 3 + 8
        (length,) = struct.unpack_from("<Q", data, offset)
        offset += 8
        tracks.append(np.frombuffer(data, dtype="<u4", count=2 * length, offset=offset).reshape(-1, 2))
        offset += 8 * length
        xyz[index] = (x, y, z)
    return xyz, tracks


def write_points3d(path, xyz, rgb, tracks):
    chunks = [struct.pack("<Q", len(xyz))]
    for index, (point, color, track) in enumerate(zip(xyz, rgb, tracks), start=1):
        chunks.append(struct.pack("<Qddd3BdQ", index, *point, *color, 0.0, len(track)))
        chunks.append(track.astype("<u4").tobytes())
    path.write_bytes(b"".join(chunks))


def read_fused_ply(path):
    vertex = PlyData.read(str(path))["vertex"].data
    xyz = np.stack([vertex["x"], vertex["y"], vertex["z"]], axis=1).astype(np.float64)
    rgb = np.stack([vertex["red"], vertex["green"], vertex["blue"]], axis=1).astype(np.uint8)
    return xyz, rgb


def run_colmap(colmap, *args):
    subprocess.run([colmap, *map(str, args)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--colmap", required=True, help="Binaire COLMAP avec CUDA pour PatchMatch")
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--sparse", required=True, type=Path, help="Dossier sparse/0 du SfM")
    parser.add_argument("--output", required=True, type=Path, help="Dossier de sortie mvs/")
    parser.add_argument("--max-image-size", required=True, type=int)
    parser.add_argument("--gpu-index", required=True, type=int)
    args = parser.parse_args()

    dense = args.output / "dense"
    model = args.output / "sparse" / "0"
    if dense.exists():
        shutil.rmtree(dense)
    dense.mkdir(parents=True)
    run_colmap(args.colmap, "image_undistorter", "--image_path", args.images,
               "--input_path", args.sparse, "--output_path", dense,
               "--output_type", "COLMAP", "--max_image_size", args.max_image_size)
    run_colmap(args.colmap, "patch_match_stereo", "--workspace_path", dense,
               "--workspace_format", "COLMAP", "--PatchMatchStereo.geom_consistency", "true",
               "--PatchMatchStereo.gpu_index", args.gpu_index)
    fused = dense / "fused.ply"
    run_colmap(args.colmap, "stereo_fusion", "--workspace_path", dense,
               "--workspace_format", "COLMAP", "--input_type", "geometric",
               "--output_path", fused)

    sparse_xyz, sparse_tracks = read_points3d(args.sparse / "points3D.bin")
    dense_xyz, dense_rgb = read_fused_ply(fused)
    if len(dense_xyz) == 0:
        raise SystemExit("[ERREUR] MVS : aucun point dense produit.")
    _, nearest = cKDTree(sparse_xyz).query(dense_xyz)
    tracks = [sparse_tracks[index] for index in nearest]

    model.mkdir(parents=True, exist_ok=True)
    for name in ("cameras.bin", "images.bin"):
        shutil.copy2(args.sparse / name, model / name)
    write_points3d(model / "points3D.bin", dense_xyz, dense_rgb, tracks)
    print(f"[MVS] {len(sparse_xyz)} points épars -> {len(dense_xyz)} points denses : {model}")


if __name__ == "__main__":
    main()
