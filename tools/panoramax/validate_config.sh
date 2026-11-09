#!/usr/bin/env bash
# Shared value validation; no Python, GPU or Slurm installation required.

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

reject_legacy_config() {
  local entry name replacement failed=0
  for entry in \
    BBOX_LAMBERT93:PANORAMAX_BBOX_LAMBERT93 \
    NUM_BLOCKS:SPLIT_NUM_BLOCKS OVERLAP_MAX_FRACTION:SPLIT_OVERLAP_MAX_FRACTION \
    NUM_ITER:TRAIN_NUM_ITER CAP_MAX:TRAIN_CAP_MAX SPLATS_PER_IMAGE:TRAIN_SPLATS_PER_IMAGE \
    GROWTH_FACTOR:TRAIN_GROWTH_FACTOR REFINE_STOP_ITER:TRAIN_REFINE_STOP_ITER \
    REFINE_STOP_NUM_ITER:TRAIN_REFINE_STOP_NUM_ITER \
    FLOATER_SUPPRESSION:TRAIN_FLOATER_SUPPRESSION CLEAN_GS:TRAIN_CLEAN \
    QUALITY:TRAIN_QUALITY NUM_ITERATIONS:TRAIN_NUM_ITER CLEAN:TRAIN_CLEAN \
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

validate_config() {
  local config_errors=0 name value
  reject_legacy_config || return 1
  for name in OUTPUT_DIR PANORAMAX_BBOX_LAMBERT93 SPLIT_NUM_BLOCKS TRAIN_NUM_ITER TRAIN_CAP_MAX TRAIN_PRESET; do
    [[ -n "${!name:-}" ]] || config_error "$name doit être défini et non vide."
  done
  config_positive_integer SPLIT_NUM_BLOCKS "${SPLIT_NUM_BLOCKS:-}"
  config_positive_integer TRAIN_NUM_ITER "${TRAIN_NUM_ITER:-}"
  config_positive_integer TRAIN_CAP_MAX "${TRAIN_CAP_MAX:-}"
  config_positive_integer SFM_MAX_IMAGE_SIZE "${SFM_MAX_IMAGE_SIZE:-4096}"
  config_positive_integer SFM_ALIKED_MAX_FEATURES "${SFM_ALIKED_MAX_FEATURES:-4096}"
  for name in PANORAMAX_IMAGE_MAX SFM_THREADS; do
    value="${!name:-}"
    [[ -z "$value" ]] || config_positive_integer "$name" "$value"
  done
  config_choice SFM_MATCHER "${SFM_MATCHER:-lightglue}" \
    'bruteforce|lightglue|loma-b128|loma-b|loma-r|loma-l|loma-g'
  config_choice SFM_FEATURES "${SFM_FEATURES:-aliked-n16rot}" \
    'sift|aliked-n16rot|aliked-n32|loma-b128|loma-b'
  config_choice SFM_PAIRS "${SFM_PAIRS:-prefilter}" 'auto|prefilter|exhaustive|sequential'
  config_choice TRAIN_PRESET "${TRAIN_PRESET:-}" \
    '3dgs|360-camera|in-the-wild|centered-object|hdr|synthetic|meshing'
  [[ -z "${TRAIN_QUALITY:-}" ]] ||
    config_choice TRAIN_QUALITY "$TRAIN_QUALITY" 'low|medium|high|ultra'
  config_choice TRAIN_DISTRACTION_ROBUSTNESS "${TRAIN_DISTRACTION_ROBUSTNESS:-off}" 'off|mild|strong'
  config_choice PANORAMAX_QUALITY "${PANORAMAX_QUALITY:-hd}" 'hd|sd|thumb'
  config_choice TRAIN_CLEAN "${TRAIN_CLEAN:-false}" 'true|false|1|0|yes|no|on|off'
  config_choice PANORAMAX_REPLACE_REQUEST "${PANORAMAX_REPLACE_REQUEST:-false}" 'true|false|1|0|yes|no|on|off'
  config_choice POSTPROCESS_FILTER_MODE "${POSTPROCESS_FILTER_MODE:-diagnostic}" 'diagnostic|apply|off'
  config_choice TRAIN_MASKS "${TRAIN_MASKS:-off}" 'on|off|true|false|1|0|yes|no'
  config_choice TRAIN_MASKS_BACKEND "${TRAIN_MASKS_BACKEND:-segmentation}" 'segmentation|spirula'
  config_choice TRAIN_MASKS_MODE "${TRAIN_MASKS_MODE:-auto}" 'auto|ignore|empty'
  config_choice MASKS_ACCEPT_LICENSE "${MASKS_ACCEPT_LICENSE:-false}" 'true|false|1|0|yes|no|on|off'
  config_choice SFM_MASKS "${SFM_MASKS:-off}" 'on|off|true|false|1|0|yes|no'
  local suffix sfm_name train_name backend
  for suffix in BACKEND PYTHON MODEL SPIRULA_MODEL SPIRULA_DEVICE LABELS CONFIDENCE MAX_FRACTION DEVICE; do
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
      [[ -n "$(printf '%s' "${SFM_MASKS_LABELS:-sky}" | tr -d ',[:space:]')" ]] ||
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
      [[ -n "$(printf '%s' "${TRAIN_MASKS_LABELS:-sky}" | tr -d ',[:space:]')" ]] ||
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
  config_choice TRAIN_FLOATER_SUPPRESSION "${TRAIN_FLOATER_SUPPRESSION:-off}" 'off|mild|strong'
  value="${TRAIN_SPLATS_PER_IMAGE:-}"
  [[ -z "$value" ]] || config_positive_integer TRAIN_SPLATS_PER_IMAGE "$value"
  value="${POSTPROCESS_SPARSE_BBOX_MARGIN:-0.05}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
    config_error "POSTPROCESS_SPARSE_BBOX_MARGIN='$value' doit être un nombre >= 0 (fraction de la bbox)."
  value="${TRAIN_GROWTH_FACTOR:-}"
  [[ -z "$value" ]] || {
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v x="$value" 'BEGIN { exit !(x > 1) }'
  } || config_error "TRAIN_GROWTH_FACTOR='$value' doit être vide ou un nombre > 1."
  for name in TRAIN_REFINE_STOP_ITER TRAIN_REFINE_STOP_NUM_ITER; do
    value="${!name:-}"
    [[ -z "$value" || "$value" =~ ^[0-9]+$ ]] ||
      config_error "$name='$value' doit être vide ou un entier >= 0."
  done
  value="${POSTPROCESS_FILTER_SIZE_FACTOR:-10}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v x="$value" 'BEGIN { exit !(x > 1 && x / 2 < x) }' ||
    config_error "POSTPROCESS_FILTER_SIZE_FACTOR='$value' doit être fini et > 1."
  for name in SFM_PAIR_MAX_DISTANCE MERGE_STRENGTH; do
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
  value="${PANORAMAX_BBOX_LAMBERT93:-}"
  awk -v bbox="$value" 'BEGIN {
    if (split(bbox, a, ",") != 4) exit 1
    for (i=1; i<=4; i++)
      if (a[i] !~ /^[[:space:]]*[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?[[:space:]]*$/ ||
          (a[i]+0 != 0 && (a[i]+0)/2 == a[i]+0)) exit 1
    exit !(a[1]+0 < a[3]+0 && a[2]+0 < a[4]+0)
  }' || config_error "PANORAMAX_BBOX_LAMBERT93='$value' attend xmin,ymin,xmax,ymax finis avec xmin<xmax et ymin<ymax."
  value="${PANORAMAX_API_URL:-https://explore.panoramax.fr/api}"
  [[ "$value" =~ ^https?://[^/[:space:]]+(/[^[:space:]]*)?$ ]] ||
    config_error "PANORAMAX_API_URL='$value' doit être une URL HTTP(S)."

  if [[ "${1:-local}" == slurm ]]; then
    config_positive_integer SLURM_CPUS_PER_TASK "${SLURM_CPUS_PER_TASK:-16}"
    config_positive_integer SLURM_BLOCK_PARALLELISM "${SLURM_BLOCK_PARALLELISM:-${SPLIT_NUM_BLOCKS:-}}"
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
