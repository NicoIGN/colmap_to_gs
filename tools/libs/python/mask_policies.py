"""Shared segmentation policies for SfM and Gaussian Splat training."""

import json
import math
import os
from pathlib import Path
import subprocess


def enabled(value):
    return value in ("on", "true", "1", "yes")


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
        "dilate": float(dilate), "max_fraction": float(env[f"{prefix}_MAX_FRACTION"]),
        "device": env[f"{prefix}_SPIRULA_DEVICE" if native else f"{prefix}_DEVICE"],
        "python": env["PYTHON_BIN"] if native else env[f"{prefix}_PYTHON"],
    }
    if not (0 <= settings["confidence"] <= 1 and
            0 <= settings["dilate"] < 0.1 and
            0 < settings["max_fraction"] <= 1 and
            all(math.isfinite(settings[key])
                for key in ("confidence", "dilate", "max_fraction"))):
        raise ValueError(f"Seuils {prefix} invalides")
    return settings


def generate(settings, images, output):
    command = [
        settings["python"],
        str(Path(__file__).resolve().parents[3] / "tools/segmentation_masks/generate_masks.py"),
        "--backend", settings["backend"], "--model", settings["model"],
        "--images", str(images), "--output", str(output),
        "--labels", ",".join(settings["labels"]),
        "--confidence", str(settings["confidence"]),
        "--dilate-fraction", str(settings["dilate"]),
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
