#!/usr/bin/env bash
# Backend Spirula : appelle tools/sfm/spirula/sfm_spirula.sh avec la config exportée.
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
prepare_args=(--no-prepare)
[[ "${SFM_PREPARE_IMAGES:-false}" != true ]] || prepare_args=(--prepare)
[[ "${SFM_SP_PRESET:-}" != panoramax ]] || prepare_args=()
exposure_args=()
[[ -z "$SFM_SP_IMAGE_EXPOSURE" ]] ||
  exposure_args=(--image-exposure "$SFM_SP_IMAGE_EXPOSURE")
metric_gps=none
# Sans inputs.json (Panoramax), Spirula choisit le mode GPS lui-même.
[[ -f "$OUTPUT_DIR/inputs.json" ]] || metric_gps=auto
sensor_args=(--no-sensor-pairs)
if [[ "$metric_gps" == auto ]]; then sensor_args=(); fi
camera_model_args=()
[[ -z "$SFM_CAMERA_MODEL" ]] || camera_model_args=(--camera-model "$SFM_CAMERA_MODEL")
pair_args=()
if [[ -n "${SFM_SP_PAIR_MAX_DISTANCE:-}" ]]; then
  pair_args=(--max-distance "$SFM_SP_PAIR_MAX_DISTANCE"
    --metadata-dir "${SFM_SP_METADATA_DIR:?SFM_SP_METADATA_DIR requis avec SFM_SP_PAIR_MAX_DISTANCE}")
fi
positions_args=()
if [[ -n "$SFM_SP_METRIC_POSITIONS" ]]; then
  positions_args=(--metric-positions "$SFM_SP_METRIC_POSITIONS")
  echo "[COLMAP] Spirula, positions métriques : $SFM_SP_METRIC_POSITIONS (GPS EXIF ignoré)."
elif [[ -z "$GEOREF_GPX_FILE" && -f "$OUTPUT_DIR/inputs.json" ]]; then
  metric_gps="$("$PYTHON_BIN" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["gps_exif"]["metric_gps"])' \
    "$OUTPUT_DIR/inputs.json")"
  [[ "$metric_gps" == none ]] || sensor_args=(--sensor-pairs --sensor-pair-radius 30)
fi
[[ -n "$SFM_SP_METRIC_POSITIONS" ]] ||
  echo "[COLMAP] Spirula, GPS EXIF=$metric_gps ; GPX configuré=${GEOREF_GPX_FILE:-non}."
reuse_matches=redo
[[ "${PIPELINE_RESUME:-false}" != true ]] || reuse_matches=auto
reuse_matches="${SFM_SP_REUSE_MATCHES:-$reuse_matches}"
reuse_features_args=()
[[ -z "${SFM_SP_REUSE_FEATURES:-}" ]] || reuse_features_args=(--reuse-features "$SFM_SP_REUSE_FEATURES")
source_dir="${SFM_RECONSTRUCT_SOURCE:-$SFM_BACKEND_IMAGES}"
images_dir_args=()
[[ -z "${SFM_SP_IMAGES_DIR:-}" ]] || images_dir_args=(--images-dir "$SFM_SP_IMAGES_DIR")
sequence_args=(--data-type individual --sequence=)
if [[ "$SFM_SP_PAIRS" == sequential ]]; then
  sequence_args=(--data-type video --sequence .)
fi
mask_args=()
[[ -z "$SFM_BACKEND_MASK_DIR" ]] || mask_args=(--feature-masks "$SFM_BACKEND_MASK_DIR")
bash "$repo_root/tools/sfm/spirula/sfm_spirula.sh" \
  --spirula-bin "$SPIRULA_BIN" --dataset "$source_dir" --output-dir "$SFM_BACKEND_DIR" \
  ${images_dir_args[@]+"${images_dir_args[@]}"} \
  --preset "$SFM_SP_PRESET" ${prepare_args[@]+"${prepare_args[@]}"} "${sequence_args[@]}" \
  --pairs "$SFM_SP_PAIRS" --quality "$SFM_SP_QUALITY" \
  --features "$SFM_SP_FEATURES" --matcher "$SFM_SP_MATCHER" \
  --max-image-size "$SFM_MAX_IMAGE_SIZE" --aliked-max-features "$SFM_ALIKED_MAX_FEATURES" \
  --prefilter-neighbors "${SFM_SP_PREFILTER_NEIGHBORS:-16}" \
  --max-error "$SFM_MAX_ERROR" --min-inliers "$SFM_MIN_INLIERS" \
  --min-tri-angle "$SFM_MIN_TRI_ANGLE" --ba-loss "$SFM_SP_BA_LOSS" \
  --ba-loss-param "$SFM_SP_BA_LOSS_PARAM" --reuse-matches "$reuse_matches" \
  ${reuse_features_args[@]+"${reuse_features_args[@]}"} \
  --camera-mode "$SFM_BACKEND_CAMERA_MODE" ${camera_model_args[@]+"${camera_model_args[@]}"} \
  --metric-gps "$metric_gps" ${sensor_args[@]+"${sensor_args[@]}"} \
  ${positions_args[@]+"${positions_args[@]}"} ${pair_args[@]+"${pair_args[@]}"} \
  --threads "$NUM_CPUS" ${mask_args[@]+"${mask_args[@]}"} \
  ${exposure_args[@]+"${exposure_args[@]}"}
