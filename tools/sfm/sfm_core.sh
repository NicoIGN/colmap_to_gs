#!/usr/bin/env bash
# Cœur SfM partagé par run_standard_images.sh (images, vidéo) et run_panoramax.sh.
# Les différences de source sont passées par l'environnement (voir sfm_reconstruct.sh).
# Usage : sfm_core.sh --sfm-dir DIR --images-dir DIR [--force]
#   --force : relance toujours la reconstruction (sinon, un sparse complet est réutilisé).
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
sfm=""
images=""
force=false
while (($#)); do
  case "$1" in
    --sfm-dir) sfm="$2"; shift 2 ;;
    --images-dir) images="$2"; shift 2 ;;
    --force) force=true; shift ;;
    *) echo "Usage : sfm_core.sh --sfm-dir DIR --images-dir DIR [--force]" >&2; exit 2 ;;
  esac
done
[[ -n "$sfm" && -n "$images" ]] || { echo "--sfm-dir et --images-dir sont obligatoires." >&2; exit 2; }
die() { echo "[ERREUR] $*" >&2; exit 1; }

PYTHON_BIN="${PYTHON_BIN:-python3}"
COLMAP_METHOD="${COLMAP_METHOD:-spirula}"
PIPELINE_RESUME="${PIPELINE_RESUME:-false}"
SFM_CAMERA_SPLIT="${SFM_CAMERA_SPLIT:-false}"
SFM_MASKS="${SFM_MASKS:-off}"
sparse="$sfm/sparse/0"
camera_split="$repo_root/tools/sfm/spirula/camera_split.py"

sparse_ready() {
  if [[ "$PIPELINE_RESUME" == true ]]; then
    local status=0
    "$PYTHON_BIN" "$repo_root/tools/images_to_splat/resume.py" "$sparse" || status=$?
    ((status < 2)) || die "Impossible de valider le sparse ; aucun recalcul lancé."
    return "$status"
  else
    [[ -s "$sparse/cameras.bin" && -s "$sparse/images.bin" && -s "$sparse/points3D.bin" ]]
  fi
}

reconstruct() {
  echo "[étape $(date '+%H:%M:%S')] reconstruction COLMAP ($COLMAP_METHOD)"
  SFM_RECONSTRUCT_IMAGES="$images" SFM_RECONSTRUCT_DIR="$sfm" \
    bash "$repo_root/tools/sfm/sfm_reconstruct.sh"
  if [[ "$PIPELINE_RESUME" == true ]]; then
    "$PYTHON_BIN" "$repo_root/tools/images_to_splat/resume.py" "$sparse"
  fi
}

if [[ "$COLMAP_METHOD" == spirula ]]; then
  "$PYTHON_BIN" "$camera_split" recover --output "$OUTPUT_DIR"
fi
if [[ "$force" == true ]]; then
  reconstruct
elif ! sparse_ready; then
  if [[ "$PIPELINE_RESUME" == true ]]; then
    for downstream in georef sparse_filtered sparse_clean sparse_rejected blocks gs gs-nerfstudio; do
      [[ ! -e "$OUTPUT_DIR/$downstream" ]] ||
        die "Sparse incomplet mais sorties $downstream déjà présentes : choisir un nouvel OUTPUT_DIR."
    done
    "$PYTHON_BIN" "$repo_root/tools/images_to_splat/resume.py" "$sparse" --archive-sparse
  fi
  reconstruct
fi
for name in cameras images points3D; do
  [[ -s "$sparse/$name.bin" ]] || die "SfM incomplet : $name.bin"
done

if [[ "$COLMAP_METHOD" == spirula && "$SFM_CAMERA_SPLIT" == true ]]; then
  split_args=(--output "$OUTPUT_DIR" --min-images "$SFM_CAMERA_SPLIT_MIN_IMAGES")
  split_status=0
  if [[ "$SFM_MASKS" == on ]]; then
    "$PYTHON_BIN" "$camera_split" plan "${split_args[@]}" --masks "$OUTPUT_DIR/feature_masks" || split_status=$?
  else
    "$PYTHON_BIN" "$camera_split" plan "${split_args[@]}" || split_status=$?
  fi
  if ((split_status == 0)); then
    echo "[étape $(date '+%H:%M:%S')] SfM avec une caméra par modèle initial"
    SFM_RECONSTRUCT_IMAGES="$OUTPUT_DIR/sfm_cameras/images" \
      SFM_RECONSTRUCT_DIR="$OUTPUT_DIR/sfm_cameras/sfm" \
      SFM_RECONSTRUCT_MASKS="$OUTPUT_DIR/sfm_cameras/masks" \
      SFM_RECONSTRUCT_CAMERA_MODE=folder \
      bash "$repo_root/tools/sfm/sfm_reconstruct.sh"
    "$PYTHON_BIN" "$camera_split" finish "${split_args[@]}"
    "$PYTHON_BIN" "$repo_root/tools/images_to_splat/resume.py" "$sparse"
  elif ((split_status != 3)); then
    die "Séparation des caméras impossible."
  fi
fi
if [[ "$COLMAP_METHOD" != spirula ]]; then
  "$PYTHON_BIN" "$repo_root/tools/images_to_splat/resume.py" "$sparse"
fi
