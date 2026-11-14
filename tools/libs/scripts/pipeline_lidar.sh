#!/usr/bin/env bash

load_lidar_config() {
  local name default defaults
  LIDAR_COLMAP_ALIGN="${LIDAR_COLMAP_ALIGN:-false}"
  LIDAR_COLMAP_INJECT="${LIDAR_COLMAP_INJECT:-false}"
  LIDAR_CLEAN="${LIDAR_CLEAN:-false}"
  LIDAR_CLEAN_GEOREF="${LIDAR_CLEAN_GEOREF:-}"
  export LIDAR_COLMAP_ALIGN LIDAR_COLMAP_INJECT LIDAR_CLEAN LIDAR_CLEAN_GEOREF
  if [[ "$LIDAR_COLMAP_ALIGN" == false &&
        -f "${LIDAR_WORKSPACE:-${OUTPUT_DIR:-.}}/lidar/colmap_inputs.json" ]]; then
    printf '[ERREUR] Workspace déjà recalé par LiDAR : conserver LIDAR_COLMAP_ALIGN=true ou choisir un nouveau workspace.\n' >&2
    return 1
  fi
  for name in LIDAR_COLMAP_ALIGN LIDAR_COLMAP_INJECT LIDAR_CLEAN; do
    [[ "${!name}" == true || "${!name}" == false ]] || {
      printf '[ERREUR] %s attend true ou false.\n' "$name" >&2
      return 1
    }
  done
  if [[ "$LIDAR_COLMAP_ALIGN" == false && "$LIDAR_CLEAN" == false ]]; then
    [[ "$LIDAR_COLMAP_INJECT" == false ]] || {
      printf '[ERREUR] LIDAR_COLMAP_INJECT nécessite LIDAR_COLMAP_ALIGN=true.\n' >&2
      return 1
    }
    return 0
  fi
  defaults="$("${PYTHON_BIN:-python3}" - "$repo_root/tools/lidar/pipeline_options.py" <<'PY'
import runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
for name, default in runpy.run_path(sys.argv[1])["DEFAULTS"].items():
    print(name + "|" + default)
PY
)" || return 1
  while IFS='|' read -r name default; do
    [[ -v "$name" ]] || printf -v "$name" '%s' "$default"
    export "$name"
  done <<< "$defaults"
  "${PYTHON_BIN:-python3}" "$repo_root/tools/lidar/pipeline_options.py" validate
}

lidar_pipeline_hook() {
  case "$1" in
    prepare|check) [[ "${LIDAR_COLMAP_ALIGN:-false}" == true ]] || return 0 ;;
    clean) [[ "${LIDAR_CLEAN:-false}" == true ]] || return 0 ;;
    preflight)
      [[ "${LIDAR_COLMAP_ALIGN:-false}" == true || "${LIDAR_CLEAN:-false}" == true ]] || return 0 ;;
  esac
  "${PYTHON_BIN:-python3}" "$repo_root/tools/lidar/pipeline_options.py" "$@"
}
