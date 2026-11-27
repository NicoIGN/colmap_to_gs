#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
grid=false
slurm=false
args=()
for arg in "$@"; do
  case "$arg" in
    --grid) grid=true ;;
    --slurm) slurm=true ;;
    *) args+=("$arg") ;;
  esac
done
if [[ "$grid" == true ]]; then
  source "$repo_root/tools/libs/scripts/grid_run.sh"
  [[ "$slurm" != true ]] || args+=(--slurm)
  grid_run "$repo_root" panoramax "${args[@]}"
  exit 0
fi
source "$repo_root/tools/libs/scripts/shell_helpers.sh"
reject_grid_params "${args[0]:-}"
if [[ "$slurm" != true ]]; then
  for arg in "${args[@]:1}"; do
    case "$arg" in
      --wait) echo "[ERREUR] --wait s'utilise avec --slurm." >&2; exit 1 ;;
    esac
  done
  exec bash "$repo_root/scripts/local/panoramax_to_3dgs.sh" "${args[@]}"
fi
exec bash "$repo_root/tools/panoramax/launch.sh" "${args[@]}"
