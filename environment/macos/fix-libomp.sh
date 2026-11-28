#!/usr/bin/env bash
# Remplace les libomp.dylib embarquées par les wheels pip (torch, pycolmap) par des liens
# vers la libomp de l'environnement conda : un seul runtime OpenMP par processus.
# Usage : bash environment/macos/fix-libomp.sh [--python PATH]
set -euo pipefail

python_bin="${PYTHON_BIN:-python3}"

die() { printf '[ERREUR] fix-libomp : %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: bash environment/macos/fix-libomp.sh [--python PATH]

Sur macOS, chaque libomp.dylib pip (*/.dylibs/libomp*.dylib) est supprimée et remplacée
par un lien symbolique vers $CONDA_PREFIX/lib/libomp.dylib. Corrige « OMP: Error #15 ».
Relancer après toute réinstallation de torch, pycolmap ou d'un paquet qui embarque libomp.
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

[[ "$(uname -s)" == Darwin ]] || die "Ce script ne concerne que macOS."
command -v "$python_bin" >/dev/null || die "Python introuvable : $python_bin"

conda_omp="$("$python_bin" -c 'import sys; print(sys.prefix)')/lib/libomp.dylib"
[[ -f "$conda_omp" ]] || die "libomp conda absente ($conda_omp) : installer llvm-openmp via environment/macos/conda_env.yml."

purelib="$("$python_bin" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
count=0
while IFS= read -r lib; do
  [[ -L "$lib" ]] && continue
  rm -f "$lib"
  ln -s "$conda_omp" "$lib"
  echo "[libomp] lien vers $conda_omp : $lib"
  count=$((count + 1))
done < <(find "$purelib" -path '*/.dylibs/libomp*.dylib' 2>/dev/null)

echo "[libomp] $count libomp pip remplacée(s)."
