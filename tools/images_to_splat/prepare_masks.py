#!/usr/bin/env python3
"""Prepare local-image mask directories using shared segmentation policies."""

import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from mask_policies import generate, policy


def policies():
    return {
        prefix: policy(prefix) if os.environ[prefix] == "on" else None
        for prefix in ("SFM_MASKS", "TRAIN_MASKS")
    }


def prepare():
    settings = policies()
    output = Path(os.environ["OUTPUT_DIR"])
    images = output / "images"
    sfm, train = settings["SFM_MASKS"], settings["TRAIN_MASKS"]
    if sfm is not None and sfm == train:
        generate(sfm, images, output / "masks")
        print("[masques] Politiques SfM/train identiques : calcul partagé.", flush=True)
    else:
        for prefix, directory in (("SFM_MASKS", "feature_masks"), ("TRAIN_MASKS", "train_masks")):
            if settings[prefix] is not None:
                generate(settings[prefix], images, output / directory)


if __name__ == "__main__":
    try:
        prepare()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"[ERREUR] Préparation des masques : {error}")
