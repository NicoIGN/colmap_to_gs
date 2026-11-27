#!/usr/bin/env python3
"""Prépare un téléchargement Panoramax pour Spirula SfM.

Garde les images de la taille majoritaire (ou de --size), les relie par lien
physique dans OUTPUT_DIR/images, et déduit la focale en pixels du champ de vue
horizontal des métadonnées : f = (largeur / 2) / tan(fov / 2).

Mode multi-caméras (PANORAMAX_MULTI_CAM=true ou --multi-cam) : les tailles
représentant au moins 1 % des images sont gardées, chacune dans son sous-dossier
LxH/ ; Spirula (--camera-mode folder) crée alors une caméra par taille. Une seule
focale ne pouvant pas couvrir plusieurs caméras, aucune focale n'est transmise et
selection.json conserve celle de chaque taille.
"""

import argparse
import json
import math
import os
import shutil
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panoramax"))
from dataset_layout import image_files, jpeg_size, metadata_index, resolved_picture_type

IMAGE_EXTENSIONS = {".jpg", ".jpeg"}


def resolve_inputs(dataset):
    """Retourne les images (toutes collections confondues) et l'index {nom: JSON} des métadonnées."""
    dataset = Path(dataset).resolve()
    images = [path for path in image_files(dataset) if path.suffix.lower() in IMAGE_EXTENSIONS]
    if images:
        return images, metadata_index(dataset) or None
    # Ancien appel : dossier d'images nu, métadonnées dans le dossier parent.
    images = [path for path in sorted(dataset.glob("*")) if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]
    return images, metadata_index(dataset.parent) or None


def parse_size(text):
    try:
        width, height = (int(value) for value in text.lower().split("x"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"taille attendue sous la forme LxH : {text}") from exc
    return width, height


def focal_from_metadata(path, width):
    try:
        feature = json.loads(path.read_text(encoding="utf-8"))["feature"]
        fov = float(feature["properties"]["pers:interior_orientation"]["field_of_view"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not 0 < fov < 180:
        return None
    return (width / 2) / math.tan(math.radians(fov) / 2)


def is_equirectangular(path):
    """Même règle que download.py (dataset_layout.resolved_picture_type), avec le XMP du JPEG en repli."""
    try:
        feature = json.loads(path.read_text(encoding="utf-8"))["feature"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    image = path.parent.parent / "images" / f"{path.stem}.jpg"
    return resolved_picture_type(feature, image if image.is_file() else None) == "equirectangular"


def env_flag(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def sync_selection(targets, target_dir):
    """Lie chaque source à target_dir/relative ; targets = {source: relative}."""
    target_dir.mkdir(parents=True, exist_ok=True)
    wanted = {target_dir / relative: source for source, relative in targets.items()}
    for existing in target_dir.rglob("*"):
        if existing.is_dir():
            continue
        source = wanted.get(existing)
        if source is None or not existing.is_file() or not os.path.samefile(existing, source):
            existing.unlink()
    for target, source in wanted.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            link_or_copy(source, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True, help="Dossier Panoramax (contenant images/ et metadata/)")
    parser.add_argument("--output-dir", required=True, help="Workspace SfM ; les images retenues vont dans images/")
    parser.add_argument("--images-dir", help="Dossier des images retenues ; défaut : <output-dir>/images")
    parser.add_argument("--size", type=parse_size, help="Taille à garder, LxH ; défaut : la plus fréquente")
    parser.add_argument(
        "--multi-cam",
        action="store_true",
        help="Garder toutes les tailles, une caméra par taille ; défaut : env PANORAMAX_MULTI_CAM",
    )
    parser.add_argument("--dry-run", action="store_true", help="Analyser sans créer de fichiers")
    parser.add_argument("--shell", action="store_true", help="Écrire images_dir= et focal= sur stdout")
    args = parser.parse_args(argv)

    images, metadata_dir = resolve_inputs(args.dataset)
    if not images:
        raise SystemExit(f"Aucune image dans les dossiers images/ de {args.dataset}")
    output_dir = Path(args.output_dir).resolve()
    target_dir = Path(args.images_dir).resolve() if args.images_dir else output_dir / "images"
    if any(path.parent.resolve() == target_dir.resolve() for path in images):
        raise SystemExit("Le workspace ne doit pas contenir le dossier d'images source")

    log = lambda message: print(f"[prepare] {message}", file=sys.stderr)
    sizes = {}
    for path in sorted(images):
        try:
            sizes[path] = jpeg_size(path)
        except (OSError, ValueError) as exc:
            log(f"ignorée {path.name} : {exc}")
    if not sizes:
        raise SystemExit(f"Aucune image JPEG dans {args.dataset}")

    counts = Counter(sizes.values())
    for (width, height), count in counts.most_common():
        log(f"taille {width}x{height} : {count} image(s)")
    multi_cam = args.multi_cam or env_flag("PANORAMAX_MULTI_CAM")
    if multi_cam and args.size:
        raise SystemExit("--size est incompatible avec le mode multi-caméras")
    if multi_cam:
        keep = None
        total = len(sizes)
        kept_sizes = {size for size, count in counts.items() if count * 100 >= total}
        if not kept_sizes:
            raise SystemExit("Aucune taille n'atteint 1 % des images")
        selected = {path: None for path in sizes if sizes[path] in kept_sizes}
        excluded = [path.name for path in sizes if sizes[path] not in kept_sizes]
        log(
            f"multi-caméras : {len(kept_sizes)} caméra(s) de {len(counts)} taille(s), "
            f"une par taille ; tailles sous 1 % écartées : {len(excluded)} image(s)"
        )
    else:
        keep = args.size or counts.most_common(1)[0][0]
        selected = {path: path.name for path, size in sizes.items() if size == keep}
        if not selected:
            raise SystemExit(f"Aucune image de taille {keep[0]}x{keep[1]}")
        excluded = [path.name for path, size in sizes.items() if size != keep]
        log(f"retenues : {len(selected)} en {keep[0]}x{keep[1]} ; écartées : {len(excluded)}")
    targets = {
        path: (f"{sizes[path][0]}x{sizes[path][1]}/{path.name}" if multi_cam else path.name)
        for path in selected
    }

    equirectangular = [
        path.stem in (metadata_dir or {}) and is_equirectangular(metadata_dir[path.stem]) for path in selected
    ]
    camera_model = None
    if equirectangular and all(equirectangular):
        camera_model = "equirectangular"
        log("images 360 : modèle de caméra equirectangular, focale non transmise")
    elif any(equirectangular):
        raise SystemExit(
            "images 360 et perspectives mélangées : un seul modèle de caméra ne peut pas les décrire. "
            "Utiliser PANORAMAX_PICTURE_TYPE=360 ou normal."
        )

    groups = {}
    for path in selected:
        groups.setdefault(sizes[path], []).append(path)
    focals = {}
    samples = {}
    for size, paths in groups.items():
        if camera_model:
            break
        values = []
        if metadata_dir:
            for path in paths:
                if path.stem not in metadata_dir:
                    continue
                focal = focal_from_metadata(metadata_dir[path.stem], size[0])
                if focal:
                    values.append(focal)
        samples[size] = len(values)
        if not values:
            log(f"focale {size[0]}x{size[1]} : aucun champ de vue dans les métadonnées")
            continue
        focal = statistics.median(values)
        focals[size] = focal
        spread = (max(values) - min(values)) / focal
        log(f"focale {size[0]}x{size[1]} : {focal:.1f} px d'après {len(values)}/{len(paths)} image(s)")
        if spread > 0.05:
            log(f"AVERTISSEMENT : focales {size[0]}x{size[1]} dispersées de {spread:.0%} ; la médiane est utilisée")

    if multi_cam:
        focal = None
        if not camera_model:
            log("focale des images source non transmise en mode multi-caméras")
    else:
        focal = focals.get(keep)

    if not args.dry_run:
        sync_selection(targets, target_dir)
        (output_dir / "selection.json").write_text(
            json.dumps(
                {
                    "source_images": str(Path(args.dataset).resolve()),
                    "multi_cam": multi_cam,
                    "size": list(keep) if keep else None,
                    "sizes": {f"{w}x{h}": len(paths) for (w, h), paths in groups.items()},
                    "selected": len(selected),
                    "excluded": excluded,
                    "focal_px": focal,
                    "focals_px": {f"{w}x{h}": value for (w, h), value in focals.items()},
                    "focal_samples": sum(samples.values()),
                    "camera_model": camera_model,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        log(f"images liées dans {target_dir}")

    if args.shell:
        print(f"images_dir={target_dir}")
        print(f"focal={focal:.2f}" if focal else "focal=")
        print(f"camera_model={camera_model or ''}")


if __name__ == "__main__":
    main()
