#!/usr/bin/env python3
"""Generate Spirula/COLMAP training masks from a local semantic segmenter.

Mask convention (Spirula and COLMAP): single-channel PNG, 255 = pixel kept,
0 = pixel masked. One mask per image, named ``<image relative path>.png``.
Masks are written in the file's raw pixel orientation, like the images.
"""

import argparse
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np

FORMAT_VERSION = 1
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
REPORT_NAME = "masks.json"
# PIL Image.Transpose values undoing exif_transpose for each EXIF orientation.
INVERSE_ORIENTATION = {2: 0, 3: 3, 4: 1, 5: 5, 6: 2, 7: 6, 8: 4}


def parse_labels(text):
    labels = []
    for item in text.split(","):
        label = " ".join(item.split())
        if label and label.lower() not in (x.lower() for x in labels):
            labels.append(label)
    if not labels:
        raise ValueError("Aucun label à masquer")
    return labels


def resolve_label_ids(id2label, requested):
    """Map requested names (case-insensitive) or numeric ids to model ids."""
    by_name = {}
    for key, name in id2label.items():
        by_name.setdefault(" ".join(str(name).split()).lower(), []).append(int(key))
    valid_ids = {int(k) for k in id2label}
    ids, unknown = [], []
    for label in requested:
        found = by_name.get(label.lower())
        if found is None and label.isdigit() and int(label) in valid_ids:
            found = [int(label)]
        if found is None:
            unknown.append(label)
        else:
            ids.extend(i for i in found if i not in ids)
    if unknown:
        hints = []
        for label in unknown:
            close = difflib.get_close_matches(label.lower(), list(by_name), n=3)
            hints.append(f"{label} (proches : {', '.join(close) or 'aucun'})")
        raise ValueError(
            "Labels absents du modèle : " + "; ".join(hints)
            + ". Voir labels.txt dans le dossier du modèle.")
    return sorted(ids)


def dilation_radius(width, height, fraction):
    return int(round(fraction * math.hypot(width, height)))


def keep_mask(probability, confidence, radius):
    """Return uint8 mask: 255 keep, 0 where selected labels reach confidence."""
    masked = np.asarray(probability) >= confidence
    if radius > 0 and masked.any():
        from scipy.ndimage import binary_dilation
        y, x = np.ogrid[-radius:radius + 1, -radius:radius + 1]
        masked = binary_dilation(masked, structure=x * x + y * y <= radius * radius)
    return np.where(masked, 0, 255).astype(np.uint8)


def list_images(root):
    images = []
    for directory, subdirs, files in os.walk(root, followlinks=True):
        subdirs.sort()
        for name in sorted(files):
            if Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                images.append(Path(directory, name).relative_to(root).as_posix())
    return images


def model_fingerprint(model_dir):
    digest = hashlib.sha256()
    for name in ("config.json", "preprocessor_config.json"):
        path = Path(model_dir, name)
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


class Segmenter:
    """transformers semantic (SegFormer, UPerNet...) or universal (Mask2Former) model."""

    def __init__(self, model_dir, device):
        import torch
        from transformers import AutoConfig, AutoImageProcessor
        from transformers import AutoModelForSemanticSegmentation

        config = AutoConfig.from_pretrained(model_dir, local_files_only=True)
        self.id2label = {int(k): v for k, v in config.id2label.items()}
        self.processor = AutoImageProcessor.from_pretrained(model_dir, local_files_only=True)
        try:
            model = AutoModelForSemanticSegmentation.from_pretrained(
                model_dir, local_files_only=True)
        except ValueError:
            from transformers import AutoModelForUniversalSegmentation
            model = AutoModelForUniversalSegmentation.from_pretrained(
                model_dir, local_files_only=True)
        if device == "auto":
            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        if device == "cpu":
            print("[masques] AVERTISSEMENT : segmentation sur CPU (lente).", file=sys.stderr)
        self.device = torch.device(device)
        self.model = model.to(self.device).eval()
        self.torch = torch

    def probability(self, image, label_ids):
        """Probability, at the image size, that a pixel belongs to label_ids."""
        torch = self.torch
        import torch.nn.functional as F

        inputs = self.processor(images=image, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            outputs = self.model(**inputs)
            ids = torch.as_tensor(label_ids, device=self.device)
            logits = getattr(outputs, "logits", None)
            if logits is not None and logits.ndim == 4:
                probability = logits[0].float().softmax(0)[ids].sum(0)
            elif getattr(outputs, "class_queries_logits", None) is not None:
                classes = outputs.class_queries_logits[0].float().softmax(-1)[:, :-1]
                masks = outputs.masks_queries_logits[0].float().sigmoid()
                semantic = torch.einsum("qc,qhw->chw", classes, masks)
                probability = semantic[ids].sum(0) / semantic.sum(0).clamp_min(1e-6)
            else:
                raise ValueError("Sortie de modèle non supportée (ni logits, ni Mask2Former)")
            probability = F.interpolate(
                probability[None, None], size=(image.height, image.width),
                mode="bilinear", align_corners=False)[0, 0].clamp(0, 1)
        return probability.cpu().numpy()


def write_png(array, path):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".png", dir=path.parent)
    os.close(handle)
    try:
        Image.fromarray(array).convert("L").save(temporary, format="PNG")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_json(data, path):
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    os.replace(temporary, path)


def mask_one(segmenter, source, label_ids, args):
    from PIL import Image, ImageOps

    with Image.open(source) as raw:
        orientation = int(raw.getexif().get(0x0112, 1) or 1)
        upright = ImageOps.exif_transpose(raw).convert("RGB")
    radius = dilation_radius(upright.width, upright.height, args.dilate_fraction)
    keep = keep_mask(segmenter.probability(upright, label_ids), args.confidence, radius)
    masked_fraction = float(np.count_nonzero(keep == 0)) / keep.size
    status = "masked"
    if masked_fraction > args.max_masked_fraction:
        keep[:] = 255
        status = "overmasked_kept"
    if orientation in INVERSE_ORIENTATION:
        keep = np.asarray(Image.fromarray(keep).transpose(
            Image.Transpose(INVERSE_ORIENTATION[orientation])))
    return keep, {"masked_fraction": round(masked_fraction, 6), "status": status,
                  "exif_orientation": orientation}


def run(args, segmenter=None):
    if not 0 < args.confidence <= 1:
        raise ValueError("--confidence doit être dans ]0,1]")
    if not 0 <= args.dilate_fraction < 0.1:
        raise ValueError("--dilate-fraction doit être dans [0,0.1[")
    if not 0 < args.max_masked_fraction <= 1:
        raise ValueError("--max-masked-fraction doit être dans ]0,1]")
    if args.images is None or args.output is None:
        raise ValueError("--images et --output sont requis")
    images_dir, output = args.images.resolve(), args.output.resolve()
    if not images_dir.is_dir():
        raise ValueError(f"Dossier d'images introuvable : {images_dir}")
    if output == images_dir or images_dir in output.parents:
        raise ValueError("--output doit être hors du dossier d'images")
    images = list_images(images_dir)
    if not images:
        raise ValueError(f"Aucune image dans {images_dir}")
    labels = parse_labels(args.labels)
    if segmenter is None:
        if not Path(args.model).is_dir():
            raise ValueError(f"Modèle local introuvable : {args.model}")
        segmenter = Segmenter(str(args.model), args.device)
    label_ids = resolve_label_ids(segmenter.id2label, labels)
    settings = {
        "format_version": FORMAT_VERSION,
        "model": str(Path(args.model).resolve()),
        "model_fingerprint": model_fingerprint(args.model),
        "labels": labels,
        "label_ids": label_ids,
        "label_names": [segmenter.id2label[i] for i in label_ids],
        "confidence": args.confidence,
        "dilate_fraction": args.dilate_fraction,
        "max_masked_fraction": args.max_masked_fraction,
        "convention": "255 = gardé, 0 = masqué ; <nom image>.png ; orientation brute",
    }

    previous = {}
    target = output
    report_path = output / REPORT_NAME
    if report_path.is_file():
        old = json.loads(report_path.read_text(encoding="utf-8"))
        if old.get("settings") == settings:
            previous = old.get("images", {})
        else:
            print("[masques] Paramètres modifiés : régénération complète.")
            target = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    elif output.exists() and any(output.iterdir()):
        raise ValueError(f"{output} existe sans {REPORT_NAME} : le déplacer avant de relancer")
    target.mkdir(parents=True, exist_ok=True)

    stats = {}
    done = skipped = 0
    try:
        if target == output and not report_path.is_file():
            write_json({"status": "partial", "settings": settings, "images": {}}, report_path)
        for index, name in enumerate(images, 1):
            path = target / (name + ".png")
            if target == output and path.is_file() and name in previous:
                stats[name] = previous[name]
                skipped += 1
                continue
            keep, stats[name] = mask_one(segmenter, images_dir / name, label_ids, args)
            write_png(keep, path)
            done += 1
            if index % 50 == 0 or index == len(images):
                print(f"[masques] {index}/{len(images)} images traitées.", flush=True)
            if done % 50 == 0:
                write_json({"status": "partial", "settings": settings, "images": stats},
                           target / REPORT_NAME)
        fractions = [s["masked_fraction"] for s in stats.values()]
        overmasked = sorted(n for n, s in stats.items() if s["status"] == "overmasked_kept")
        oriented = sorted(n for n, s in stats.items() if s["exif_orientation"] != 1)
        summary = {
            "images": len(stats), "generated": done, "reused": skipped,
            "mean_masked_fraction": round(float(np.mean(fractions)), 6),
            "max_masked_fraction": round(float(np.max(fractions)), 6),
            "overmasked_kept": len(overmasked),
            "exif_rotated": len(oriented),
        }
        write_json({"status": "complete", "settings": settings, "summary": summary,
                    "images": stats}, target / REPORT_NAME)
    except BaseException:
        if target != output:
            shutil.rmtree(target, ignore_errors=True)
        raise
    if target != output:
        shutil.rmtree(output)
        os.replace(target, output)

    print(f"[masques] {summary['images']} masques ({done} générés, {skipped} réutilisés) "
          f"dans {output}")
    print(f"[masques] Fraction masquée : moyenne {summary['mean_masked_fraction']:.1%}, "
          f"max {summary['max_masked_fraction']:.1%}")
    if overmasked:
        print(f"[masques] AVERTISSEMENT : {len(overmasked)} image(s) masquée(s) à plus de "
              f"{args.max_masked_fraction:.0%} ; masque désactivé pour elles.", file=sys.stderr)
    if oriented:
        print(f"[masques] AVERTISSEMENT : {len(oriented)} image(s) avec orientation EXIF ; "
              "vérifier l'alignement des masques dans Spirula.", file=sys.stderr)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--images", type=Path, help="Dossier des images")
    parser.add_argument("--output", type=Path, help="Dossier des masques")
    parser.add_argument("--model", type=Path, required=True, help="Modèle transformers local")
    parser.add_argument("--labels", default="sky",
                        help="Labels à masquer, séparés par des virgules (défaut : sky)")
    parser.add_argument("--confidence", type=float, default=0.5,
                        help="Probabilité minimale des labels choisis pour masquer (0.5)")
    parser.add_argument("--dilate-fraction", type=float, default=0.0,
                        help="Dilatation du masque, fraction de la diagonale de l'image (0)")
    parser.add_argument("--max-masked-fraction", type=float, default=0.8,
                        help="Au-delà, le masque de l'image est désactivé (0.8)")
    parser.add_argument("--device", default="auto", help="auto, cuda, cuda:N, mps ou cpu")
    parser.add_argument("--list-labels", action="store_true",
                        help="Afficher les labels du modèle et quitter")
    args = parser.parse_args(argv)
    try:
        if args.list_labels:
            from transformers import AutoConfig
            config = AutoConfig.from_pretrained(str(args.model), local_files_only=True)
            for key, name in sorted((int(k), v) for k, v in config.id2label.items()):
                print(f"{key}\t{name}")
            return 0
        run(args)
    except ValueError as error:
        print(f"[ERREUR] {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
