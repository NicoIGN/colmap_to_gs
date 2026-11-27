#!/usr/bin/env python3
"""Compile and smoke-test the CleanGS CUDA renderer on the current GPU."""

import shutil


def main():
    if shutil.which("nvcc") is None:
        raise RuntimeError("nvcc requis : charger le module CUDA 12.8 du cluster")
    import torch
    from gsplat import rasterization

    if not torch.cuda.is_available():
        raise RuntimeError("GPU CUDA indisponible : lancer dans une allocation GPU")
    device = "cuda"
    with torch.no_grad():
        rendered, _, _ = rasterization(
            means=torch.tensor([[0., 0., 2.]], device=device),
            quats=torch.tensor([[1., 0., 0., 0.]], device=device),
            scales=torch.full((1, 3), 0.1, device=device),
            opacities=torch.ones(1, device=device),
            colors=torch.ones((1, 3), device=device),
            viewmats=torch.eye(4, device=device)[None],
            Ks=torch.tensor([[[32., 0., 16.], [0., 32., 16.], [0., 0., 1.]]], device=device),
            width=32, height=32, packed=False,
        )
        torch.cuda.synchronize()
        if not torch.isfinite(rendered).all() or rendered.max().item() <= 0:
            raise RuntimeError("Rendu gsplat invalide")
    print(f"[clean-gs] Compilation et rendu CUDA validés : {torch.cuda.get_device_name()}")


if __name__ == "__main__":
    main()
