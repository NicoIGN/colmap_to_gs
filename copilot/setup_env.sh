#!/usr/bin/env bash
# Crée (ou met à jour) le venv de développement colmap_to_gs avec les dépendances de test et pipeline.
# Usage : bash copilot/setup_env.sh [chemin_venv]
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
venv="${1:-$HOME/.venvs/colmap_to_gs}"

python3 -m venv "$venv"
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" -m pip install \
  pillow numpy opencv-python-headless tqdm scipy plyfile \
  pyproj laspy lazrs

# pycolmap : seulement pour HLOC / COLMAP ; échec non bloquant.
"$venv/bin/python" -m pip install "pycolmap>=4.2" || echo "[avertissement] pycolmap non installé"

echo "Venv prêt : source $venv/bin/activate"
echo "Racine du dépôt : $repo_root"
