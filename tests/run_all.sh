#!/usr/bin/env bash
# Lance tous les tests du dépôt, un par un, dans l'ordre alphabétique.
# Usage : tests/run_all.sh [--phase NOM]   (ex. tests/run_all.sh --phase sfm)
# --phase limite la suite au dossier tests/NOM (même nom que la phase dans tools/).
set -uo pipefail

tests_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$tests_dir/.." && pwd)"
python_bin="${PYTHON_BIN:-python3}"
phase=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase) phase="${2:?--phase demande un nom}"; shift 2 ;;
    -h|--help) sed -n '2,4p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[ERREUR] Option inconnue : $1" >&2; exit 2 ;;
  esac
done

search_dir="$tests_dir"
[[ -z "$phase" ]] || search_dir="$tests_dir/$phase"
[[ -d "$search_dir" ]] || { echo "[ERREUR] Phase introuvable : $phase" >&2; exit 2; }

mapfile -t files < <(find "$search_dir" -name 'test_*.py' -not -path '*/__pycache__/*' | sort)
[[ ${#files[@]} -gt 0 ]] || { echo "[ERREUR] Aucun test trouvé dans $search_dir" >&2; exit 2; }

passed=0
failed=()
for file in "${files[@]}"; do
  rel="${file#"$repo_root"/}"
  echo "=== $rel"
  if (cd "$repo_root" && "$python_bin" "$file"); then
    passed=$((passed + 1))
  else
    failed+=("$rel")
  fi
done

echo
echo "Résumé : $passed réussi(s), ${#failed[@]} échoué(s) sur ${#files[@]}."
if [[ ${#failed[@]} -gt 0 ]]; then
  printf '  [ÉCHEC] %s\n' "${failed[@]}"
  exit 1
fi
