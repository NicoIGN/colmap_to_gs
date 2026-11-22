# Filtre de densité du sparse, partagé par tools/sfm/run.sh et Panoramax.
# Requiert repo_root, PYTHON_BIN, NUM_CPUS, SFM_DENSITY_* et die.
#
# Usage : sparse_density_stage INPUT_DIR OUTPUT_DIR GUARD_DIR...
#   Produit OUTPUT_DIR_clean (et OUTPUT_DIR_rejected) ; le marqueur .density_done
#   contient la version du filtre et les réglages, pour invalider le cache.
#   Un GUARD_DIR non vide interdit de changer les réglages (les sorties suivantes
#   seraient incohérentes avec le sparse filtré).
sparse_density_stage() {
  local input="$1" output="$2" clean cached="" key version guard
  shift 2
  clean="${output}_clean"
  local filter="$repo_root/tools/sfm/filter_sparse_density.py"
  local resume="$repo_root/tools/images_to_splat/resume.py"

  version="$("$PYTHON_BIN" "$filter" --version)"
  [[ "$version" =~ ^[0-9]+$ ]] || die "Version du filtre de densité invalide : $version"
  awk -v v="$SFM_DENSITY_VOTE_FRACTION" 'BEGIN { exit !(v > 0 && v < 1) }' ||
    die "SFM_DENSITY_VOTE_FRACTION='$SFM_DENSITY_VOTE_FRACTION' doit être dans ]0,1[."
  awk -v v="$SFM_DENSITY_FAR_SUPPORT_RATE" 'BEGIN { exit !(v > 0 && v <= 1) }' ||
    die "SFM_DENSITY_FAR_SUPPORT_RATE='$SFM_DENSITY_FAR_SUPPORT_RATE' doit être dans ]0,1]."

  key="$version $SFM_DENSITY_VOTE_FRACTION $SFM_DENSITY_FAR_SUPPORT_RATE"
  if [[ -f "$clean/.density_done" ]]; then
    IFS= read -r cached < "$clean/.density_done" || true
  fi
  if [[ "$cached" == "$key" ]]; then
    [[ -s "$clean/density_report.json" ]] || die "Filtrage sparse incomplet."
    "$PYTHON_BIN" "$resume" "$clean"
    return 0
  fi
  for guard in "$@"; do
    [[ ! -e "$guard" || -z "$(ls -A "$guard" 2>/dev/null)" ]] ||
      die "Réglages ou version du filtre de densité modifiés : choisir un nouvel OUTPUT_DIR pour refaire le découpage et l'entraînement."
  done
  "$PYTHON_BIN" "$filter" \
    --input "$input" --output "$output" \
    --vote-fraction "$SFM_DENSITY_VOTE_FRACTION" \
    --far-support-rate "$SFM_DENSITY_FAR_SUPPORT_RATE" \
    --workers "$NUM_CPUS" --verbose
  "$PYTHON_BIN" "$resume" "$clean"
  printf '%s\n' "$key" > "$clean/.density_done"
}
