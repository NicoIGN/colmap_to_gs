#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${TRAIN_BACKEND:-spirula}" in
  spirula) exec bash "$script_dir/train_block_spirula.sh" "$@" ;;
  nerfstudio) exec "${PYTHON_BIN:-python3}" "$script_dir/../python/train_block_nerfstudio.py" "$@" ;;
  *) echo "[ERREUR] TRAIN_BACKEND inconnu : $TRAIN_BACKEND" >&2; exit 1 ;;
esac
