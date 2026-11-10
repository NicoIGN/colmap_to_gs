#!/usr/bin/env bash
# Shared configuration for local execution and Slurm submission.
die() { printf '[ERREUR] %s\n' "$*" >&2; exit 1; }

load_config() {
  local config="$1" images="${2:-}" name replacement
  [[ -f "$config" ]] || die "Configuration introuvable : $config"
  set -a
  source "$config"
  source "$repo_root/scripts/pipeline_config.sh"
  init_sfm_geometry_config
  for name in INPUT_IMAGES_DIR INPUT_RECURSIVE MASKS_SPIRULA_MODEL MASKS_SPIRULA_DEVICE \
    MASKS_LABELS MASKS_CONFIDENCE MASKS_DILATE MASKS_MAX_FRACTION; do
    if [[ -v "$name" ]]; then
      case "$name" in
        INPUT_IMAGES_DIR) replacement=SFM_IMAGES_DIR ;;
        INPUT_RECURSIVE) replacement=SFM_IMAGES_RECURSIVE ;;
        *) replacement="SFM_$name / TRAIN_$name" ;;
      esac
      die "Ancien paramètre $name : utiliser $replacement."
    fi
  done
  SFM_IMAGES_DIR="${images:-${SFM_IMAGES_DIR:-}}"
  SFM_IMAGES_RECURSIVE="${SFM_IMAGES_RECURSIVE:-true}"
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  SPIRULA_BIN="${SPIRULA_BIN:-spirula}"
  PIPELINE_RESUME="${PIPELINE_RESUME:-false}"
  SFM_FEATURES="${SFM_FEATURES:-aliked-n16rot}"
  SFM_MATCHER="${SFM_MATCHER:-bruteforce}"
  SFM_PAIRS="${SFM_PAIRS:-prefilter}"
  SFM_MAX_IMAGE_SIZE="${SFM_MAX_IMAGE_SIZE:-4096}"
  SFM_ALIKED_MAX_FEATURES="${SFM_ALIKED_MAX_FEATURES:-8192}"
  SFM_THREADS="${SFM_THREADS:-16}"
  SFM_CAMERA_MODEL="${SFM_CAMERA_MODEL:-opencv}"
  SFM_CAMERA_MODE="${SFM_CAMERA_MODE:-folder}"
  GEOREF_GPX_FILE="${GEOREF_GPX_FILE:-}"
  GEOREF_VIEW_PREFIX="${GEOREF_VIEW_PREFIX:-}"
  GEOREF_EPSG="${GEOREF_EPSG:-2154}"
  GEOREF_ALLOW_UNCERTAIN="${GEOREF_ALLOW_UNCERTAIN:-false}"
  SFM_MASKS="${SFM_MASKS:-on}"
  TRAIN_MASKS="${TRAIN_MASKS:-on}"
  TRAIN_MASKS_BACKEND="${TRAIN_MASKS_BACKEND:-spirula}"
  TRAIN_MASKS_PYTHON="${TRAIN_MASKS_PYTHON:-$PYTHON_BIN}"
  TRAIN_MASKS_MODEL="${TRAIN_MASKS_MODEL:-}"
  TRAIN_MASKS_SPIRULA_MODEL="${TRAIN_MASKS_SPIRULA_MODEL:-}"
  TRAIN_MASKS_SPIRULA_DEVICE="${TRAIN_MASKS_SPIRULA_DEVICE:-auto}"
  TRAIN_MASKS_LABELS="${TRAIN_MASKS_LABELS:-sky,cloud}"
  TRAIN_MASKS_CONFIDENCE="${TRAIN_MASKS_CONFIDENCE:-0.5}"
  TRAIN_MASKS_DILATE="${TRAIN_MASKS_DILATE:-0.002}"
  TRAIN_MASKS_MAX_FRACTION="${TRAIN_MASKS_MAX_FRACTION:-1.0}"
  TRAIN_MASKS_DEVICE="${TRAIN_MASKS_DEVICE:-auto}"
  local suffix sfm_name train_name
  for suffix in BACKEND PYTHON MODEL SPIRULA_MODEL SPIRULA_DEVICE LABELS CONFIDENCE MAX_FRACTION DEVICE; do
    sfm_name="SFM_MASKS_$suffix"
    train_name="TRAIN_MASKS_$suffix"
    printf -v "$sfm_name" '%s' "${!sfm_name:-${!train_name}}"
    export "$sfm_name"
  done
  SFM_MASKS_DILATE="${SFM_MASKS_DILATE:-0.002}"
  MASKS_ACCEPT_LICENSE="${MASKS_ACCEPT_LICENSE:-false}"
  TRAIN_MASKS_MODE="${TRAIN_MASKS_MODE:-empty}"
  SPLIT_NUM_BLOCKS="${SPLIT_NUM_BLOCKS:-4}"
  SPLIT_OVERLAP_MAX_FRACTION="${SPLIT_OVERLAP_MAX_FRACTION:-0.10}"
  TRAIN_PRESET="${TRAIN_PRESET:-3dgs}"
  TRAIN_QUALITY="${TRAIN_QUALITY:-ultra}"
  TRAIN_NUM_ITER="${TRAIN_NUM_ITER:-20000}"
  TRAIN_CAP_MAX="${TRAIN_CAP_MAX:-2000000}"
  TRAIN_GROWTH_FACTOR="${TRAIN_GROWTH_FACTOR:-}"
  TRAIN_REFINE_STOP_ITER="${TRAIN_REFINE_STOP_ITER:-14000}"
  TRAIN_REFINE_STOP_NUM_ITER="${TRAIN_REFINE_STOP_NUM_ITER:-4000}"
  TRAIN_FLOATER_SUPPRESSION="${TRAIN_FLOATER_SUPPRESSION:-mild}"
  TRAIN_DISTRACTION_ROBUSTNESS="${TRAIN_DISTRACTION_ROBUSTNESS:-off}"
  TRAIN_CLEAN="${TRAIN_CLEAN:-false}"
  POSTPROCESS_SPARSE_BBOX_MODE="${POSTPROCESS_SPARSE_BBOX_MODE:-apply}"
  POSTPROCESS_SPARSE_BBOX_MARGIN="${POSTPROCESS_SPARSE_BBOX_MARGIN:-0.05}"
  POSTPROCESS_MAX_REMOVAL_FRACTION="${POSTPROCESS_MAX_REMOVAL_FRACTION:-0.20}"
  MERGE_STRENGTH="${MERGE_STRENGTH:-1.0}"
  EXPORT_SPLAT_NAME="${EXPORT_SPLAT_NAME:-scene.splat}"
  SLURM_PARTITION="${SLURM_PARTITION:-jz}"
  SLURM_CPUS_PER_TASK="${SLURM_CPUS_PER_TASK:-16}"
  SLURM_MEMORY="${SLURM_MEMORY:-64G}"
  SLURM_TIME_LIMIT="${SLURM_TIME_LIMIT:-48:00:00}"
  SLURM_GPU_TYPE="${SLURM_GPU_TYPE:-}"
  SLURM_BLOCK_PARALLELISM="${SLURM_BLOCK_PARALLELISM:-1}"
  set +a
  [[ "$SFM_IMAGES_DIR" == /* && -d "$SFM_IMAGES_DIR" ]] ||
    die "SFM_IMAGES_DIR doit être un dossier existant avec un chemin absolu."
  [[ "${OUTPUT_DIR:-}" == /* ]] || die "OUTPUT_DIR doit être un chemin absolu."
  SFM_IMAGES_DIR="$(realpath -e -- "$SFM_IMAGES_DIR")"
  OUTPUT_DIR="$(realpath -m -- "$OUTPUT_DIR")"
  [[ "$OUTPUT_DIR/" != "$SFM_IMAGES_DIR/"* ]] ||
    die "OUTPUT_DIR doit être distinct et extérieur au dossier d'images."
  export SFM_IMAGES_DIR OUTPUT_DIR

  reject_legacy_config || exit 1
  local config_errors=0
  validate_sfm_geometry_config
  for name in SFM_MAX_IMAGE_SIZE SFM_ALIKED_MAX_FEATURES SFM_THREADS \
    SPLIT_NUM_BLOCKS TRAIN_NUM_ITER TRAIN_CAP_MAX SLURM_CPUS_PER_TASK SLURM_BLOCK_PARALLELISM; do
    config_positive_integer "$name" "${!name}"
  done
  config_choice SFM_IMAGES_RECURSIVE "$SFM_IMAGES_RECURSIVE" 'true|false'
  config_choice PIPELINE_RESUME "$PIPELINE_RESUME" 'true|false'
  config_choice GEOREF_ALLOW_UNCERTAIN "$GEOREF_ALLOW_UNCERTAIN" 'true|false'
  config_positive_integer GEOREF_EPSG "$GEOREF_EPSG"
  if [[ -n "$GEOREF_GPX_FILE" ]]; then
    [[ "$GEOREF_GPX_FILE" == /* && -f "$GEOREF_GPX_FILE" ]] ||
      config_error "GEOREF_GPX_FILE doit être un fichier GPX existant, chemin absolu."
  fi
  config_choice SFM_MASKS "$SFM_MASKS" 'on|off'
  config_choice TRAIN_MASKS "$TRAIN_MASKS" 'on|off'
  config_choice MASKS_ACCEPT_LICENSE "$MASKS_ACCEPT_LICENSE" 'true|false'
  config_choice TRAIN_CLEAN "$TRAIN_CLEAN" 'true|false'
  config_choice TRAIN_MASKS_MODE "$TRAIN_MASKS_MODE" 'auto|ignore|empty'
  config_choice SFM_FEATURES "$SFM_FEATURES" 'sift|aliked-n16rot|aliked-n32|loma-b128|loma-b'
  config_choice SFM_MATCHER "$SFM_MATCHER" 'bruteforce|lightglue|loma-b128|loma-b|loma-r|loma-l|loma-g'
  config_choice SFM_PAIRS "$SFM_PAIRS" 'auto|prefilter|exhaustive'
  config_choice SFM_CAMERA_MODE "$SFM_CAMERA_MODE" 'folder|single|image'
  config_choice TRAIN_PRESET "$TRAIN_PRESET" '3dgs|360-camera|in-the-wild|centered-object|hdr|synthetic|meshing'
  config_choice TRAIN_QUALITY "$TRAIN_QUALITY" 'low|medium|high|ultra'
  config_choice TRAIN_FLOATER_SUPPRESSION "$TRAIN_FLOATER_SUPPRESSION" 'off|mild|strong'
  config_choice TRAIN_DISTRACTION_ROBUSTNESS "$TRAIN_DISTRACTION_ROBUSTNESS" 'off|mild|strong'
  config_choice POSTPROCESS_SPARSE_BBOX_MODE "$POSTPROCESS_SPARSE_BBOX_MODE" 'apply|diagnostic|off'
  for name in TRAIN_REFINE_STOP_ITER TRAIN_REFINE_STOP_NUM_ITER; do
    [[ "${!name}" =~ ^[0-9]+$ ]] || config_error "$name doit être un entier >= 0."
  done
  if [[ -n "$TRAIN_GROWTH_FACTOR" ]]; then
    [[ "$TRAIN_GROWTH_FACTOR" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
      awk -v x="$TRAIN_GROWTH_FACTOR" 'BEGIN { exit !(x > 1) }' ||
      config_error "TRAIN_GROWTH_FACTOR doit être vide ou > 1."
  fi
  for name in SPLIT_OVERLAP_MAX_FRACTION \
    POSTPROCESS_SPARSE_BBOX_MARGIN POSTPROCESS_MAX_REMOVAL_FRACTION MERGE_STRENGTH; do
    [[ "${!name}" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
      config_error "$name doit être un nombre positif ou nul."
  done
  awk -v o="$SPLIT_OVERLAP_MAX_FRACTION" -v r="$POSTPROCESS_MAX_REMOVAL_FRACTION" \
    -v s="$MERGE_STRENGTH" 'BEGIN {
      exit !(o < 1 && r <= 1 && s > 0)
    }' || config_error "Seuils hors limites : consulter config.sh.example."
  [[ -n "$SFM_CAMERA_MODEL" ]] || config_error "SFM_CAMERA_MODEL ne doit pas être vide."
  local prefix backend model_key
  for prefix in SFM_MASKS TRAIN_MASKS; do
    [[ "${!prefix}" == on ]] || continue
    name="${prefix}_BACKEND"; backend="${!name}"
    config_choice "$name" "$backend" 'spirula|segmentation'
    if [[ "$backend" == spirula ]]; then
      model_key="${prefix}_SPIRULA_MODEL"
      [[ -f "${!model_key}" ]] || config_error "$model_key introuvable."
    else
      model_key="${prefix}_MODEL"
      [[ -d "${!model_key}" ]] || config_error "$model_key doit être un modèle local."
    fi
    name="${prefix}_LABELS"
    [[ -n "$(printf '%s' "${!name}" | tr -d ',[:space:]')" ]] ||
      config_error "$name doit contenir au moins un label."
    for suffix in CONFIDENCE DILATE MAX_FRACTION; do
      name="${prefix}_$suffix"
      if [[ "$suffix" == DILATE && "${!name}" == auto ]]; then continue; fi
      [[ "${!name}" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
        awk -v x="${!name}" -v field="$suffix" 'BEGIN {
          if (field == "CONFIDENCE") exit !(x <= 1)
          if (field == "DILATE") exit !(x < 0.1)
          exit !(x > 0 && x <= 1)
        }' || config_error "$name hors limites : consulter config.sh.example."
    done
  done
  [[ "$EXPORT_SPLAT_NAME" == *.splat && "$EXPORT_SPLAT_NAME" != */* ]] ||
    config_error "EXPORT_SPLAT_NAME doit être un nom de fichier .splat sans chemin."
  [[ -z "$SLURM_GPU_TYPE" || "$SLURM_GPU_TYPE" =~ ^[a-zA-Z0-9_-]+$ ]] ||
    config_error "SLURM_GPU_TYPE invalide."
  ((config_errors == 0)) || exit 1
}
