#!/usr/bin/env bash
# Invoked with the validated pipeline configuration exported in the environment.
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
# Overrides used by the per-model camera split pass.
selected="${SFM_RECONSTRUCT_IMAGES:-$OUTPUT_DIR/images}"
sfm="${SFM_RECONSTRUCT_DIR:-$OUTPUT_DIR/sfm}"
camera_mode="${SFM_RECONSTRUCT_CAMERA_MODE:-$SFM_CAMERA_MODE}"
mask_args=()
if [[ "$SFM_MASKS" == on ]]; then
  mask_dir="$OUTPUT_DIR/feature_masks"
  mask_dir="${SFM_RECONSTRUCT_MASKS:-$mask_dir}"
  mask_args=(--feature-masks "$mask_dir")
fi

case "$COLMAP_METHOD" in
  spirula)
    prepare_args=(--no-prepare)
    [[ "${SFM_PREPARE_IMAGES:-false}" != true ]] || prepare_args=(--prepare)
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
    reuse_matches=redo
    [[ "${PIPELINE_RESUME:-false}" != true ]] || reuse_matches=auto
    sequence_args=(--data-type individual --sequence=)
    if [[ "$SFM_SP_PAIRS" == sequential ]]; then
      sequence_args=(--data-type video --sequence .)
    fi
    bash "$repo_root/tools/libs/scripts/run_spirula_sfm.sh" \
      --spirula-bin "$SPIRULA_BIN" --dataset "$selected" --output-dir "$sfm" \
      --preset video "${prepare_args[@]}" "${sequence_args[@]}" \
      --pairs "$SFM_SP_PAIRS" --quality "$SFM_SP_QUALITY" \
      --features "$SFM_SP_FEATURES" --matcher "$SFM_SP_MATCHER" \
      --max-image-size "$SFM_MAX_IMAGE_SIZE" --aliked-max-features "$SFM_ALIKED_MAX_FEATURES" \
      --max-error "$SFM_MAX_ERROR" --min-inliers "$SFM_MIN_INLIERS" \
      --min-tri-angle "$SFM_MIN_TRI_ANGLE" --ba-loss "$SFM_SP_BA_LOSS" \
      --ba-loss-param "$SFM_SP_BA_LOSS_PARAM" --reuse-matches "$reuse_matches" \
      --camera-mode "$camera_mode" --camera-model "$SFM_CAMERA_MODEL" \
      --metric-gps "$metric_gps" ${sensor_args[@]+"${sensor_args[@]}"} \
      --threads "$NUM_CPUS" ${mask_args[@]+"${mask_args[@]}"} \
      ${exposure_args[@]+"${exposure_args[@]}"}
    ;;
  colmap|nerfstudio)
    "$PYTHON_BIN" "$repo_root/tools/images_to_colmap/reconstruct.py"
    ;;
  hloc)
    projection_args=()
    if [[ "$HLOC_CAMERA_MODEL" == EQUIRECTANGULAR ]]; then
      projection_args=(--perspective-view-size "$SFM_EQUIRECTANGULAR_VIEW_SIZE")
    fi
    "$PYTHON_BIN" "$repo_root/tools/images_to_colmap/hloc_reconstruct.py" \
      --images "$selected" --output "$sfm/sparse/0" --work "$sfm/hloc" \
      --features "$HLOC_FEATURES" --matcher "$HLOC_MATCHER" --pairs "$HLOC_PAIRS" \
      --retrieval "$HLOC_RETRIEVAL" --num-neighbors "$HLOC_NUM_NEIGHBORS" \
      --camera-model "$HLOC_CAMERA_MODEL" --camera-mode "$camera_mode" \
      ${mask_args[@]+"${mask_args[@]}"} ${projection_args[@]+"${projection_args[@]}"}
    ;;
  orientations)
    "$PYTHON_BIN" "$repo_root/tools/images_to_colmap/orientations_to_colmap.py" \
      --orientations "$ORIENTATIONS_JSON" --points "$LIDAR_PLY" \
      --images "$selected" --output "$sfm/sparse/0"
    ;;
esac
