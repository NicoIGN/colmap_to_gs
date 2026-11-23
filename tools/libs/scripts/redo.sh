#!/usr/bin/env bash
# Reprise explicite (--redo) commune aux pipelines images, vidéos et Panoramax.
# Le caller source shell_helpers.sh (die) avant ce fichier.

# --redo ne reprend que l'entraînement d'un bloc ou la fusion.
redo_check_phase() {
  local phase="$1"
  [[ "$phase" == train-block || "$phase" == merge ]] ||
    die "--redo nécessite --phase train-block ou merge."
}

# Chaque bloc doit avoir un splat brut non vide et un export terminé avant fusion.
# Args: gs_root block_count
redo_check_blocks() {
  local gs_root="$1" count="$2" index id
  for ((index=0; index<count; index++)); do
    printf -v id 'block_%03d' "$index"
    [[ -s "$gs_root/$id/splats/$id.splat" ]] || die "Splat brut absent : $gs_root/$id/splats/$id.splat"
    [[ -f "$gs_root/$id/.export_done" ]] || die "Export du bloc incomplet : $id"
  done
}

# Supprime les sorties produites par la fusion, sans toucher aux blocs entraînés.
# Args: output_dir gs_root export_splat_name block_count
redo_clean_merge_outputs() {
  local output_dir="$1" gs_root="$2" export_name="$3" count="$4"
  local stem="${export_name%.splat}" index id
  rm -f -- "$output_dir/.merge_done" "$output_dir/$export_name" \
    "$output_dir/$stem.unfiltered.splat" "$output_dir/$stem.filter.json" \
    "$output_dir/$stem.metadata.json" "$output_dir/$stem.georef.json"
  rm -rf -- "$output_dir/$stem.georef"
  for ((index=0; index<count; index++)); do
    printf -v id 'block_%03d' "$index"
    rm -f -- "$gs_root/$id/splats/$id.bbox.splat"
  done
}
