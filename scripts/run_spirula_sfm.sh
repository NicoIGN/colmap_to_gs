#!/usr/bin/env bash
set -euo pipefail

spirula_bin="${SPIRULA_BIN:-/Users/nbellaiche/DATA/dev/gsplat/spirula-studio/build/spirula}"
dataset="${DATASET:-}"
output_dir="${OUTPUT_DIR:-}"
dry_run=false

# Compatible avec le Bash 3.2 fourni par macOS.
# Chaque option correspond à une variable d'environnement :
# --max-image-size -> MAX_IMAGE_SIZE, etc.
option_names=(
  data-type quality pairs sequence overlap
  features matcher max-image-size
  max-features peak-threshold edge-threshold
  aliked-max-features aliked-min-score lightglue-min-score
  prefilter-neighbors
  max-error min-inliers
  camera-mode camera-model focal
  metric-gps
  masks feature-masks image-exposure
  reuse-features reuse-matches
  threads device
)
option_values=()

die() {
  printf 'Erreur : %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage :
  bash run_spirula_sfm.sh --dataset PATH --output-dir PATH [options]

Entrées :
  --spirula-bin PATH         Exécutable Spirula
  --dataset PATH             Dossier d'images ou dataset contenant images/
  --output-dir PATH          Workspace SFM

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
  --max-error X              3 [défaut], en pixels d'extraction
  --min-inliers N            15 [défaut], minimum par paire vérifiée

Caméras :
  --camera-mode MODE         folder [défaut] | single | image
  --camera-model MODEL       opencv [défaut] ; autres modèles selon Spirula
                             Exemple 360 : equirectangular
  --focal X                  Focale initiale en pixels ; omission = Spirula
  --metric-gps MODE          auto [défaut] | none | horizontal | full

Masques et exposition :
  --masks DIR                Masques d'extraction
  --feature-masks DIR        Masques supplémentaires réservés aux points
  --image-exposure VALUE     auto ou correction en diaphs, par exemple 2 ou -1

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
  SPIRULA_BIN, DATASET, OUTPUT_DIR, puis noms d'options en majuscules
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
    max-error) value=3 ;;
    min-inliers) value=15 ;;
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
  for ((i = 0; i < ${#option_names[@]}; i++)); do
    if [[ "${option_names[$i]}" == "$name" ]]; then
      option_values[$i]="$value"
      return 0
    fi
  done
  die "Option inconnue : --$name ; utilise --help"
}

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

# Spirula valide les types numériques, les valeurs autorisées et les
# compatibilités entre options. Le wrapper ne duplique pas son parseur.
cmd=(
  "$spirula_bin" sfm auto "$dataset"
  --output "$output_dir"
)

for ((i = 0; i < ${#option_names[@]}; i++)); do
  if [[ -n "${option_values[$i]}" ]]; then
    cmd+=("--${option_names[$i]}" "${option_values[$i]}")
  fi
done

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

# Conserver le code de retour exact, notamment pour les résultats partiels.
if "${cmd[@]}"; then
  printf '[SFM] Reconstruction terminée : %s\n' "$output_dir"
else
  status=$?
  case "$status" in
    1) message="Erreur d'utilisation ou d'exécution" ;;
    2) message="Aucune reconstruction" ;;
    3) message="Modèle écrit mais reconstruction partielle ou erreur élevée" ;;
    4) message="Modèle écrit mais repère métrique demandé non obtenu" ;;
    *) message="Échec ou interruption" ;;
  esac
  printf '[SFM] %s (code %s). Workspace : %s\n' \
    "$message" "$status" "$output_dir" >&2
  exit "$status"
fi
