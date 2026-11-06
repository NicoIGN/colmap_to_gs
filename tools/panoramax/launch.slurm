#!/usr/bin/env bash
set -euo pipefail

die() {
  printf '[ERREUR] %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 2 ]] || die "Usage : launch.slurm REPO_ROOT CONFIG_SH (via launch.sh)"
repo_root="$1"
config_path="$2"
: "${SLURM_JOB_ID:?Ce script doit être lancé dans une allocation Slurm}"
[[ -f "$config_path" ]] || die "Configuration inaccessible sur le nœud : $config_path"
[[ -f "$repo_root/tools/panoramax/run_pipeline.sh" ]] ||
  die "Dépôt inaccessible sur le nœud : $repo_root"
source "$config_path"

# Site-specific modules (Vulkan, drivers, Conda) must be loaded on the node.
if [[ -n "${SLURM_ENV_SETUP:-}" ]]; then
  [[ -f "$SLURM_ENV_SETUP" ]] || die "Script d'environnement absent : $SLURM_ENV_SETUP"
  source "$SLURM_ENV_SETUP"
fi
if [[ -n "${SLURM_CONDA_ENV:-}" ]]; then
  if [[ -z "${SLURM_CONDA_BASE:-}" ]]; then
    command -v conda >/dev/null 2>&1 ||
      die "Conda introuvable ; définir SLURM_CONDA_BASE ou SLURM_ENV_SETUP."
    SLURM_CONDA_BASE="$(conda info --base)"
  fi
  [[ -f "$SLURM_CONDA_BASE/etc/profile.d/conda.sh" ]] ||
    die "Initialisation Conda absente : $SLURM_CONDA_BASE"
  source "$SLURM_CONDA_BASE/etc/profile.d/conda.sh"
  set +u
  conda activate "$SLURM_CONDA_ENV"
  set -u
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python introuvable : $PYTHON_BIN"
command -v srun >/dev/null 2>&1 || die "srun introuvable."
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
runtime_parent="${SLURM_TMPDIR:-${TMPDIR:-/tmp}}"
XDG_RUNTIME_DIR="$(mktemp -d "$runtime_parent/panoramax-${SLURM_JOB_ID}-XXXXXXXX")"
export XDG_RUNTIME_DIR
cleanup() {
  rmdir -- "$XDG_RUNTIME_DIR" ||
    printf '[AVERTISSEMENT] Dossier runtime conservé : %s\n' "$XDG_RUNTIME_DIR" >&2
}
trap cleanup EXIT

printf '[slurm] Job=%s hôte=%s dossier=%s\n' "$SLURM_JOB_ID" "$(hostname)" "$PWD"
printf '[slurm] Python=%s\n' "$(command -v "$PYTHON_BIN")"
"$PYTHON_BIN" -c 'import numpy, scipy, tqdm, pyproj, plyfile'

# The allocation (and its device cgroup) is inherited by this single job step.
# Spirula uses Vulkan, not PyTorch/CUDA; all blocks run sequentially on one GPU.
exec_status=0
srun --unbuffered --export=ALL bash -c '
  set -euo pipefail
  command -v nvidia-smi >/dev/null 2>&1 || {
    echo "[ERREUR] nvidia-smi introuvable ; GPU NVIDIA requis." >&2
    exit 1
  }
  nvidia-smi -L
  bash "$1/tools/panoramax/run_pipeline.sh" "$2"
' bash "$repo_root" "$config_path" || exec_status=$?
if [[ "$exec_status" -ne 0 ]]; then
  printf '[ERREUR] Pipeline GPU en échec (job %s, hôte %s, code %s).\n' \
    "$SLURM_JOB_ID" "$(hostname)" "$exec_status" >&2
  printf '[ERREUR] srun relaie un arrêt du pipeline ou du job ; consulter la cause et l’étape indiquées plus haut.\n' >&2
  printf '[ERREUR] Journaux : %s/logs/panoramax-%s.out et %s/logs/panoramax-%s.err\n' \
    "$OUTPUT_DIR" "$SLURM_JOB_ID" "$OUTPUT_DIR" "$SLURM_JOB_ID" >&2
  exit "$exec_status"
fi
printf '[slurm] Pipeline GPU terminé ; .splat et .georef dans OUTPUT_DIR.\n'
