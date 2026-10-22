#!/usr/bin/env python3
import argparse
from pathlib import Path

import numpy as np
import torch
from plyfile import PlyData
from gsplat import export_splats


def load_ply_to_splats(path):
    """Lit un PLY 3DGS standard INRIA, non compressé, sur CPU."""
    ply = PlyData.read(str(path))
    vertex = ply["vertex"].data
    names = set(vertex.dtype.names or ())

    required = (
        ["x", "y", "z", "opacity"]
        + [f"scale_{i}" for i in range(3)]
        + [f"rot_{i}" for i in range(4)]
        + [f"f_dc_{i}" for i in range(3)]
    )
    missing = sorted(set(required) - names)
    if missing:
        raise ValueError(
            "Ce fichier n'est pas un PLY 3DGS standard non compressé. "
            f"Propriétés manquantes : {', '.join(missing)}"
        )

    n = len(vertex)
    if n == 0:
        raise ValueError("Le fichier ne contient aucun Gaussian Splat.")

    def columns(fields):
        array = np.stack([vertex[field] for field in fields], axis=1)
        return torch.from_numpy(
            np.array(array, dtype=np.float32, order="C", copy=True)
        )

    rest_names = sorted(
        (name for name in names if name.startswith("f_rest_")),
        key=lambda name: int(name.rsplit("_", 1)[1]),
    )

    if rest_names:
        expected = [f"f_rest_{i}" for i in range(len(rest_names))]
        if rest_names != expected or len(rest_names) not in (9, 24, 45):
            raise ValueError(
                "Coefficients f_rest_* non conformes aux degrés SH 1, 2 ou 3."
            )

        # PLY INRIA: tous les coeffs R, puis G, puis B.
        # export_splats attend (N, K, 3).
        shN = (
            columns(rest_names)
            .reshape(n, 3, -1)
            .transpose(1, 2)
            .contiguous()
        )
    else:
        shN = torch.empty((n, 0, 3), dtype=torch.float32)

    return {
        "means": columns(["x", "y", "z"]),
        "scales": columns([f"scale_{i}" for i in range(3)]),      # log-scales
        "quats": columns([f"rot_{i}" for i in range(4)]),
        "opacities": columns(["opacity"]).squeeze(1),             # logits
        "sh0": columns([f"f_dc_{i}" for i in range(3)]).unsqueeze(1),
        "shN": shN,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Convertit un PLY 3DGS vers .compressed.ply ou .splat via gsplat"
    )
    parser.add_argument("input", help="Fichier d'entrée .ply")
    parser.add_argument(
        "output",
        nargs="?",
        help="Sortie (défaut: .compressed.ply ou .splat selon --format)",
    )
    parser.add_argument(
        "--format",
        choices=["ply_compressed", "splat"],
        default="ply_compressed",
        help="Format de sortie (défaut: ply_compressed)",
    )
    args = parser.parse_args()

    inp = Path(args.input).expanduser().resolve()
    if not inp.is_file():
        raise FileNotFoundError(f"Fichier introuvable: {inp}")

    if args.output:
        out = Path(args.output).expanduser().resolve()
    else:
        out = inp.with_suffix(".splat") if args.format == "splat" else inp.with_name(f"{inp.stem}.compressed.ply")

    if out == inp:
        raise ValueError("Le fichier de sortie doit être différent de l'entrée.")

    out.parent.mkdir(parents=True, exist_ok=True)

    splats = load_ply_to_splats(inp)
    export_splats(
        means=splats["means"],
        scales=splats["scales"],
        quats=splats["quats"],
        opacities=splats["opacities"],
        sh0=splats["sh0"],
        shN=splats["shN"],
        format=args.format,
        save_to=str(out),
    )

    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("Conversion terminée mais fichier de sortie vide/inexistant.")

    in_mb = inp.stat().st_size / (1024 * 1024)
    out_mb = out.stat().st_size / (1024 * 1024)
    ratio = (out.stat().st_size / inp.stat().st_size) if inp.stat().st_size else 0.0
    print(f"✅ OK: {out}")
    print(f"Taille: {in_mb:.2f} MiB -> {out_mb:.2f} MiB (ratio {ratio:.3f})")


if __name__ == "__main__":
    main()
