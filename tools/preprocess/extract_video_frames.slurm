#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 4 ]] || { echo "[ERREUR] Utiliser launch_videos_to_3dgs.sh CONFIG" >&2; exit 1; }
: "${SLURM_JOB_ID:?Allocation Slurm requise}"
repo_root="$1"; config="$2"; video="$3"; frames="$4"
source "$config"
if [[ -n "${SLURM_ENV_SETUP:-}" ]]; then
  [[ -f "$SLURM_ENV_SETUP" ]] || { echo "[ERREUR] SLURM_ENV_SETUP absent" >&2; exit 1; }
  source "$SLURM_ENV_SETUP"
fi
export OMP_NUM_THREADS="${NUM_CPUS:-8}"
export PYTHONUNBUFFERED=1
srun --unbuffered --export=ALL "${PYTHON_BIN:-python3}" \
  "$repo_root/tools/preprocess/prepare_video_frames.py" \
  --video "$video" --out "$frames" --num-frames "$NUM_FRAMES"
