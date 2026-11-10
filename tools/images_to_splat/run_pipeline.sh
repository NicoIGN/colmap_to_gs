#!/usr/bin/env bash
set -Eeuo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"

usage() {
  echo "Usage : bash tools/images_to_splat/run_pipeline.sh CONFIG [--images DIR] [--phase all|prepare|train-block|merge] [--block INDEX]"
}
[[ "${1:-}" != --help && "${1:-}" != -h ]] || { usage; exit 0; }
[[ $# -ge 1 ]] || { usage >&2; exit 1; }
config="$1"; shift
images=""; phase=all; block=""
while (($#)); do
  [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 1; }
  case "$1" in
    --images) images="$2" ;;
    --phase) phase="$2" ;;
    --block) block="$2" ;;
    *) usage >&2; exit 1 ;;
  esac
  shift 2
done
source "$script_dir/common.sh"
load_config "$config" "$images"
case "$phase" in all|prepare|train-block|merge) ;; *) die "Étape inconnue : $phase" ;; esac
if [[ "$phase" == train-block ]]; then
  [[ "$block" =~ ^(0|[1-9][0-9]*)$ ]] && ((block < SPLIT_NUM_BLOCKS)) ||
    die "--block doit être compris entre 0 et $((SPLIT_NUM_BLOCKS - 1))."
elif [[ -n "$block" ]]; then
  die "--block nécessite --phase train-block."
fi
command -v "$PYTHON_BIN" >/dev/null || die "Python introuvable : $PYTHON_BIN"
command -v "$SPIRULA_BIN" >/dev/null || die "Spirula introuvable : $SPIRULA_BIN"

mkdir -p "$OUTPUT_DIR/logs"
lock="$OUTPUT_DIR/.${phase}-${block:-all}.lock"
mkdir "$lock" 2>/dev/null || die "Étape déjà verrouillée : $lock"
trap 'rmdir "$lock" || echo "[AVERTISSEMENT] Verrou conservé : $lock" >&2' EXIT
stage="initialisation"
trap 'status=$?; echo "[ERREUR] Étape $stage, code $status, ligne $LINENO" >&2; exit "$status"' ERR
exec > >(tee -a "$OUTPUT_DIR/logs/${phase}-${block:-all}.log") 2>&1
export PYTHONUNBUFFERED=1
sfm="$OUTPUT_DIR/sfm"
masks="$OUTPUT_DIR/masks"
blocks="$OUTPUT_DIR/blocks"
selected="$OUTPUT_DIR/images"
sparse="$sfm/sparse/0"
if [[ -n "$GEOREF_GPX_FILE" ]]; then
  sparse="$OUTPUT_DIR/georef/0"
fi

prepare() {
  stage="sélection des images"
  "$PYTHON_BIN" "$script_dir/prepare_inputs.py"
  rm -f -- "$OUTPUT_DIR/.prepare_done"
  feature_args=()
  if [[ "$SFM_MASKS" == on || "$TRAIN_MASKS" == on ]]; then
    stage="masques partagés SfM / entraînement"
    license_args=()
    [[ "$MASKS_ACCEPT_LICENSE" != true ]] || license_args+=(--accept-spirula-license)
    "$PYTHON_BIN" "$repo_root/tools/segmentation_masks/generate_masks.py" \
      --images "$selected" --output "$masks" --backend spirula \
      --spirula-bin "$SPIRULA_BIN" --model "$MASKS_SPIRULA_MODEL" \
      --spirula-device "$MASKS_SPIRULA_DEVICE" --labels "$MASKS_LABELS" \
      --confidence "$MASKS_CONFIDENCE" --dilate-fraction "$MASKS_DILATE" \
      --max-masked-fraction "$MASKS_MAX_FRACTION" "${license_args[@]}"
    [[ "$SFM_MASKS" != on ]] || feature_args=(--feature-masks "$masks")
  fi
  if [[ ! -s "$sfm/sparse/0/cameras.bin" || ! -s "$sfm/sparse/0/images.bin" ||
        ! -s "$sfm/sparse/0/points3D.bin" ]]; then
    stage="reconstruction SfM"
    bash "$repo_root/scripts/run_spirula_sfm.sh" \
      --spirula-bin "$SPIRULA_BIN" --dataset "$selected" --output-dir "$sfm" \
      --preset video --no-prepare --data-type individual --sequence= \
      --pairs "$SFM_PAIRS" --features "$SFM_FEATURES" --matcher "$SFM_MATCHER" \
      --max-image-size "$SFM_MAX_IMAGE_SIZE" --aliked-max-features "$SFM_ALIKED_MAX_FEATURES" \
      --camera-mode "$SFM_CAMERA_MODE" --camera-model "$SFM_CAMERA_MODEL" \
      --metric-gps none --no-sensor-pairs --threads "$SFM_THREADS" "${feature_args[@]}"
  fi
  for name in cameras images points3D; do
    [[ -s "$sfm/sparse/0/$name.bin" ]] || die "SfM incomplet : $name.bin"
  done
  if [[ -n "$GEOREF_GPX_FILE" ]]; then
    stage="recalage du SfM sur le GPX"
    "$PYTHON_BIN" "$script_dir/georeference.py" prepare
  fi
  if [[ ! -s "$blocks/manifest.json" ]]; then
    stage="découpage COLMAP"
    "$PYTHON_BIN" "$repo_root/tools/preprocess/colmap_split_blocks.py" \
      --input "$sparse" --output "$blocks" --num-blocks "$SPLIT_NUM_BLOCKS" \
      --overlap-max-fraction "$SPLIT_OVERLAP_MAX_FRACTION" --export-binary
  fi
  "$PYTHON_BIN" - "$blocks/manifest.json" "$SPLIT_NUM_BLOCKS" <<'PY'
import json, sys
from pathlib import Path
manifest = Path(sys.argv[1])
if len(json.loads(manifest.read_text())["blocks"]) != int(sys.argv[2]):
    raise SystemExit("[ERREUR] Nombre de blocs incompatible")
for index in range(int(sys.argv[2])):
    for name in ("cameras", "images", "points3D"):
        path = manifest.parent / f"block_{index:03d}" / f"{name}.bin"
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f"[ERREUR] Bloc incomplet : {path}")
PY
  touch "$OUTPUT_DIR/.prepare_done"
}

if [[ "$phase" == all || "$phase" == prepare ]]; then
  prepare
else
  stage="vérification du workspace"
  "$PYTHON_BIN" "$script_dir/prepare_inputs.py" --check
  if [[ -n "$GEOREF_GPX_FILE" ]]; then
    "$PYTHON_BIN" "$script_dir/georeference.py" check
  fi
fi
[[ "$phase" != prepare ]] || exit 0

if [[ "$phase" == all || "$phase" == train-block ]]; then
  first="${block:-0}"; last="$SPLIT_NUM_BLOCKS"
  [[ "$phase" != train-block ]] || last=$((block + 1))
  for ((index=first; index<last; index++)); do
    printf -v id 'block_%03d' "$index"
    stage="entraînement $id"
    args=(--sparse-dir "$blocks/$id" --images-dir "$selected" --output-dir "$OUTPUT_DIR/gs/$id"
          --spirula-bin "$SPIRULA_BIN" --python-bin "$PYTHON_BIN"
          --export-filter-mode off --name "$id.splat")
    [[ "$TRAIN_MASKS" != on ]] || args+=(--masks-dir "$masks" --mask-mode "$TRAIN_MASKS_MODE")
    bash "$repo_root/scripts/run_spirula_block.sh" "${args[@]}"
  done
fi
[[ "$phase" != train-block ]] || exit 0

inputs=()
for ((index=0; index<SPLIT_NUM_BLOCKS; index++)); do
  printf -v id 'block_%03d' "$index"
  raw="$OUTPUT_DIR/gs/$id/splats/$id.splat"
  [[ -s "$raw" ]] || die "Splat brut absent : $raw"
  [[ -f "$OUTPUT_DIR/gs/$id/.export_done" ]] || die "Export du bloc incomplet : $id"
  stage="recadrage COLMAP $id avant fusion"
  if [[ "$POSTPROCESS_SPARSE_BBOX_MODE" != off ]]; then
    cropped="$OUTPUT_DIR/gs/$id/splats/$id.bbox.splat"
    "$PYTHON_BIN" "$repo_root/tools/postprocess/filter_splats.py" \
      --input "$raw" --output "$cropped" --mode off \
      --sparse-points "$sparse/points3D.bin" \
      --sparse-bbox-mode "$POSTPROCESS_SPARSE_BBOX_MODE" \
      --sparse-bbox-margin "$POSTPROCESS_SPARSE_BBOX_MARGIN" \
      --max-removal-fraction "$POSTPROCESS_MAX_REMOVAL_FRACTION" --overwrite
    inputs+=("$cropped")
  else
    inputs+=("$raw")
  fi
done
stage="fusion des blocs"
"$PYTHON_BIN" "$repo_root/tools/postprocess/merge_splats.py" "${inputs[@]}" \
  --output "$OUTPUT_DIR/$EXPORT_SPLAT_NAME" --strength "$MERGE_STRENGTH"
[[ -s "$OUTPUT_DIR/$EXPORT_SPLAT_NAME" ]] || die "Splat final absent ou vide"
if [[ -n "$GEOREF_GPX_FILE" ]]; then
  stage="export de la référence géographique du splat"
  "$PYTHON_BIN" "$script_dir/georeference.py" export
fi
echo "[TERMINE] $OUTPUT_DIR/$EXPORT_SPLAT_NAME"
