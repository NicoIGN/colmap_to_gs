#!/usr/bin/env bash
# Caller supplies repo_root, PYTHON_BIN and OUTPUT_DIR, and sources georef_phase.sh and pipeline_lidar.sh.
# Coordinate-frame selection stays upstream.

# Tail commun : référence géographique puis nettoyage LiDAR du splat final.
finalize_splat() {
  local splat="$1"
  [[ -s "$splat" ]] || {
    printf '[ERREUR] Splat final absent ou vide : %s\n' "$splat" >&2
    return 1
  }
  georef_export "$splat"
  georef_lidar_env
  lidar_pipeline_hook clean --workspace "$OUTPUT_DIR" --splat "$splat"
}

crop_splat_to_sparse() {
  local input="$1" output="$2" sparse="$3" mode="$4" margin="$5"
  shift 5
  "$PYTHON_BIN" "$repo_root/tools/postprocess/filter_splats.py" \
    --input "$input" --output "$output" --mode off \
    --sparse-points "$sparse/points3D.bin" \
    --sparse-bbox-mode "$mode" --sparse-bbox-margin "$margin" \
    "$@" --overwrite
  [[ -s "$output" ]] || {
    printf '[ERREUR] Splat recadré absent ou vide : %s\n' "$output" >&2
    return 1
  }
}

merge_pipeline_splats() {
  local output="$1" strength="$2"
  shift 2
  if [[ $# -eq 1 && "${1##*.}" == "${output##*.}" ]]; then
    cp -f "$1" "$output"
  else
    "$PYTHON_BIN" "$repo_root/tools/postprocess/merge_splats.py" \
      "$@" --output "$output" --strength "$strength"
  fi
  [[ -s "$output" ]] || {
    printf '[ERREUR] Splat fusionné absent ou vide : %s\n' "$output" >&2
    return 1
  }
}
