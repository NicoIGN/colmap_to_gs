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
mkdir "$lock" 2>/dev/null || die "Production COLMAP déjà verrouillée : $lock"
trap 'rmdir "$lock"' EXIT
trap 'status=$?; echo "[ERREUR] Production COLMAP : $stage (code $status)." >&2; exit "$status"' ERR
sfm="$OUTPUT_DIR/sfm"
selected="$OUTPUT_DIR/images"
sfm_masks="$OUTPUT_DIR/feature_masks"
train_masks="$OUTPUT_DIR/train_masks"
blocks="$OUTPUT_DIR/blocks"
sparse="$sfm/sparse/0"
[[ -z "$GEOREF_GPX_FILE" ]] || sparse="$OUTPUT_DIR/georef/0"
filter_input="$sparse"
[[ "$SFM_FILTER_DENSITY" != true ]] || sparse="$OUTPUT_DIR/sparse_clean"
check_filtered_sparse() {
  [[ -f "$sparse/.density_done" && -s "$sparse/density_report.json" ]] || die "Filtrage sparse incomplet."
  "$PYTHON_BIN" "$script_dir/resume.py" "$sparse"
}
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
  stage="sélection des images"
  "$PYTHON_BIN" "$script_dir/prepare_inputs.py"
  rm -f -- "$OUTPUT_DIR/.prepare_done"
  feature_args=()
  if [[ "$SFM_MASKS" == on || "$TRAIN_MASKS" == on ]]; then
    stage="masques SfM / entraînement"
    "$PYTHON_BIN" "$script_dir/prepare_masks.py"
    if [[ -d "$OUTPUT_DIR/masks" ]]; then
      sfm_masks="$OUTPUT_DIR/masks"
      train_masks="$OUTPUT_DIR/masks"
    fi
    [[ "$SFM_MASKS" != on ]] || feature_args=(--feature-masks "$sfm_masks")
  fi
  if ! sparse_ready; then
    if [[ "$PIPELINE_RESUME" == true ]]; then
      for downstream in georef sparse_filtered sparse_clean sparse_rejected blocks gs gs-nerfstudio; do
        [[ ! -e "$OUTPUT_DIR/$downstream" ]] ||
          die "Sparse incomplet mais sorties $downstream déjà présentes : choisir un nouvel OUTPUT_DIR."
      done
    fi
    stage="reconstruction COLMAP ($COLMAP_METHOD)"
    bash "$repo_root/tools/images_to_colmap/reconstruct.sh"
    if [[ "$PIPELINE_RESUME" == true ]]; then
      "$PYTHON_BIN" "$script_dir/resume.py" "$sfm/sparse/0"
    fi
  fi
  for name in cameras images points3D; do
    [[ -s "$sfm/sparse/0/$name.bin" ]] || die "SfM incomplet : $name.bin"
  done
  if [[ "$COLMAP_METHOD" != spirula ]]; then
    "$PYTHON_BIN" "$script_dir/resume.py" "$sfm/sparse/0"
  fi
  if [[ -n "$GEOREF_GPX_FILE" ]]; then
    stage="recalage du SfM sur le GPX"
    "$PYTHON_BIN" "$script_dir/georeference.py" prepare
  fi
  if [[ "$SFM_FILTER_DENSITY" == true ]]; then
    stage="filtrage du sparse par densité"
    if [[ ! -f "$sparse/.density_done" ]]; then
      [[ ! -e "$blocks" && ! -e "$OUTPUT_DIR/gs" && ! -e "$OUTPUT_DIR/gs-nerfstudio" ]] ||
        die "Filtrage absent mais blocs/train présents : choisir un nouvel OUTPUT_DIR."
      "$PYTHON_BIN" "$repo_root/tools/preprocess/filter_sparse_density.py" \
        --input "$filter_input" --output "$OUTPUT_DIR/sparse" \
        --workers "$SFM_THREADS" --verbose --no-progress
      "$PYTHON_BIN" "$script_dir/resume.py" "$sparse"
      touch "$sparse/.density_done"
    else
      check_filtered_sparse
    fi
  fi
}
stage="initialisation"
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
