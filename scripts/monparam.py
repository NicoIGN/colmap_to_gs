#!/usr/bin/env python3
"""Décrit les paramètres des pipelines à partir de config/defaults et des config.default.sh."""

import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_DIR = ROOT / "config" / "defaults"
PIPELINE_OVERRIDES = {
    "panoramax": ROOT / "tools" / "panoramax" / "config.default.sh",
    "images_to_splat": ROOT / "tools" / "images_to_splat" / "config.default.sh",
}

PHASES = {
    "commun": "utilisé par plusieurs phases",
    "download": "recherche et téléchargement Panoramax",
    "sfm": "SfM Spirula",
    "masques": "masques SfM et/ou entraînement",
    "split": "découpage en blocs",
    "train": "entraînement 3DGS des blocs",
    "postprocess": "nettoyage des splats",
    "export": "fusion et export .splat",
    "slurm": "exécution Slurm et parallélisme",
    "lidar": "LiDAR optionnel",
}

COMMON_KEYS = {
    "OUTPUT_DIR", "TRAIN_BACKEND", "SPIRULA_BIN", "PYTHON_BIN", "MVK_CONFIG_LOG_LEVEL",
    "SS_NN_LOG", "NUM_CPUS", "NUM_GPUS", "SPIRULA_DEVICE", "MASKS_ACCEPT_LICENSE",
}
SEGMENTATION_ONLY_KEYS = {
    "TRAIN_MASKS_MODEL", "TRAIN_MASKS_PYTHON", "TRAIN_MASKS_DEVICE",
    "SFM_MASKS_MODEL", "SFM_MASKS_PYTHON", "SFM_MASKS_DEVICE",
}
SPIRULA_MODEL_KEYS = {"TRAIN_MASKS_SPIRULA_MODEL", "SFM_MASKS_SPIRULA_MODEL"}

ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$")
INTERVAL = re.compile(r"[\[\]]\s*-?[\d.]+\s*,\s*-?[\d.]+\s*[\[\]]")


class Param:
    def __init__(self, key, default, comment):
        self.key = key
        self.default = default
        self.comment = comment
        self.defaults = {}

    def default_text(self):
        values = set(self.defaults.values()) or {self.default}
        if len(values) == 1:
            return next(iter(values)) or "(vide)"
        return " ; ".join(f"{src}={val or '(vide)'}" for src, val in self.defaults.items())


def parse_value(raw):
    raw = raw.strip()
    quoted = re.match(r'^"([^"]*)"', raw)
    if quoted:
        return quoted.group(1)
    return raw.split(" #")[0].strip()


def load_params(path):
    if not path.is_file():
        sys.exit(f"Fichier introuvable : {path}")
    params = {}
    block = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            if text and not re.match(r"^(=+|-{3,}.*)$", text):
                block.append(text)
            continue
        match = ASSIGNMENT.match(stripped)
        if match:
            key, raw = match.groups()
            params[key] = Param(key, parse_value(raw), list(block))
        block = []
    return params


def load_all():
    base = {}
    for path in sorted(DEFAULTS_DIR.glob("*.default.sh")):
        base.update(load_params(path))
    merged = {key: Param(key, param.default, param.comment) for key, param in base.items()}
    for source, path in PIPELINE_OVERRIDES.items():
        overrides = load_params(path)
        for key, param in overrides.items():
            if key not in merged:
                merged[key] = Param(key, param.default, param.comment)
            elif not merged[key].comment:
                merged[key].comment = param.comment
        for key in set(base) | set(overrides):
            if key in overrides:
                merged[key].defaults[source] = overrides[key].default
            else:
                merged[key].defaults[source] = base[key].default
    return merged


def phases_of(key):
    if key in COMMON_KEYS:
        return ["commun"]
    if key == "COLMAP_METHOD" or key.startswith("HLOC_"):
        return ["sfm"]
    if key == "PANORAMAX_MULTI_CAM":
        return ["download", "sfm"]
    if key.startswith("PANORAMAX_"):
        return ["download"]
    if key.startswith("SFM_MASKS"):
        return ["sfm", "masques"]
    if key.startswith("SFM_"):
        return ["sfm"]
    if key.startswith("TRAIN_MASKS"):
        return ["masques", "train"]
    if key.startswith("TRAIN_"):
        return ["train"]
    if key.startswith("SPLIT_"):
        return ["split"]
    if key in {"EXPORT_SPLAT_NAME", "MERGE_STRENGTH"}:
        return ["export"]
    if key.startswith(("POSTPROCESS_", "CLEANGS_")):
        return ["postprocess"]
    if key.startswith("SLURM_"):
        return ["slurm"]
    if key.startswith("LIDAR_"):
        return ["lidar"]
    return ["commun"]


def backends_of(key):
    if key == "COLMAP_METHOD":
        return ["tous"]
    if key.startswith("HLOC_"):
        return ["hloc"]
    if key in {"SFM_MASKS", "SFM_CAMERA_MODE", "SFM_CAMERA_MODEL"}:
        return ["spirula", "hloc"]
    if key == "TRAIN_BACKEND":
        return ["spirula", "nerfstudio"]
    if key.startswith("TRAIN_NS_"):
        return ["nerfstudio"]
    if key.startswith(("TRAIN_MASKS", "SFM_MASKS")):
        if key in SPIRULA_MODEL_KEYS:
            return ["spirula"]
        if key in SEGMENTATION_ONLY_KEYS:
            return ["segmentation"]
        return ["segmentation", "spirula"]
    if key.startswith(("SFM_", "TRAIN_SP_", "SPIRULA_", "SS_NN_LOG", "MVK_CONFIG_LOG_LEVEL")):
        return ["spirula"]
    if key == "MASKS_ACCEPT_LICENSE":
        return ["spirula"]
    if key.startswith("CLEANGS_") or key == "POSTPROCESS_CLEANER":
        return ["cleangs"]
    return ["tous"]


def summary(param):
    text = " ".join(param.comment)
    if not text:
        return "(pas de commentaire dans config/defaults ou config.default.sh)"
    first = re.split(r"(?<=\.)\s", text, maxsplit=1)[0]
    return first if len(first) <= 110 else first[:107] + "..."


def show_param(param):
    text = " ".join(param.comment) or "(pas de commentaire dans config/defaults ou config.default.sh)"
    intervals = INTERVAL.findall(text)
    print(param.key)
    print(f"  Phases    : {', '.join(phases_of(param.key))}")
    print(f"  Backends  : {', '.join(backends_of(param.key))}")
    print(f"  Défaut    : {param.default_text()}")
    print(f"  Intervalle: {', '.join(intervals) if intervals else 'aucun intervalle explicite'}")
    print("  Rôle et valeurs :")
    for line in text.split(". "):
        print(f"    {line.rstrip('.')}.")


def show_unknown(params, name):
    print(f"Paramètre inconnu : {name}")
    matches = difflib.get_close_matches(name, params.keys(), n=5, cutoff=0.3)
    contains = [k for k in params if name in k and k not in matches]
    suggestions = matches + contains[:5]
    if suggestions:
        print("Paramètres les plus proches :")
        for key in suggestions:
            print(f"  {key} : {summary(params[key])}")
    return 1


def show_phase(params, phase):
    if phase not in PHASES:
        print(f"Phase inconnue : {phase}. Phases : {', '.join(PHASES)}")
        return 1
    print(f"Phase {phase} ({PHASES[phase]}), commun inclus :")
    for key, param in params.items():
        phases = phases_of(key)
        if phase in phases or "commun" in phases:
            scope = "toutes phases" if "commun" in phases else ", ".join(phases)
            print(f"  {key} = {param.default_text()}  [{scope}]")
            print(f"      {summary(param)}")
    return 0


def list_phases():
    print("Phases disponibles :")
    for phase, label in PHASES.items():
        print(f"  {phase} : {label}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Décrit les paramètres Panoramax et images_to_splat.")
    parser.add_argument("params", nargs="*", help="nom(s) de paramètre, ex. TRAIN_MASKS_MAX_FRACTION")
    parser.add_argument("--phase", help="liste les paramètres applicables à une phase")
    args = parser.parse_args(argv)

    if args.phase:
        return show_phase(load_all(), args.phase.lower())
    if not args.params:
        list_phases()
        return 0

    params = load_all()
    status = 0
    for name in args.params:
        key = name.upper()
        if key in params:
            show_param(params[key])
        else:
            status = show_unknown(params, key)
        print()
    return status


if __name__ == "__main__":
    sys.exit(main())
