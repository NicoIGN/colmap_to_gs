#!/usr/bin/env bash
# Invoked with the validated pipeline configuration exported in the environment.
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
selected="$OUTPUT_DIR/images"
sfm="$OUTPUT_DIR/sfm"
mask_args=()
if [[ "$SFM_MASKS" == on ]]; then
  mask_dir="$OUTPUT_DIR/feature_masks"
  [[ ! -d "$OUTPUT_DIR/masks" ]] || mask_dir="$OUTPUT_DIR/masks"
  mask_args=(--feature-masks "$mask_dir")
fi

case "$COLMAP_METHOD" in
  spirula)
    exposure_args=()
    [[ -z "$SFM_SP_IMAGE_EXPOSURE" ]] ||
      exposure_args=(--image-exposure "$SFM_SP_IMAGE_EXPOSURE")
    metric_gps=none
    sensor_args=(--no-sensor-pairs)
    if [[ -z "$GEOREF_GPX_FILE" ]]; then
      metric_gps="$("$PYTHON_BIN" -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["gps_exif"]["metric_gps"])' \
        "$OUTPUT_DIR/inputs.json")"
      [[ "$metric_gps" == none ]] || sensor_args=(--sensor-pairs --sensor-pair-radius 30)
    fi
    echo "[COLMAP] Spirula, GPS EXIF=$metric_gps ; GPX configuré=${GEOREF_GPX_FILE:-non}."
    bash "$repo_root/tools/libs/scripts/run_spirula_sfm.sh" \
      --spirula-bin "$SPIRULA_BIN" --dataset "$selected" --output-dir "$sfm" \
      --preset video --no-prepare --data-type individual --sequence= \
      --pairs "$SFM_SP_PAIRS" --features "$SFM_SP_FEATURES" --matcher "$SFM_SP_MATCHER" \
      --max-image-size "$SFM_MAX_IMAGE_SIZE" --aliked-max-features "$SFM_ALIKED_MAX_FEATURES" \
      --max-error "$SFM_MAX_ERROR" --min-inliers "$SFM_MIN_INLIERS" \
      --min-tri-angle "$SFM_MIN_TRI_ANGLE" --ba-loss "$SFM_SP_BA_LOSS" \
      --ba-loss-param "$SFM_SP_BA_LOSS_PARAM" --reuse-matches redo \
      --camera-mode "$SFM_CAMERA_MODE" --camera-model "$SFM_CAMERA_MODEL" \
      --metric-gps "$metric_gps" "${sensor_args[@]}" \
      --threads "$SFM_THREADS" "${mask_args[@]}" \
      "${exposure_args[@]}"
    ;;
  colmap|nerfstudio)
    "$PYTHON_BIN" "$repo_root/tools/images_to_colmap/reconstruct.py"
    ;;
  orientations)
    "$PYTHON_BIN" "$repo_root/tools/images_to_colmap/orientations_to_colmap.py" \
      --orientations "$ORIENTATIONS_JSON" --points "$LIDAR_PLY" \
      --images "$selected" --output "$sfm/sparse/0"
    ;;
esac
