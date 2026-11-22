#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
script_dir="$repo_root/tools/images_to_splat"
usage() { echo "Usage : bash tools/images_to_colmap/run.sh CONFIG [--images DIR]"; }
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then usage; exit 0; fi
[[ $# -ge 1 ]] || { usage >&2; exit 1; }
config="$1"; shift
images=""
if (($#)); then
  [[ $# == 2 && "$1" == --images && -n "$2" ]] || { usage >&2; exit 1; }
  images="$2"
fi
source "$script_dir/common.sh"
load_config "$config" "$images"
if [[ "$COLMAP_METHOD" == nerfstudio ]]; then
  "$PYTHON_BIN" "$repo_root/tools/libs/python/nerfstudio_runtime.py"
fi
mkdir -p "$OUTPUT_DIR/logs"
lock="$OUTPUT_DIR/.colmap.lock"
lock_helper="$repo_root/tools/libs/python/pipeline_lock.py"
"$PYTHON_BIN" "$lock_helper" "$lock" --pid "$$" --resume "$PIPELINE_RESUME" --ignore "$REMOVE_LOCKS" --workspace "$OUTPUT_DIR"
trap '"$PYTHON_BIN" "$lock_helper" "$lock" --pid "$$" --release' EXIT
source "$repo_root/tools/libs/scripts/pipeline_report.sh"
pipeline_report_start "$OUTPUT_DIR" "images_to_colmap"
colmap_exit() {
  local status=$?
  trap - EXIT
  pipeline_report_finish "$status" || { [[ "$status" != 0 ]] || status=1; }
  "$PYTHON_BIN" "$lock_helper" "$lock" --pid "$$" --release
  exit "$status"
}
trap colmap_exit EXIT
trap 'status=$?; echo "[ERREUR] Production COLMAP : $stage (code $status)." >&2; exit "$status"' ERR
begin_stage() {
  stage="$1"
  pipeline_report_phase "$stage"
  echo "[étape $(date '+%H:%M:%S')] $stage"
}
sfm="$OUTPUT_DIR/sfm"
selected="$OUTPUT_DIR/images"
sfm_masks="$OUTPUT_DIR/feature_masks"
train_masks="$OUTPUT_DIR/train_masks"
blocks="$OUTPUT_DIR/blocks"
sparse="$sfm/sparse/0"
[[ "$COLMAP_USE_MVS" != true ]] || sparse="$sfm/mvs/sparse/0"
sparse_ready() {
  if [[ "$PIPELINE_RESUME" == true ]]; then
    local status=0
    "$PYTHON_BIN" "$script_dir/resume.py" "$sfm/sparse/0" || status=$?
    ((status < 2)) || die "Impossible de valider le sparse ; aucun recalcul lancé."
    return "$status"
  else
    [[ -s "$sfm/sparse/0/cameras.bin" && -s "$sfm/sparse/0/images.bin" &&
       -s "$sfm/sparse/0/points3D.bin" ]]
  fi
}

prepare() {
  begin_stage "sélection des images"
  "$PYTHON_BIN" "$script_dir/prepare_inputs.py"
  rm -f -- "$OUTPUT_DIR/.prepare_done"
  feature_args=()
  if [[ "$SFM_MASKS" == on || "$TRAIN_MASKS" == on ]]; then
    begin_stage "masques SfM / entraînement"
    "$PYTHON_BIN" "$script_dir/prepare_masks.py"
    [[ "$SFM_MASKS" != on ]] || feature_args=(--feature-masks "$sfm_masks")
  fi
  camera_split="$repo_root/tools/images_to_colmap/camera_split.py"
  if [[ "$COLMAP_METHOD" == spirula ]]; then
    "$PYTHON_BIN" "$camera_split" recover --output "$OUTPUT_DIR"
  fi
  if ! sparse_ready; then
    if [[ "$PIPELINE_RESUME" == true ]]; then
      for downstream in georef sparse_filtered sparse_clean sparse_rejected blocks gs gs-nerfstudio; do
        [[ ! -e "$OUTPUT_DIR/$downstream" ]] ||
          die "Sparse incomplet mais sorties $downstream déjà présentes : choisir un nouvel OUTPUT_DIR."
      done
      "$PYTHON_BIN" "$script_dir/resume.py" "$sfm/sparse/0" --archive-sparse
    fi
    begin_stage "reconstruction COLMAP ($COLMAP_METHOD)"
    bash "$repo_root/tools/images_to_colmap/reconstruct.sh"
    if [[ "$PIPELINE_RESUME" == true ]]; then
      "$PYTHON_BIN" "$script_dir/resume.py" "$sfm/sparse/0"
    fi
  fi
  for name in cameras images points3D; do
    [[ -s "$sfm/sparse/0/$name.bin" ]] || die "SfM incomplet : $name.bin"
  done
  if [[ "$COLMAP_METHOD" == spirula && "$SFM_CAMERA_SPLIT" == true ]]; then
    split_args=(--output "$OUTPUT_DIR" --min-images "$SFM_CAMERA_SPLIT_MIN_IMAGES")
    split_status=0
    if [[ "$SFM_MASKS" == on ]]; then
      "$PYTHON_BIN" "$camera_split" plan "${split_args[@]}" --masks "$sfm_masks" || split_status=$?
    else
      "$PYTHON_BIN" "$camera_split" plan "${split_args[@]}" || split_status=$?
    fi
    if ((split_status == 0)); then
      begin_stage "SfM avec une caméra par modèle initial"
      SFM_RECONSTRUCT_IMAGES="$OUTPUT_DIR/sfm_cameras/images" \
        SFM_RECONSTRUCT_DIR="$OUTPUT_DIR/sfm_cameras/sfm" \
        SFM_RECONSTRUCT_MASKS="$OUTPUT_DIR/sfm_cameras/masks" \
        SFM_RECONSTRUCT_CAMERA_MODE=folder \
        bash "$repo_root/tools/images_to_colmap/reconstruct.sh"
      "$PYTHON_BIN" "$camera_split" finish "${split_args[@]}"
      "$PYTHON_BIN" "$script_dir/resume.py" "$sfm/sparse/0"
    elif ((split_status != 3)); then
      die "Séparation des caméras impossible."
    fi
  fi
  if [[ "$COLMAP_METHOD" != spirula ]]; then
    "$PYTHON_BIN" "$script_dir/resume.py" "$sfm/sparse/0"
  fi
  if [[ "$COLMAP_USE_MVS" == true ]]; then
    begin_stage "densification MVS"
    if [[ ! -s "$sfm/mvs/sparse/0/points3D.bin" ]]; then
      "$PYTHON_BIN" "$repo_root/tools/images_to_colmap/mvs_densify.py" \
        --colmap "$SFM_COLMAP_BIN" --images "$selected" \
        --sparse "$sfm/sparse/0" --output "$sfm/mvs" \
        --max-image-size "$MVS_MAX_IMAGE_SIZE" --gpu-index "$MVS_GPU_INDEX"
    fi
    for name in cameras images points3D; do
      [[ -s "$sfm/mvs/sparse/0/$name.bin" ]] || die "MVS incomplet : $name.bin"
    done
  fi
}
begin_stage "initialisation"
prepare
"$PYTHON_BIN" - "$OUTPUT_DIR" "$sparse" <<'PYINFO'
import json, sys
from pathlib import Path
output, sparse = map(Path, sys.argv[1:])
record = {"images": "images", "sparse_model": str(sparse.relative_to(output))}
(output / "colmap_dataset.json").write_text(json.dumps(record, indent=2) + "\n")
PYINFO
touch "$OUTPUT_DIR/.colmap_done"
echo "[COLMAP] Dataset : $OUTPUT_DIR/colmap_dataset.json"
