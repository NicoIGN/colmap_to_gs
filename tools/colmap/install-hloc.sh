#!/usr/bin/env bash
# Installe HLOC (avec LightGlue) et le réseau SuperGlue dans l'environnement Python courant.
# Usage : bash tools/colmap/install-hloc.sh [--python PATH]
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../config/defaults/commun.default.sh
source "$script_dir/../../config/defaults/commun.default.sh"

python_bin="${PYTHON_BIN:-python3}"
sg_dir_name="SuperGluePretrainedNetwork"

die() { printf '[ERREUR] Installation HLOC : %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: bash tools/colmap/install-hloc.sh [--python PATH]

Installe hloc (+ LightGlue) via pip depuis GitHub, clone SuperGluePretrainedNetwork
dans site-packages (requis par hloc/extractors/superpoint.py), puis vérifie les imports.
h5py et llvm-openmp : installer via conda-forge (voir environment/*/conda_env.yml).
pycolmap est installé via pip (pinné dans conda_env.yml). Les versions torch, torchvision
et numpy déjà présentes sont figées pendant l'installation pour éviter qu'hloc ne les remplace.
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

# Garde torch/torchvision/numpy déjà installés : pip ne doit pas les remplacer.
constraints="$(mktemp)"
trap 'rm -f "$constraints"' EXIT
"$python_bin" -m pip freeze 2>/dev/null | grep -E '^(torch|torchvision)==' > "$constraints" || true
echo "numpy<2" >> "$constraints"

"$python_bin" -m pip install --upgrade -c "$constraints" \
  "git+https://github.com/cvg/Hierarchical-Localization.git"

site_packages="$("$python_bin" -c 'import site; print(site.getsitepackages()[0])')"
target="$site_packages/$sg_dir_name"
if [[ ! -f "$target/__init__.py" ]]; then
  rm -rf "$target"
  git clone --depth 1 https://github.com/magicleap/SuperGluePretrainedNetwork.git "$target"
  touch "$target/__init__.py"
fi

# Sur macOS, torch et pycolmap (wheels pip) embarquent chacun leur libomp : deux runtimes
# OpenMP dans le même processus provoquent un SIGSEGV. On pointe les copies pip vers la libomp conda.
if [[ "$(uname -s)" == Darwin ]]; then
  conda_omp="$("$python_bin" -c 'import sys; print(sys.prefix)')/lib/libomp.dylib"
  purelib="$("$python_bin" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  if [[ -f "$conda_omp" ]]; then
    while IFS= read -r lib; do
      [[ -L "$lib" ]] && continue
      rm -f "$lib"
      ln -s "$conda_omp" "$lib"
      echo "[HLOC] libomp pip remplacée par un lien vers $conda_omp : $lib"
    done < <(find "$purelib" -path '*/.dylibs/libomp*.dylib' 2>/dev/null)
  else
    echo "[HLOC] attention : $conda_omp introuvable, libomp pip non dédupliquée."
  fi
fi

"$python_bin" - <<'PY' || die "Imports HLOC incomplets (voir erreur ci-dessus). Pour h5py : mamba install -c conda-forge h5py"
import hloc, lightglue, h5py
from SuperGluePretrainedNetwork.models import superpoint
import torch, pycolmap  # même processus : vérifie l'absence de conflit OpenMP
print("HLOC, LightGlue, h5py, SuperGlue, torch et pycolmap : OK")
PY
