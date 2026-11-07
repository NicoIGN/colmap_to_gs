#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${CLEANGS_INSTALL_DIR:-$HOME/.local/share/colmap-to-gs/cleangs}"
python_bin="${CLEANGS_INSTALL_PYTHON:-python3}"
check_gpu=false

die() { printf '[ERREUR] Installation Clean-GS : %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: bash tools/clean_gs/install.sh [--install-dir PATH] [--python PATH] [--check-gpu]

Prépare un environnement isolé, Clean-GS et le modèle local SegFormer.
Nécessite Linux, Python 3.11/3.12, git, accès réseau et espace disque.
--check-gpu compile et teste aussi gsplat sur un GPU NVIDIA (CUDA 12.8,
nvcc et compilateur C++ requis). Sans cette option, ce test reste à effectuer
sur un nœud GPU avant le premier nettoyage.
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir|--python)
      [[ $# -ge 2 && -n "$2" ]] || die "Valeur manquante pour $1"
      if [[ "$1" == --install-dir ]]; then install_dir="$2"; else python_bin="$2"; fi
      shift 2 ;;
    --check-gpu) check_gpu=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Argument inconnu : $1" ;;
  esac
done
[[ "$(uname -s)" == Linux ]] || die "Linux requis ; ce backend ne fonctionne pas sur macOS."
command -v git >/dev/null || die "git introuvable."
command -v "$python_bin" >/dev/null || die "Python introuvable : $python_bin"
"$python_bin" -c 'import sys; assert (3,11) <= sys.version_info[:2] < (3,13), "Python 3.11 ou 3.12 requis"'
mkdir -p "$install_dir"
install_dir="$(cd -- "$install_dir" && pwd)"
bootstrap="$install_dir/installer-venv"
[[ -d "$bootstrap" ]] || "$python_bin" -m venv "$bootstrap"
"$bootstrap/bin/python" -m pip install 'uv==0.9.15'
uv="$bootstrap/bin/uv"
source_dir="$install_dir/source"
revision=78a39e941c14df5857903b1112040d117f230ac4
if [[ ! -e "$source_dir" ]]; then
  git clone https://github.com/smlab-niser/clean-gs.git "$source_dir"
  git -C "$source_dir" checkout --detach "$revision"
else
  [[ "$(git -C "$source_dir" rev-parse HEAD)" == "$revision" ]] ||
    die "Révision inattendue dans $source_dir ; choisir un autre dossier d'installation."
  [[ -z "$(git -C "$source_dir" status --porcelain)" ]] ||
    die "Modifications locales dans $source_dir ; aucune modification automatique."
fi
# The upstream lock selects CUDA 12.8 wheels and all pinned CleanGS dependencies.
"$uv" sync --project "$source_dir" --frozen --python "$python_bin"
runtime="$source_dir/.venv/bin/python"
"$uv" pip install --python "$runtime" 'transformers==4.46.3' pyproj
model_dir="$install_dir/models/segformer"
"$runtime" - "$model_dir" <<'PY'
import sys
from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation

model_id = "nvidia/segformer-b0-finetuned-ade-512-512"
processor = AutoImageProcessor.from_pretrained(model_id)
model = AutoModelForSemanticSegmentation.from_pretrained(model_id)
if not any(v.strip().lower() == "sky" for v in model.config.id2label.values()):
    raise ValueError("Classe sky absente du modèle")
processor.save_pretrained(sys.argv[1])
model.save_pretrained(sys.argv[1], safe_serialization=True)
AutoImageProcessor.from_pretrained(sys.argv[1], local_files_only=True)
AutoModelForSemanticSegmentation.from_pretrained(sys.argv[1], local_files_only=True)
from cleangs._vendor.gsplat_colmap.colmap import Parser
import cv2, gsplat, numpy, scipy, tqdm, plyfile, pyproj
print("[installation] Imports et modèle local vérifiés.")
PY
if [[ "$check_gpu" == true ]]; then
  "$runtime" "$script_dir/check_runtime.py"
else
  printf '[AVERTISSEMENT] CUDA non testé. Sur un nœud GPU, exécuter :\n'
  printf '%q %q\n' "$runtime" "$script_dir/check_runtime.py"
fi
printf '\nÀ ajouter dans config.sh :\n'
printf 'SPLAT_CLEANER="cleangs"\nCLEANGS_PYTHON="%s"\nCLEANGS_MODEL="%s"\n' "$runtime" "$model_dir"
printf 'CLEANGS_MODE="diagnostic"\nCLEANGS_MIN_VIEWS="2"\nCLEANGS_SKY_CONFIDENCE="0.9"\nCLEANGS_MAX_REMOVAL_FRACTION="0.2"\n'
