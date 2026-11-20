#!/usr/bin/env python3
"""Run native Spirula stages with an external GPS match filter before mapping."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys

from dataset_layout import image_files, metadata_index
from filter_matches_gps import filter_matches
from prepare_sfm import main as prepare_images


def signature(args, images, metadata):
    files = [
        (str(p.relative_to(args.dataset)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in images
    ]
    metadata_hashes = [
        (str(p.relative_to(args.dataset)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(metadata.values())
    ]
    settings = {key: str(value) for key, value in vars(args).items()
                if key != "feature_masks" or value is not None}
    masks = []
    if args.feature_masks:
        if not args.feature_masks.is_dir():
            raise ValueError(f"Dossier de masques SfM introuvable : {args.feature_masks}")
        masks = [(str(p.relative_to(args.feature_masks)), hashlib.sha256(p.read_bytes()).hexdigest())
                 for p in sorted(args.feature_masks.rglob("*"))
                 if p.is_file() and p.name != "masks.json"]
        report = args.feature_masks / "masks.json"
        if report.is_file():
            masks.append(("settings", json.loads(report.read_text())["settings"]))
    fingerprint = [settings, files, metadata_hashes]
    if args.feature_masks:
        fingerprint.append(masks)
    return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()


def run(args):
    if not math.isfinite(args.max_distance) or args.max_distance <= 0:
        raise ValueError("SFM_SP_PAIR_MAX_DISTANCE doit être fini et strictement positif")
    if args.threads < 0 or args.max_image_size <= 0 or args.aliked_max_features <= 0:
        raise ValueError("Résolution et budget de points doivent être positifs ; fils CPU >= 0")
    if (not 4 <= args.min_inliers <= 1000000 or
            not all(math.isfinite(x) and x > 0 for x in
                    (args.max_error, args.min_tri_angle, args.ba_loss_param)) or
            not 0.1 <= args.max_error <= 100 or
            args.min_tri_angle > 90 or args.ba_loss_param > 1000):
        raise ValueError("Seuils géométriques invalides : erreur [0.1,100], "
                         "inliers [4,1000000], angle ]0,90], échelle ]0,1000]")
    args.dataset = args.dataset.resolve()
    args.output = args.output.resolve()
    marker = args.output / "gps_filter_run.json"
    images = [p for p in image_files(args.dataset) if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    metadata = metadata_index(args.dataset)
    if not images or not metadata:
        raise ValueError("Le SfM filtré nécessite un dataset Panoramax avec images/ et metadata/")
    fingerprint = signature(args, images, metadata)
    sparse = args.output / "sparse" / "0"
    completed = all((sparse / name).is_file() and (sparse / name).stat().st_size
                    for name in ("cameras.bin", "images.bin", "points3D.bin"))
    previous = json.loads(marker.read_text()) if marker.is_file() else {}
    if not isinstance(previous, dict):
        raise ValueError(f"Signature SfM invalide : {marker}")
    same_settings = previous.get("signature") == fingerprint
    if same_settings and previous.get("complete") and completed:
        print(f"[SFM GPS] Modèle filtré existant réutilisé : {sparse}")
        return 0
    # Never train from an old model or mix a new map with an old partition.
    if ((args.output / "sparse").exists() or any(args.output.glob("blocks-*")) or
            (not same_settings and any((args.output / name).exists()
                                      for name in ("features", "matches.bin")))):
        raise ValueError(
            f"SfM existant ou partiel sans signature filtrée identique : {args.output}. "
            "Déplacer ce workspace en sauvegarde avant de recalculer ; garder les images téléchargées."
        )
    prepare_images(["--dataset", str(args.dataset), "--output-dir", str(args.output)])
    marker.write_text(json.dumps(dict(signature=fingerprint, complete=False), indent=2) + "\n")
    selection = json.loads((args.output / "selection.json").read_text())
    if args.camera_model is None:
        args.camera_model = selection.get("camera_model") or "opencv"
    images = args.output / "images"
    features = args.output / "features"
    original = args.output / "matches.bin"
    filtered = args.output / "matches-gps.bin"
    common = ["--threads", str(args.threads)] if args.threads else []
    extract = [
        args.spirula_bin, "sfm", "extract", str(images), "--output", str(features),
        "--features", args.features, "--max-image-size", str(args.max_image_size),
        "--aliked-max-features", str(args.aliked_max_features),
    ]
    if args.threads:
        extract += ["--decode-threads", str(args.threads)]
    if args.feature_masks:
        extract += ["--feature-masks", str(args.feature_masks.resolve())]
    match = [
        args.spirula_bin, "sfm", "match", str(features), "--output", str(original),
        "--pairs", ("prefilter" if selection["selected"] >= 100 else "exhaustive")
        if args.pairs == "auto" else args.pairs,
        "--matcher", args.matcher, "--camera-mode", "folder", "--camera-model", args.camera_model,
        "--max-error", str(args.max_error), "--min-inliers", str(args.min_inliers),
        *common,
    ]
    # Une focale déduite du champ de vue n'a pas de sens pour une équirectangulaire.
    if selection["focal_px"] and args.camera_model != "equirectangular":
        match += ["--focal", str(selection["focal_px"])]
    mapping = [
        args.spirula_bin, "sfm", "map", str(filtered), str(features),
        "--output", str(args.output / "sparse"), "--image-dir", str(images),
        "--metric-gps", "full", *common,
        "--max-error", str(args.max_error), "--min-tri-angle", str(args.min_tri_angle),
        "--ba-loss", args.ba_loss, "--ba-loss-param", str(args.ba_loss_param),
    ]
    for stage, command in (("extraction", extract), ("appariement", match)):
        print(f"[SFM GPS] {stage} : {shlex.join(command)}", flush=True)
        subprocess.run(command, check=True)
    counts = filter_matches(original, filtered, metadata, args.max_distance)
    (args.output / "gps_filter_summary.json").write_text(json.dumps(counts, indent=2) + "\n")
    if not counts["kept_pairs"]:
        raise ValueError("Aucune paire après filtrage GPS ; augmenter SFM_SP_PAIR_MAX_DISTANCE")
    print(f"[SFM GPS] reconstruction : {shlex.join(mapping)}", flush=True)
    subprocess.run(mapping, check=True)
    if not all((sparse / name).is_file() and (sparse / name).stat().st_size
               for name in ("cameras.bin", "images.bin", "points3D.bin")):
        raise ValueError(f"Modèle COLMAP absent ou incomplet après reconstruction : {sparse}")
    marker.write_text(json.dumps(dict(signature=fingerprint, complete=True), indent=2) + "\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--spirula-bin", required=True)
    parser.add_argument("--max-distance", required=True, type=float)
    parser.add_argument("--features", default="aliked-n16rot")
    parser.add_argument("--matcher", default="lightglue")
    parser.add_argument("--pairs", choices=("auto", "prefilter", "exhaustive", "sequential"), default="prefilter")
    parser.add_argument("--max-image-size", type=int, default=4096)
    parser.add_argument("--aliked-max-features", type=int, default=4096)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--max-error", type=float, default=2)
    parser.add_argument("--min-inliers", type=int, default=30)
    parser.add_argument("--min-tri-angle", type=float, default=6)
    parser.add_argument("--ba-loss", choices=("huber", "cauchy", "trivial"), default="huber")
    parser.add_argument("--ba-loss-param", type=float, default=2)
    parser.add_argument("--camera-model", default=None,
                        choices=("opencv", "pinhole", "opencv-fisheye", "equirectangular"),
                        help="Défaut : détecté depuis les images (360° -> equirectangular)")
    parser.add_argument("--feature-masks", type=Path)
    args = parser.parse_args()
    try:
        return run(args)
    except subprocess.CalledProcessError as exc:
        print(f"[ERREUR] SfM GPS : commande en échec (code {exc.returncode}) : "
              f"{shlex.join(exc.cmd)}", file=sys.stderr)
        return exc.returncode if exc.returncode > 0 else 128 - exc.returncode
    except (OSError, ValueError) as exc:
        print(f"[ERREUR] SfM GPS : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
