#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${TRAIN_MASKS_INSTALL_DIR:-$HOME/.local/share/colmap-to-gs/segmentation-masks}"
python_bin="${TRAIN_MASKS_INSTALL_PYTHON:-python3}"
model_id="nvidia/segformer-b5-finetuned-ade-640-640"
labels="sky"

die() { printf '[ERREUR] Installation masques : %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: bash tools/segmentation_masks/install.sh [--install-dir PATH] [--python PATH]
                                                [--model HF_ID] [--labels LISTE]

Crée un environnement Python isolé (torch, transformers, Pillow, SciPy),
télécharge le modèle de segmentation en local, écrit labels.txt et vérifie
que les labels demandés existent (défaut : sky).

Modèle par défaut : nvidia/segformer-b5-finetuned-ade-640-640 (ADE20K,
150 classes : sky, person, car, bicycle, tree...). Alternative rue :
facebook/mask2former-swin-large-mapillary-vistas-semantic (Mapillary Vistas).
Python >= 3.9, accès réseau (Hugging Face, PyPI) et quelques Go de disque.
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir|--python|--model|--labels)
      [[ $# -ge 2 && -n "$2" ]] || die "Valeur manquante pour $1"
      case "$1" in
        --install-dir) install_dir="$2" ;;
        --python) python_bin="$2" ;;
        --model) model_id="$2" ;;
        --labels) labels="$2" ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Argument inconnu : $1" ;;
  esac
done
[[ "$model_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "Identifiant de modèle invalide : $model_id"
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
"$runtime" - "$model_id" "$model_dir" "$labels" "$script_dir" <<'PY'
import sys
from pathlib import Path

from transformers import AutoConfig, AutoImageProcessor
from transformers import AutoModelForSemanticSegmentation, AutoModelForUniversalSegmentation

model_id, model_dir, labels, script_dir = sys.argv[1:]
sys.path.insert(0, script_dir)
from generate_masks import parse_labels, resolve_label_ids

target = Path(model_dir)
if not (target / "config.json").is_file():
    processor = AutoImageProcessor.from_pretrained(model_id)
    try:
        model = AutoModelForSemanticSegmentation.from_pretrained(model_id)
    except ValueError:
        model = AutoModelForUniversalSegmentation.from_pretrained(model_id)
    target.mkdir(parents=True, exist_ok=True)
    processor.save_pretrained(target)
    model.save_pretrained(target, safe_serialization=True)
config = AutoConfig.from_pretrained(target, local_files_only=True)
id2label = {int(k): v for k, v in config.id2label.items()}
(target / "labels.txt").write_text(
    "".join(f"{key}\t{name}\n" for key, name in sorted(id2label.items())), encoding="utf-8")
ids = resolve_label_ids(id2label, parse_labels(labels))
AutoImageProcessor.from_pretrained(target, local_files_only=True)
print(f"[installation] Modèle local : {target} ({len(id2label)} labels)")
print(f"[installation] Labels demandés : {', '.join(id2label[i] for i in ids)}")
PY

device="$("$runtime" -c 'import torch
print("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")')"
printf '[installation] Accélérateur détecté ici : %s\n' "$device"
[[ "$device" != cpu ]] ||
  printf '[AVERTISSEMENT] Pas de GPU ici : la génération restera possible mais lente.\n'

printf '\nÀ ajouter dans config.sh :\n'
printf 'TRAIN_MASKS="on"\nTRAIN_MASKS_BACKEND="segmentation"\nTRAIN_MASKS_PYTHON="%s"\nTRAIN_MASKS_MODEL="%s"\n' "$runtime" "$model_dir"
printf 'TRAIN_MASKS_LABELS="%s"\nTRAIN_MASKS_MODE="auto"\n' "$labels"
printf 'Labels disponibles : %s/labels.txt\n' "$model_dir"
