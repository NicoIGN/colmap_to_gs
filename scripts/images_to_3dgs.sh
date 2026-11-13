#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script_dir="$repo_root/tools/images_to_splat"
source "$repo_root/tools/libs/scripts/pipeline_postprocess.sh"

usage() {
  echo "Usage : bash scripts/images_to_3dgs.sh CONFIG [--images DIR] [--phase all|prepare|mask-setup|mask-sfm|mask-train|train-block|merge] [--block INDEX]"
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
case "$phase" in all|prepare|mask-setup|mask-sfm|mask-train|train-block|merge) ;; *) die "Étape inconnue : $phase" ;; esac
if [[ "$phase" == train-block ]]; then
  [[ "$block" =~ ^(0|[1-9][0-9]*)$ ]] && ((block < SPLIT_NUM_BLOCKS)) ||
    die "--block doit être compris entre 0 et $((SPLIT_NUM_BLOCKS - 1))."
elif [[ -n "$block" ]]; then
  die "--block nécessite --phase train-block."
fi
command -v "$PYTHON_BIN" >/dev/null || die "Python introuvable : $PYTHON_BIN"
if [[ "$TRAIN_BACKEND" == nerfstudio ]]; then
  if [[ "$phase" == all || "$phase" == train-block ]]; then
    "$PYTHON_BIN" "$repo_root/tools/libs/python/nerfstudio_runtime.py"
  fi
else
  command -v "$SPIRULA_BIN" >/dev/null || die "Spirula introuvable : $SPIRULA_BIN"
fi

mkdir -p "$OUTPUT_DIR/logs"
lock="$OUTPUT_DIR/.${phase}-${block:-all}.lock"
mkdir "$lock" 2>/dev/null || die "Étape déjà verrouillée : $lock"
trap 'rmdir "$lock" || echo "[AVERTISSEMENT] Verrou conservé : $lock" >&2' EXIT
stage="initialisation"
trap 'status=$?; echo "[ERREUR] Étape $stage, code $status, ligne $LINENO" >&2; exit "$status"' ERR
exec > >(tee -a "$OUTPUT_DIR/logs/${phase}-${block:-all}.log") 2>&1
export PYTHONUNBUFFERED=1
sfm="$OUTPUT_DIR/sfm"
sfm_masks="$OUTPUT_DIR/feature_masks"
train_masks="$OUTPUT_DIR/train_masks"
blocks="$OUTPUT_DIR/blocks"
gs_root="$OUTPUT_DIR/gs"
[[ "$TRAIN_BACKEND" != nerfstudio ]] || gs_root="$OUTPUT_DIR/gs-nerfstudio"
selected="$OUTPUT_DIR/images"
sparse="$sfm/sparse/0"
if [[ -n "$GEOREF_GPX_FILE" ]]; then
  sparse="$OUTPUT_DIR/georef/0"
fi
filter_input="$sparse"
if [[ "$SFM_FILTER_DENSITY" == true ]]; then
  sparse="$OUTPUT_DIR/sparse_clean"
fi

check_filtered_sparse() {
  [[ -f "$sparse/.density_done" && -s "$sparse/density_report.json" ]] ||
    die "Filtrage sparse incomplet : relancer --phase prepare."
  "$PYTHON_BIN" "$script_dir/resume.py" "$sparse"
}

prepare() {
  stage="production COLMAP"
  bash "$repo_root/tools/images_to_colmap/run.sh" "$config" --images "$SFM_IMAGES_DIR"
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
elif [[ "$phase" == mask-setup ]]; then
  stage="préparation des entrées pour les masques"
  "$PYTHON_BIN" "$script_dir/prepare_inputs.py"
  exit 0
elif [[ "$phase" == mask-sfm || "$phase" == mask-train ]]; then
  stage="vérification des entrées pour les masques"
  [[ -f "$OUTPUT_DIR/inputs.json" && -d "$selected" ]] ||
    die "Entrées des masques non préparées : relancer le lanceur Slurm."
  stage="calcul des masques ${phase#mask-}"
  policy="${phase#mask-}"
  "$PYTHON_BIN" "$script_dir/prepare_masks.py" --policy "$policy"
  exit 0
else
  stage="vérification du workspace"
  "$PYTHON_BIN" "$script_dir/prepare_inputs.py" --check
  if [[ -n "$GEOREF_GPX_FILE" ]]; then
    "$PYTHON_BIN" "$script_dir/georeference.py" check
  fi
  if [[ "$SFM_FILTER_DENSITY" == true ]]; then
    check_filtered_sparse
  fi
fi
[[ "$phase" != prepare ]] || exit 0
if [[ -d "$OUTPUT_DIR/masks" ]]; then
  train_masks="$OUTPUT_DIR/masks"
fi

if [[ "$phase" == all || "$phase" == train-block ]]; then
  first="${block:-0}"; last="$SPLIT_NUM_BLOCKS"
  [[ "$phase" != train-block ]] || last=$((block + 1))
  for ((index=first; index<last; index++)); do
    printf -v id 'block_%03d' "$index"
    stage="entraînement $id"
    args=(--sparse-dir "$blocks/$id" --images-dir "$selected" --output-dir "$gs_root/$id"
          --spirula-bin "$SPIRULA_BIN" --python-bin "$PYTHON_BIN"
          --export-filter-mode "${POSTPROCESS_FILTER_MODE:-off}" --name "$id.splat")
    [[ -z "${POSTPROCESS_SHAPE_MAX_ASPECT_RATIO:-}" ]] ||
      args+=(--shape-max-aspect-ratio "$POSTPROCESS_SHAPE_MAX_ASPECT_RATIO" --shape-isolation-factor "$POSTPROCESS_SHAPE_ISOLATION_FACTOR")
    [[ "$TRAIN_MASKS" != on ]] || args+=(--masks-dir "$train_masks" --mask-mode "$TRAIN_MASKS_MODE")
    bash "$repo_root/tools/libs/scripts/run_3dgs_block.sh" "${args[@]}"
  done
fi
[[ "$phase" != train-block ]] || exit 0

inputs=()
for ((index=0; index<SPLIT_NUM_BLOCKS; index++)); do
  printf -v id 'block_%03d' "$index"
  raw="$gs_root/$id/splats/$id.splat"
  [[ -s "$raw" ]] || die "Splat brut absent : $raw"
  [[ -f "$gs_root/$id/.export_done" ]] || die "Export du bloc incomplet : $id"
  stage="recadrage COLMAP $id avant fusion"
  if [[ "$POSTPROCESS_SPARSE_BBOX_MODE" != off ]]; then
    cropped="$gs_root/$id/splats/$id.bbox.splat"
    crop_splat_to_sparse "$raw" "$cropped" "$blocks/$id" \
      "$POSTPROCESS_SPARSE_BBOX_MODE" "$POSTPROCESS_SPARSE_BBOX_MARGIN" \
      --max-removal-fraction "$POSTPROCESS_MAX_REMOVAL_FRACTION"
    inputs+=("$cropped")
  else
    inputs+=("$raw")
  fi
done
stage="fusion des blocs"
merge_pipeline_splats "$OUTPUT_DIR/$EXPORT_SPLAT_NAME" "$MERGE_STRENGTH" "${inputs[@]}"
if [[ -n "$GEOREF_GPX_FILE" ]]; then
  stage="export de la référence géographique du splat"
  "$PYTHON_BIN" "$script_dir/georeference.py" export
fi
echo "[TERMINE] $OUTPUT_DIR/$EXPORT_SPLAT_NAME"
