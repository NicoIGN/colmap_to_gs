#!/usr/bin/env python3
"""Prepare local-image mask directories using shared segmentation policies."""

import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/libs/python"))
from mask_policies import generate, policy
from resume import validate_masks


def policies():
    result = {}
    for prefix in ("SFM_MASKS", "TRAIN_MASKS"):
        if os.environ[prefix] != "on":
            result[prefix] = None
            continue
        directory = os.environ.get(f"{prefix}_DIR", "")
        result[prefix] = (
            {"external_dir": str(Path(directory).expanduser().resolve())}
            if directory else policy(prefix)
        )
    return result


def prepare(policy_only=None):
    settings = policies()
    output = Path(os.environ["OUTPUT_DIR"])
    images = output / "images"
    sfm, train = settings["SFM_MASKS"], settings["TRAIN_MASKS"]

    def prepare_policy(settings, directory):
        external = settings.get("external_dir")
        if external:
            source = Path(external)
            report_path = source / "masks.json"
            if report_path.is_file():
                report = json.loads(report_path.read_text())
                if not isinstance(report, dict) or report.get("status") != "complete":
                    raise ValueError(f"Rapport de masques externe incomplet : {report_path}")
            validate_masks(images, source)
            if directory.exists() or directory.is_symlink():
                if directory.resolve() != source:
                    raise ValueError(
                        f"Le dossier de sortie existe déjà et ne pointe pas vers "
                        f"les masques externes : {directory}. Le sauvegarder avant de continuer."
                    )
            else:
                directory.symlink_to(source, target_is_directory=True)
            print(f"[masques] Réutilisation des masques externes : {source}", flush=True)
            return

        report_path = directory / "masks.json"

        def verify_report(strict):
            if not report_path.is_file():
                return False
            report = json.loads(report_path.read_text())
            if not isinstance(report, dict) or not isinstance(report.get("settings"), dict):
                raise ValueError(f"Rapport de masques invalide : {report_path}")
            recorded = report["settings"]

            def incompatible(message):
                if strict:
                    raise ValueError(f"{message} : {directory}")
                return None

            for key, value in (
                ("backend", settings["backend"]),
                ("model", settings["model"]),
                ("confidence", settings["confidence"]),
                ("dilate_fraction", settings["dilate"]),
                ("max_masked_fraction", settings["max_fraction"]),
            ):
                if key in recorded and recorded[key] != value:
                    return incompatible(f"Politique de masques modifiée ({key})")
            if "labels" in recorded:
                labels = recorded["labels"]
                if isinstance(labels, str):
                    labels = labels.split(",")
                if sorted(labels) != settings["labels"]:
                    return incompatible("Labels de masques modifiés")
            return report

        if os.environ.get("PIPELINE_RESUME") == "true" and directory.is_dir():
            verify_report(strict=True)
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
            if directory.is_dir():
                report = verify_report(strict=False)
                if report and report.get("status") == "complete":
                    validate_masks(images, directory)
                    print(
                        f"[masques] Masques complets réutilisés : {directory}",
                        flush=True,
                    )
                    return
            generate(settings, images, directory)

    if policy_only is not None:
        prefix, directory = {
            "sfm": ("SFM_MASKS", "feature_masks"),
            "train": ("TRAIN_MASKS", "train_masks"),
        }[policy_only]
        selected = settings[prefix]
        if selected is None:
            raise ValueError(f"{prefix} est désactivé")
        prepare_policy(selected, output / directory)
        return

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
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=("sfm", "train"),
                        help="Générer seulement la politique demandée (jobs Slurm parallèles)")
    parser.add_argument("--policies-equal", action="store_true",
                        help="Indiquer si les politiques SfM et train sont identiques")
    args = parser.parse_args()
    try:
        if args.policies_equal:
            current = policies()
            print(str(current["SFM_MASKS"] == current["TRAIN_MASKS"]).lower())
        else:
            prepare(args.policy)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"[ERREUR] Préparation des masques : {error}")
