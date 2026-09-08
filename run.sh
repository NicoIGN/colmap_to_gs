#!/usr/bin/env bash
set -euo pipefail

# =========================================
# Constants (fixed for this pipeline)
# =========================================
DEVICE="gpu"
MODEL="splatfacto"

# =========================================
# Defaults
# =========================================
OUTPUT_ROOT="runs/default"
DATASET_DIR=""
GSPLAT_PROFILE=""
MAX_JOBS=2
SKIP_CONDA=false
SKIP_TRAINING=false
SKIP_EXPORT=false
TWO_STAGES=false
IGNORE_PROXY=false

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# =========================================
# Utils
# =========================================
SCRIPT_START=$(date +%s)

format_duration() {
  local seconds=$1
  local h=$((seconds / 3600))
  local m=$(((seconds % 3600) / 60))
  local s=$((seconds % 60))
  if [ $h -gt 0 ]; then printf "%02dh %02dm %02ds" "$h" "$m" "$s"
  elif [ $m -gt 0 ]; then printf "%02dm %02ds" "$m" "$s"
  else printf "%02ds" "$s"; fi
}

print_step_time() {
  local label="$1"; local start_ts="$2"
  local elapsed=$(( $(date +%s) - start_ts ))
  echo ""
  echo "⏱️  ${label} completed in $(format_duration "$elapsed")"
  echo ""
}

die() { echo "❌ $*" >&2; exit 1; }

show_help() {
  cat << EOF
Usage:
  ./run.sh --dataset-dir <dossier> [options]

Required:
  --dataset-dir <dir>         Racine dataset

Expected layout:
  <dataset-dir>/
    ├── colmap/sparse/0/{cameras.bin,images.bin,points3D.bin}
    ├── images/
    ├── sparse_pc.ply
    └── transforms.json

Options:
  --output_dir, -o <dir>      Output root (default: runs/default)
  --name <name>               Basename export PLY (default: gsplat_<timestamp>)
  --gsplat-profile <name>     fast | balanced | quality | quality_plus
  --max-jobs <int>            Parallel jobs
  --two-stages                Coarse -> full training
  --skip-conda
  --skip-training
  --skip-export
  --no-proxy
EOF
}

# =========================================
# Args
# =========================================
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset-dir) DATASET_DIR="$2"; shift 2 ;;
    --output_dir|-o) OUTPUT_ROOT="$2"; shift 2 ;;
    --name) BASENAME="$2"; shift 2 ;;
    --gsplat-profile) GSPLAT_PROFILE="$2"; shift 2 ;;
    --max-jobs) MAX_JOBS="$2"; shift 2 ;;
    --two-stages) TWO_STAGES=true; shift ;;
    --skip-conda) SKIP_CONDA=true; shift ;;
    --skip-training) SKIP_TRAINING=true; shift ;;
    --skip-export) SKIP_EXPORT=true; shift ;;
    --no-proxy) IGNORE_PROXY=true; shift ;;
    --help) show_help; exit 0 ;;
    *) die "Unknown param: $1" ;;
  esac
done

[ -n "${DATASET_DIR}" ] || { show_help; die "--dataset-dir is required"; }
DATASET_DIR="$(cd "$DATASET_DIR" && pwd)"
BASENAME="${BASENAME:-gsplat_$(date +%Y%m%d_%H%M%S)}"

# =========================================
# Validate dataset layout
# =========================================
COLMAP_SPARSE_DIR="$DATASET_DIR/colmap/sparse/0"
IMAGE_DIR="$DATASET_DIR/images"
TRANSFORMS_JSON="$DATASET_DIR/transforms.json"
SPARSE_PC_PLY="$DATASET_DIR/sparse_pc.ply"

[ -d "$DATASET_DIR" ] || die "Dataset dir not found: $DATASET_DIR"
[ -d "$COLMAP_SPARSE_DIR" ] || die "Missing: $COLMAP_SPARSE_DIR"
[ -f "$COLMAP_SPARSE_DIR/cameras.bin" ] || die "Missing cameras.bin"
[ -f "$COLMAP_SPARSE_DIR/images.bin" ] || die "Missing images.bin"
[ -f "$COLMAP_SPARSE_DIR/points3D.bin" ] || die "Missing points3D.bin"
[ -d "$IMAGE_DIR" ] || die "Missing image dir: $IMAGE_DIR"
#[ -f "$TRANSFORMS_JSON" ] || die "Missing transforms.json: $TRANSFORMS_JSON"
[ -f "$SPARSE_PC_PLY" ] || die "Missing sparse_pc.ply: $SPARSE_PC_PLY"

IMAGE_COUNT=$(find "$IMAGE_DIR" -maxdepth 1 -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \) | wc -l | tr -d ' ')
[ "$IMAGE_COUNT" -ge 2 ] || die "At least 2 images required in $IMAGE_DIR (found: $IMAGE_COUNT)"

echo "📦 DATASET: $DATASET_DIR"
echo "📦 IMAGES : $IMAGE_DIR"
echo "📦 COLMAP : $COLMAP_SPARSE_DIR"
echo "🧠 MODEL  : $MODEL"
echo "🖥️ DEVICE : $DEVICE"

# =========================================
# Load config + profile
# =========================================
NO_PROXY="$IGNORE_PROXY" MAX_JOBS="$MAX_JOBS" SKIP_TRAINING="$SKIP_TRAINING" \
  source "$SCRIPT_DIR/config/config.sh"

if [ -n "$GSPLAT_PROFILE" ]; then
  [ -f "config/profiles/${GSPLAT_PROFILE}.sh" ] || die "Profile not found: ${GSPLAT_PROFILE}"
  source "config/profiles/${GSPLAT_PROFILE}.sh"
  echo "👉 using profile: ${GSPLAT_PROFILE}"
fi

# =========================================
# GPU check (required)
# =========================================
if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/dev/null 2>&1; then
  die "GPU/CUDA not available (DEVICE is fixed to gpu)."
fi

# =========================================
# Optional conda setup
# =========================================
if [ "$SKIP_CONDA" = false ]; then
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate "$CONDA_ENV_NAME" || die "Cannot activate conda env: $CONDA_ENV_NAME"
  bash scripts/check_torch_stack.sh
else
  echo "⏩ Skipping conda setup"
fi

PY_VER=$(python --version 2>&1)
[[ "$PY_VER" == *"3.10"* || "$PY_VER" == *"3.11"* ]] || die "Python 3.10/3.11 required, got: $PY_VER"

# =========================================
# Output dirs
# =========================================
OUTPUT_DIR="$OUTPUT_ROOT/model3d"
EXPORT_DIR="$OUTPUT_ROOT/exports"
TRAIN_DIR="$OUTPUT_ROOT"
mkdir -p "$OUTPUT_DIR" "$EXPORT_DIR" "$TRAIN_DIR"

# =========================================
# Estimate near/far from existing COLMAP
# =========================================
echo "📏 Estimating near/far from COLMAP..."
ESTIMATE_SCRIPT="$SCRIPT_DIR/python/estimate_planes.py"
[ -f "$ESTIMATE_SCRIPT" ] || die "Missing script: $ESTIMATE_SCRIPT"

EST_OUTPUT=$(python3 "$ESTIMATE_SCRIPT" --input "$COLMAP_SPARSE_DIR")
NEAR=$(echo "$EST_OUTPUT" | grep NEAR | cut -d= -f2)
FAR=$(echo "$EST_OUTPUT" | grep FAR  | cut -d= -f2)
[[ -n "${NEAR}" && -n "${FAR}" && "${NEAR}" != "nan" && "${FAR}" != "nan" ]] || die "Invalid near/far: $EST_OUTPUT"

export COLLIDER_NEAR="$NEAR"
export COLLIDER_FAR="$FAR"
export ENABLE_COLLIDER="True"
echo "✅ near=$NEAR far=$FAR"

# =========================================
# Train
# =========================================
if [ "$SKIP_TRAINING" = false ]; then
  STEP_START=$(date +%s)
  echo "🧠 Training (splatfacto)..."

  if [ "$TWO_STAGES" = true ]; then
    echo "👉 two-stage mode"

    ORIG_MAX_ITER="$MAX_ITER"
    ORIG_CAMERA_RES_SCALE_FACTOR="$CAMERA_RES_SCALE_FACTOR"
    ORIG_NUM_DOWNSCALES="${NUM_DOWNSCALES:-}"
    ORIG_REFINE_EVERY="$REFINE_EVERY"
    ORIG_STOP_SPLIT_AT="$STOP_SPLIT_AT"
    ORIG_MAX_RES="$MAX_RES"

    # Stage 1
    CAMERA_RES_SCALE_FACTOR="0.5"
    NUM_DOWNSCALES="2"
    MAX_RES="8192"
    RELOAD_FROM_CHECKPOINT="False"

    DATA="$DATASET_DIR" RELOAD_FROM_CHECKPOINT="$RELOAD_FROM_CHECKPOINT" MODEL="$MODEL" \
    MODEL_IMPLEMENTATION="$MODEL_IMPLEMENTATION" DEVICE="$DEVICE" MAX_ITER="$MAX_ITER" \
    REFINE_EVERY="$REFINE_EVERY" MAX_JOBS="$MAX_JOBS" STEPS_PER_SAVE="$STEPS_PER_SAVE" \
    STEPS_PER_EVAL_ALL_IMAGES="$STEPS_PER_EVAL_ALL_IMAGES" EXPERIMENT_NAME="$EXPERIMENT_NAME" \
    OUTPUTDIR="$TRAIN_DIR" TRAIN_RAYS_PER_BATCH="$TRAIN_RAYS_PER_BATCH" \
    CAMERA_RES_SCALE_FACTOR="$CAMERA_RES_SCALE_FACTOR" NUM_DOWNSCALES="$NUM_DOWNSCALES" \
    NUM_NERF_SAMPLES_PER_RAY="$NUM_NERF_SAMPLES_PER_RAY" NUM_PROPOSAL_SAMPLES_PER_RAY="$NUM_PROPOSAL_SAMPLES_PER_RAY" \
    MAX_RES="$MAX_RES" MAX_GAUSS_RATIO="$MAX_GAUSS_RATIO" DENSIFY_GRAD_THRESH="$DENSIFY_GRAD_THRESH" \
    CULL_ALPHA_THRESH="$CULL_ALPHA_THRESH" CULL_SCREEN_SIZE="$CULL_SCREEN_SIZE" \
    SPLIT_SCREEN_SIZE="$SPLIT_SCREEN_SIZE" STOP_SPLIT_AT="$STOP_SPLIT_AT" \
    CULL_SCALE_THRESH="$CULL_SCALE_THRESH" RESET_ALPHA_EVERY="$RESET_ALPHA_EVERY" \
    USE_SCALE_REGULARIZATION="$USE_SCALE_REGULARIZATION" SSIM_LAMBDA="$SSIM_LAMBDA" \
    COLLIDER_NEAR="$COLLIDER_NEAR" COLLIDER_FAR="$COLLIDER_FAR" \
    ENABLE_COLLIDER="$ENABLE_COLLIDER" USE_BILATERAL_GRID="$USE_BILATERAL_GRID" \
    USE_DEFAULTS="False" MIXED_PRECISION="$MIXED_PRECISION" USE_GRAD_SCALER="$USE_GRAD_SCALER" \
    bash scripts/train.sh

    # Stage 2
    MAX_ITER=$((ORIG_MAX_ITER + 1000))
    CAMERA_RES_SCALE_FACTOR="$ORIG_CAMERA_RES_SCALE_FACTOR"
    NUM_DOWNSCALES="$ORIG_NUM_DOWNSCALES"
    MAX_RES="$ORIG_MAX_RES"
    REFINE_EVERY="999999"
    STOP_SPLIT_AT="0"
    RELOAD_FROM_CHECKPOINT="True"

    DATA="$DATASET_DIR" RELOAD_FROM_CHECKPOINT="$RELOAD_FROM_CHECKPOINT" MODEL="$MODEL" \
    MODEL_IMPLEMENTATION="$MODEL_IMPLEMENTATION" DEVICE="$DEVICE" MAX_ITER="$MAX_ITER" \
    REFINE_EVERY="$REFINE_EVERY" MAX_JOBS="$MAX_JOBS" STEPS_PER_SAVE="1000" \
    STEPS_PER_EVAL_ALL_IMAGES="$STEPS_PER_EVAL_ALL_IMAGES" EXPERIMENT_NAME="$EXPERIMENT_NAME" \
    OUTPUTDIR="$TRAIN_DIR" TRAIN_RAYS_PER_BATCH="$TRAIN_RAYS_PER_BATCH" \
    CAMERA_RES_SCALE_FACTOR="$CAMERA_RES_SCALE_FACTOR" NUM_DOWNSCALES="$NUM_DOWNSCALES" \
    NUM_NERF_SAMPLES_PER_RAY="$NUM_NERF_SAMPLES_PER_RAY" NUM_PROPOSAL_SAMPLES_PER_RAY="$NUM_PROPOSAL_SAMPLES_PER_RAY" \
    MAX_RES="$MAX_RES" MAX_GAUSS_RATIO="$MAX_GAUSS_RATIO" DENSIFY_GRAD_THRESH="$DENSIFY_GRAD_THRESH" \
    CULL_ALPHA_THRESH="$CULL_ALPHA_THRESH" CULL_SCREEN_SIZE="$CULL_SCREEN_SIZE" \
    SPLIT_SCREEN_SIZE="$SPLIT_SCREEN_SIZE" STOP_SPLIT_AT="$STOP_SPLIT_AT" \
    CULL_SCALE_THRESH="$CULL_SCALE_THRESH" RESET_ALPHA_EVERY="$RESET_ALPHA_EVERY" \
    USE_SCALE_REGULARIZATION="$USE_SCALE_REGULARIZATION" SSIM_LAMBDA="$SSIM_LAMBDA" \
    COLLIDER_NEAR="$COLLIDER_NEAR" COLLIDER_FAR="$COLLIDER_FAR" \
    ENABLE_COLLIDER="$ENABLE_COLLIDER" USE_BILATERAL_GRID="$USE_BILATERAL_GRID" \
    USE_DEFAULTS="False" MIXED_PRECISION="$MIXED_PRECISION" USE_GRAD_SCALER="$USE_GRAD_SCALER" \
    bash scripts/train.sh
  else
    DATA="$DATASET_DIR" RELOAD_FROM_CHECKPOINT="False" MODEL="$MODEL" \
    MODEL_IMPLEMENTATION="$MODEL_IMPLEMENTATION" DEVICE="$DEVICE" MAX_ITER="$MAX_ITER" \
    REFINE_EVERY="$REFINE_EVERY" MAX_JOBS="$MAX_JOBS" STEPS_PER_SAVE="$STEPS_PER_SAVE" \
    STEPS_PER_EVAL_ALL_IMAGES="$STEPS_PER_EVAL_ALL_IMAGES" EXPERIMENT_NAME="$EXPERIMENT_NAME" \
    OUTPUTDIR="$TRAIN_DIR" TRAIN_RAYS_PER_BATCH="$TRAIN_RAYS_PER_BATCH" \
    CAMERA_RES_SCALE_FACTOR="$CAMERA_RES_SCALE_FACTOR" NUM_NERF_SAMPLES_PER_RAY="$NUM_NERF_SAMPLES_PER_RAY" \
    NUM_PROPOSAL_SAMPLES_PER_RAY="$NUM_PROPOSAL_SAMPLES_PER_RAY" MAX_RES="$MAX_RES" \
    MAX_GAUSS_RATIO="$MAX_GAUSS_RATIO" DENSIFY_GRAD_THRESH="$DENSIFY_GRAD_THRESH" \
    CULL_ALPHA_THRESH="$CULL_ALPHA_THRESH" CULL_SCREEN_SIZE="$CULL_SCREEN_SIZE" \
    SPLIT_SCREEN_SIZE="$SPLIT_SCREEN_SIZE" STOP_SPLIT_AT="$STOP_SPLIT_AT" \
    CULL_SCALE_THRESH="$CULL_SCALE_THRESH" RESET_ALPHA_EVERY="$RESET_ALPHA_EVERY" \
    USE_SCALE_REGULARIZATION="$USE_SCALE_REGULARIZATION" SSIM_LAMBDA="$SSIM_LAMBDA" \
    COLLIDER_NEAR="$COLLIDER_NEAR" COLLIDER_FAR="$COLLIDER_FAR" \
    ENABLE_COLLIDER="$ENABLE_COLLIDER" USE_BILATERAL_GRID="$USE_BILATERAL_GRID" \
    USE_DEFAULTS="False" MIXED_PRECISION="$MIXED_PRECISION" USE_GRAD_SCALER="$USE_GRAD_SCALER" \
    bash scripts/train.sh
  fi

  print_step_time "TRAINING" "$STEP_START"
else
  echo "⏩ Skipping training"
fi

# =========================================
# Export
# =========================================
PLY_FILE=""
if [ "$SKIP_EXPORT" = false ]; then
  STEP_START=$(date +%s)
  echo "🚀 Exporting..."
  OUTPUT_DIR="$TRAIN_DIR/$EXPERIMENT_NAME" EXPORT_DIR="$OUTPUT_DIR" ZIP_RUN=0 \
    bash scripts/export_splat_to_ply.sh
  PLY_FILE=$(find "$OUTPUT_DIR" -type f -name "*.ply" | head -n 1 || true)
  [ -f "$PLY_FILE" ] || die "PLY export failed"
  print_step_time "EXPORT" "$STEP_START"
else
  echo "⏩ Skipping export"
fi

echo "✅ Done: $EXPORT_DIR/${BASENAME}.ply"
echo "⏱️  Total: $(format_duration $(( $(date +%s) - SCRIPT_START )))"
