#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=spirula_common.sh
source "$script_dir/spirula_common.sh"

spirula_bin="${SPIRULA_BIN:-$(default_spirula_bin)}"
dataset="${DATASET:-}"
output_dir="${OUTPUT_DIR:-}"
images_dir=""
dry_run=false
preset="${PRESET:-}"
sensor_pairs="${SENSOR_PAIRS:-false}"
prepare="${PREPARE:-}"
keep_size="${KEEP_SIZE:-}"
camera_model_explicit="${CAMERA_MODEL+true}"

# Compatible avec le Bash 3.2 fourni par macOS.
# Chaque option correspond à une variable d'environnement :
# --max-image-size -> MAX_IMAGE_SIZE, etc.
option_names=(
  data-type quality pairs sequence overlap
  features matcher max-image-size
  max-features peak-threshold edge-threshold
  aliked-max-features aliked-min-score lightglue-min-score
  prefilter-neighbors
  max-error min-inliers min-tri-angle ba-loss ba-loss-param
  camera-mode camera-model focal
  metric-gps sensor-pair-radius
  masks feature-masks image-exposure
  reuse-features reuse-matches
  threads device
  max-distance metadata-dir
)
# Options du driver, absentes de `sfm auto` : seul le filtrage GPS les utilise.
driver_only_options=" max-distance metadata-dir "
option_values=()

usage() {
  cat <<'EOF'
Usage :
  bash run_spirula_sfm.sh --dataset PATH --output-dir PATH [options]

Entrées :
  --spirula-bin PATH         Exécutable Spirula ; défaut : spirula du PATH,
                             sinon <dépôt>/../spirula-studio/build/spirula
  --dataset PATH             Dossier d'images ou dataset contenant images/
  --output-dir PATH          Workspace SFM

Préréglage :
  --preset NAME              video [défaut] | panoramax
                             panoramax : photos individuelles géolocalisées
                             (data-type individual, pairs auto, sans séquence,
                             metric-gps full, --sensor-pairs, rayon 30 m)
                             et préparation des images (voir --prepare).
                             Les autres options l'emportent sur le préréglage.

Préparation (activée par --preset panoramax) :
  --prepare                  Avant le calcul : garder les images de la taille
                             la plus fréquente, les lier dans OUTPUT_DIR/images
                             et déduire --focal du champ de vue des
                             métadonnées Panoramax (metadata/*.json).
                             Résumé dans OUTPUT_DIR/selection.json.
  --no-prepare               Utiliser le dataset tel quel
  --keep-size LxH            Taille à garder au lieu de la plus fréquente

Séquence vidéo :
  --data-type TYPE           video [défaut] | individual | internet
  --pairs MODE               sequential [défaut] | auto | exhaustive | prefilter
  --sequence PREFIX          . [défaut] ; "" pour ne pas déclarer de séquence
  --overlap N                10 [défaut], fenêtre de voisinage

Extraction et appariement :
  --quality LEVEL            high [défaut] | low | medium | extreme
  --features NAME            aliked-n16rot [défaut] | aliked-n32 | sift |
                             loma-b128 | loma-b
  --matcher NAME             lightglue [défaut] | bruteforce |
                             loma-b128 | loma-b | loma-r | loma-l | loma-g
  --max-image-size N         1600 [défaut], plus grand côté d'extraction
  --aliked-max-features N    2048 [défaut], budget de points appris
  --aliked-min-score X       0.2 [défaut], seuil de détection ALIKED
  --lightglue-min-score X    0.1 [défaut], seuil de confiance LightGlue
  --max-features N           Budget SIFT ; défaut Spirula si omis
  --peak-threshold X         Seuil de détection SIFT
  --edge-threshold X         Filtrage des réponses d'arête SIFT
  --prefilter-neighbors N    Voisins retenus par la sélection de paires GPU

Vérification géométrique :
  --max-error X              2 [défaut], en pixels d'extraction
  --min-inliers N            30 [défaut], minimum par paire vérifiée
  --min-tri-angle X          6 [défaut], degrés, parallaxe minimale
  --ba-loss LOSS             huber [défaut] | cauchy | trivial
  --ba-loss-param X          2 [défaut], échelle de perte, pixels d'extraction

Caméras :
  --camera-mode MODE         folder [défaut] | single | image
  --camera-model MODEL       opencv [défaut] ; autres modèles selon Spirula
                             Exemple 360 : equirectangular
                             Avec --prepare, equirectangular est déduit si toutes
                             les images retenues sont à 360° (sauf option explicite)
  --focal X                  Focale initiale en pixels ; omission = Spirula
  --metric-gps MODE          auto [défaut] | none | horizontal | full
                             Repère ENU en mètres à partir du GPS EXIF
  --sensor-pairs             Apparie aussi les images proches selon le GPS
  --sensor-pair-radius X     Rayon GPS en mètres ; omission = Spirula (20)

Masques et exposition :
  --masks DIR                Masques d'extraction
  --feature-masks DIR        Masques supplémentaires réservés aux points
  --image-exposure VALUE     auto ou correction en diaphs, par exemple 2 ou -1

Filtrage GPS des paires (remplace sfm auto ; nécessite --prepare) :
  --max-distance M           Rayon strict en mètres ; ne garde que les paires
                             dont les positions GPS sont plus proches.
                             Étapes : extract, match, filter_matches_gps.py, map.
  --metadata-dir DIR         Métadonnées Panoramax ; défaut : DATASET/metadata

Reprise et exécution :
  --reuse-features MODE      auto [défaut] | keep | redo
  --reuse-matches MODE       auto [défaut] | keep | redo
  --threads N                Nombre de fils ; omission = Spirula
  --device VALUE             Périphérique Vulkan ; omission = Spirula
  --dry-run                  Afficher la commande sans l'exécuter
  -h, --help                 Afficher cette aide

Toutes les options à valeur acceptent --option=valeur.
Une valeur vide retire l'option de la commande transmise.

Variables d'environnement :
  SPIRULA_BIN, DATASET, OUTPUT_DIR, PRESET, SENSOR_PAIRS, PREPARE,
  KEEP_SIZE, puis noms d'options en majuscules
  avec "_" : DATA_TYPE, MAX_IMAGE_SIZE, ALIKED_MAX_FEATURES, etc.
  Une variable vide retire l'option correspondante.

Options Spirula supplémentaires :
  Placer après "--" les arguments à transmettre tels quels.
  Exemple : -- --quadratic-overlap --audit

Les images d'une séquence doivent avoir des noms triables :
  frame_000001.jpg, frame_000002.jpg, ... frame_000010.jpg

Aucun entraînement Gaussian Splats. Aucune suppression automatique.
EOF
}

# Valeurs de départ ; les options omises restent gérées par Spirula.
for name in "${option_names[@]}"; do
  value=""
  case "$name" in
    data-type) value=video ;;
    quality) value=high ;;
    pairs) value=sequential ;;
    sequence) value=. ;;
    overlap) value=10 ;;
    features) value=aliked-n16rot ;;
    matcher) value=lightglue ;;
    max-image-size) value=1600 ;;
    aliked-max-features) value=2048 ;;
    aliked-min-score) value=0.2 ;;
    lightglue-min-score) value=0.1 ;;
    max-error) value=2 ;;
    min-inliers) value=30 ;;
    min-tri-angle) value=6 ;;
    ba-loss) value=huber ;;
    ba-loss-param) value=2 ;;
    camera-mode) value=folder ;;
    camera-model) value=opencv ;;
    metric-gps) value=auto ;;
    reuse-features|reuse-matches) value=auto ;;
  esac

  env_name="$(printf '%s' "${name//-/_}" | tr '[:lower:]' '[:upper:]')"

  # Une variable définie, même vide, remplace le défaut.
  if [[ "${!env_name+x}" == x ]]; then
    value="${!env_name}"
  fi

  option_values+=("$value")
done

set_option() {
  local name="$1" value="$2" i
  [[ "$name" != camera-model ]] || camera_model_explicit=true
  for ((i = 0; i < ${#option_names[@]}; i++)); do
    if [[ "${option_names[$i]}" == "$name" ]]; then
      option_values[$i]="$value"
      return 0
    fi
  done
  die "Option inconnue : --$name ; utilise --help"
}

# Le préréglage est lu avant les autres options pour qu'elles le surchargent.
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    --) break ;;
    --preset=*) preset="${args[$i]#*=}" ;;
    --preset) preset="${args[$((i + 1))]:-}" ;;
  esac
done

case "$preset" in
  ""|video) ;;
  panoramax)
    set_option data-type individual
    set_option pairs auto
    set_option sequence ""
    set_option metric-gps full
    set_option sensor-pair-radius 30
    sensor_pairs=true
    [[ -n "$prepare" ]] || prepare=true
    ;;
  *) die "Préréglage inconnu : $preset (video | panoramax)" ;;
esac

extra_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --sensor-pairs)
      sensor_pairs=true
      shift
      ;;
    --no-sensor-pairs)
      sensor_pairs=false
      shift
      ;;
    --prepare)
      prepare=true
      shift
      ;;
    --no-prepare)
      prepare=false
      shift
      ;;
    --keep-size=*)
      keep_size="${1#*=}"
      shift
      ;;
    --keep-size)
      [[ $# -ge 2 ]] || die "Valeur manquante pour --keep-size"
      keep_size="$2"
      shift 2
      ;;
    --preset=*)
      shift
      ;;
    --preset)
      shift 2
      ;;
    --)
      shift
      extra_args=("$@")
      break
      ;;
    --*=*)
      name="${1%%=*}"
      name="${name#--}"
      value="${1#*=}"
      case "$name" in
        spirula-bin) spirula_bin="$value" ;;
        dataset) dataset="$value" ;;
        output-dir) output_dir="$value" ;;
        images-dir) images_dir="$value" ;;
        *) set_option "$name" "$value" ;;
      esac
      shift
      ;;
    --*)
      [[ $# -ge 2 ]] || die "Valeur manquante pour $1"
      [[ "$2" != --* ]] ||
        die "Valeur manquante pour $1 ; utilise --option=valeur si nécessaire"
      name="${1#--}"
      value="$2"
      case "$name" in
        spirula-bin) spirula_bin="$value" ;;
        dataset) dataset="$value" ;;
        output-dir) output_dir="$value" ;;
        images-dir) images_dir="$value" ;;
        *) set_option "$name" "$value" ;;
      esac
      shift 2
      ;;
    *)
      die "Argument inattendu : $1"
      ;;
  esac
done

[[ -n "$dataset" ]] || die "--dataset ou DATASET est obligatoire"
[[ -n "$output_dir" ]] || die "--output-dir ou OUTPUT_DIR est obligatoire"
[[ -d "$dataset" ]] || die "Dataset introuvable : $dataset"

if [[ "$spirula_bin" == */* ]]; then
  [[ -x "$spirula_bin" ]] ||
    die "Exécutable introuvable ou non exécutable : $spirula_bin"
else
  command -v "$spirula_bin" >/dev/null 2>&1 ||
    die "Commande introuvable : $spirula_bin"
fi

source_dataset="$dataset"
case "$prepare" in
  1|true|yes|on)
    prepare_cmd=(
      python3 "$script_dir/../../../tools/panoramax/prepare_sfm.py"
      --dataset "$dataset" --output-dir "$output_dir" --shell
    )
    [[ -z "$keep_size" ]] || prepare_cmd+=(--size "$keep_size")
    [[ -z "$images_dir" ]] || prepare_cmd+=(--images-dir "$images_dir")
    [[ "$dry_run" != true ]] || prepare_cmd+=(--dry-run)
    prepare_output="$("${prepare_cmd[@]}")" || die "Préparation des images échouée"
    prepared_images=""
    prepared_focal=""
    prepared_camera_model=""
    while IFS='=' read -r key value; do
      case "$key" in
        images_dir) prepared_images="$value" ;;
        focal) prepared_focal="$value" ;;
        camera_model) prepared_camera_model="$value" ;;
      esac
    done <<< "$prepare_output"
    [[ -n "$prepared_images" ]] || die "La préparation n'a pas indiqué de dossier d'images"
    dataset="$prepared_images"
    for ((i = 0; i < ${#option_names[@]}; i++)); do
      if [[ "${option_names[$i]}" == camera-model && "$prepared_camera_model" == equirectangular && "$camera_model_explicit" == true && "${option_values[$i]}" != equirectangular ]]; then
        die "Images 360 détectées mais --camera-model ${option_values[$i]} demandé : utiliser equirectangular ou retirer SFM_CAMERA_MODEL."
      fi
    done
    for ((i = 0; i < ${#option_names[@]}; i++)); do
      if [[ "${option_names[$i]}" == focal && -z "${option_values[$i]}" ]]; then
        option_values[$i]="$prepared_focal"
      fi
      if [[ "${option_names[$i]}" == camera-model && -n "$prepared_camera_model" && "$camera_model_explicit" != true ]]; then
        option_values[$i]="$prepared_camera_model"
        printf '[SFM] Images 360 détectées : --camera-model %s\n' "$prepared_camera_model" >&2
      fi
    done
    ;;
  0|false|no|off|"") ;;
  *) die "PREPARE invalide : $prepare" ;;
esac

# Le device n'est pas un argument positionnel de `sfm auto` : il passe par
# --device. Un --device explicite prime sur SPIRULA_DEVICE.
spirula_device="$(resolve_spirula_device "$spirula_bin" "${SPIRULA_DEVICE:-auto}")"
for ((i = 0; i < ${#option_names[@]}; i++)); do
  if [[ "${option_names[$i]}" == device && -z "${option_values[$i]}" && "$spirula_device" != auto ]]; then
    option_values[$i]="$spirula_device"
  fi
done

progress_args=()
case "${VERBOSE:-False}" in
  1|true|True|yes|on) progress_args=(--verbose) ;;
esac
# Sortie Spirula : barre de progression ; détail seulement en verbose.
run_relayed() {
  "$@" 2>&1 | "${PYTHON_BIN:-python3}" "$script_dir/../python/spirula_progress.py" ${progress_args[@]+"${progress_args[@]}"}
}

gps_option() {
  local name="$1" i
  for ((i = 0; i < ${#option_names[@]}; i++)); do
    if [[ "${option_names[$i]}" == "$name" ]]; then
      printf '%s' "${option_values[$i]}"
      return
    fi
  done
}

fail_step() {
  printf 'Erreur : %s (code %s).\n' "$1" "$2" >&2
  exit "$2"
}

show_gps_command() {
  printf '  '
  printf ' %q' "$@"
  printf '\n'
}

max_distance="$(gps_option max-distance)"
if [[ -n "$max_distance" ]]; then
  [[ "$max_distance" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v radius="$max_distance" 'BEGIN { exit !(radius > 0) }' ||
    die "max-distance doit être un nombre positif."
  [[ -n "${prepared_images:-}" ]] || die "Le filtrage GPS nécessite --prepare (préréglage panoramax)."
  [[ ${#extra_args[@]} -eq 0 ]] || die "Options Spirula supplémentaires non prises en charge avec le filtrage GPS."
  metadata_dir="$(gps_option metadata-dir)"
  metadata_dir="${metadata_dir:-$source_dataset/metadata}"

  images="${images_dir:-$output_dir/images}"
  features_dir="$output_dir/features"
  original="$output_dir/matches.bin"
  filtered="$output_dir/matches-gps.bin"
  sparse="$output_dir/sparse"
  marker="$output_dir/.gps_filter_done"

  gps_pairs="$(gps_option pairs)"
  if [[ "$gps_pairs" == auto || -z "$gps_pairs" ]]; then
    selected="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["selected"])' "$output_dir/selection.json")"
    if [[ "$selected" -ge 100 ]]; then gps_pairs=prefilter; else gps_pairs=exhaustive; fi
  fi
  [[ "$gps_pairs" == prefilter || "$gps_pairs" == exhaustive ]] ||
    die "Filtrage GPS : --pairs doit être prefilter, exhaustive ou auto (reçu : $gps_pairs)."

  gps_threads="$(gps_option threads)"
  gps_camera_model="$(gps_option camera-model)"
  gps_camera_model="${gps_camera_model:-opencv}"
  gps_focal="$(gps_option focal)"
  gps_features="$(gps_option features)"
  gps_matcher="$(gps_option matcher)"
  gps_max_image_size="$(gps_option max-image-size)"
  gps_aliked_max_features="$(gps_option aliked-max-features)"
  gps_prefilter_neighbors="$(gps_option prefilter-neighbors)"
  gps_camera_mode="$(gps_option camera-mode)"
  gps_max_error="$(gps_option max-error)"
  gps_min_inliers="$(gps_option min-inliers)"
  gps_min_tri_angle="$(gps_option min-tri-angle)"
  gps_ba_loss="$(gps_option ba-loss)"
  gps_ba_loss_param="$(gps_option ba-loss-param)"

  # Empreinte des masques de features : seules les images PNG comptent, pas les statistiques de masks.json.
  gps_feature_masks="$(gps_option feature-masks)"
  mask_digest=""
  if [[ -n "$gps_feature_masks" ]]; then
    mask_digest="$(python3 - "$gps_feature_masks" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(root.rglob("*.png")):
    digest.update(path.relative_to(root).as_posix().encode())
    digest.update(path.read_bytes())
print(digest.hexdigest())
PY
)" || die "Empreinte des masques impossible : $gps_feature_masks"
  fi
  # Chaque paramètre qui change le modèle entre dans la signature : tout écart est refusé.
  signature="|$max_distance|$gps_pairs|${gps_features:-aliked-n16rot}|${gps_matcher:-lightglue}|${gps_max_image_size:-1600}"
  signature+="|${gps_aliked_max_features:-2048}|${gps_prefilter_neighbors:-16}|${gps_camera_mode:-folder}"
  signature+="|$gps_camera_model|${gps_focal:-}|${gps_max_error:-2}|${gps_min_inliers:-30}|${gps_min_tri_angle:-6}"
  signature+="|${gps_ba_loss:-huber}|${gps_ba_loss_param:-2}|${gps_threads:-}|$mask_digest"

  sparse_complete=false
  if [[ -s "$sparse/0/cameras.bin" && -s "$sparse/0/images.bin" && -s "$sparse/0/points3D.bin" ]]; then
    sparse_complete=true
  fi
  if [[ "$sparse_complete" == true && -f "$marker" && -f "$output_dir/.sfm_done" &&
        "$(cat -- "$marker")" == "$signature" ]]; then
    printf '[SFM GPS] Modèle filtré existant réutilisé : %s\n' "$sparse/0"
    exit 0
  fi
  # Un workspace existant sans signature identique est refusé : ni ancien modèle, ni mélange de partitions.
  for stale in "$marker" "$output_dir/.sfm_done" "$sparse" "$features_dir" "$original" "$filtered"; do
    [[ ! -e "$stale" ]] || die "Workspace SfM existant ou partiel, paramètres GPS/masques différents ou non filtré : $output_dir. Déplacer ce workspace en sauvegarde ; garder les images téléchargées."
  done

  extract_cmd=(
    "$spirula_bin" sfm extract "$images" --output "$features_dir"
    --features "${gps_features:-aliked-n16rot}"
    --max-image-size "${gps_max_image_size:-1600}"
    --aliked-max-features "${gps_aliked_max_features:-2048}"
  )
  [[ -z "$gps_threads" ]] || extract_cmd+=(--decode-threads "$gps_threads")
  [[ -z "$gps_feature_masks" ]] || extract_cmd+=(--feature-masks "$gps_feature_masks")
  match_cmd=(
    "$spirula_bin" sfm match "$features_dir" --output "$original"
    --pairs "$gps_pairs" --prefilter-neighbors "${gps_prefilter_neighbors:-16}"
    --matcher "${gps_matcher:-lightglue}" --camera-mode "${gps_camera_mode:-folder}"
    --camera-model "$gps_camera_model"
    --max-error "${gps_max_error:-2}" --min-inliers "${gps_min_inliers:-30}"
  )
  # Une focale déduite du champ de vue n'a pas de sens pour une équirectangulaire.
  [[ -z "$gps_focal" || "$gps_camera_model" == equirectangular ]] || match_cmd+=(--focal "$gps_focal")
  [[ -z "$gps_threads" ]] || match_cmd+=(--threads "$gps_threads")
  filter_cmd=(
    "${PYTHON_BIN:-python3}" "$script_dir/../../panoramax/filter_matches_gps.py"
    --input "$original" --output "$filtered"
    --metadata-dir "$metadata_dir" --max-distance "$max_distance"
  )
  map_cmd=(
    "$spirula_bin" sfm map "$filtered" "$features_dir"
    --output "$sparse" --image-dir "$images" --metric-gps full
    --max-error "${gps_max_error:-2}" --min-tri-angle "${gps_min_tri_angle:-6}"
    --ba-loss "${gps_ba_loss:-huber}" --ba-loss-param "${gps_ba_loss_param:-2}"
  )
  [[ -z "$gps_threads" ]] || map_cmd+=(--threads "$gps_threads")

  printf '[SFM GPS] Commandes :\n'
  show_gps_command "${extract_cmd[@]}"
  show_gps_command "${match_cmd[@]}"
  show_gps_command "${filter_cmd[@]}"
  show_gps_command "${map_cmd[@]}"
  if [[ "$dry_run" == true ]]; then
    printf '[SFM GPS] Aperçu uniquement : aucun calcul ni dossier créé.\n'
    exit 0
  fi

  mkdir -p -- "$output_dir"
  printf '[SFM GPS] Extraction des points.\n'
  # Les codes de sortie de Spirula sont conservés : le pipeline les interprète (ex. SIGSEGV).
  run_relayed "${extract_cmd[@]}" || fail_step "Extraction SfM GPS échouée" $?
  printf '[SFM GPS] Appariement des paires.\n'
  run_relayed "${match_cmd[@]}" || fail_step "Appariement SfM GPS échoué" $?
  printf '[SFM GPS] Filtrage des paires par distance GPS (< %s m).\n' "$max_distance"
  "${filter_cmd[@]}" || fail_step "Filtrage GPS échoué" $?
  printf '[SFM GPS] Reconstruction.\n'
  run_relayed "${map_cmd[@]}" || fail_step "Reconstruction SfM GPS échouée" $?
  if [[ ! -s "$sparse/0/cameras.bin" || ! -s "$sparse/0/images.bin" || ! -s "$sparse/0/points3D.bin" ]]; then
    die "Modèle COLMAP absent ou incomplet après reconstruction : $sparse/0"
  fi
  printf '%s' "$signature" > "$marker"
  touch -- "$output_dir/.sfm_done"
  printf '[SFM GPS] Reconstruction filtrée terminée : %s\n' "$sparse/0"
  exit 0
fi

# Spirula valide les types numériques, les valeurs autorisées et les
# compatibilités entre options. Le wrapper ne duplique pas son parseur.
cmd=(
  "$spirula_bin" sfm auto "$dataset"
  --output "$output_dir"
)

for ((i = 0; i < ${#option_names[@]}; i++)); do
  if [[ -n "${option_values[$i]}" && "$driver_only_options" != *" ${option_names[$i]} "* ]]; then
    cmd+=("--${option_names[$i]}" "${option_values[$i]}")
  fi
done


case "$sensor_pairs" in
  1|true|yes|on) cmd+=(--sensor-pairs) ;;
  0|false|no|off|"") ;;
  *) die "SENSOR_PAIRS invalide : $sensor_pairs" ;;
esac

if [[ ${#extra_args[@]} -gt 0 ]]; then
  cmd+=("${extra_args[@]}")
fi

printf '[SFM] Commande :'
printf ' %q' "${cmd[@]}"
printf '\n'

if [[ "$dry_run" == true ]]; then
  printf '[SFM] Aperçu uniquement : aucun calcul ni dossier créé.\n'
  exit 0
fi

mkdir -p -- "$output_dir"
# A stale marker must not vouch for a reconstruction that is about to change.
rm -f -- "$output_dir/.sfm_done"

# Do not mistake files from a previous reconstruction for recovered output.
existing_sparse=false
for name in cameras images points3D; do
  [[ ! -e "$output_dir/sparse/0/$name.bin" ]] || existing_sparse=true
done

if run_relayed "${cmd[@]}"; then
  touch -- "$output_dir/.sfm_done"
  printf '[SFM] Reconstruction terminée : %s\n' "$output_dir"
else
  status=$?
  case "$status" in
    1) message="Erreur d'utilisation ou d'exécution" ;;
    2) message="Aucune reconstruction" ;;
    3) message="Modèle écrit mais reconstruction partielle ou erreur élevée" ;;
    4) message="Modèle écrit mais repère métrique demandé non obtenu" ;;
    139) message="Segmentation fault dans Spirula (SIGSEGV)" ;;
    *) message="Échec ou interruption" ;;
  esac
  printf '[SFM] %s (code %s). Workspace : %s\n' \
    "$message" "$status" "$output_dir" >&2
  if [[ "$status" -eq 139 ]]; then
    if [[ "$existing_sparse" == false ]] &&
       PYTHONPATH="$script_dir/../../..${PYTHONPATH:+:$PYTHONPATH}" \
       "${PYTHON_BIN:-python3}" - "$output_dir/sparse/0" <<'PY'
from pathlib import Path
import sys

from tools.preprocess.colmap_split_blocks import (
    read_cameras_binary, read_images_binary, read_points3d_binary,
)

root = Path(sys.argv[1])
try:
    counts = []
    for name, reader in (
        ("cameras", read_cameras_binary),
        ("images", read_images_binary),
        ("points3D", read_points3d_binary),
    ):
        path = root / f"{name}.bin"
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Fichier absent ou vide : {path}")
        count = len(reader(path))
        if count == 0:
            raise ValueError(f"Modèle vide : {path}")
        counts.append(count)
except (OSError, ValueError, EOFError) as exc:
    print(f"[SFM] Validation des fichiers après SIGSEGV impossible : {exc}", file=sys.stderr)
    sys.exit(1)
print(f"[SFM] sparse/0 lisible : {counts[0]} caméras, {counts[1]} images, {counts[2]} points.")
PY
    then
      printf '[AVERTISSEMENT] Spirula a terminé avec SIGSEGV (code 139), mais sparse/0 vient d’être écrit et se lit entièrement. Poursuite avec ce modèle ; la cause du crash reste à diagnostiquer.\n' >&2
      touch -- "$output_dir/.sfm_done"
      exit 0
    fi
    if [[ "$existing_sparse" == true ]]; then
      printf '[SFM] Fichiers sparse/0 préexistants : récupération automatique refusée pour éviter de valider un ancien modèle.\n' >&2
    fi
    printf '[SFM] Si gemm/LightGlue est mentionné plus haut : vérifier la version Spirula et essayer --matcher bruteforce. Ce contournement ne garantit pas de corriger tous les SIGSEGV.\n' >&2
  fi
  exit "$status"
fi
