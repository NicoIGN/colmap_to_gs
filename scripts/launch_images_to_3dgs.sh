#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
grid=false
args=()
for arg in "$@"; do
  if [[ "$arg" == --grid ]]; then grid=true; else args+=("$arg"); fi
done
if [[ "$grid" == true ]]; then
  exec bash "$repo_root/scripts/grid_to_3dgs.sh" "${args[@]}" --source images
fi
source "$repo_root/tools/libs/scripts/shell_helpers.sh"
reject_grid_params "${args[0]:-}"
exec bash "$repo_root/tools/libs/launch.sh" "$@"
