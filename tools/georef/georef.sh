# Phase de géoréférencement optionnelle, appelée après le SfM et avant le filtrage, le découpage et l'entraînement.
# Requiert repo_root, PYTHON_BIN, OUTPUT_DIR, GEOREF_*, COLMAP_USE_MVS et die.
#
# Modes : gpx si GEOREF_GPX_FILE est défini ; positions si SFM_SP_METRIC_POSITIONS existe ;
# exif si GEOREF_FROM_EXIF=true et des coordonnées EXIF existent ; none sinon.
# Le mode est figé dans $OUTPUT_DIR/.georef_mode ; le changer exige un nouvel OUTPUT_DIR.
# Sortie commune : $OUTPUT_DIR/georef/0.

georef_resolved_mode() {
  if [[ -n "$GEOREF_GPX_FILE" ]]; then
    echo gpx
  elif [[ -n "$SFM_SP_METRIC_POSITIONS" && -f "$SFM_SP_METRIC_POSITIONS" ]]; then
    echo positions
  elif [[ "$GEOREF_FROM_EXIF" == true && -f "$OUTPUT_DIR/inputs.json" ]] &&
       "$PYTHON_BIN" -c 'import json, sys
metric = json.load(open(sys.argv[1])).get("gps_exif", {}).get("metric_gps", "none")
sys.exit(0 if metric != "none" else 1)' "$OUTPUT_DIR/inputs.json"; then
    echo exif
  else
    echo none
  fi
}

georef_stored_mode() {
  if [[ -f "$OUTPUT_DIR/.georef_mode" ]]; then
    cat "$OUTPUT_DIR/.georef_mode"
  else
    echo none
  fi
}

georef_check_mode() {
  local resolved stored
  resolved="$(georef_resolved_mode)"
  stored="$(georef_stored_mode)"
  if [[ -f "$OUTPUT_DIR/.georef_mode" && "$stored" != "$resolved" ]]; then
    die "Mode de géoréférencement modifié ($stored -> $resolved) : choisir un nouvel OUTPUT_DIR."
  fi
  georef_mode="$resolved"
}

# Usage : georef_run SFM_MODEL_DIR
georef_run() {
  local input="$1" output="$OUTPUT_DIR/georef/0"
  georef_check_mode
  case "$georef_mode" in
    gpx)
      [[ "$COLMAP_USE_MVS" != true ]] ||
        die "COLMAP_USE_MVS=true est incompatible avec GEOREF_GPX_FILE (le recalage lit le sparse, pas le dense)."
      "$PYTHON_BIN" "$repo_root/tools/georef/georeference.py" prepare
      ;;
    exif)
      local args=(--sparse "$input" --output "$output" --epsg "$GEOREF_EPSG"
                  --images "$OUTPUT_DIR/images" --allow-missing)
      [[ ! -f "$OUTPUT_DIR/projection.json" ]] || args+=(--projection "$OUTPUT_DIR/projection.json")
      "$PYTHON_BIN" "$repo_root/tools/georef/georef_gps.py" "${args[@]}"
      "$PYTHON_BIN" "$repo_root/tools/sfm/resume.py" "$output"
      ;;
    positions)
      "$PYTHON_BIN" "$repo_root/tools/georef/georef_positions.py" --sparse "$input" --output "$output" \
        --positions "$SFM_SP_METRIC_POSITIONS" --epsg "$GEOREF_EPSG"
      "$PYTHON_BIN" "$repo_root/tools/sfm/resume.py" "$output"
      ;;
  esac
  printf '%s\n' "$georef_mode" > "$OUTPUT_DIR/.georef_mode"
}

# Usage : georef_check SFM_MODEL_DIR
georef_check() {
  local input="$1" args
  georef_check_mode
  case "$georef_mode" in
    gpx) "$PYTHON_BIN" "$repo_root/tools/georef/georeference.py" check ;;
    exif)
      args=(--sparse "$input" --output "$OUTPUT_DIR/georef/0" --epsg "$GEOREF_EPSG"
            --images "$OUTPUT_DIR/images" --allow-missing --check)
      [[ ! -f "$OUTPUT_DIR/projection.json" ]] || args+=(--projection "$OUTPUT_DIR/projection.json")
      "$PYTHON_BIN" "$repo_root/tools/georef/georef_gps.py" "${args[@]}"
      ;;
    positions)
      "$PYTHON_BIN" "$repo_root/tools/georef/georef_positions.py" --sparse "$input" --output "$OUTPUT_DIR/georef/0" \
        --positions "$SFM_SP_METRIC_POSITIONS" --epsg "$GEOREF_EPSG" --check
      ;;
  esac
}

# Exporte la référence géographique à côté du splat final.
georef_export() {
  local splat="$1" source
  case "$(georef_stored_mode)" in
    gpx) "$PYTHON_BIN" "$repo_root/tools/georef/georeference.py" export ;;
    exif | positions)
      source=georef/0/gps_positions.json
      [[ "$(georef_stored_mode)" == exif ]] || source="$SFM_SP_METRIC_POSITIONS"
      "$PYTHON_BIN" - "$OUTPUT_DIR/georef/0/georef_stats.json" "$OUTPUT_DIR" "$splat" "$source" <<'PY'
import json, sys
from pathlib import Path
stats, output, splat, source = sys.argv[1:]
data = json.loads(Path(stats).read_text())
data["splat"] = Path(splat).name
data["source_positions"] = source
(Path(output) / (Path(splat).stem + ".georef.json")).write_text(json.dumps(data, indent=2) + "\n")
PY
      ;;
  esac
}

# Vrai si georef/0 est référencé : GPX, ou EXIF avec recalage réussi (status georeferenced).
georef_applied() {
  local mode stats="$OUTPUT_DIR/georef/0/georef_stats.json"
  mode="$(georef_stored_mode)"
  [[ "$mode" != none && -s "$stats" ]] || return 1
  [[ "$mode" == gpx ]] || grep -q '"status": "georeferenced"' "$stats"
}

# Transmet la référence aux nettoyages LiDAR, uniquement si le recalage a réussi.
georef_lidar_env() {
  local stats="$OUTPUT_DIR/georef/0/georef_stats.json"
  georef_applied || return 0
  if [[ "$LIDAR_COLMAP_ALIGN" == true ]]; then
    [[ -n "$LIDAR_COLMAP_GEOREF" ]] || export LIDAR_COLMAP_GEOREF="$stats"
  else
    [[ -n "$LIDAR_CLEAN_GEOREF" ]] || export LIDAR_CLEAN_GEOREF="$stats"
  fi
}
