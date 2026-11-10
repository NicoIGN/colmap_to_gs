#!/usr/bin/env bash
# Caller supplies repo_root and PYTHON_BIN; coordinate-frame selection stays upstream.

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
  "$PYTHON_BIN" "$repo_root/tools/postprocess/merge_splats.py" \
    "$@" --output "$output" --strength "$strength"
  [[ -s "$output" ]] || {
    printf '[ERREUR] Splat fusionné absent ou vide : %s\n' "$output" >&2
    return 1
  }
}
