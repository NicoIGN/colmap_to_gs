#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${TRAIN_MASKS_INSTALL_DIR:-$HOME/.local/share/colmap-to-gs/segmentation-masks}"
python_bin="${TRAIN_MASKS_INSTALL_PYTHON:-python3}"

# Modèle généraliste conservé comme solution de repli.
model_id="nvidia/segformer-b5-finetuned-ade-640-640"
# Modèle spécialisé pour les scènes urbaines extérieures (Mapillary Vistas).
mapillary_model_id="facebook/mask2former-swin-large-mapillary-vistas-semantic"
labels="sky"

die() { printf '[ERREUR] Installation masques : %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: bash tools/segmentation_masks/install.sh [--install-dir PATH] [--python PATH]
                                                [--model HF_ID] [--mapillary-model HF_ID]
                                                [--labels LISTE]

Crée un environnement Python isolé (torch, transformers, Pillow, SciPy),
télécharge deux modèles de segmentation en local, écrit un labels.txt pour
chacun et vérifie que les labels demandés existent.

Modèle généraliste : nvidia/segformer-b5-finetuned-ade-640-640 (ADE20K).
Modèle urbain : facebook/mask2former-swin-large-mapillary-vistas-semantic
(Mapillary Vistas, scènes extérieures au niveau de la rue, 65 classes dont Sky).

Le modèle généraliste reste le modèle actif dans la configuration générée.
Pour tester Mapillary, une ligne de remplacement TRAIN_MASKS_MODEL est affichée.

Python >= 3.9, accès réseau (Hugging Face, PyPI) et espace disque suffisant
pour les deux modèles sont nécessaires.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir|--python|--model|--mapillary-model|--labels)
      [[ $# -ge 2 && -n "$2" ]] || die "Valeur manquante pour $1"
      case "$1" in
        --install-dir) install_dir="$2" ;;
        --python) python_bin="$2" ;;
        --model) model_id="$2" ;;
        --mapillary-model) mapillary_model_id="$2" ;;
        --labels) labels="$2" ;;
      esac
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "Argument inconnu : $1" ;;
  esac
done

validate_model_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
    die "Identifiant de modèle invalide : $1"
}
validate_model_id "$model_id"
validate_model_id "$mapillary_model_id"
command -v "$python_bin" >/dev/null || die "Python introuvable : $python_bin"
"$python_bin" -c 'import sys; assert sys.version_info[:2] >= (3, 9), "Python >= 3.9 requis"' ||
  die "Python >= 3.9 requis"

mkdir -p "$install_dir"
install_dir="$(cd -- "$install_dir" && pwd)"
venv="$install_dir/venv"
[[ -d "$venv" ]] || "$python_bin" -m venv "$venv"
runtime="$venv/bin/python"

"$runtime" -m pip install --upgrade pip
# PyPI torch wheels include CUDA on Linux x86_64 and MPS on macOS arm64.
"$runtime" -m pip install 'torch>=2.1,<2.9' 'transformers==4.46.3' 'safetensors' \
  'pillow' 'scipy' 'numpy>=1.24,<2'

# Older macOS torch wheels use the NumPy 1.x ABI; check both bridge directions.
"$runtime" -c 'import numpy as np, torch
a = np.zeros((2, 2), dtype=np.float32)
assert np.array_equal(torch.from_numpy(a).numpy(), a)' ||
  die "Le pont NumPy/PyTorch ne fonctionne pas dans $venv."

model_dir="$install_dir/models/${model_id//\//--}"
mapillary_model_dir="$install_dir/models/${mapillary_model_id//\//--}"

"$runtime" - "$model_id" "$model_dir" "$mapillary_model_id" "$mapillary_model_dir" "$labels" "$script_dir" <<'PY'
import gc
import sys
from pathlib import Path

from transformers import AutoConfig, AutoImageProcessor
from transformers import AutoModelForSemanticSegmentation, AutoModelForUniversalSegmentation

model_specs = [
    (sys.argv[1], Path(sys.argv[2]), "généraliste ADE20K"),
    (sys.argv[3], Path(sys.argv[4]), "urbain Mapillary Vistas"),
]
labels = sys.argv[5]
script_dir = sys.argv[6]
sys.path.insert(0, script_dir)
from generate_masks import parse_labels, resolve_label_ids


def install_model(model_id: str, target: Path, description: str) -> None:
    print(f"[installation] Vérification du modèle {description} : {model_id}", flush=True)
    target.mkdir(parents=True, exist_ok=True)
    has_weights = (target / "model.safetensors").is_file() or (target / "pytorch_model.bin").is_file()
    complete = (target / "config.json").is_file() and has_weights

    if not complete:
        print(f"[installation] Téléchargement de {model_id}...", flush=True)
        processor = AutoImageProcessor.from_pretrained(model_id)
        try:
            model = AutoModelForSemanticSegmentation.from_pretrained(model_id)
        except ValueError:
            model = AutoModelForUniversalSegmentation.from_pretrained(model_id)
        target.mkdir(parents=True, exist_ok=True)
        processor.save_pretrained(target)
        model.save_pretrained(target, safe_serialization=True)
        del model, processor
        gc.collect()

    config = AutoConfig.from_pretrained(target, local_files_only=True)
    id2label = {int(k): v for k, v in config.id2label.items()}
    (target / "labels.txt").write_text(
        "".join(f"{key}\t{name}\n" for key, name in sorted(id2label.items())),
        encoding="utf-8",
    )
    ids = resolve_label_ids(id2label, parse_labels(labels))
    AutoImageProcessor.from_pretrained(target, local_files_only=True)
    print(f"[installation] Modèle local : {target} ({len(id2label)} labels)")
    print(f"[installation] Labels demandés : {', '.join(id2label[i] for i in ids)}")


for model_id, target, description in model_specs:
    install_model(model_id, target, description)
PY

device="$("$runtime" -c 'import torch
print("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")')"
printf '[installation] Accélérateur détecté ici : %s\n' "$device"
[[ "$device" != cpu ]] ||
  printf '[AVERTISSEMENT] Pas de GPU ici : la génération restera possible mais lente.\n'

printf '\nÀ ajouter dans config.sh (modèle actuel conservé) :\n'
printf 'TRAIN_MASKS="on"\nTRAIN_MASKS_BACKEND="segmentation"\nTRAIN_MASKS_PYTHON="%s"\nTRAIN_MASKS_MODEL="%s"\n' "$runtime" "$model_dir"
printf 'TRAIN_MASKS_LABELS="%s"\nTRAIN_MASKS_MODE="auto"\n' "$labels"
printf '\nPour tester le modèle urbain Mapillary Vistas, remplacer TRAIN_MASKS_MODEL par :\n'
printf 'TRAIN_MASKS_MODEL="%s"\n' "$mapillary_model_dir"
printf '\nLabels généralistes : %s/labels.txt\n' "$model_dir"
printf 'Labels Mapillary : %s/labels.txt\n' "$mapillary_model_dir"
