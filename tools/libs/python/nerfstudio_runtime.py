#!/usr/bin/env python3
"""Fail explicitly unless the selected interpreter can use an NVIDIA CUDA GPU."""

import sys


def check_runtime():
    try:
        import torch
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Nerfstudio exige PyTorch dans cet environnement Python."
        ) from exc
    if not torch.version.cuda or not torch.cuda.is_available():
        raise RuntimeError(
            "Nerfstudio exige une carte NVIDIA utilisable par PyTorch/CUDA ; "
            "CPU et Apple Metal ne sont pas pris en charge par ce pipeline."
        )
    try:
        device = torch.device("cuda:0")
        torch.ones(1, device=device).add_(1)
        torch.cuda.synchronize()
    except RuntimeError as exc:
        raise RuntimeError(f"GPU NVIDIA présent mais CUDA inutilisable : {exc}") from exc
    print(f"[Nerfstudio] PyTorch {torch.__version__}, GPU {torch.cuda.get_device_name(0)}")


if __name__ == "__main__":
    try:
        check_runtime()
    except RuntimeError as error:
        sys.exit(f"[ERREUR] {error}")
