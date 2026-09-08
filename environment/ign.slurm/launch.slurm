#!/bin/bash
#SBATCH --job-name=gsplat
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:1
#SBATCH --mem=16G
#SBATCH --time=48:00:00
#SBATCH --partition=jean-zellou
#SBATCH -o /mnt/common/hdd/slurm/logs/gsplat-%j.out
#SBATCH -e /mnt/common/hdd/slurm/logs/gsplat-%j.err

set -euo pipefail

# =========================
# LOAD USER CONFIG
# =========================
echo "CONFIG_SH: ${CONFIG_SH:-<unset>}"
: "${CONFIG_SH:?❌ CONFIG_SH is not set}"
source "$CONFIG_SH"

VERBOSE="${VERBOSE:-false}"

is_verbose() {
  case "${VERBOSE:-false}" in
    1|true|TRUE|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# Required in config.sh:
#   GIT_ROOT
#   OUTPUT_DIR
#   BASENAME
#   GSPLAT_PROFILE
#   DATASET_DIR
# Optional:
#   SKIP_TRAINING=true|false
#   SKIP_EXPORT=true|false

: "${GIT_ROOT:?❌ GIT_ROOT is not set in config.sh}"
: "${OUTPUT_DIR:?❌ OUTPUT_DIR is not set in config.sh}"
: "${BASENAME:?❌ BASENAME is not set in config.sh}"
: "${GSPLAT_PROFILE:?❌ GSPLAT_PROFILE is not set in config.sh}"
: "${DATASET_DIR:?❌ DATASET_DIR is not set in config.sh}"

[ -d "$GIT_ROOT" ] || { echo "❌ GIT_ROOT does not exist: $GIT_ROOT"; exit 1; }
[ -d "$DATASET_DIR" ] || { echo "❌ DATASET_DIR does not exist: $DATASET_DIR"; exit 1; }

cd "$GIT_ROOT"

# =========================
# RUNTIME ENV
# =========================
export PYTHONUNBUFFERED=1
export NCCL_BLOCKING_WAIT=1
export NCCL_ASYNC_ERROR_HANDLING=1

: "${HOME_SLURM:=$HOME}"
export MPLCONFIGDIR="$HOME_SLURM/.config/matplotlib"
mkdir -p "$MPLCONFIGDIR"

export XDG_RUNTIME_DIR="$HOME_SLURM/.runtime"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# =========================
# CONDA
# =========================
CONDA_BASE="$(conda info --base 2>/dev/null || true)"
[ -n "$CONDA_BASE" ] || { echo "❌ conda not found"; exit 1; }

source "$CONDA_BASE/etc/profile.d/conda.sh"
set +u
conda activate gsplat
set -u

# =========================
# DEBUG (minimal)
# =========================
echo "========================"
echo "🚀 GSPLAT RUN ENV"
echo "========================"
echo "job_id: ${SLURM_JOB_ID:-N/A}"
echo "host  : $(hostname)"
echo "pwd   : $(pwd)"
echo "python: $(which python)"
echo "OUTPUT_DIR=$OUTPUT_DIR"
echo "BASENAME=$BASENAME"
echo "GSPLAT_PROFILE=$GSPLAT_PROFILE"
echo "DATASET_DIR=$DATASET_DIR"
echo "VERBOSE=$VERBOSE"

# =========================
# OPTIONAL MONITOR
# =========================
MONITOR_PID=""
cleanup() {
  if [ -n "${MONITOR_PID:-}" ]; then
    kill "$MONITOR_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if is_verbose; then
  (
    while true; do
      echo
      echo "⏱ RESOURCE SNAPSHOT $(date)"
      free -h || true
      if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu --format=csv || true
      fi
      sleep 120
    done
  ) &
  MONITOR_PID=$!
fi

# =========================
# RUN
# =========================
EXTRA_ARGS=()
[ "${SKIP_TRAINING:-false}" = "true" ] && EXTRA_ARGS+=(--skip-training)
[ "${SKIP_EXPORT:-false}" = "true" ] && EXTRA_ARGS+=(--skip-export)

srun --unbuffered bash scripts/run.sh \
  --dataset-dir "$DATASET_DIR" \
  --output_dir "$OUTPUT_DIR" \
  --name "$BASENAME" \
  --gsplat-profile "$GSPLAT_PROFILE" \
  --skip-conda \
  "${EXTRA_ARGS[@]}"

echo "✅ Job complete"
