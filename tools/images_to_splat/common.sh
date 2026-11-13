#!/usr/bin/env bash
# Shared configuration for local execution and Slurm submission.
die() { printf '[ERREUR] %s\n' "$*" >&2; exit 1; }
# realpath -e/-m n'existe pas sur macOS : Python est portable.
resolve_path() { python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"; }

load_config() {
  local config="$1" images="${2:-}" name replacement directory
  local config_errors=0
  [[ -f "$config" ]] || die "Configuration introuvable : $config"
  set -a
  source "$config"
  source "$repo_root/tools/libs/scripts/pipeline_config.sh"
  normalize_backend_config_names || exit 1
  init_sfm_geometry_config "${COLMAP_METHOD:-spirula}"
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
  SFM_FILTER_DENSITY="${SFM_FILTER_DENSITY:-false}"
  COLMAP_METHOD="${COLMAP_METHOD:-spirula}"
  SFM_COLMAP_BIN="${SFM_COLMAP_BIN:-colmap}"
  SFM_COLMAP_USE_GPU="${SFM_COLMAP_USE_GPU:-true}"
  SFM_COLMAP_MATCHING="${SFM_COLMAP_MATCHING:-exhaustive}"
  ORIENTATIONS_JSON="${ORIENTATIONS_JSON:-}"
  LIDAR_PLY="${LIDAR_PLY:-}"
  SFM_NS_PROCESS_DATA_BIN="${SFM_NS_PROCESS_DATA_BIN:-ns-process-data}"
  TRAIN_BACKEND="${TRAIN_BACKEND:-spirula}"
  TRAIN_NS_TRAIN_BIN="${TRAIN_NS_TRAIN_BIN:-ns-train}"
  TRAIN_NS_EXPORT_BIN="${TRAIN_NS_EXPORT_BIN:-ns-export}"
  TRAIN_NS_DOWNSCALE_FACTOR="${TRAIN_NS_DOWNSCALE_FACTOR:-1}"
  if [[ "$COLMAP_METHOD" == spirula ]]; then
    SFM_SP_FEATURES="${SFM_SP_FEATURES:-aliked-n16rot}"
    SFM_SP_MATCHER="${SFM_SP_MATCHER:-bruteforce}"
    SFM_SP_PAIRS="${SFM_SP_PAIRS:-prefilter}"
    SFM_SP_QUALITY="${SFM_SP_QUALITY:-high}"
  else
    SFM_SP_FEATURES="${SFM_SP_FEATURES:-}"
    SFM_SP_MATCHER="${SFM_SP_MATCHER:-}"
    SFM_SP_PAIRS="${SFM_SP_PAIRS:-}"
    SFM_SP_QUALITY="${SFM_SP_QUALITY:-}"
  fi
  SFM_MAX_IMAGE_SIZE="${SFM_MAX_IMAGE_SIZE:-4096}"
  SFM_ALIKED_MAX_FEATURES="${SFM_ALIKED_MAX_FEATURES:-8192}"
  SFM_THREADS="${SFM_THREADS:-16}"
  SFM_CAMERA_MODEL="${SFM_CAMERA_MODEL:-opencv}"
  SFM_CAMERA_MODE="${SFM_CAMERA_MODE:-folder}"
  SFM_SP_IMAGE_EXPOSURE="${SFM_SP_IMAGE_EXPOSURE:-}"
  GEOREF_GPX_FILE="${GEOREF_GPX_FILE:-}"
  GEOREF_VIEW_PREFIX="${GEOREF_VIEW_PREFIX:-}"
  GEOREF_EPSG="${GEOREF_EPSG:-2154}"
  GEOREF_ALLOW_UNCERTAIN="${GEOREF_ALLOW_UNCERTAIN:-false}"
  SFM_MASKS="${SFM_MASKS:-on}"
  TRAIN_MASKS="${TRAIN_MASKS:-on}"
  SFM_MASKS_DIR="${SFM_MASKS_DIR:-}"
  TRAIN_MASKS_DIR="${TRAIN_MASKS_DIR:-}"
  local default_mask_backend=spirula
  [[ "$TRAIN_BACKEND" != nerfstudio ]] || default_mask_backend=segmentation
  TRAIN_MASKS_BACKEND="${TRAIN_MASKS_BACKEND:-$default_mask_backend}"
  TRAIN_MASKS_PYTHON="${TRAIN_MASKS_PYTHON:-$PYTHON_BIN}"
  TRAIN_MASKS_MODEL="${TRAIN_MASKS_MODEL:-}"
  TRAIN_MASKS_SPIRULA_MODEL="${TRAIN_MASKS_SPIRULA_MODEL:-}"
  TRAIN_MASKS_SPIRULA_DEVICE="${TRAIN_MASKS_SPIRULA_DEVICE:-auto}"
  TRAIN_MASKS_LABELS="${TRAIN_MASKS_LABELS:-sky}"
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
  TRAIN_SP_PRESET="${TRAIN_SP_PRESET:-3dgs}"
  TRAIN_SP_QUALITY="${TRAIN_SP_QUALITY:-ultra}"
  TRAIN_NUM_ITER="${TRAIN_NUM_ITER:-20000}"
  TRAIN_SP_CAP_MAX="${TRAIN_SP_CAP_MAX:-2000000}"
  TRAIN_SP_GROWTH_FACTOR="${TRAIN_SP_GROWTH_FACTOR:-}"
  TRAIN_SP_REFINE_STOP_ITER="${TRAIN_SP_REFINE_STOP_ITER:-14000}"
  TRAIN_SP_REFINE_STOP_NUM_ITER="${TRAIN_SP_REFINE_STOP_NUM_ITER:-4000}"
  TRAIN_SP_FLOATER_SUPPRESSION="${TRAIN_SP_FLOATER_SUPPRESSION:-mild}"
  TRAIN_SP_DISTRACTION_ROBUSTNESS="${TRAIN_SP_DISTRACTION_ROBUSTNESS:-off}"
  TRAIN_SP_USE_PPISP="${TRAIN_SP_USE_PPISP:-}"
  TRAIN_SP_PPISP_PARAM_TYPE="${TRAIN_SP_PPISP_PARAM_TYPE:-}"
  TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF="${TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF:-}"
  TRAIN_SP_USE_BILATERAL_GRID="${TRAIN_SP_USE_BILATERAL_GRID:-}"
  TRAIN_SP_BILAGRID_TYPE="${TRAIN_SP_BILAGRID_TYPE:-}"
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
  SFM_IMAGES_DIR="$(resolve_path "$SFM_IMAGES_DIR")"
  OUTPUT_DIR="$(resolve_path "$OUTPUT_DIR")"
  [[ "$OUTPUT_DIR/" != "$SFM_IMAGES_DIR/"* ]] ||
    die "OUTPUT_DIR doit être distinct et extérieur au dossier d'images."
  export SFM_IMAGES_DIR OUTPUT_DIR

  reject_legacy_config || exit 1
  validate_sfm_geometry_config
  validate_spirula_photometric_config "$TRAIN_BACKEND" "$COLMAP_METHOD"
  for name in SFM_MAX_IMAGE_SIZE SFM_ALIKED_MAX_FEATURES SFM_THREADS \
    SPLIT_NUM_BLOCKS TRAIN_NUM_ITER SLURM_CPUS_PER_TASK SLURM_BLOCK_PARALLELISM; do
    config_positive_integer "$name" "${!name}"
  done
  if [[ "$TRAIN_BACKEND" == spirula ]]; then
    config_positive_integer TRAIN_SP_CAP_MAX "$TRAIN_SP_CAP_MAX"
  fi
  config_choice SFM_IMAGES_RECURSIVE "$SFM_IMAGES_RECURSIVE" 'true|false'
  config_choice PIPELINE_RESUME "$PIPELINE_RESUME" 'true|false'
  config_choice SFM_FILTER_DENSITY "$SFM_FILTER_DENSITY" 'true|false'
  config_choice COLMAP_METHOD "$COLMAP_METHOD" 'spirula|colmap|nerfstudio|orientations'
  config_choice SFM_COLMAP_USE_GPU "$SFM_COLMAP_USE_GPU" 'true|false'
  config_choice SFM_COLMAP_MATCHING "$SFM_COLMAP_MATCHING" 'exhaustive|sequential'
  config_choice TRAIN_BACKEND "$TRAIN_BACKEND" 'spirula|nerfstudio'
  if [[ "$TRAIN_BACKEND" == nerfstudio ]]; then
    config_positive_integer TRAIN_NS_DOWNSCALE_FACTOR "$TRAIN_NS_DOWNSCALE_FACTOR"
  fi
  if [[ "$COLMAP_METHOD" == orientations ]]; then
    for name in ORIENTATIONS_JSON LIDAR_PLY; do
      [[ "${!name}" == /* && -s "${!name}" ]] ||
        config_error "$name doit être un fichier existant avec un chemin absolu."
    done
  fi
  if [[ "$COLMAP_METHOD" == nerfstudio && "$SFM_MASKS" == on ]]; then
    config_error "ns-process-data ne transmet pas les masques SfM ; utiliser COLMAP_METHOD=colmap ou spirula, ou SFM_MASKS=off."
  fi
  if [[ "$COLMAP_METHOD" == nerfstudio ]]; then
    config_choice SFM_CAMERA_MODE "$SFM_CAMERA_MODE" 'single'
    config_choice SFM_CAMERA_MODEL "$SFM_CAMERA_MODEL" 'opencv|pinhole|simple_pinhole|opencv_fisheye'
  fi
  if [[ "$COLMAP_METHOD" == orientations && "$SFM_MASKS" == on ]]; then
    config_error "La conversion d'orientations ne calcule pas de SfM : définir SFM_MASKS=off."
  fi
  if [[ "$TRAIN_BACKEND" == nerfstudio && "$TRAIN_MASKS" == on && "$TRAIN_MASKS_MODE" != ignore ]]; then
    config_error "Nerfstudio prend en charge TRAIN_MASKS_MODE=ignore, pas empty/auto ; choisir ignore explicitement."
  fi
  if [[ "$TRAIN_BACKEND" == nerfstudio && "$TRAIN_MASKS" == on && "$TRAIN_MASKS_BACKEND" != segmentation ]]; then
    config_error "Nerfstudio : utiliser TRAIN_MASKS_BACKEND=segmentation avec Mask2Former ou SegFormer."
  fi
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
  if [[ "$COLMAP_METHOD" == spirula ]]; then
    config_choice SFM_SP_FEATURES "$SFM_SP_FEATURES" 'sift|aliked-n16rot|aliked-n32|loma-b128|loma-b'
    config_choice SFM_SP_MATCHER "$SFM_SP_MATCHER" 'bruteforce|lightglue|loma-b128|loma-b|loma-r|loma-l|loma-g'
    config_choice SFM_SP_PAIRS "$SFM_SP_PAIRS" 'auto|prefilter|exhaustive'
    config_choice SFM_SP_QUALITY "$SFM_SP_QUALITY" 'low|medium|high|extreme'
  elif [[ -n "$SFM_SP_FEATURES" || -n "$SFM_SP_MATCHER" || -n "$SFM_SP_PAIRS" || -n "$SFM_SP_QUALITY" ]]; then
    config_error "SFM_SP_FEATURES, SFM_SP_MATCHER, SFM_SP_PAIRS et SFM_SP_QUALITY ne s'appliquent qu'à COLMAP_METHOD=spirula."
  fi
  config_choice SFM_CAMERA_MODE "$SFM_CAMERA_MODE" 'folder|single|image'
  if [[ "$TRAIN_BACKEND" == spirula ]]; then
    config_choice TRAIN_SP_PRESET "$TRAIN_SP_PRESET" '3dgs|360-camera|in-the-wild|centered-object|hdr|synthetic|meshing'
    config_choice TRAIN_SP_QUALITY "$TRAIN_SP_QUALITY" 'low|medium|high|ultra'
    config_choice TRAIN_SP_FLOATER_SUPPRESSION "$TRAIN_SP_FLOATER_SUPPRESSION" 'off|mild|strong'
    config_choice TRAIN_SP_DISTRACTION_ROBUSTNESS "$TRAIN_SP_DISTRACTION_ROBUSTNESS" 'off|mild|strong'
  fi
  config_choice POSTPROCESS_SPARSE_BBOX_MODE "$POSTPROCESS_SPARSE_BBOX_MODE" 'apply|diagnostic|off'
  if [[ "$TRAIN_BACKEND" == spirula ]]; then
    for name in TRAIN_SP_REFINE_STOP_ITER TRAIN_SP_REFINE_STOP_NUM_ITER; do
      [[ "${!name}" =~ ^[0-9]+$ ]] || config_error "$name doit être un entier >= 0."
    done
    if [[ -n "$TRAIN_SP_GROWTH_FACTOR" ]]; then
      [[ "$TRAIN_SP_GROWTH_FACTOR" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
        awk -v x="$TRAIN_SP_GROWTH_FACTOR" 'BEGIN { exit !(x > 1) }' ||
        config_error "TRAIN_SP_GROWTH_FACTOR doit être vide ou > 1."
    fi
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
    directory="${prefix}_DIR"
    if [[ -n "${!directory}" ]]; then
      [[ "${!directory}" == /* && -d "${!directory}" ]] ||
        config_error "$directory doit être un dossier existant avec un chemin absolu."
      printf -v "$directory" '%s' "$(resolve_path "${!directory}")"
      export "$directory"
      continue
    fi
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
