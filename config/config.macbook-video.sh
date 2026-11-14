#!/usr/bin/env bash
# Configuration prudente pour traiter cette vidéo en local sur MacBook Pro.
# Spirula Studio est supposé installé dans ../spirula-studio à côté du dépôt.
_config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
_repo_root="$(cd -- "$_config_dir/.." && pwd)"
SPIRULA_ROOT="${SPIRULA_ROOT:-$(dirname -- "$_repo_root")/spirula-studio}"
if [[ -z "${SPIRULA_BIN:-}" ]]; then
  for _spirula_candidate in "$SPIRULA_ROOT"/build*/spirula; do
    if [[ -x "$_spirula_candidate" ]]; then
      SPIRULA_BIN="$_spirula_candidate"
      break
    fi
  done
  SPIRULA_BIN="${SPIRULA_BIN:-$SPIRULA_ROOT/build/spirula}"
fi
unset _spirula_candidate
unset _config_dir _repo_root

PYTHON_BIN="${PYTHON_BIN:-python3}"
SFM_IMAGES_DIR="/Users/nbellaiche/Documents/temp/IGNF/frames"
SFM_IMAGES_RECURSIVE="true"
OUTPUT_DIR="/Users/nbellaiche/Documents/temp/IGNF/Video_20260515_1755_16_064_3dgs"

COLMAP_METHOD="spirula"
TRAIN_BACKEND="spirula"

# Masquer le ciel pour le SfM et le considérer comme vide à l'entraînement.
# Le masque est calculé une seule fois car les politiques SfM/train sont identiques.
SFM_MASKS="on"
TRAIN_MASKS="on"
TRAIN_MASKS_BACKEND="spirula"
TRAIN_MASKS_SPIRULA_MODEL="${TRAIN_MASKS_SPIRULA_MODEL:-$SPIRULA_ROOT/models/sam3-q4_0.ggml}"
TRAIN_MASKS_LABELS="sky"
TRAIN_MASKS_MODE="empty"
TRAIN_MASKS_SPIRULA_DEVICE="auto"
MASKS_JOBS="1"
# Passer à true uniquement après avoir accepté la licence SAM 3.
MASKS_ACCEPT_LICENSE="false"
export SS_NN_LOG=1

# Réduire la charge mémoire et CPU pour un traitement local.
SFM_MAX_IMAGE_SIZE="2048"
SFM_ALIKED_MAX_FEATURES="4096"
SFM_THREADS="6"
SFM_SP_QUALITY="medium"
SFM_SP_PAIRS="prefilter"

SPLIT_NUM_BLOCKS="2"
SPLIT_OVERLAP_MAX_FRACTION="0.10"
TRAIN_NUM_ITER="12000"
TRAIN_SP_PRESET="3dgs"
TRAIN_SP_QUALITY="high"
TRAIN_SP_CAP_MAX="500000"
TRAIN_SP_REFINE_STOP_ITER="10000"
TRAIN_SP_REFINE_STOP_NUM_ITER="2000"
TRAIN_SP_FLOATER_SUPPRESSION="mild"

TRAIN_CLEAN="false"
POSTPROCESS_SPARSE_BBOX_MODE="apply"
POSTPROCESS_MAX_REMOVAL_FRACTION="0.20"
EXPORT_SPLAT_NAME="Video_20260515_1755_16_064_3dgs.splat"
