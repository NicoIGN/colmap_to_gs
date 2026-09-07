#!/bin/bash
#SBATCH --job-name=gsplat
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:1
#SBATCH --mem=16G
#SBATCH --time=48:00:00
#SBATCH --partition=jean-zellou
#SBATCH -o /mnt/common/hdd/slurm/logs/gsplat-%j.out
#SBATCH -e /mnt/common/hdd/slurm/logs/gsplat-%j.err

set -euo pipefail

unset SRUN_CPUS_PER_TASK
unset SLURM_NTASKS
unset SLURM_NPROCS
unset SLURM_TASKS_PER_NODE
unset SLURM_CPUS_PER_TASK

# =========================
# LOAD USER CONFIG
# =========================
echo "CONFIG_SH: ${CONFIG_SH:-<unset>}"
: "${CONFIG_SH:?❌ CONFIG_SH is not set}"
source "$CONFIG_SH"
VERBOSE="${VERBOSE:-false}"

# Expected variables in config.sh:
#   GIT_ROOT
#   OUTPUT_DIR
#   BASENAME
#   GSPLAT_PROFILE
#   DATASET_DIR
# Optional:
#   SKIP_TRAINING=true|false
#   SKIP_EXPORT=true|false

# =========================
# ENV SETTINGS
# =========================
export NCCL_BLOCKING_WAIT=1
export NCCL_ASYNC_ERROR_HANDLING=1
export PYTHONUNBUFFERED=1

export HTTP_PROXY="${HTTP_PROXY:-}"
export HTTPS_PROXY="${HTTPS_PROXY:-}"
export http_proxy="${http_proxy:-}"
export https_proxy="${https_proxy:-}"
export NO_PROXY="${NO_PROXY:-}"
export MAX_JOBS="${MAX_JOBS:-}"

: "${HOME_SLURM:=$HOME}"
export MPLCONFIGDIR="$HOME_SLURM/.config/matplotlib"
mkdir -p "$MPLCONFIGDIR"

export XDG_RUNTIME_DIR="$HOME_SLURM/.runtime"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# =========================
# PROJECT
# =========================
: "${GIT_ROOT:?❌ GIT_ROOT is not set}"

if [ ! -d "$GIT_ROOT" ]; then
  echo "❌ GIT_ROOT does not exist: $GIT_ROOT"
  exit 1
fi
cd "$GIT_ROOT"

# =========================
# CONDA ENV
# =========================
CONDA_BASE="$(conda info --base 2>/dev/null || echo "")"
if [ -z "$CONDA_BASE" ]; then
  echo "❌ conda not found"
  exit 1
fi

source "$CONDA_BASE/etc/profile.d/conda.sh"
set +u
conda activate gsplat
set -u

# =========================
# INPUT VALIDATION
# =========================
: "${OUTPUT_DIR:?❌ OUTPUT_DIR is not set in config.sh}"
: "${BASENAME:?❌ BASENAME is not set in config.sh}"
: "${GSPLAT_PROFILE:?❌ GSPLAT_PROFILE is not set in config.sh}"
: "${DATASET_DIR:?❌ DATASET_DIR is not set in config.sh}"

[ -d "$DATASET_DIR" ] || { echo "❌ DATASET_DIR does not exist: $DATASET_DIR"; exit 1; }

# =========================
# DEBUG INFO
# =========================
echo "========================"
echo "🚀 GSPLAT RUN ENV"
echo "========================"
echo "job_id: ${SLURM_JOB_ID:-N/A}"
echo "hostname: $(hostname)"
echo "user: $(whoami)"
echo "pwd: $(pwd)"
echo "python: $(which python)"
echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-<unset>}"
echo "OUTPUT_DIR=$OUTPUT_DIR"
echo "BASENAME=$BASENAME"
echo "GSPLAT_PROFILE=$GSPLAT_PROFILE"
echo "DATASET_DIR=$DATASET_DIR"
echo "VERBOSE=$VERBOSE"

python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
print("cuda device count:", torch.cuda.device_count())
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        p = torch.cuda.get_device_properties(i)
        print(f"gpu[{i}]: {p.name} | total_vram={p.total_memory/1024**3:.2f} GiB")
PY

# =========================
# OPTIONAL BACKGROUND MONITOR
# =========================
MONITOR_PID=""

cleanup() {
  if [ -n "${MONITOR_PID:-}" ]; then
    kill "$MONITOR_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [ "$VERBOSE" = "true" ]; then
  (
    while true; do
      echo
      echo "========================"
      echo "⏱ RESOURCE SNAPSHOT $(date)"
      echo "========================"
      free -h || true
      echo
      if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu --format=csv || true
        echo
        nvidia-smi --query-compute-apps=pid,process_name,gpu_uuid,used_gpu_memory --format=csv || true
      fi
      sleep 120
    done
  ) &
  MONITOR_PID=$!
fi

# =========================
# RUN TRAINING (NEW ARGS)
# =========================
EXTRA_ARGS=()
if [ "${SKIP_TRAINING:-false}" = "true" ]; then EXTRA_ARGS+=(--skip-training); fi
if [ "${SKIP_EXPORT:-false}" = "true" ]; then EXTRA_ARGS+=(--skip-export); fi

srun --unbuffered bash run.sh \
  --dataset-dir "$DATASET_DIR" \
  --output_dir "$OUTPUT_DIR" \
  --name "$BASENAME" \
  --gsplat-profile "$GSPLAT_PROFILE" \
  --skip-conda \
  "${EXTRA_ARGS[@]}"

echo "✅ Job complete"
