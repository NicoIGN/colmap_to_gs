#!/usr/bin/env bash
# Shared configuration primitives; sourced by the source-specific validators.

config_error() {
  printf '[ERREUR] Configuration : %s\n' "$*" >&2
  config_errors=$((config_errors + 1))
}

config_choice() {
  local name="$1" value="$2" allowed="$3"
  case "|$allowed|" in
    *"|$value|"*) ;;
    *) config_error "$name='$value' invalide ; valeurs autorisées : $allowed." ;;
  esac
}

config_positive_integer() {
  [[ "$2" =~ ^[1-9][0-9]*$ ]] ||
    config_error "$1='$2' doit être un entier strictement positif."
}

init_sfm_geometry_config() {
  local sfm_backend="${1:-${COLMAP_METHOD:-spirula}}"
  SFM_MAX_ERROR="${SFM_MAX_ERROR:-2}"
  SFM_MIN_INLIERS="${SFM_MIN_INLIERS:-30}"
  SFM_MIN_TRI_ANGLE="${SFM_MIN_TRI_ANGLE:-3}"
  if [[ "$sfm_backend" == spirula ]]; then
    SFM_SP_BA_LOSS="${SFM_SP_BA_LOSS:-huber}"
    SFM_SP_BA_LOSS_PARAM="${SFM_SP_BA_LOSS_PARAM:-2}"
  else
    SFM_SP_BA_LOSS="${SFM_SP_BA_LOSS:-}"
    SFM_SP_BA_LOSS_PARAM="${SFM_SP_BA_LOSS_PARAM:-}"
  fi
}

validate_sfm_geometry_config() {
  local sfm_backend="${1:-${COLMAP_METHOD:-spirula}}"
  local inliers="${SFM_MIN_INLIERS:-30}"
  [[ "$inliers" =~ ^[1-9][0-9]*$ ]] &&
    awk -v x="$inliers" 'BEGIN { exit !(x >= 4 && x <= 1000000) }' ||
    config_error "SFM_MIN_INLIERS doit être un entier dans [4,1000000]."
  if [[ "$sfm_backend" == spirula ]]; then
    config_choice SFM_SP_BA_LOSS "${SFM_SP_BA_LOSS:-huber}" 'trivial|huber|cauchy'
  elif [[ -n "${SFM_SP_BA_LOSS:-}" || -n "${SFM_SP_BA_LOSS_PARAM:-}" ]]; then
    config_error "SFM_SP_BA_LOSS* ne s'applique qu'à COLMAP_METHOD=spirula."
  fi
  local name value
  for name in SFM_MAX_ERROR SFM_MIN_TRI_ANGLE; do
    case "$name" in
      SFM_MAX_ERROR) value="${SFM_MAX_ERROR:-2}" ;;
      SFM_MIN_TRI_ANGLE) value="${SFM_MIN_TRI_ANGLE:-3}" ;;
    esac
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
      awk -v x="$value" -v name="$name" 'BEGIN {
        if (name == "SFM_MAX_ERROR") exit !(x >= 0.1 && x <= 100)
        exit !(x > 0 && x <= 90)
      }' || config_error "$name hors limites : erreur [0.1,100], angle ]0,90]."
  done
  if [[ "$sfm_backend" == spirula ]]; then
    local loss_scale="${SFM_SP_BA_LOSS_PARAM:-2}"
    [[ "$loss_scale" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
      awk -v x="$loss_scale" 'BEGIN { exit !(x > 0 && x <= 1000) }' ||
      config_error "SFM_SP_BA_LOSS_PARAM hors limites : échelle ]0,1000]."
  fi
}

validate_spirula_photometric_config() {
  local train_backend="${1:-spirula}" sfm_backend="${2:-spirula}"
  local name value
  for name in TRAIN_SP_USE_PPISP TRAIN_SP_USE_BILATERAL_GRID \
    TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF; do
    value="${!name:-}"
    [[ -z "$value" ]] || config_choice "$name" "$value" 'true|false'
    if [[ -n "$value" && "$train_backend" != spirula ]]; then
      config_error "$name est propre à Spirula et exige TRAIN_BACKEND=spirula."
    fi
  done
  value="${TRAIN_SP_PPISP_PARAM_TYPE:-}"
  [[ -z "$value" ]] || config_choice TRAIN_SP_PPISP_PARAM_TYPE "$value" \
    'original|rqs|no_crf|no_crf_clamp|no_crf_no_vig|no_crf_no_vig_clamp'
  [[ -z "$value" || "$train_backend" == spirula ]] ||
    config_error "TRAIN_SP_PPISP_PARAM_TYPE est propre à Spirula et exige TRAIN_BACKEND=spirula."
  value="${TRAIN_SP_BILAGRID_TYPE:-}"
  [[ -z "$value" ]] || config_choice TRAIN_SP_BILAGRID_TYPE "$value" \
    'affine|ppisp|loglinear'
  [[ -z "$value" || "$train_backend" == spirula ]] ||
    config_error "TRAIN_SP_BILAGRID_TYPE est propre à Spirula et exige TRAIN_BACKEND=spirula."

  value="${SFM_SP_IMAGE_EXPOSURE:-}"
  if [[ -n "$value" && ! "$value" =~ ^(auto|[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+))$ ]]; then
    config_error "SFM_SP_IMAGE_EXPOSURE doit être vide, auto ou un nombre de stops."
  fi
  [[ -z "$value" || "$sfm_backend" == spirula ]] ||
    config_error "SFM_SP_IMAGE_EXPOSURE est propre au SfM Spirula."
}

normalize_backend_config_names() {
  local entry old new old_value new_value
  for entry in \
    SFM_FEATURES:SFM_SP_FEATURES SFM_MATCHER:SFM_SP_MATCHER SFM_PAIRS:SFM_SP_PAIRS \
    SFM_BA_LOSS:SFM_SP_BA_LOSS SFM_BA_LOSS_PARAM:SFM_SP_BA_LOSS_PARAM \
    SFM_PAIR_MAX_DISTANCE:SFM_SP_PAIR_MAX_DISTANCE \
    TRAIN_PRESET:TRAIN_SP_PRESET TRAIN_QUALITY:TRAIN_SP_QUALITY \
    TRAIN_CAP_MAX:TRAIN_SP_CAP_MAX TRAIN_SPLATS_PER_IMAGE:TRAIN_SP_SPLATS_PER_IMAGE \
    TRAIN_GROWTH_FACTOR:TRAIN_SP_GROWTH_FACTOR \
    TRAIN_REFINE_STOP_ITER:TRAIN_SP_REFINE_STOP_ITER \
    TRAIN_REFINE_STOP_NUM_ITER:TRAIN_SP_REFINE_STOP_NUM_ITER \
    TRAIN_FLOATER_SUPPRESSION:TRAIN_SP_FLOATER_SUPPRESSION \
    TRAIN_DISTRACTION_ROBUSTNESS:TRAIN_SP_DISTRACTION_ROBUSTNESS \
    NS_PROCESS_DATA_BIN:SFM_NS_PROCESS_DATA_BIN NS_TRAIN_BIN:TRAIN_NS_TRAIN_BIN \
    NS_EXPORT_BIN:TRAIN_NS_EXPORT_BIN NS_DOWNSCALE_FACTOR:TRAIN_NS_DOWNSCALE_FACTOR \
    COLMAP_BIN:SFM_COLMAP_BIN COLMAP_USE_GPU:SFM_COLMAP_USE_GPU \
    COLMAP_MATCHING:SFM_COLMAP_MATCHING; do
    old="${entry%%:*}"
    new="${entry#*:}"
    [[ "${!old+x}" == x ]] || continue
    old_value="${!old}"
    if [[ "${!new+x}" == x ]]; then
      new_value="${!new}"
      if [[ "$new_value" != "$old_value" ]]; then
        config_error "$old et $new sont tous deux définis avec des valeurs différentes."
        continue
      fi
    else
      printf -v "$new" '%s' "$old_value"
      export "$new"
    fi
    printf '[AVERTISSEMENT] %s est déprécié ; utiliser %s.\n' "$old" "$new" >&2
  done
  ((config_errors == 0))
}

reject_legacy_config() {
  local entry name replacement failed=0
  for entry in \
    BBOX_LAMBERT93:PANORAMAX_BBOX PANORAMAX_BBOX_LAMBERT93:PANORAMAX_BBOX \
    NUM_BLOCKS:SPLIT_NUM_BLOCKS OVERLAP_MAX_FRACTION:SPLIT_OVERLAP_MAX_FRACTION \
    NUM_ITER:TRAIN_NUM_ITER CAP_MAX:TRAIN_SP_CAP_MAX SPLATS_PER_IMAGE:TRAIN_SP_SPLATS_PER_IMAGE \
    GROWTH_FACTOR:TRAIN_SP_GROWTH_FACTOR REFINE_STOP_ITER:TRAIN_SP_REFINE_STOP_ITER \
    REFINE_STOP_NUM_ITER:TRAIN_SP_REFINE_STOP_NUM_ITER \
    FLOATER_SUPPRESSION:TRAIN_SP_FLOATER_SUPPRESSION CLEAN_GS:TRAIN_CLEAN \
    QUALITY:TRAIN_SP_QUALITY NUM_ITERATIONS:TRAIN_NUM_ITER CLEAN:TRAIN_CLEAN \
    SAM_ACCEPT_LICENSE:MASKS_ACCEPT_LICENSE SPLAT_NAME:EXPORT_SPLAT_NAME \
    SPLAT_MERGE_STRENGTH:MERGE_STRENGTH SPLAT_CLEANER:POSTPROCESS_CLEANER \
    SPLAT_FILTER_MODE:POSTPROCESS_FILTER_MODE \
    SPLAT_FILTER_SIZE_FACTOR:POSTPROCESS_FILTER_SIZE_FACTOR \
    SPLAT_SPARSE_BBOX_MODE:POSTPROCESS_SPARSE_BBOX_MODE \
    SPLAT_SPARSE_BBOX_MARGIN:POSTPROCESS_SPARSE_BBOX_MARGIN; do
    name="${entry%%:*}"
    replacement="${entry#*:}"
    if [[ "${!name+x}" == x ]]; then
      printf '[ERREUR] Configuration : ancien paramètre %s interdit ; utiliser %s.\n' \
        "$name" "$replacement" >&2
      failed=1
    fi
  done
  [[ "$failed" -eq 0 ]]
}
