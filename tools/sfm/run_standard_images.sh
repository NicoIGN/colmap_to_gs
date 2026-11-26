#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
script_dir="$repo_root/tools/images_to_splat"
usage() { echo "Usage : bash tools/sfm/run_standard_images.sh CONFIG [--images DIR]"; }
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

prepare() {
  begin_stage "sélection des images"
  "$PYTHON_BIN" "$script_dir/prepare_images.py"
  rm -f -- "$OUTPUT_DIR/.prepare_done"
  feature_args=()
  if [[ "$SFM_MASKS" == on || "$TRAIN_MASKS" == on ]]; then
    begin_stage "masques SfM / entraînement"
    "$PYTHON_BIN" "$script_dir/../segmentation_masks/masks.py"
    [[ "$SFM_MASKS" != on ]] || feature_args=(--feature-masks "$sfm_masks")
  fi
  bash "$repo_root/tools/sfm/sfm_run.sh" --sfm-dir "$sfm" --images-dir "$selected"
  if [[ "$COLMAP_USE_MVS" == true ]]; then
    begin_stage "densification MVS"
    if [[ ! -s "$sfm/mvs/sparse/0/points3D.bin" ]]; then
      "$PYTHON_BIN" "$repo_root/tools/sfm/colmap/mvs_densify.py" \
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
