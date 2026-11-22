#!/usr/bin/env python3
"""Prepare local-image mask directories using shared segmentation policies."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/libs/python"))
from mask_policies import generate, policy
from mask_preparation import read_report, write_json
from resume import MissingMaskError, validate_masks


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def input_digest(images):
    # Masks are only valid for the exact photos they were generated from.
    digest = hashlib.sha256()
    for path in sorted(images.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            digest.update(path.relative_to(images).as_posix().encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def record_inputs(directory, digest):
    report = read_report(directory / "masks.json") or {}
    report["input_digest"] = digest
    write_json(directory / "masks.json", report)


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
            report = read_report(source / "masks.json")
            if report is not None and report.get("status") != "complete":
                raise ValueError(f"Rapport de masques externe incomplet : {source / 'masks.json'}")
            validate_masks(images, source)
            if directory.is_symlink():
                if directory.resolve() != source:
                    raise ValueError(
                        f"Lien de masques vers un autre dossier : {directory}. Le sauvegarder avant de continuer."
                    )
                directory.unlink()
            if directory.exists():
                existing = read_report(directory / "masks.json") or {}
                if (existing.get("settings") or {}).get("external_dir") != str(source):
                    raise ValueError(
                        "Le dossier de sortie existe déjà et ne provient pas des masques externes : "
                        f"{directory}. Le sauvegarder avant de continuer."
                    )
                validate_masks(images, directory)
                print(f"[masques] Masques externes déjà copiés et signés : {directory}", flush=True)
                return
            shutil.copytree(source, directory)
            write_json(directory / "masks.json", {
                "status": "complete",
                "settings": {"source": "external", "external_dir": str(source), "requested_policy": settings},
                "validation": "PNG monochrome 0/255, dimensions et couverture vérifiées",
            })
            validate_masks(images, directory)
            print(f"[masques] Masques externes copiés et signés (sans génération) : {source}", flush=True)
            return

        report_path = directory / "masks.json"
        digest = input_digest(images)
        recorded_digest = (read_report(report_path) or {}).get("input_digest")
        if directory.is_dir() and any(directory.iterdir()) and recorded_digest not in (None, digest):
            raise ValueError(
                f"Images modifiées depuis la génération des masques : {directory}. "
                "Sauvegarder ce dossier ou choisir un nouvel OUTPUT_DIR."
            )

        def verify_report(strict):
            report = read_report(report_path)
            if report is None:
                return None
            if not isinstance(report.get("settings"), dict):
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
            report = verify_report(strict=True)
            try:
                validate_masks(images, directory)
            except MissingMaskError:
                empty = report is None and not any(directory.iterdir())
                partial = (report is not None and report.get("status") == "partial"
                           and isinstance(report.get("images"), dict))
                if not (empty or partial):
                    raise
                if directory.is_symlink():
                    raise ValueError(f"Reprise de génération refusée sur un lien externe : {directory}")
                downstream = [name for name in (
                    "sfm", "georef", "colmap_lidar", "sparse_filtered", "sparse_clean",
                    "sparse_rejected", "blocks", "gs", "gs-nerfstudio", ".prepare_done",
                ) if (output / name).exists()]
                if downstream:
                    raise ValueError(
                        "Masques incomplets déjà susceptibles d'avoir été utilisés : "
                        f"{', '.join(downstream)}. Choisir un nouvel OUTPUT_DIR."
                    )
                # Validate every available PNG, not only the prefix before the first hole.
                missing = validate_masks(images, directory, allow_missing=True)
                print(
                    f"[masques] Génération interrompue : {len(missing)} masque(s) manquant(s) "
                    f"dans {directory} ; reprise avec la même politique.",
                    flush=True,
                )
                generate(settings, images, directory)
                record_inputs(directory, digest)
                validate_masks(images, directory)
                return
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
                write_json(report_path, report)
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
            record_inputs(directory, digest)

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

    legacy = output / "masks"
    if legacy.exists():
        raise ValueError(
            f"Ancien dossier {legacy} : la disposition est désormais feature_masks/ et train_masks/. "
            "Le déplacer en sauvegarde ou choisir un nouvel OUTPUT_DIR."
        )
    if sfm is not None and sfm == train:
        train_dir, feature_dir = output / "train_masks", output / "feature_masks"
        prepare_policy(sfm, train_dir)
        if feature_dir.exists() and not feature_dir.is_symlink():
            raise ValueError(f"{feature_dir} est un dossier réel : choisir un nouveau workspace pour partager les masques")
        if feature_dir.is_symlink():
            feature_dir.unlink()
        feature_dir.symlink_to(train_dir, target_is_directory=True)
        print("[masques] Politiques SfM/train identiques : calcul partagé "
              f"({feature_dir} -> {train_dir}).", flush=True)
    else:
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
