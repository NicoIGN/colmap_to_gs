#!/usr/bin/env bash
# Invoked by launch.sh; allocation parameters come from the same config.
set -euo pipefail
[[ $# -eq 4 ]] || { echo "[ERREUR] Utiliser launch.sh CONFIG" >&2; exit 1; }
: "${SLURM_JOB_ID:?Allocation Slurm requise}"
repo_root="$1"; config="$2"; phase="$3"; images="$4"
source "$config"
if [[ -n "${SLURM_ENV_SETUP:-}" ]]; then
  [[ -f "$SLURM_ENV_SETUP" ]] || { echo "[ERREUR] SLURM_ENV_SETUP absent" >&2; exit 1; }
  source "$SLURM_ENV_SETUP"
fi
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-16}"
export PYTHONUNBUFFERED=1
runtime="$(mktemp -d "${SLURM_TMPDIR:-${TMPDIR:-/tmp}}/images-${SLURM_JOB_ID}-XXXXXXXX")"
export XDG_RUNTIME_DIR="$runtime"
trap 'rmdir "$XDG_RUNTIME_DIR" || echo "[AVERTISSEMENT] Runtime conservé : $XDG_RUNTIME_DIR" >&2' EXIT
if [[ "$phase" != merge && "$phase" != mask-setup ]]; then
  srun --unbuffered --export=ALL nvidia-smi -L
fi
args=("$config" --phase "$phase")
[[ -z "$images" ]] || args+=(--images "$images")
if [[ "$phase" == train-block ]]; then
  : "${SLURM_ARRAY_TASK_ID:?Indice du bloc requis}"
  args+=(--block "$SLURM_ARRAY_TASK_ID")
fi
srun --unbuffered --export=ALL bash "$repo_root/scripts/images_to_3dgs.sh" "${args[@]}"
