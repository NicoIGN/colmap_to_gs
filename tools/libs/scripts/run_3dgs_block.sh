#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${TRAIN_BACKEND:-spirula}" in
  spirula) exec bash "$script_dir/run_spirula_block.sh" "$@" ;;
  nerfstudio) exec "${PYTHON_BIN:-python3}" "$script_dir/../python/nerfstudio_block.py" "$@" ;;
  *) echo "[ERREUR] TRAIN_BACKEND inconnu : $TRAIN_BACKEND" >&2; exit 1 ;;
esac
