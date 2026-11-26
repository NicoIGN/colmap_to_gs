#!/usr/bin/env python3
"""Single entry point for every mask source used by the Gaussian Splatting pipelines.

Each policy (SFM_MASKS for feature matching, TRAIN_MASKS for training) comes from one of:
- input-dir: {PREFIX}_MASKS_DIR holds ready masks; they are validated, copied and signed, never regenerated.
- spirula: SAM through the native Spirula backend ({PREFIX}_BACKEND=spirula).
- segmentation: Transformers segmenter ({PREFIX}_BACKEND=segmentation).

Generated masks are produced by backends/generate_masks.py. Policy, resume, signatures, SfM/train
sharing and validation all live here, so pipelines only call this CLI.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

BACKENDS = Path(__file__).resolve().parent / "backends"
GENERATOR = BACKENDS / "generate_masks.py"
sys.path.insert(0, str(BACKENDS))

from generate_masks import progress  # noqa: E402
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


class MissingMaskError(ValueError):
    pass


def enabled(value):
    return value in ("on", "true", "1", "yes")


def read_report(path):
    """Return the parsed JSON object at path, or None when the file is absent."""
    if not path.is_file():
        return None
    report = json.loads(path.read_text())
    if not isinstance(report, dict):
        raise ValueError(f"Rapport invalide : {path}")
    return report


def write_json(path, data):
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def without_device(settings):
    # The device selects where SAM runs, not which masks are produced.
    if not settings:
        return settings
    return {key: value for key, value in settings.items() if key != "device"}


def policy(prefix):
    env = os.environ
    labels = sorted({" ".join(label.lower().split())
                     for label in env[f"{prefix}_LABELS"].split(",") if label.strip()})
    if not labels:
        raise ValueError(f"{prefix}_LABELS doit contenir au moins un label")
    backend = env[f"{prefix}_BACKEND"]
    if backend not in ("spirula", "segmentation"):
        raise ValueError(f"{prefix}_BACKEND doit être spirula ou segmentation")
    native = backend == "spirula"
    model = env[f"{prefix}_SPIRULA_MODEL" if native else f"{prefix}_MODEL"]
    if not model:
        raise ValueError(f"Modèle {prefix} non défini")
    path = Path(model).expanduser().resolve()
    if not (path.is_file() if native else path.is_dir()):
        raise ValueError(f"Modèle {prefix} introuvable : {path}")
    dilate = env[f"{prefix}_DILATE"]
    if dilate == "auto":
        mode = env.get("TRAIN_MASKS_MODE", "auto") if prefix == "TRAIN_MASKS" else "ignore"
        if mode == "auto":
            mode = "empty" if labels == ["sky"] else "ignore"
        dilate = "0" if mode == "empty" else "0.002"
    settings = {
        "backend": backend, "model": str(path), "labels": labels,
        "confidence": float(env[f"{prefix}_CONFIDENCE"]),
        "jobs": int(env["NUM_CPUS"]),
        "dilate": float(dilate), "max_fraction": float(env[f"{prefix}_MAX_FRACTION"]),
        "device": env["SPIRULA_DEVICE"] if native else env[f"{prefix}_DEVICE"],
        "python": env["PYTHON_BIN"] if native else env[f"{prefix}_PYTHON"],
    }
    if not (0 <= settings["confidence"] <= 1 and
            0 <= settings["dilate"] < 0.1 and
            0 < settings["max_fraction"] <= 1 and
            all(math.isfinite(settings[key])
                for key in ("confidence", "dilate", "max_fraction"))):
        raise ValueError(f"Seuils {prefix} invalides")
    return settings


def policies():
    result = {}
    for prefix in ("SFM_MASKS", "TRAIN_MASKS"):
        if os.environ.get(prefix, "off") != "on":
            result[prefix] = None
            continue
        directory = os.environ.get(f"{prefix}_DIR", "")
        result[prefix] = (
            {"external_dir": str(Path(directory).expanduser().resolve())}
            if directory else policy(prefix)
        )
    return result


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


def validate_masks(images, output, *, allow_missing=False):
    from PIL import Image

    sources = sorted(
        path for path in images.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not sources:
        raise ValueError(f"Aucune image sélectionnée : {images}")
    if not output.is_dir():
        raise ValueError(
            f"Dossier de masques introuvable : {output}. "
            "Vérifier SFM_MASKS_DIR / TRAIN_MASKS_DIR dans la config (ou SFM_MASKS / TRAIN_MASKS = off)."
        )
    missing = []
    progress(0, len(sources), None, "Vérification des masques")
    for index, source in enumerate(sources, 1):
        progress(index, len(sources), None, "Vérification des masques")
        mask_path = output / (source.relative_to(images).as_posix() + ".png")
        if not mask_path.is_file():
            if allow_missing:
                missing.append(mask_path)
                continue
            raise MissingMaskError(
                f"Masque attendu absent : {mask_path} (image {source.relative_to(images).as_posix()} "
                f"non couverte par {output})"
            )
        with Image.open(source) as image, Image.open(mask_path) as mask:
            mask.load()
            if mask.format != "PNG" or mask.mode != "L" or mask.size != image.size:
                raise ValueError(
                    f"Masque invalide (PNG monochrome aux dimensions de l'image requis) : {mask_path}"
                )
            histogram = mask.histogram()
            if sum(histogram[1:255]):
                raise ValueError(f"Masque non binaire (0/255 requis) : {mask_path}")
    print(f"[reprise] {len(sources)-len(missing)} masques vérifiés dans {output}.", flush=True)
    return missing


def generate(settings, images, output):
    command = [
        settings["python"], str(GENERATOR),
        "--backend", settings["backend"], "--model", settings["model"],
        "--images", str(images), "--output", str(output),
        "--labels", ",".join(settings["labels"]),
        "--confidence", str(settings["confidence"]),
        "--jobs", str(settings["jobs"]),
        "--dilate-fraction", f"{settings['dilate']:g}",
        "--max-masked-fraction", str(settings["max_fraction"]),
        "--spirula-bin", os.environ["SPIRULA_BIN"],
        "--spirula-device", settings["device"] if settings["backend"] == "spirula" else "auto",
        "--device", settings["device"] if settings["backend"] != "spirula" else "auto",
    ]
    if settings["backend"] == "spirula" and enabled(os.environ.get("MASKS_ACCEPT_LICENSE", "false")):
        command.append("--accept-spirula-license")
    print(f"[masques] {output.name} : {settings['backend']}, {settings['labels']}", flush=True)
    subprocess.run(command, check=True)
    report = json.loads((output / "masks.json").read_text())
    if report.get("status") != "complete":
        raise ValueError(f"Masques incomplets : {output}")
    print(f"[masques] {output.name} : calcul terminé", flush=True)


def sfm_signature(output, sfm, perspective):
    # A completed SfM is only valid for the SfM mask policy and the exact inputs it was built from.
    parts = [without_device(sfm), input_digest(output / "images")]
    if perspective:
        parts.append(hashlib.sha256((output / "projection.json").read_bytes()).hexdigest())
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def guard_sfm(output, signature):
    workspace = output / "sfm"
    marker = workspace / "sfm_mask_policy.json"
    previous = read_report(marker) or {}
    artifacts = any((workspace / name).exists() for name in ("sparse", "features", "matches.bin"))
    artifacts = artifacts or (output / "blocks").exists()
    if (artifacts or previous) and previous.get("signature") != signature:
        raise ValueError(
            "SfM existant/partiel avec une autre politique de masques (ou sans signature). "
            f"Déplacer {workspace} en sauvegarde ou choisir un nouvel OUTPUT_DIR. "
            "TRAIN_CLEAN ne recalcule pas le SfM."
        )
    return marker


def record_sfm(marker, signature, sfm):
    marker.parent.mkdir(parents=True, exist_ok=True)
    write_json(marker, {"signature": signature, "settings": sfm, "status": "complete"})


def prepare_policy(settings, images, directory):
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
            ) if (images.parent / name).exists()]
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
        return
    if directory.is_dir():
        report = verify_report(strict=False)
        if report and report.get("status") == "complete":
            validate_masks(images, directory)
            print(f"[masques] Masques complets réutilisés : {directory}", flush=True)
            return
    generate(settings, images, directory)
    record_inputs(directory, digest)


def prepare(policy_only=None, perspective=False):
    settings = policies()
    output = Path(os.environ["OUTPUT_DIR"])
    images = output / "images"
    sfm, train = settings["SFM_MASKS"], settings["TRAIN_MASKS"]
    signature = sfm_signature(output, sfm, perspective) if sfm is not None else None
    marker = guard_sfm(output, signature) if signature is not None else None

    if policy_only is not None:
        prefix, directory = {
            "sfm": ("SFM_MASKS", "feature_masks"),
            "train": ("TRAIN_MASKS", "train_masks"),
        }[policy_only]
        selected = settings[prefix]
        if selected is None:
            raise ValueError(f"{prefix} est désactivé")
        prepare_policy(selected, images, output / directory)
        if policy_only == "sfm":
            record_sfm(marker, signature, sfm)
        return

    if sfm is not None and sfm == train:
        train_dir, feature_dir = output / "train_masks", output / "feature_masks"
        prepare_policy(sfm, images, train_dir)
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
                prepare_policy(settings[prefix], images, output / directory)
    if marker is not None:
        record_sfm(marker, signature, sfm)


def check(output, settings, perspective):
    # Validate the signed state written by prepare() without generating anything.
    sfm = settings["SFM_MASKS"]
    if sfm is not None:
        marker = output / "sfm" / "sfm_mask_policy.json"
        if (read_report(marker) or {}).get("signature") != sfm_signature(output, sfm, perspective):
            raise ValueError("Politique de masques SfM non préparée ou modifiée : relancer la préparation")
    for prefix, directory in (("SFM_MASKS", "feature_masks"), ("TRAIN_MASKS", "train_masks")):
        if settings[prefix] is None:
            continue
        report = read_report(output / directory / "masks.json") or {}
        if report.get("status") != "complete":
            raise ValueError(f"Masques incomplets : {output / directory}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", choices=("sfm", "train"),
                        help="Générer seulement la politique demandée (jobs Slurm parallèles)")
    parser.add_argument("--policies-equal", action="store_true",
                        help="Indiquer si les politiques SfM et train sont identiques")
    parser.add_argument("--check", action="store_true",
                        help="Valider la préparation existante sans générer de masques")
    parser.add_argument("--perspective", action="store_true",
                        help="Les images sont des vues projetées (projection.json entre dans la signature SfM)")
    args = parser.parse_args()
    try:
        if args.policies_equal:
            current = policies()
            print(str(current["SFM_MASKS"] == current["TRAIN_MASKS"]).lower())
        elif args.check:
            check(Path(os.environ["OUTPUT_DIR"]), policies(), args.perspective)
        else:
            prepare(args.policy, perspective=args.perspective)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"[ERREUR] Préparation des masques : {error}")


if __name__ == "__main__":
    main()
