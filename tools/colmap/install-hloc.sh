#!/usr/bin/env bash
# Installe HLOC (avec LightGlue) et le réseau SuperGlue dans l'environnement Python courant.
# Usage : bash tools/colmap/install-hloc.sh [--python PATH]
set -euo pipefail

python_bin="${PYTHON_BIN:-python3}"
sg_dir_name="SuperGluePretrainedNetwork"

die() { printf '[ERREUR] Installation HLOC : %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: bash tools/colmap/install-hloc.sh [--python PATH]

Installe hloc (+ LightGlue) via pip depuis GitHub, clone SuperGluePretrainedNetwork
dans site-packages (requis par hloc/extractors/superpoint.py), puis vérifie les imports.
h5py et pycolmap : installer via conda-forge (mamba install -c conda-forge h5py pycolmap).
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      [[ $# -ge 2 && -n "$2" ]] || die "Valeur manquante pour --python"
      python_bin="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Argument inconnu : $1" ;;
  esac
done

command -v git >/dev/null || die "git introuvable."
command -v "$python_bin" >/dev/null || die "Python introuvable : $python_bin"

"$python_bin" -m pip install --upgrade \
  "git+https://github.com/cvg/Hierarchical-Localization.git"

site_packages="$("$python_bin" -c 'import site; print(site.getsitepackages()[0])')"
target="$site_packages/$sg_dir_name"
if [[ ! -f "$target/__init__.py" ]]; then
  rm -rf "$target"
  git clone --depth 1 https://github.com/magicleap/SuperGluePretrainedNetwork.git "$target"
  touch "$target/__init__.py"
fi

"$python_bin" - <<'PY' || die "Imports HLOC incomplets (voir erreur ci-dessus). Pour h5py : mamba install -c conda-forge h5py"
import hloc, lightglue, h5py
from SuperGluePretrainedNetwork.models import superpoint
print("HLOC, LightGlue, h5py et SuperGlue : OK")
PY
