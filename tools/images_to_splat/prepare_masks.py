#!/usr/bin/env python3
"""Prepare local-image mask directories using shared segmentation policies."""

import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from mask_policies import generate, policy
from resume import validate_masks


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

    def prepare_policy(settings, directory):
        if os.environ.get("PIPELINE_RESUME") == "true" and directory.is_dir():
            report_path = directory / "masks.json"
            if report_path.is_file():
                report = json.loads(report_path.read_text())
                if not isinstance(report, dict) or not isinstance(report.get("settings"), dict):
                    raise ValueError(f"Rapport de masques invalide : {report_path}")
                recorded = report["settings"]
                for key, value in (
                    ("backend", settings["backend"]),
                    ("model", settings["model"]),
                    ("confidence", settings["confidence"]),
                    ("dilate_fraction", settings["dilate"]),
                    ("max_masked_fraction", settings["max_fraction"]),
                ):
                    if key in recorded and recorded[key] != value:
                        raise ValueError(f"Politique de masques modifiée ({key}) : {directory}")
                if "labels" in recorded:
                    labels = recorded["labels"]
                    if isinstance(labels, str):
                        labels = labels.split(",")
                    if sorted(labels) != settings["labels"]:
                        raise ValueError(f"Labels de masques modifiés : {directory}")
            validate_masks(images, directory)
            if not report_path.is_file():
                report = {
                    "status": "complete",
                    "settings": {
                        "source": "verified_existing_masks",
                        "provenance": "unknown",
                        "requested_policy": settings,
                    },
                    "validation": "PNG monochrome 0/255, dimensions et couverture vérifiées",
                }
                temporary = directory / ".masks.resume.json.tmp"
                temporary.write_text(json.dumps(report, indent=2) + "\n")
                temporary.replace(report_path)
            print(
                f"[AVERTISSEMENT] Reprise des masques existants : {directory}. "
                "Leur politique d'origine doit être conservée.",
                flush=True,
            )
        else:
            generate(settings, images, directory)

    if sfm is not None and sfm == train:
        prepare_policy(sfm, output / "masks")
        print("[masques] Politiques SfM/train identiques : calcul partagé.", flush=True)
    else:
        if (os.environ.get("PIPELINE_RESUME") == "true"
                and (output / "masks").exists()):
            raise ValueError(
                "Masques partagés préexistants mais politiques SfM/train non partagées : "
                "conserver la configuration d'origine ou choisir un nouvel OUTPUT_DIR."
            )
        for prefix, directory in (("SFM_MASKS", "feature_masks"), ("TRAIN_MASKS", "train_masks")):
            if settings[prefix] is not None:
                prepare_policy(settings[prefix], output / directory)


if __name__ == "__main__":
    try:
        prepare()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"[ERREUR] Préparation des masques : {error}")
