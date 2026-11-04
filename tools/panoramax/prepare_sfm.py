#!/usr/bin/env python3
"""Prépare un téléchargement Panoramax pour Spirula SfM.

Garde les images de la taille majoritaire (ou de --size), les relie par lien
physique dans OUTPUT_DIR/images, et déduit la focale en pixels du champ de vue
horizontal des métadonnées : f = (largeur / 2) / tan(fov / 2).
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

IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_size(path):
    with path.open("rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            raise ValueError("pas un JPEG")
        while True:
            byte = stream.read(1)
            if not byte:
                raise ValueError("en-tête SOF introuvable")
            if byte != b"\xff":
                continue
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker:
                raise ValueError("en-tête SOF introuvable")
            code = marker[0]
            if code in (0x01, 0xD8) or 0xD0 <= code <= 0xD7:
                continue
            length_bytes = stream.read(2)
            if len(length_bytes) != 2:
                raise ValueError("segment JPEG tronqué")
            length = int.from_bytes(length_bytes, "big")
            if code in SOF_MARKERS:
                data = stream.read(5)
                if len(data) != 5:
                    raise ValueError("segment SOF tronqué")
                return int.from_bytes(data[3:5], "big"), int.from_bytes(data[1:3], "big")
            stream.seek(length - 2, os.SEEK_CUR)


def resolve_inputs(dataset):
    dataset = Path(dataset).resolve()
    images_dir = dataset / "images" if (dataset / "images").is_dir() else dataset
    metadata_dir = images_dir.parent / "metadata"
    return images_dir, metadata_dir if metadata_dir.is_dir() else None


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


def link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def sync_selection(selected, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    wanted = {path.name: path for path in selected}
    for existing in target_dir.iterdir():
        source = wanted.get(existing.name)
        if source is None or not existing.is_file() or not os.path.samefile(existing, source):
            if existing.is_dir() and not existing.is_symlink():
                raise SystemExit(f"Dossier inattendu dans {target_dir} : {existing.name}")
            existing.unlink()
    for name, source in wanted.items():
        target = target_dir / name
        if not target.exists():
            link_or_copy(source, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True, help="Dossier Panoramax (contenant images/ et metadata/)")
    parser.add_argument("--output-dir", required=True, help="Workspace SfM ; les images retenues vont dans images/")
    parser.add_argument("--size", type=parse_size, help="Taille à garder, LxH ; défaut : la plus fréquente")
    parser.add_argument("--dry-run", action="store_true", help="Analyser sans créer de fichiers")
    parser.add_argument("--shell", action="store_true", help="Écrire images_dir= et focal= sur stdout")
    args = parser.parse_args(argv)

    images_dir, metadata_dir = resolve_inputs(args.dataset)
    if not images_dir.is_dir():
        raise SystemExit(f"Dossier d'images introuvable : {images_dir}")
    output_dir = Path(args.output_dir).resolve()
    target_dir = output_dir / "images"
    if target_dir.resolve() == images_dir:
        raise SystemExit("Le workspace ne doit pas contenir le dossier d'images source")

    log = lambda message: print(f"[prepare] {message}", file=sys.stderr)
    sizes = {}
    for path in sorted(images_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.is_file():
            continue
        try:
            sizes[path] = jpeg_size(path)
        except (OSError, ValueError) as exc:
            log(f"ignorée {path.name} : {exc}")
    if not sizes:
        raise SystemExit(f"Aucune image JPEG dans {images_dir}")

    counts = Counter(sizes.values())
    for (width, height), count in counts.most_common():
        log(f"taille {width}x{height} : {count} image(s)")
    keep = args.size or counts.most_common(1)[0][0]
    selected = [path for path, size in sizes.items() if size == keep]
    if not selected:
        raise SystemExit(f"Aucune image de taille {keep[0]}x{keep[1]}")
    excluded = [path.name for path, size in sizes.items() if size != keep]
    log(f"retenues : {len(selected)} en {keep[0]}x{keep[1]} ; écartées : {len(excluded)}")

    focals = []
    if metadata_dir:
        for path in selected:
            focal = focal_from_metadata(metadata_dir / f"{path.stem}.json", keep[0])
            if focal:
                focals.append(focal)
    focal = statistics.median(focals) if focals else None
    if focal:
        spread = (max(focals) - min(focals)) / focal
        log(f"focale : {focal:.1f} px d'après le champ de vue de {len(focals)}/{len(selected)} image(s)")
        if spread > 0.05:
            log(f"AVERTISSEMENT : focales dispersées de {spread:.0%} ; la médiane est utilisée")
    else:
        log("focale : aucun champ de vue dans les métadonnées, Spirula l'estimera")

    if not args.dry_run:
        sync_selection(selected, target_dir)
        (output_dir / "selection.json").write_text(
            json.dumps(
                {
                    "source_images": str(images_dir),
                    "size": list(keep),
                    "selected": len(selected),
                    "excluded": excluded,
                    "focal_px": focal,
                    "focal_samples": len(focals),
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


if __name__ == "__main__":
    main()
