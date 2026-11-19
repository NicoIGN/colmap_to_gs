#!/usr/bin/env bash
# Panoramax-specific validation, using shared pipeline primitives.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/tools/libs/scripts/pipeline_config.sh"

validate_config() {
  local config_errors=0 name value train_backend="${TRAIN_BACKEND:-spirula}"
  reject_legacy_config || return 1
  config_choice COLMAP_METHOD "${COLMAP_METHOD:-spirula}" 'spirula|hloc'
  config_choice SFM_EQUIRECTANGULAR_VIEWS "${SFM_EQUIRECTANGULAR_VIEWS:-false}" 'true|false'
  if [[ "${COLMAP_METHOD:-spirula}" == hloc ]]; then
    [[ "${SFM_EQUIRECTANGULAR_VIEWS:-false}" == true && "${PANORAMAX_PICTURE_TYPE:-all}" == 360 ]] ||
      config_error "Panoramax HLOC exige PANORAMAX_PICTURE_TYPE=360 et SFM_EQUIRECTANGULAR_VIEWS=true."
    [[ -z "${SFM_SP_PAIR_MAX_DISTANCE:-}" ]] ||
      config_error "SFM_SP_PAIR_MAX_DISTANCE est réservé à Spirula."
    [[ "${COLMAP_USE_MVS:-false}" != true ]] || config_error "MVS n'est pas disponible pour Panoramax HLOC."
    [[ "${LIDAR_COLMAP_ALIGN:-false}" != true && "${LIDAR_CLEAN:-false}" != true ]] ||
      config_error "Le traitement LiDAR n'est pas encore compatible avec Panoramax HLOC."
    config_positive_integer SFM_EQUIRECTANGULAR_VIEW_SIZE "${SFM_EQUIRECTANGULAR_VIEW_SIZE:-1024}"
    value="${SFM_EQUIRECTANGULAR_VIEW_SIZE:-1024}"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] && (( value >= 2 )) ||
      config_error "SFM_EQUIRECTANGULAR_VIEW_SIZE doit être >= 2."
    config_choice HLOC_PAIRS "${HLOC_PAIRS:-retrieval}" 'retrieval|exhaustive'
    config_positive_integer HLOC_NUM_NEIGHBORS "${HLOC_NUM_NEIGHBORS:-20}"
  elif [[ "${SFM_EQUIRECTANGULAR_VIEWS:-false}" == true ]]; then
    config_error "SFM_EQUIRECTANGULAR_VIEWS=true exige COLMAP_METHOD=hloc."
  fi
  config_choice TRAIN_BACKEND "$train_backend" 'spirula|nerfstudio'
  if [[ "$train_backend" == nerfstudio ]]; then
    config_positive_integer TRAIN_NS_DOWNSCALE_FACTOR "${TRAIN_NS_DOWNSCALE_FACTOR:-1}"
    case "${TRAIN_MASKS:-off}" in
      on|true|1|yes)
        [[ "${TRAIN_MASKS_MODE:-auto}" == ignore ]] ||
          config_error "Nerfstudio exige TRAIN_MASKS_MODE=ignore."
        [[ "${TRAIN_MASKS_BACKEND:-segmentation}" == segmentation ]] ||
          config_error "Nerfstudio : utiliser TRAIN_MASKS_BACKEND=segmentation avec Mask2Former ou SegFormer." ;;
    esac
  fi
  validate_sfm_geometry_config
  validate_spirula_photometric_config "${TRAIN_BACKEND:-spirula}" "${COLMAP_METHOD:-spirula}"
  if [[ -n "${SFM_SP_IMAGE_EXPOSURE:-}" && -n "${SFM_SP_PAIR_MAX_DISTANCE:-}" ]]; then
    config_error "SFM_SP_IMAGE_EXPOSURE n'est pas disponible avec le filtrage GPS des paires (SFM_SP_PAIR_MAX_DISTANCE)."
  fi
  for name in OUTPUT_DIR PANORAMAX_BBOX SPLIT_NUM_BLOCKS TRAIN_NUM_ITER; do
    [[ -n "${!name:-}" ]] || config_error "$name doit être défini et non vide."
  done
  if [[ "$train_backend" == spirula ]]; then
    for name in TRAIN_SP_CAP_MAX TRAIN_SP_PRESET; do
      [[ -n "${!name:-}" ]] || config_error "$name doit être défini pour TRAIN_BACKEND=spirula."
    done
  fi
  config_positive_integer SPLIT_NUM_BLOCKS "${SPLIT_NUM_BLOCKS:-}"
  config_positive_integer TRAIN_NUM_ITER "${TRAIN_NUM_ITER:-}"
  if [[ "$train_backend" == spirula ]]; then
    config_positive_integer TRAIN_SP_CAP_MAX "${TRAIN_SP_CAP_MAX:-}"
  fi
  config_positive_integer SFM_MAX_IMAGE_SIZE "${SFM_MAX_IMAGE_SIZE:-4096}"
  config_positive_integer SFM_ALIKED_MAX_FEATURES "${SFM_ALIKED_MAX_FEATURES:-4096}"
  config_choice SFM_CAMERA_MODEL "${SFM_CAMERA_MODEL:-opencv}" \
    'opencv|pinhole|opencv-fisheye|equirectangular'
  if [[ "${SFM_CAMERA_MODEL:-opencv}" == equirectangular && "$train_backend" != spirula && "${COLMAP_METHOD:-spirula}" != hloc ]]; then
    config_error "SFM_CAMERA_MODEL=equirectangular exige TRAIN_BACKEND=spirula."
  fi
  for name in PANORAMAX_IMAGE_MAX NUM_CPUS; do
    value="${!name:-}"
    [[ -z "$value" ]] || config_positive_integer "$name" "$value"
  done
  config_choice SFM_SP_MATCHER "${SFM_SP_MATCHER:-lightglue}" \
    'bruteforce|lightglue|loma-b128|loma-b|loma-r|loma-l|loma-g'
  config_choice SFM_SP_FEATURES "${SFM_SP_FEATURES:-aliked-n16rot}" \
    'sift|aliked-n16rot|aliked-n32|loma-b128|loma-b'
  config_choice SFM_SP_PAIRS "${SFM_SP_PAIRS:-prefilter}" 'auto|prefilter|exhaustive|sequential'
  if [[ "$train_backend" == spirula ]]; then
    config_choice TRAIN_SP_PRESET "${TRAIN_SP_PRESET:-}" \
      '3dgs|360-camera|in-the-wild|centered-object|hdr|synthetic|meshing'
    [[ -z "${TRAIN_SP_QUALITY:-}" ]] ||
      config_choice TRAIN_SP_QUALITY "$TRAIN_SP_QUALITY" 'low|medium|high|ultra'
    config_choice TRAIN_SP_DISTRACTION_ROBUSTNESS "${TRAIN_SP_DISTRACTION_ROBUSTNESS:-off}" 'off|mild|strong'
    config_choice TRAIN_SP_WARP_SPHERICAL "${TRAIN_SP_WARP_SPHERICAL:-true}" 'true|false'
    config_choice TRAIN_SP_FLOATER_SUPPRESSION "${TRAIN_SP_FLOATER_SUPPRESSION:-off}" 'off|mild|strong'
  fi
  config_choice PANORAMAX_QUALITY "${PANORAMAX_QUALITY:-hd}" 'hd|sd|thumb'
  config_choice PANORAMAX_PICTURE_TYPE "${PANORAMAX_PICTURE_TYPE:-all}" 'all|normal|360'
  config_choice PANORAMAX_MULTI_CAM "${PANORAMAX_MULTI_CAM:-false}" 'true|false|1|0|yes|no|on|off'
  case "${PANORAMAX_MULTI_CAM:-false}" in
    true|1|yes|on)
      [[ -z "${SFM_CAMERA_MODE:-}" || "$SFM_CAMERA_MODE" == folder ]] ||
        config_error "PANORAMAX_MULTI_CAM=true est incompatible avec SFM_CAMERA_MODE='$SFM_CAMERA_MODE' : chaque taille d'image devient une caméra (mode folder). Mettre SFM_CAMERA_MODE=folder ou le retirer de la configuration." ;;
  esac
  config_choice TRAIN_CLEAN "${TRAIN_CLEAN:-false}" 'true|false|1|0|yes|no|on|off'
  config_choice PANORAMAX_REPLACE_REQUEST "${PANORAMAX_REPLACE_REQUEST:-false}" 'true|false|1|0|yes|no|on|off'
  config_choice POSTPROCESS_FILTER_MODE "${POSTPROCESS_FILTER_MODE:-apply}" 'diagnostic|apply|off'
  config_choice TRAIN_MASKS "${TRAIN_MASKS:-off}" 'on|off|true|false|1|0|yes|no'
  config_choice TRAIN_MASKS_BACKEND "${TRAIN_MASKS_BACKEND:-segmentation}" 'segmentation|spirula'
  config_choice TRAIN_MASKS_MODE "${TRAIN_MASKS_MODE:-auto}" 'auto|ignore|empty'
  config_choice MASKS_ACCEPT_LICENSE "${MASKS_ACCEPT_LICENSE:-false}" 'true|false|1|0|yes|no|on|off'
  config_choice SFM_MASKS "${SFM_MASKS:-off}" 'on|off|true|false|1|0|yes|no'
  local suffix sfm_name train_name backend
  for suffix in BACKEND PYTHON MODEL SPIRULA_MODEL LABELS CONFIDENCE MAX_FRACTION DEVICE; do
    sfm_name="SFM_MASKS_$suffix"
    train_name="TRAIN_MASKS_$suffix"
    value="${!sfm_name:-${!train_name:-}}"
    local "$sfm_name=$value"
  done
  case "${SFM_MASKS:-off}" in
    on|true|1|yes)
      backend="${SFM_MASKS_BACKEND:-segmentation}"
      config_choice SFM_MASKS_BACKEND "$backend" 'segmentation|spirula'
      if [[ "$backend" == spirula ]]; then
        [[ -n "$SFM_MASKS_SPIRULA_MODEL" ]] || config_error "SFM_MASKS_SPIRULA_MODEL doit être défini."
      else
        [[ -n "$SFM_MASKS_MODEL" ]] || config_error "SFM_MASKS_MODEL doit être défini."
      fi
      [[ -n "$(printf '%s' "${SFM_MASKS_LABELS-sky}" | tr -d ',[:space:]')" ]] ||
        config_error "SFM_MASKS_LABELS doit contenir au moins un label."
      value="${SFM_MASKS_CONFIDENCE:-0.5}"
      [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
        awk -v x="$value" 'BEGIN { exit !(x >= 0 && x <= 1) }' ||
        config_error "SFM_MASKS_CONFIDENCE doit être dans [0,1]."
      value="${SFM_MASKS_MAX_FRACTION:-0.8}"
      [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
        awk -v x="$value" 'BEGIN { exit !(x > 0 && x <= 1) }' ||
        config_error "SFM_MASKS_MAX_FRACTION doit être dans ]0,1]."
      value="${SFM_MASKS_DILATE:-0.002}"
      [[ "$value" == auto ]] || {
        [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
          awk -v x="$value" 'BEGIN { exit !(x < 0.1) }'
      } || config_error "SFM_MASKS_DILATE doit être auto ou dans [0,0.1[."
      ;;
  esac
  case "${TRAIN_MASKS:-off}" in
    on|true|1|yes)
      if [[ "${TRAIN_MASKS_BACKEND:-segmentation}" == spirula ]]; then
        [[ -n "${TRAIN_MASKS_SPIRULA_MODEL:-}" ]] ||
          config_error "TRAIN_MASKS_SPIRULA_MODEL doit désigner le checkpoint SAM 3 GGML local."
      else
        [[ -n "${TRAIN_MASKS_MODEL:-}" ]] ||
          config_error "TRAIN_MASKS_MODEL doit désigner le modèle local (tools/segmentation_masks/install.sh)."
      fi
      [[ -n "$(printf '%s' "${TRAIN_MASKS_LABELS-sky}" | tr -d ',[:space:]')" ]] ||
        config_error "TRAIN_MASKS_LABELS doit contenir au moins un label."
      ;;
  esac
  value="${TRAIN_MASKS_CONFIDENCE:-0.5}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v x="$value" 'BEGIN { exit !(x >= 0 && x <= 1) }' ||
    config_error "TRAIN_MASKS_CONFIDENCE='$value' doit être dans [0,1]."
  value="${TRAIN_MASKS_DILATE:-auto}"
  [[ "$value" == auto ]] || {
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v x="$value" 'BEGIN { exit !(x < 0.1) }'
  } || config_error "TRAIN_MASKS_DILATE='$value' doit être auto ou dans [0,0.1[."
  value="${TRAIN_MASKS_MAX_FRACTION:-0.8}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v x="$value" 'BEGIN { exit !(x > 0 && x <= 1) }' ||
    config_error "TRAIN_MASKS_MAX_FRACTION='$value' doit être dans ]0,1]."
  config_choice POSTPROCESS_CLEANER "${POSTPROCESS_CLEANER:-contextual}" 'contextual|cleangs'
  config_choice CLEANGS_MODE "${CLEANGS_MODE:-diagnostic}" 'diagnostic|apply'
  config_positive_integer CLEANGS_MIN_VIEWS "${CLEANGS_MIN_VIEWS:-2}"
  if [[ "${POSTPROCESS_CLEANER:-contextual}" == cleangs && -z "${CLEANGS_MODEL:-}" ]]; then
    config_error "CLEANGS_MODEL doit désigner le modèle de segmentation local."
  fi
  value="${CLEANGS_SKY_CONFIDENCE:-0.9}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v x="$value" 'BEGIN { exit !(x > 0.5 && x <= 1) }' ||
    config_error "CLEANGS_SKY_CONFIDENCE='$value' doit être dans ]0.5,1]."
  value="${CLEANGS_MAX_REMOVAL_FRACTION:-0.2}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v x="$value" 'BEGIN { exit !(x >= 0 && x < 1) }' ||
    config_error "CLEANGS_MAX_REMOVAL_FRACTION='$value' doit être dans [0,1[."
  config_choice POSTPROCESS_SPARSE_BBOX_MODE "${POSTPROCESS_SPARSE_BBOX_MODE:-apply}" 'apply|diagnostic|off'
  if [[ "$train_backend" == spirula ]]; then
    value="${TRAIN_SP_SPLATS_PER_IMAGE:-}"
    [[ -z "$value" ]] || config_positive_integer TRAIN_SP_SPLATS_PER_IMAGE "$value"
  fi
  value="${POSTPROCESS_SPARSE_BBOX_MARGIN:-0.05}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
    config_error "POSTPROCESS_SPARSE_BBOX_MARGIN='$value' doit être un nombre >= 0 (fraction de la bbox)."
  if [[ "$train_backend" == spirula ]]; then
    value="${TRAIN_SP_GROWTH_FACTOR:-}"
    [[ -z "$value" ]] || {
      [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v x="$value" 'BEGIN { exit !(x > 1) }'
    } || config_error "TRAIN_SP_GROWTH_FACTOR='$value' doit être vide ou un nombre > 1."
    for name in TRAIN_SP_REFINE_STOP_ITER TRAIN_SP_REFINE_STOP_NUM_ITER; do
      value="${!name:-}"
      [[ -z "$value" || "$value" =~ ^[0-9]+$ ]] ||
        config_error "$name='$value' doit être vide ou un entier >= 0."
    done
  fi
  value="${POSTPROCESS_FILTER_SIZE_FACTOR:-10}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v x="$value" 'BEGIN { exit !(x > 1 && x / 2 < x) }' ||
    config_error "POSTPROCESS_FILTER_SIZE_FACTOR='$value' doit être fini et > 1."
  for name in SFM_SP_PAIR_MAX_DISTANCE MERGE_STRENGTH; do
    value="${!name:-}"
    [[ "$name" != MERGE_STRENGTH ]] || value="${value:-1.0}"
    [[ -n "$value" ]] || continue
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
      awk -v x="$value" 'BEGIN { exit !(x > 0 && x / 2 < x) }' ||
      config_error "$name='$value' doit être un nombre fini strictement positif."
  done
  value="${SPLIT_OVERLAP_MAX_FRACTION:-0.05}"
  [[ "$value" =~ ^(0([.][0-9]+)?|1([.]0+)?)$ ]] ||
    config_error "SPLIT_OVERLAP_MAX_FRACTION='$value' doit être dans [0,1]."
  value="${EXPORT_SPLAT_NAME:-panoramax.splat}"
  [[ "$value" == *.splat && "$value" != */* && "$value" != .splat ]] ||
    config_error "EXPORT_SPLAT_NAME='$value' doit être un nom de fichier terminé par .splat."
  config_positive_integer PANORAMAX_BBOX_EPSG "${PANORAMAX_BBOX_EPSG:-2154}"
  value="${PANORAMAX_BBOX:-}"
  awk -v bbox="$value" 'BEGIN {
    if (split(bbox, a, ",") != 4) exit 1
    for (i=1; i<=4; i++)
      if (a[i] !~ /^[[:space:]]*[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?[[:space:]]*$/ ||
          (a[i]+0 != 0 && (a[i]+0)/2 == a[i]+0)) exit 1
    exit !(a[1]+0 < a[3]+0 && a[2]+0 < a[4]+0)
  }' || config_error "PANORAMAX_BBOX='$value' attend xmin,ymin,xmax,ymax finis avec xmin<xmax et ymin<ymax."
  value="${PANORAMAX_API_URL:-https://explore.panoramax.fr/api}"
  [[ "$value" =~ ^https?://[^/[:space:]]+(/[^[:space:]]*)?$ ]] ||
    config_error "PANORAMAX_API_URL='$value' doit être une URL HTTP(S)."

  if [[ "${1:-local}" == slurm ]]; then
    config_positive_integer NUM_CPUS "${NUM_CPUS:-2}"
    config_positive_integer NUM_GPUS "${NUM_GPUS:-1}"
    value="${SLURM_MEMORY:-64G}"
    [[ "$value" =~ ^[0-9]+[KkMmGgTt]?$ ]] ||
      config_error "SLURM_MEMORY='$value' attend un entier et une unité K, M, G ou T facultative."
    value="${SLURM_TIME_LIMIT:-48:00:00}"
    [[ "$value" == UNLIMITED || "$value" =~ ^[0-9]+(-[0-9]+)?(:[0-5]?[0-9]){0,2}$ ]] ||
      config_error "SLURM_TIME_LIMIT='$value' : durée Slurm invalide (exemple 48:00:00)."
    value="${SLURM_GPU_TYPE:-}"
    [[ -z "$value" || "$value" =~ ^[a-zA-Z0-9_-]+$ ]] ||
      config_error "SLURM_GPU_TYPE='$value' : type GPU attendu, sans ':'."
  fi
  [[ "$config_errors" -eq 0 ]]
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [[ $# -lt 1 || $# -gt 2 || ! -f "$1" ||
        ( "${2:-local}" != local && "${2:-local}" != slurm ) ]]; then
    printf 'Usage: bash validate_config.sh /path/to/config.sh [local|slurm]\n' >&2
    exit 1
  fi
  validation_mode="${2:-local}"
  source "$1"
  validate_config "$validation_mode" || exit 1
  printf '[config] Paramètres valides (%s). Aucun calcul ni job lancé.\n' "$validation_mode"
fi
