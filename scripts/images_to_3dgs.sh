#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script_dir="$repo_root/tools/libs"
source "$repo_root/tools/export/export.sh"
source "$repo_root/tools/libs/scripts/redo.sh"
source "$repo_root/tools/georef/georef.sh"
source "$repo_root/tools/sfm/sparse_density.sh"

usage() {
  echo "Usage : bash scripts/images_to_3dgs.sh CONFIG [--images DIR] [--validate-only] [--phase all|prepare|mask-setup|mask-sfm|mask-train|train-block|merge] [--block INDEX] [--redo]"
}
[[ "${1:-}" != --help && "${1:-}" != -h ]] || { usage; exit 0; }
[[ $# -ge 1 ]] || { usage >&2; exit 1; }
config="$1"; shift
source "$repo_root/tools/libs/scripts/shell_helpers.sh"
reject_grid_params "$config"
images=""; phase=all; block=""; validate_only=false; redo=false
while (($#)); do
  case "$1" in
    --validate-only) validate_only=true; shift ;;
    --redo) redo=true; shift ;;
    --images|--phase|--block)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 1; }
      case "$1" in
        --images) images="$2" ;;
        --phase) phase="$2" ;;
        --block) block="$2" ;;
      esac
      shift 2 ;;
    *) usage >&2; exit 1 ;;
  esac
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
if [[ "$redo" == true ]]; then
  redo_check_phase "$phase"
fi
command -v "$PYTHON_BIN" >/dev/null || die "Python introuvable : $PYTHON_BIN"
lidar_pipeline_hook preflight
if [[ "$TRAIN_BACKEND" == nerfstudio ]]; then
  if [[ "$phase" == all || "$phase" == train-block ]]; then
    "$PYTHON_BIN" "$repo_root/tools/libs/python/nerfstudio_runtime.py"
  fi
else
  command -v "$SPIRULA_BIN" >/dev/null || die "Spirula introuvable : $SPIRULA_BIN"
fi

if [[ "$validate_only" == true ]]; then
  lidar_pipeline_hook preflight
  for prefix in SFM_MASKS TRAIN_MASKS; do
    [[ "${!prefix}" == on ]] || continue
    dir_key="${prefix}_DIR"
    [[ -z "${!dir_key}" ]] || continue
    backend_key="${prefix}_BACKEND"
    backend="${!backend_key}"
    if [[ "$backend" == spirula ]]; then
      mask_python="$PYTHON_BIN"
      command -v "$SPIRULA_BIN" >/dev/null ||
        die "Spirula requis pour les masques mais introuvable : $SPIRULA_BIN"
    else
      python_key="${prefix}_PYTHON"
      mask_python="${!python_key}"
    fi
    command -v "$mask_python" >/dev/null ||
      die "Python des masques introuvable : $mask_python"
    imports='import numpy; from PIL import Image'
    required_modules="numpy, Pillow"
    if [[ "$backend" == segmentation ]]; then
      imports+='; import torch, transformers'
      required_modules+=", torch, transformers"
    fi
    dilate_key="${prefix}_DILATE"
    dilate="${!dilate_key}"
    needs_scipy=false
    if [[ "$dilate" == auto ]]; then
      if [[ "$prefix" == SFM_MASKS ]]; then
        needs_scipy=true
      elif [[ "$TRAIN_MASKS_MODE" == empty ]]; then
        needs_scipy=false
      elif [[ "$TRAIN_MASKS_MODE" == auto &&
              "$(printf '%s' "$TRAIN_MASKS_LABELS" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')" == sky ]]; then
        needs_scipy=false
      else
        needs_scipy=true
      fi
    elif awk -v value="$dilate" 'BEGIN { exit !(value > 0) }'; then
      needs_scipy=true
    fi
    if [[ "$needs_scipy" == true ]]; then
      imports+='; import scipy'
      required_modules+=", scipy"
    fi
    "$mask_python" -c "$imports" >/dev/null 2>&1 ||
      die "Dépendances Python manquantes pour les masques ($backend, $mask_python). Vérifier $required_modules."
  done
  echo "[prévalidation] Configuration, Spirula/modèle et dépendances des masques disponibles."
  exit 0
fi

mkdir -p "$OUTPUT_DIR/logs"
lock="$OUTPUT_DIR/.${phase}-${block:-all}.lock"
lock_helper="$repo_root/tools/libs/python/pipeline_lock.py"
"$PYTHON_BIN" "$lock_helper" "$lock" --pid "$$" --resume "$PIPELINE_RESUME" --ignore "$REMOVE_LOCKS" --workspace "$OUTPUT_DIR"
trap '"$PYTHON_BIN" "$lock_helper" "$lock" --pid "$$" --release' EXIT
source "$repo_root/tools/libs/scripts/pipeline_report.sh"
pipeline_report_start "$OUTPUT_DIR" "images_to_3dgs:$phase:${block:-all}"
pipeline_exit() {
  local status=$?
  trap - EXIT
  pipeline_report_finish "$status" || { [[ "$status" != 0 ]] || status=1; }
  "$PYTHON_BIN" "$lock_helper" "$lock" --pid "$$" --release ||
    echo "[AVERTISSEMENT] Verrou conservé : $lock" >&2
  exit "$status"
}
trap pipeline_exit EXIT
set_stage() { stage="$1"; pipeline_report_phase "$stage"; }
stage="initialisation"
pipeline_report_phase "$stage"
trap 'status=$?; echo "[ERREUR] Étape $stage, code $status, ligne $LINENO" >&2; exit "$status"' ERR
exec > >(tee -a "$OUTPUT_DIR/logs/${phase}-${block:-all}.log") 2>&1
export PYTHONUNBUFFERED=1
export POSTPROCESS_FILTER_MODE="${POSTPROCESS_FILTER_MODE:-apply}"
sfm="$OUTPUT_DIR/sfm"
sfm_masks="$OUTPUT_DIR/feature_masks"
train_masks="$OUTPUT_DIR/train_masks"
blocks="$OUTPUT_DIR/blocks"
gs_root="$OUTPUT_DIR/gs"
[[ "$TRAIN_BACKEND" != nerfstudio ]] || gs_root="$OUTPUT_DIR/gs-nerfstudio"
selected="$OUTPUT_DIR/images"
sfm_model="$sfm/sparse/0"
[[ "$COLMAP_USE_MVS" != true ]] || sfm_model="$sfm/mvs/sparse/0"
# Modèle source du filtrage : géoréférencé si le recalage a réussi, sinon SfM.
georef_input() {
  if georef_applied; then
    echo "$OUTPUT_DIR/georef/0"
  else
    echo "$sfm_model"
  fi
}
# Renseigne sparse (entrée du découpage) et lidar_input (entrée du recalage LiDAR).
select_sparse() {
  sparse="$(georef_input)"
  if [[ "$SFM_FILTER_DENSITY" == true ]]; then
    sparse="$OUTPUT_DIR/sparse_clean"
  fi
  lidar_input="$sparse"
  if [[ "$LIDAR_COLMAP_ALIGN" == true ]]; then
    sparse="$OUTPUT_DIR/colmap_lidar"
  fi
}
select_sparse

check_filtered_sparse() {
  [[ -f "$lidar_input/.density_done" && -s "$lidar_input/density_report.json" ]] ||
    die "Filtrage sparse incomplet : relancer --phase prepare."
  "$PYTHON_BIN" "$repo_root/tools/sfm/resume.py" "$lidar_input"
}

select_models_for_blocks() {
  rm -f -- "$OUTPUT_DIR/.models_done"
  [[ -d "$sfm/sparse/1" ]] || return 0
  set_stage "sélection des modèles SfM"
  local total georef_available=true largest_only=false selected_models configs_text ws_config
  total="$(find "$selected" -mindepth 1 -maxdepth 1 \( -type f -o -type l \) | wc -l)"
  [[ "$(georef_resolved_mode)" != none ]] || { georef_available=false; largest_only=true; }
  local args=(--sparse-root "$sfm/sparse" --total-images "$total"
              --min-fraction "$SFM_PARTIAL_MIN_FRACTION" --min-images "$SFM_PARTIAL_MIN_IMAGES"
              --output "$sfm/representative_models.json")
  [[ "$largest_only" != true ]] || args+=(--largest-only)
  selected_models="$("$PYTHON_BIN" "$repo_root/tools/sfm/representative_models.py" "${args[@]}")"
  [[ -n "$selected_models" ]] ||
    die "Aucun modèle SfM représentatif (SFM_PARTIAL_MIN_FRACTION=$SFM_PARTIAL_MIN_FRACTION, SFM_PARTIAL_MIN_IMAGES=$SFM_PARTIAL_MIN_IMAGES)."
  [[ "$selected_models" != 0 ]] || return 0
  echo "[modèles] Blocs par modèle : $selected_models (géoréférencement : $georef_available)"
  configs_text="$("$PYTHON_BIN" "$repo_root/tools/sfm/model_workspaces.py" \
    --parent "$OUTPUT_DIR" --config "$config" --indices "$selected_models")"
  while IFS= read -r ws_config; do
    set_stage "pipeline du sous-workspace $(dirname -- "$ws_config")"
    PIPELINE_MODEL_WORKSPACE=true bash "$repo_root/scripts/images_to_3dgs.sh" "$ws_config" \
      --images "$SFM_IMAGES_DIR" --phase all
  done <<< "$configs_text"
  touch "$OUTPUT_DIR/.models_done"
  echo "[modèles] Terminé : $OUTPUT_DIR/models/{$selected_models}"
  exit 0
}

prepare() {
  if [[ "${PIPELINE_MODEL_WORKSPACE:-false}" != true ]]; then
    set_stage "production COLMAP"
    bash "$repo_root/tools/sfm/run_standard_images.sh" "$config" --images "$SFM_IMAGES_DIR"
    select_models_for_blocks
  fi
  set_stage "géoréférencement"
  georef_run "$sfm_model"
  georef_lidar_env
  if [[ "$SFM_FILTER_DENSITY" == true ]]; then
    set_stage "filtrage de densité du sparse"
    sparse_density_stage "$(georef_input)" "$OUTPUT_DIR/sparse" "$blocks" "$gs_root"
  fi
  select_sparse
  if [[ "$LIDAR_COLMAP_ALIGN" == true ]]; then
    [[ ! -s "$blocks/manifest.json" || -f "$OUTPUT_DIR/lidar/colmap_inputs.json" ]] ||
      die "Activer LiDAR nécessite un nouveau workspace avant découpage."
    set_stage "recalage et enrichissement COLMAP LiDAR"
    lidar_pipeline_hook prepare --workspace "$OUTPUT_DIR" --model "$lidar_input" --images "$selected"
  fi
  "$PYTHON_BIN" "$repo_root/tools/sfm/resume.py" "$OUTPUT_DIR" --partition prepare
  if [[ ! -s "$blocks/manifest.json" ]]; then
    set_stage "découpage COLMAP"
    "$PYTHON_BIN" "$repo_root/tools/sfm/colmap_split_blocks.py" \
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
  set_stage "préparation des entrées pour les masques"
  "$PYTHON_BIN" "$repo_root/tools/prepare/prepare_images.py" --phase mask-setup
  exit 0
elif [[ "$phase" == mask-sfm || "$phase" == mask-train ]]; then
  set_stage "vérification des entrées pour les masques"
  [[ -f "$OUTPUT_DIR/inputs.json" && -d "$selected" ]] ||
    die "Entrées des masques non préparées : relancer le lanceur Slurm."
  set_stage "calcul des masques ${phase#mask-}"
  policy="${phase#mask-}"
  "$PYTHON_BIN" "$script_dir/../masks/masks.py" --policy "$policy"
  exit 0
else
  [[ ! -f "$OUTPUT_DIR/.models_done" || "${PIPELINE_MODEL_WORKSPACE:-false}" == true ]] ||
    die "Blocs par modèle : les phases par bloc s'appliquent aux sous-workspaces models/N (lancer --phase all sur le workspace parent)."
  set_stage "vérification du workspace"
  "$PYTHON_BIN" "$repo_root/tools/prepare/prepare_images.py" --check --phase train
  "$PYTHON_BIN" "$repo_root/tools/sfm/resume.py" "$OUTPUT_DIR" --partition check
  georef_check "$sfm_model"
  select_sparse
  if [[ "$SFM_FILTER_DENSITY" == true ]]; then
    check_filtered_sparse
  fi
  lidar_pipeline_hook check --workspace "$OUTPUT_DIR" --model "$lidar_input" --images "$selected"
fi
[[ "$phase" != prepare ]] || exit 0

if [[ "$redo" == true ]]; then
  [[ "$phase" != merge ]] || redo_check_blocks "$gs_root" "$SPLIT_NUM_BLOCKS"
  set_stage "nettoyage des sorties à refaire ($phase)"
  redo_clean_merge_outputs "$OUTPUT_DIR" "$gs_root" "$EXPORT_SPLAT_NAME" "$SPLIT_NUM_BLOCKS"
fi

if [[ "$phase" == all || "$phase" == train-block ]]; then
  first="${block:-0}"; last="$SPLIT_NUM_BLOCKS"
  [[ "$phase" != train-block ]] || last=$((block + 1))
  for ((index=first; index<last; index++)); do
    printf -v id 'block_%03d' "$index"
    set_stage "entraînement $id"
    args=(--sparse-dir "$blocks/$id" --images-dir "$selected" --output-dir "$gs_root/$id"
          --spirula-bin "$SPIRULA_BIN" --python-bin "$PYTHON_BIN"
          --export-filter-mode "${POSTPROCESS_FILTER_MODE:-apply}" --name "$id.splat")
    [[ "$redo" != true ]] || args+=(--clean)
    [[ -z "${POSTPROCESS_SHAPE_MAX_ASPECT_RATIO:-}" ]] ||
      args+=(--shape-max-aspect-ratio "$POSTPROCESS_SHAPE_MAX_ASPECT_RATIO" --shape-isolation-factor "$POSTPROCESS_SHAPE_ISOLATION_FACTOR")
    [[ "$TRAIN_MASKS" != on ]] || args+=(--masks-dir "$train_masks" --mask-mode "$TRAIN_MASKS_MODE")
    bash "$repo_root/tools/training/train_block.sh" "${args[@]}"
  done
fi
[[ "$phase" != train-block ]] || exit 0

finish_block_splats "$gs_root" "$SPLIT_NUM_BLOCKS" "$OUTPUT_DIR/$EXPORT_SPLAT_NAME" \
  images_to_3dgs "$OUTPUT_DIR/georef/0/georef_stats.json"
echo "[TERMINE] $OUTPUT_DIR/$EXPORT_SPLAT_NAME"
