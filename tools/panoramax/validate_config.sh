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

validate_config() {
  local config_errors=0 name value
  for name in OUTPUT_DIR BBOX_LAMBERT93 NUM_BLOCKS NUM_ITER CAP_MAX TRAIN_PRESET; do
    [[ -n "${!name:-}" ]] || config_error "$name doit être défini et non vide."
  done
  config_positive_integer NUM_BLOCKS "${NUM_BLOCKS:-}"
  config_positive_integer NUM_ITER "${NUM_ITER:-}"
  config_positive_integer CAP_MAX "${CAP_MAX:-}"
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
  config_choice PANORAMAX_QUALITY "${PANORAMAX_QUALITY:-hd}" 'hd|sd|thumb'
  config_choice CLEAN_GS "${CLEAN_GS:-false}" 'true|false|1|0|yes|no|on|off'
  config_choice PANORAMAX_REPLACE_REQUEST "${PANORAMAX_REPLACE_REQUEST:-false}" 'true|false|1|0|yes|no|on|off'
  config_choice SPLAT_FILTER_MODE "${SPLAT_FILTER_MODE:-diagnostic}" 'diagnostic|apply|off'
  config_choice SPLAT_CLEANER "${SPLAT_CLEANER:-contextual}" 'contextual|cleangs'
  config_choice CLEANGS_MODE "${CLEANGS_MODE:-diagnostic}" 'diagnostic|apply'
  config_positive_integer CLEANGS_MIN_VIEWS "${CLEANGS_MIN_VIEWS:-2}"
  if [[ "${SPLAT_CLEANER:-contextual}" == cleangs && -z "${CLEANGS_MODEL:-}" ]]; then
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
  config_choice SPLAT_SPARSE_BBOX_MODE "${SPLAT_SPARSE_BBOX_MODE:-apply}" 'apply|diagnostic|off'
  config_choice FLOATER_SUPPRESSION "${FLOATER_SUPPRESSION:-off}" 'off|mild|strong'
  value="${SPLATS_PER_IMAGE:-}"
  [[ -z "$value" ]] || config_positive_integer SPLATS_PER_IMAGE "$value"
  value="${SPLAT_SPARSE_BBOX_MARGIN:-0.05}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
    config_error "SPLAT_SPARSE_BBOX_MARGIN='$value' doit être un nombre >= 0 (fraction de la bbox)."
  value="${GROWTH_FACTOR:-}"
  [[ -z "$value" ]] || {
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v x="$value" 'BEGIN { exit !(x > 1) }'
  } || config_error "GROWTH_FACTOR='$value' doit être vide ou un nombre > 1."
  for name in REFINE_STOP_ITER REFINE_STOP_NUM_ITER; do
    value="${!name:-}"
    [[ -z "$value" || "$value" =~ ^[0-9]+$ ]] ||
      config_error "$name='$value' doit être vide ou un entier >= 0."
  done
  value="${SPLAT_FILTER_SIZE_FACTOR:-10}"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v x="$value" 'BEGIN { exit !(x > 1 && x / 2 < x) }' ||
    config_error "SPLAT_FILTER_SIZE_FACTOR='$value' doit être fini et > 1."
  for name in SFM_PAIR_MAX_DISTANCE SPLAT_MERGE_STRENGTH; do
    value="${!name:-}"
    [[ "$name" != SPLAT_MERGE_STRENGTH ]] || value="${value:-1.0}"
    [[ -n "$value" ]] || continue
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
      awk -v x="$value" 'BEGIN { exit !(x > 0 && x / 2 < x) }' ||
      config_error "$name='$value' doit être un nombre fini strictement positif."
  done
  value="${OVERLAP_MAX_FRACTION:-0.05}"
  [[ "$value" =~ ^(0([.][0-9]+)?|1([.]0+)?)$ ]] ||
    config_error "OVERLAP_MAX_FRACTION='$value' doit être dans [0,1]."
  value="${SPLAT_NAME:-panoramax.splat}"
  [[ "$value" == *.splat && "$value" != */* && "$value" != .splat ]] ||
    config_error "SPLAT_NAME='$value' doit être un nom de fichier terminé par .splat."
  value="${BBOX_LAMBERT93:-}"
  awk -v bbox="$value" 'BEGIN {
    if (split(bbox, a, ",") != 4) exit 1
    for (i=1; i<=4; i++)
      if (a[i] !~ /^[[:space:]]*[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?[[:space:]]*$/ ||
          (a[i]+0 != 0 && (a[i]+0)/2 == a[i]+0)) exit 1
    exit !(a[1]+0 < a[3]+0 && a[2]+0 < a[4]+0)
  }' || config_error "BBOX_LAMBERT93='$value' attend xmin,ymin,xmax,ymax finis avec xmin<xmax et ymin<ymax."
  value="${PANORAMAX_API_URL:-https://explore.panoramax.fr/api}"
  [[ "$value" =~ ^https?://[^/[:space:]]+(/[^[:space:]]*)?$ ]] ||
    config_error "PANORAMAX_API_URL='$value' doit être une URL HTTP(S)."

  if [[ "${1:-local}" == slurm ]]; then
    config_positive_integer SLURM_CPUS_PER_TASK "${SLURM_CPUS_PER_TASK:-16}"
    config_positive_integer SLURM_BLOCK_PARALLELISM "${SLURM_BLOCK_PARALLELISM:-${NUM_BLOCKS:-}}"
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
