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
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import numpy as np
FORMAT_VERSION = 2
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
REPORT_NAME = "masks.json"
MODEL_PRESETS = {
    "ade20k": {
        "directory": "nvidia--segformer-b5-finetuned-ade-640-640",
        "description": "SegFormer B5 entraîné sur ADE20K (généraliste)",
    },
    "mapillary": {
        "directory": "facebook--mask2former-swin-large-mapillary-vistas-semantic",
        "description": "Mask2Former Swin-Large entraîné sur Mapillary Vistas (scènes urbaines extérieures)",
    },
}
DEFAULT_INSTALL_DIR = Path(
    os.environ.get(
        "TRAIN_MASKS_INSTALL_DIR",
        "~/.local/share/colmap-to-gs/segmentation-masks",
    )
).expanduser()

def resolve_model_argument(value, backend):
    """Resolve a preset name or keep a user-supplied local model path."""
    if value in MODEL_PRESETS:
        if backend == "spirula":
            raise ValueError(
                "Avec --backend spirula, --model doit être le chemin d'un checkpoint SAM GGML ; "
                "les préréglages ade20k et mapillary sont réservés au backend segmentation."
            )
        model_dir = DEFAULT_INSTALL_DIR / "models" / MODEL_PRESETS[value]["directory"]
        return model_dir.resolve()
    return Path(value).expanduser().resolve()
# PIL Image.Transpose values undoing exif_transpose for each EXIF orientation.
INVERSE_ORIENTATION = {2: 0, 3: 3, 4: 1, 5: 5, 6: 2, 7: 6, 8: 4}

def default_jobs():
    """MASKS_JOBS si défini, sinon le nombre de CPU accessibles."""
    configured = os.environ.get("MASKS_JOBS", "").strip()
    if configured:
        return int(configured)
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


SPIRULA_IMAGE_LINE = re.compile(r"^image (\d+) : (\S+) -- ")


def relay_spirula_output(stream, names):
    """Print Spirula's per-image lines with the final frame index and image name."""
    for line in stream:
        match = SPIRULA_IMAGE_LINE.match(line)
        if match and match.group(2) in names:
            index, source = names[match.group(2)]
            line = f"image {index} : {source} -- " + line[match.end():]
        print(line, end="", flush=True)


def shard_bounds(total, jobs):
    """Découpe 0..total en blocs contigus (au plus jobs, au moins 1)."""
    count = max(1, min(jobs, total))
    base, extra = divmod(total, count)
    bounds, start = [], 0
    for index in range(count):
        size = base + (index < extra)
        bounds.append((start, start + size))
        start += size
    return bounds


def log(message, args=None, *, force=False):
    """Print a diagnostic only when verbose mode is enabled."""
    if force or (args is not None and args.verbose):
        print(f"[masques] {message}", flush=True)

def progress(current, total, args, label="Traitement", *, force=False):
    """Keep progress readable in both terminals and redirected job logs."""
    step = max(1, math.ceil(total / 100))
    if not force and current not in (0, total) and current % step:
        return
    total = max(total, 1)
    width = 32
    ratio = min(current / total, 1.0)
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    percent = ratio * 100
    print(
        f"[masques] {label:<22} [{bar}] {current}/{total} ({percent:5.1f}%)",
        flush=True,
    )

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

def keep_mask(probability, confidence, radius, label_map=None, label_ids=None):
    """Mask requested winning classes, optionally applying a secondary score floor."""
    probability = np.asarray(probability, dtype=np.float32)
    if label_map is not None and label_ids is not None:
        masked = np.isin(np.asarray(label_map), np.asarray(label_ids)) & (probability >= confidence)
    else:
        masked = probability >= confidence
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
    if Path(model_dir).is_file():
        stat = Path(model_dir).stat()
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
        return digest.hexdigest()
    for name in ("config.json", "preprocessor_config.json"):
        path = Path(model_dir, name)
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()

class SpirulaSegmenter:
    """Run native SAM once on independent images, preserving raw orientation."""

    def __init__(self, args):
        self.args = args
        self.id2label = dict(enumerate(parse_labels(args.labels)))
        self.temporary = tempfile.TemporaryDirectory(prefix="spirula-masks-")
        self.paths = {}
        binary = shutil.which(args.spirula_bin)
        if binary is None:
            self.close()
            raise ValueError(f"Spirula introuvable : {args.spirula_bin}")
        self.binary = str(Path(binary).resolve())
        stat = Path(self.binary).stat()
        self.settings = {"backend": "spirula", "binary": self.binary,
                         "binary_fingerprint": f"{stat.st_size}:{stat.st_mtime_ns}",
                         "device": args.spirula_device}
        self.jobs = getattr(args, "jobs", None) or default_jobs()
        if self.jobs < 1:
            self.close()
            raise ValueError("--jobs / MASKS_JOBS doit être au moins 1")

    def close(self):
        self.temporary.cleanup()

    def command(self, frames, masks):
        command = [self.binary, "sam", "track", "--model", str(self.args.model.resolve()),
             "--frames", str(frames), "--out", str(masks),
             "--text", "; ".join(self.id2label.values()),
             "--mask-mode", "image", "--remove-prompted", "--dilate-ratio", "0",
             "--threshold", str(self.args.confidence),
             "--device", self.args.spirula_device]
        if self.args.accept_spirula_license:
            command += ["--accept-license", "sam3=yes"]
        return command

    def prepare(self, images_dir, names):
        if not names:
            return
        from PIL import Image
        root = Path(self.temporary.name)
        final = root / "masks"
        final.mkdir()
        shards = []
        shard_names = []
        progress(0, len(names), self.args, "Préparation des images")
        for shard, (start, stop) in enumerate(shard_bounds(len(names), self.jobs)):
            frames, masks = root / f"frames-{shard}", root / f"masks-{shard}"
            frames.mkdir()
            masks.mkdir()
            local_names = {}
            for local, name in enumerate(names[start:stop], 1):
                source = images_dir / name
                # SAM track has a flat input directory and a restricted extension list.
                if source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}:
                    raise ValueError(f"Format non pris en charge par Spirula SAM : {source}")
                with Image.open(source) as image:
                    image.load()
                frame_name = f"{local:09d}{source.suffix.lower()}"
                (frames / frame_name).symlink_to(source)
                local_names[frame_name] = (start + local - 1, name)
                self.paths[str(source)] = final / f"frame_{start + local - 1:05d}.png"
                progress(start + local, len(names), self.args, "Préparation des images")
            shards.append((start, masks, self.command(frames, masks)))
            shard_names.append(local_names)
        if self.args.accept_spirula_license:
            print("[masques] Acceptation explicite de la licence SAM 3 transmise à Spirula.", flush=True)
        print(f"[masques] Calcul SAM : {len(shards)} processus Spirula en parallèle "
              f"(MASKS_JOBS={self.jobs}).", flush=True)
        progress(0, len(names), self.args, "Calcul SAM")
        completed = 0
        running = {}
        try:
            for shard, (_, _, command) in enumerate(shards):
                process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True,
                                           encoding="utf-8", errors="replace")
                threading.Thread(target=relay_spirula_output,
                                 args=(process.stdout, shard_names[shard]),
                                 daemon=True).start()
                running[shard] = process
            while running:
                for shard in list(running):
                    status = running[shard].poll()
                    if status is None:
                        continue
                    del running[shard]
                    if status:
                        raise subprocess.CalledProcessError(status, shards[shard][2])
                count = sum(sum(1 for _ in masks.glob("frame_*.png"))
                            for _, masks, _ in shards)
                if count != completed:
                    progress(count, len(names), self.args, "Calcul SAM", force=True)
                    completed = count
                if running:
                    time.sleep(0.5)
        finally:
            for process in running.values():
                if process.poll() is None:
                    process.kill()
                process.wait()
        for start, masks, _ in shards:
            for path in sorted(masks.glob("frame_*.png")):
                local = int(path.stem.split("_")[1])
                os.replace(path, final / f"frame_{start + local:05d}.png")
        for source, path in self.paths.items():
            if not path.is_file():
                raise ValueError(f"Spirula n'a pas écrit le masque de {source} : {path}")
            with Image.open(source) as image, Image.open(path) as mask:
                mask.load()
                if mask.mode != "L" or mask.size != image.size:
                    raise ValueError(f"Masque Spirula invalide (mode ou dimensions) : {path}")

    def raw_mask(self, source, args):
        from PIL import Image
        with Image.open(source) as image, Image.open(self.paths[str(source)]) as mask:
            orientation = int(image.getexif().get(0x0112, 1) or 1)
            probability = np.asarray(mask) < 128
            radius = dilation_radius(image.width, image.height, args.dilate_fraction)
        return keep_mask(probability, 0.5, radius), orientation

class Segmenter:
    """transformers semantic (SegFormer, UPerNet...) or universal (Mask2Former) model."""

    def __init__(self, model_dir, device, verbose=False):
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
            if verbose:
                print("[masques] AVERTISSEMENT : segmentation sur CPU (lente).", file=sys.stderr)
        self.device = torch.device(device)
        self.model = model.to(self.device).eval()
        self.torch = torch
        if verbose:
            print(f"[masques] Modèle chargé sur {self.device}.", flush=True)

    def prediction(self, image, label_ids):
        """Return selected-class score and class map using Hugging Face post-processing."""
        torch = self.torch
        import torch.nn.functional as F
        target_size = (image.height, image.width)
        inputs = self.processor(images=image, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            outputs = self.model(**inputs)
            ids = torch.as_tensor(label_ids, device=self.device, dtype=torch.long)
            # Mask2Former: use Hugging Face's official semantic post-processor
            # for the definitive class map. It resizes mask logits to 384x384,
            # combines query classes and masks, then upsamples to the source size.
            if getattr(outputs, "class_queries_logits", None) is not None:
                post_process = getattr(self.processor, "post_process_semantic_segmentation", None)
                if post_process is None:
                    raise ValueError(
                        "Le processeur Hugging Face ne fournit pas "
                        "post_process_semantic_segmentation (Mask2Former)."
                    )
                class_map = post_process(outputs, target_sizes=[target_size])[0]
                # Build only selected-class and total score maps, avoiding a
                # full 65-channel tensor at the original high image resolution.
                masks_logits = F.interpolate(
                    outputs.masks_queries_logits.float(),
                    size=(384, 384),
                    mode="bilinear",
                    align_corners=False,
                )
                masks_probs = masks_logits.sigmoid()
                class_probs = outputs.class_queries_logits.float().softmax(dim=-1)[..., :-1]
                selected_weights = class_probs[:, :, ids].sum(dim=-1)
                total_weights = class_probs.sum(dim=-1)
                selected_scores = torch.einsum("bq,bqhw->bhw", selected_weights, masks_probs)
                total_scores = torch.einsum("bq,bqhw->bhw", total_weights, masks_probs)
                selected_scores = F.interpolate(
                    selected_scores[:, None], size=target_size,
                    mode="bilinear", align_corners=False
                )[0, 0]
                total_scores = F.interpolate(
                    total_scores[:, None], size=target_size,
                    mode="bilinear", align_corners=False
                )[0, 0]
                selected_probability = (
                    selected_scores / total_scores.clamp_min(1e-6)
                ).clamp(0.0, 1.0)
            # Standard semantic models (SegFormer, UPerNet, ...): resize logits
            # first, as in Hugging Face semantic post-processing, then take argmax.
            else:
                logits = getattr(outputs, "logits", None)
                if logits is None or logits.ndim != 4:
                    raise ValueError(
                        "Sortie de modèle non supportée (ni logits, ni Mask2Former)."
                    )
                resized_logits = F.interpolate(
                    logits.float(), size=target_size,
                    mode="bilinear", align_corners=False
                )[0]
                class_probs = resized_logits.softmax(dim=0)
                selected_probability = class_probs[ids].sum(dim=0)
                class_map = resized_logits.argmax(dim=0)
        return (
            selected_probability.cpu().numpy().astype(np.float32),
            class_map.cpu().numpy().astype(np.uint16),
        )

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

def label_color(class_id):
    """Stable, reasonably distinct RGB color for a semantic class ID."""
    import colorsys
    hue = (int(class_id) * 0.618033988749895) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.98)
    return tuple(round(channel * 255) for channel in (red, green, blue))

def write_label_diagnostics(label_map, probability, source_rgb, relative_name, diagnostic_dirs, confidence, label_ids):
    """Write diagnostics in separate subfolders, preserving input subdirectories."""
    from PIL import Image
    relative = Path(relative_name)
    ids = np.asarray(label_map, dtype=np.uint16)
    colors = np.zeros((*ids.shape, 3), dtype=np.uint8)
    for class_id in np.unique(ids):
        colors[ids == class_id] = label_color(int(class_id))

    def save_image(folder_key, suffix, image, image_format):
        path = diagnostic_dirs[folder_key] / (relative.as_posix() + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format=image_format)
    # Semantic class map in RGB false colors.
    save_image("semantic", ".png", Image.fromarray(colors), "PNG")
    # Original image blended with the semantic class colors.
    original = Image.fromarray(np.asarray(source_rgb, dtype=np.uint8))
    color_image = Image.fromarray(colors)
    save_image("overlay", ".overlay.jpg", Image.blend(original, color_image, 0.48), "JPEG")
    # Selected-label probability (black = 0, white = 1).
    probability = np.clip(np.asarray(probability, dtype=np.float32), 0.0, 1.0)
    probability_u8 = np.round(probability * 255).astype(np.uint8)
    save_image("probability", ".probability.png",
               Image.fromarray(probability_u8), "PNG")
    # Exact numeric class IDs, retained as a 16-bit PNG.
    save_image("ids", ".ids.png", Image.fromarray(ids), "PNG")
    selected = np.isin(ids, np.asarray(label_ids))
    binary = np.where(selected & (probability >= confidence), 0, 255).astype(np.uint8)
    save_image("threshold", ".threshold.png",
               Image.fromarray(binary), "PNG")

def write_label_legend(segmenter, path):
    """Describe diagnostic subfolders, class IDs and their display colors."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Diagnostics de segmentation — generate_masks.py",
        "",
        "Sous-dossiers :",
        "  semantic/    carte des classes en fausses couleurs RGB ;",
        "  overlay/     image originale avec les classes superposées ;",
        "  probability/ score normalisé du ou des labels demandés (noir=0, blanc=1) ;",
        "  threshold/   pixels classés dans le label demandé et dépassant --confidence ;",
        "  ids/         identifiants numériques des classes en PNG 16 bits.",
        "",
        "Les couleurs sont déterministes et données en hexadécimal ci-dessous.",
        "",
    ]
    if isinstance(segmenter, SpirulaSegmenter):
        lines += [
            "Backend : Spirula SAM (segmentation par prompt, sans classes sémantiques).",
            f"0 : zone non détectée — couleur #{''.join(f'{v:02X}' for v in label_color(0))}",
            f"1 : région détectée — union des prompts : {', '.join(segmenter.id2label.values())} "
            f"— couleur #{''.join(f'{v:02X}' for v in label_color(1))}",
            "Attention : SAM ne fournit pas ici une classe distincte par prompt ; la carte est binaire.",
        ]
    else:
        lines += [
            "Backend : Transformers — classe de probabilité maximale prédite à chaque pixel.",
            "Les classes demandées pour le masquage sont signalées par [MASQUER].",
            "",
        ]
        for class_id, name in sorted(segmenter.id2label.items()):
            marker = " [MASQUER]" if class_id in getattr(segmenter, "_requested_label_ids", []) else ""
            color = ''.join(f"{value:02X}" for value in label_color(class_id))
            lines.append(f"{class_id} : {name}{marker} — couleur #{color}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def write_json(data, path):
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    os.replace(temporary, path)

def mask_one(segmenter, source, label_ids, args):
    from PIL import Image, ImageOps
    label_map = None
    probability = None
    debug_rgb = None
    if isinstance(segmenter, SpirulaSegmenter):
        keep, orientation = segmenter.raw_mask(source, args)
        if args.verbose:
            # Spirula SAM returns one union mask for all text prompts, not class IDs.
            label_map = np.where(keep == 0, 1, 0).astype(np.uint16)
            probability = (keep == 0).astype(np.float32)
            with Image.open(source) as raw:
                debug_rgb = np.asarray(raw.convert("RGB"))
    else:
        with Image.open(source) as raw:
            orientation = int(raw.getexif().get(0x0112, 1) or 1)
            upright = ImageOps.exif_transpose(raw).convert("RGB")
        radius = dilation_radius(upright.width, upright.height, args.dilate_fraction)
        probability, label_map = segmenter.prediction(upright, label_ids)
        debug_rgb = np.asarray(upright)
        keep = keep_mask(probability, args.confidence, radius, label_map, label_ids)
    masked_fraction = float(np.count_nonzero(keep == 0)) / keep.size
    status = "masked"
    if masked_fraction > args.max_masked_fraction:
        keep[:] = 255
        status = "overmasked_kept"
    if orientation in INVERSE_ORIENTATION and not isinstance(segmenter, SpirulaSegmenter):
        transpose = Image.Transpose(INVERSE_ORIENTATION[orientation])
        keep = np.asarray(Image.fromarray(keep).transpose(transpose))
        if label_map is not None:
            label_map = np.asarray(Image.fromarray(
                label_map.astype(np.uint16)).transpose(transpose))
        if probability is not None:
            probability = np.asarray(Image.fromarray(
                probability.astype(np.float32)).transpose(transpose))
        debug_rgb = np.asarray(Image.fromarray(debug_rgb).transpose(transpose))
    return keep, {"masked_fraction": round(masked_fraction, 6), "status": status,
                  "exif_orientation": orientation}, label_map, probability, debug_rgb

def run(args, segmenter=None):
    if not 0 <= args.confidence <= 1:
        raise ValueError("--confidence doit être dans [0,1]")
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
    log(f"{output.name} : {len(images)} images ; labels={','.join(labels)}",
        force=True)
    if segmenter is None:
        if not Path(args.model).is_dir():
            raise ValueError(f"Modèle local introuvable : {args.model}")
        segmenter = Segmenter(str(args.model), args.device, verbose=args.verbose)
    label_ids = resolve_label_ids(segmenter.id2label, labels)
    segmenter._requested_label_ids = label_ids
    labels_dir = output / "tmp_labels"
    if args.verbose:
        log(f"Écriture des cartes de labels dans {labels_dir}", args)
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
    if isinstance(segmenter, SpirulaSegmenter):
        settings.update(segmenter.settings)
    previous = {}
    target = output
    report_path = output / REPORT_NAME
    if report_path.is_file():
        old = json.loads(report_path.read_text(encoding="utf-8"))
        if old.get("settings") == settings:
            previous = old.get("images", {})
        else:
            log("Paramètres modifiés : régénération complète.", args)
            target = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    elif output.exists() and any(output.iterdir()):
        raise ValueError(f"{output} existe sans {REPORT_NAME} : le déplacer avant de relancer")
    target.mkdir(parents=True, exist_ok=True)
    stats = {}
    done = skipped = 0
    try:
        write_json({"status": "partial", "settings": settings, "images": previous},
                   target / REPORT_NAME)
        if args.verbose:
            labels_dir = target / "tmp_labels"
            if labels_dir.exists():
                shutil.rmtree(labels_dir)
            labels_dir.mkdir(parents=True, exist_ok=True)
            diagnostic_dirs = {
                "semantic": labels_dir / "semantic",
                "overlay": labels_dir / "overlay",
                "probability": labels_dir / "probability",
                "threshold": labels_dir / "threshold",
                "ids": labels_dir / "ids",
            }
            for directory in diagnostic_dirs.values():
                directory.mkdir(parents=True, exist_ok=True)
            write_label_legend(segmenter, labels_dir / "labels.txt")
            log("Diagnostics écrits dans tmp_labels/{semantic,overlay,probability,threshold,ids}/", args)
            log(f"Seuil de score complémentaire : {args.confidence:g} (0 = aucune filtration des classes prédites).", args)
        if isinstance(segmenter, SpirulaSegmenter):
            pending = [name for name in images if args.verbose or not (
                target == output and (target / (name + ".png")).is_file() and name in previous)]
            log(f"Calcul SAM : {len(pending)} à calculer, "
                f"{len(images) - len(pending)} déjà disponibles.", force=True)
            segmenter.prepare(images_dir, pending)
            log("Préparation Spirula terminée.", args)
        progress(0, len(images), args, "Génération des masques")
        for index, name in enumerate(images, 1):
            path = target / (name + ".png")
            if target == output and path.is_file() and name in previous and not args.verbose:
                stats[name] = previous[name]
                skipped += 1
                progress(index, len(images), args, "Génération des masques")
                continue
            keep, stats[name], label_map, probability, debug_rgb = mask_one(
                segmenter, images_dir / name, label_ids, args)
            write_png(keep, path)
            if args.verbose and label_map is not None:
                write_label_diagnostics(
                    label_map, probability, debug_rgb, name,
                    diagnostic_dirs, args.confidence, label_ids)
                if not isinstance(segmenter, SpirulaSegmenter):
                    selected_pixels = np.isin(label_map, label_ids)
                    argmax_fraction = float(selected_pixels.mean())
                    threshold_fraction = float((selected_pixels & (probability >= args.confidence)).mean())
                    selected_scores = probability[selected_pixels]
                    max_selected_score = float(selected_scores.max()) if selected_scores.size else 0.0
                    log(
                        f"{name}: pixels classés dans le(s) label(s) demandé(s)={argmax_fraction:.1%}, "
                        f"retenus après seuil {args.confidence:g}={threshold_fraction:.1%}, "
                        f"score max du label={max_selected_score:.3f}", args)
            done += 1
            progress(index, len(images), args, "Génération des masques")
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
    log(f"{summary['images']} masques ({done} générés, {skipped} réutilisés) dans {output}",
        force=True)
    log(f"Fraction masquée : moyenne {summary['mean_masked_fraction']:.1%}, max {summary['max_masked_fraction']:.1%}", args)
    if overmasked and args.verbose:
        print(f"[masques] AVERTISSEMENT : {len(overmasked)} image(s) masquée(s) à plus de "
              f"{args.max_masked_fraction:.0%} ; masque désactivé pour elles.", file=sys.stderr)
    if oriented and args.verbose:
        print(f"[masques] AVERTISSEMENT : {len(oriented)} image(s) avec orientation EXIF ; "
              "vérifier l'alignement des masques dans Spirula.", file=sys.stderr)
    return summary

def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Préréglages de modèles disponibles :\n"
            "  ade20k     SegFormer B5 / ADE20K (modèle généraliste, défaut)\n"
            "  mapillary  Mask2Former Swin-Large / Mapillary Vistas "
            "(scènes urbaines extérieures)\n\n"
            "On peut aussi fournir à --model le chemin d'un autre modèle Transformers "
            "local. Avec --backend spirula, fournir obligatoirement le chemin d'un "
            "checkpoint SAM GGML.\n"
            "Les préréglages sont cherchés dans "
            "${TRAIN_MASKS_INSTALL_DIR:-~/.local/share/colmap-to-gs/segmentation-masks}/models/."
        ),
    )
    parser.add_argument("--images", type=Path, help="Dossier des images")
    parser.add_argument("--output", type=Path, help="Dossier des masques")
    parser.add_argument("--backend", choices=("segmentation", "spirula"), default="segmentation")
    parser.add_argument("--spirula-bin", default="spirula", help="Exécutable Spirula avec SAM")
    parser.add_argument("--spirula-device", default="auto", help="Périphérique Vulkan : auto, index, nom ou uuid")
    parser.add_argument("--accept-spirula-license", action="store_true",
                        help="Confirmer explicitement l'acceptation de la licence SAM 3")
    parser.add_argument(
        "--model",
        default=os.environ.get("TRAIN_MASKS_MODEL", "ade20k"),
        metavar="{ade20k,mapillary,PATH}",
        help=(
            "Modèle à utiliser : ade20k, mapillary ou chemin local. "
            "Défaut : TRAIN_MASKS_MODEL si défini, sinon ade20k."
        ),
    )
    parser.add_argument("--labels", default="sky",
                        help="Labels à masquer, séparés par des virgules (défaut : sky)")
    parser.add_argument("--confidence", type=float, default=None,
                        help="Seuil de score supplémentaire (défaut automatique : segmentation=0, spirula=0.5)")
    parser.add_argument("--dilate-fraction", type=float, default=0.0,
                        help="Dilatation du masque, fraction de la diagonale de l'image (0)")
    parser.add_argument("--max-masked-fraction", type=float, default=0.8,
                        help="Au-delà, le masque de l'image est désactivé (0.8)")
    parser.add_argument("--device", default="auto", help="auto, cuda, cuda:N, mps ou cpu")
    parser.add_argument("--jobs", type=int, default=None,
                        help="Processus Spirula parallèles (défaut : MASKS_JOBS ou CPU accessibles)")
    parser.add_argument("--verbose", action="store_true", help="Afficher les étapes et la progression")
    parser.add_argument("--list-labels", action="store_true",
                        help="Afficher les labels du modèle et quitter")
    parsed_argv = sys.argv[1:] if argv is None else argv
    if not parsed_argv:
        parser.print_help()
        return 0
    args = parser.parse_args(parsed_argv)
    if args.confidence is None:
        args.confidence = 0.5 if args.backend == "spirula" else 0.0
    try:
        args.model = resolve_model_argument(args.model, args.backend)
    except ValueError as error:
        parser.error(str(error))
    native = None
    try:
        if args.backend == "spirula":
            if args.list_labels:
                raise ValueError("SAM utilise des prompts libres, pas une liste de classes")
            if not args.model.is_file():
                raise ValueError(f"Checkpoint SAM GGML introuvable : {args.model}")
            native = SpirulaSegmenter(args)
            if args.verbose:
                log(f"Backend Spirula SAM : {native.binary}", args)
        if args.list_labels:
            from transformers import AutoConfig
            config = AutoConfig.from_pretrained(str(args.model), local_files_only=True)
            for key, name in sorted((int(k), v) for k, v in config.id2label.items()):
                print(f"{key}\t{name}")
            return 0
        if args.verbose and args.backend == "segmentation":
            log("Chargement du modèle de segmentation…", args)
        run(args, segmenter=native)
    except ValueError as error:
        print(f"[ERREUR] {error}", file=sys.stderr)
        return 2
    finally:
        if native is not None:
            native.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
