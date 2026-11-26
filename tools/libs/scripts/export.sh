#!/usr/bin/env bash
# Caller supplies repo_root, PYTHON_BIN and OUTPUT_DIR, and sources georef.sh and pipeline_lidar.sh.
# Coordinate-frame selection stays upstream.

# Tail commun : référence géographique puis nettoyage LiDAR du splat final.
finalize_splat() {
  local splat="$1" pipeline="$2" stats="$3"
  [[ -s "$splat" ]] || {
    printf '[ERREUR] Splat final absent ou vide : %s\n' "$splat" >&2
    return 1
  }
  georef_export "$splat"
  "$PYTHON_BIN" "$repo_root/tools/libs/python/splat_metadata.py" \
    --splat "$splat" --pipeline "$pipeline" --stats "$stats"
  georef_lidar_env
  lidar_pipeline_hook clean --workspace "$OUTPUT_DIR" --splat "$splat"
}

# Tail commun images, vidéo et Panoramax : les blocs sont déjà filtrés par export_ply
# (support 3σ et filtre contextuel), puis fusionnés et reçoivent la référence géographique.
# Args: gs_root block_count final_splat pipeline stats_path
finish_block_splats() {
  local gs_root="$1" count="$2" final="$3" pipeline="$4" stats="$5"
  local index id
  local inputs=()
  redo_check_blocks "$gs_root" "$count"
  for ((index=0; index<count; index++)); do
    printf -v id 'block_%03d' "$index"
    inputs+=("$gs_root/$id/splats/$id.splat")
  done
  pipeline_report_phase "fusion des splats des blocs"
  merge_pipeline_splats "$final" "$MERGE_STRENGTH" "${inputs[@]}"
  pipeline_report_phase "référence géographique et nettoyage LiDAR"
  finalize_splat "$final" "$pipeline" "$stats"
  touch -- "$OUTPUT_DIR/.merge_done"
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
