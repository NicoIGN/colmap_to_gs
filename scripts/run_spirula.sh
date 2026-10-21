#!/usr/bin/env bash
set -euo pipefail

spirula_bin="${SPIRULA_BIN:-./build/spirula}"
dataset="${DATASET:-}"
output_dir="${OUTPUT_DIR:-}"
clean="${CLEAN:-false}"
train_preset="${TRAIN_PRESET:-3dgs}"

max_resolution="${MAX_RESOLUTION:-}"
pairs="${PAIRS:-}"
matcher="${MATCHER:-}"
sfm_max_res_option="${SFM_MAX_RES_OPTION:-}"

# Nouveaux paramètres SFM GPS / 360
metric_gps="${METRIC_GPS:-}"
camera_model="${CAMERA_MODEL:-}"

# Paramètres d'entraînement Gaussian Splats
# Vides par défaut : Spirula applique les valeurs du preset.
quality="${QUALITY:-}"
cap_max="${CAP_MAX:-}"
densify_loss_map_mode="${DENSIFY_LOSS_MAP_MODE:-}"
densify_score_mode="${DENSIFY_SCORE_MODE:-}"
min_init_fraction="${MIN_INIT_FRACTION:-}"
num_iterations="${NUM_ITERATIONS:-}"
train_resolution_divisor="${TRAIN_RESOLUTION_DIVISOR:-}"

# Trois valeurs : opacity initiale, opacity finale, nombre d'étapes
long_axis_split_opacity_k=()
if [[ -n "${LONG_AXIS_SPLIT_OPACITY_K:-}" ]]; then
  read -r -a long_axis_split_opacity_k <<< "$LONG_AXIS_SPLIT_OPACITY_K"
fi

sfm_extra_args=()

usage() {
  cat <<'EOF'
Usage:
  bash run_spirula.sh [options]

Options:
  --spirula-bin PATH    Chemin vers l'exécutable Spirula
                        Env: SPIRULA_BIN
                        Défaut: ./build/spirula

  --dataset PATH        Dossier du dataset d'entrée
                        Env: DATASET

  --output-dir PATH     Dossier de sortie / workspace SFM
                        Env: OUTPUT_DIR

  --train-preset NAME   Preset d'entraînement Spirula
                        Env: TRAIN_PRESET
                        Défaut: 3dgs
                        Valeurs typiques:
                          3dgs
                          360-camera
                          in-the-wild
                          centered-object
                          hdr
                          synthetic
                          meshing

  --clean               Supprime les données existantes avant exécution
                        Env: CLEAN
                        Défaut: false

  --max-resolution N    Résolution max des images pour SFM
                        Env: MAX_RESOLUTION
                        Défaut: celle de Spirula
                        Nécessite aussi SFM_MAX_RES_OPTION si fourni

  --pairs MODE          Sélection des paires SFM
                        Env: PAIRS
                        Défaut: celle de Spirula
                        Valeurs:
                          auto
                          exhaustive
                          sequential
                          prefilter
                          gps

  --matcher NAME        Méthode d'appariement SFM
                        Env: MATCHER
                        Défaut: celle de Spirula
                        Valeurs:
                          bruteforce
                          lightglue
                          loma-b128
                          loma-b
                          loma-r
                          loma-l
                          loma-g

  --metric-gps MODE     Active le repère métrique via GPS EXIF
                        Env: METRIC_GPS
                        Valeurs:
                          horizontal
                          3d

  --camera-model NAME   Force un modèle caméra SFM
                        Env: CAMERA_MODEL
                        Valeurs:
                          equirect

  --sfm-arg ARG         Argument supplémentaire passé tel quel à 'sfm auto'
                        Répétable
                        Exemple:
                          --sfm-arg --sequence --sfm-arg .
                          --sfm-arg --overlap --sfm-arg 12

  Paramètres d'entraînement Gaussian Splats :
  --quality LEVEL       Niveau de qualité : low|medium|high|ultra
                        Env: QUALITY
                        Défaut: celui du preset (généralement medium)

  --cap-max N           Nombre maximal de splats
                        Env: CAP_MAX
                        Défaut: celui du preset (généralement 1000000)

  --densify-loss-map-mode MODE
                        Mode de calcul de la carte d'erreur pour la densification
                        Env: DENSIFY_LOSS_MAP_MODE
                        Valeurs:
                          none
                          loss_full
                          ssim_full
                          ssim_cs
                          ssim_structure
                          edge_aware
                          robust_edge_aware
                          loss_full_nms
                          ssim_full_nms
                          ssim_cs_nms
                          ssim_structure_nms

  --densify-score-mode MODE
                        Agrégation du score de densification
                        Env: DENSIFY_SCORE_MODE
                        Valeurs: mean|max|median|geom

  --long-axis-split-opacity-k A B C
                        Paramètres de fractionnement des splats
                        Env: LONG_AXIS_SPLIT_OPACITY_K
                        Valeurs séparées par des espaces
                        Exemple: "0.5 0.6 30000"
                        Défaut: celui du preset

  --min-init-fraction N Fraction minimale du budget à l'initialisation
                        Env: MIN_INIT_FRACTION
                        Défaut: celui du preset (généralement 0)

  --num-iterations N    Nombre d'étapes d'entraînement
                        Env: NUM_ITERATIONS
                        Défaut: 30000

  --train-resolution-divisor N
                        Diviseur de résolution d'entraînement
                        Env: TRAIN_RESOLUTION_DIVISOR
                        Défaut: 0 (valeur automatique de Spirula)

  -h, --help            Afficher cette aide

Pour les options compatibles, la forme --option=valeur est également acceptée.
Pour --long-axis-split-opacity-k, la forme avec "=" utilise des virgules :
  --long-axis-split-opacity-k=0.5,0.6,30000

Priorité:
  1. options CLI
  2. variables d'environnement
  3. valeurs par défaut du preset Spirula
EOF
}

die() {
  echo "Erreur: $*" >&2
  exit 1
}

log() {
  echo "[run_spirula] $*"
}

need_arg() {
  [[ $# -ge 2 ]] || die "Valeur manquante pour $1"
  [[ -n "${2:-}" ]] || die "Valeur vide pour $1"
}

is_true() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|y|on) return 0 ;;
    0|false|no|n|off|"") return 1 ;;
    *) die "Booléen invalide: $1" ;;
  esac
}

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

is_nonnegative_integer() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

is_number() {
  [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

split_parent_and_name() {
  local path="$1"
  output_parent_dir="$(dirname "$path")"
  output_name="$(basename "$path")"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --spirula-bin)
      need_arg "$@"
      spirula_bin="$2"
      shift 2
      ;;
    --dataset)
      need_arg "$@"
      dataset="$2"
      shift 2
      ;;
    --output-dir)
      need_arg "$@"
      output_dir="$2"
      shift 2
      ;;
    --train-preset)
      need_arg "$@"
      train_preset="$2"
      shift 2
      ;;
    --clean)
      clean=true
      shift
      ;;
    --max-resolution)
      need_arg "$@"
      max_resolution="$2"
      shift 2
      ;;
    --pairs)
      need_arg "$@"
      pairs="$2"
      shift 2
      ;;
    --matcher)
      need_arg "$@"
      matcher="$2"
      shift 2
      ;;
    --metric-gps)
      need_arg "$@"
      metric_gps="$2"
      shift 2
      ;;
    --camera-model)
      need_arg "$@"
      camera_model="$2"
      shift 2
      ;;
    --sfm-arg)
      need_arg "$@"
      sfm_extra_args+=("$2")
      shift 2
      ;;

    # Paramètres d'entraînement
    --quality)
      need_arg "$@"
      quality="$2"
      shift 2
      ;;
    --cap-max)
      need_arg "$@"
      cap_max="$2"
      shift 2
      ;;
    --densify-loss-map-mode)
      need_arg "$@"
      densify_loss_map_mode="$2"
      shift 2
      ;;
    --densify-score-mode)
      need_arg "$@"
      densify_score_mode="$2"
      shift 2
      ;;
    --long-axis-split-opacity-k)
      [[ $# -ge 4 ]] || die "Valeurs attendues pour --long-axis-split-opacity-k : A B C"
      [[ -n "$2" && -n "$3" && -n "$4" ]] ||
        die "Valeurs vides pour --long-axis-split-opacity-k"
      long_axis_split_opacity_k=("$2" "$3" "$4")
      shift 4
      ;;
    --min-init-fraction)
      need_arg "$@"
      min_init_fraction="$2"
      shift 2
      ;;
    --num-iterations)
      need_arg "$@"
      num_iterations="$2"
      shift 2
      ;;
    --train-resolution-divisor)
      need_arg "$@"
      train_resolution_divisor="$2"
      shift 2
      ;;

    --spirula-bin=*)
      spirula_bin="${1#*=}"
      shift
      ;;
    --dataset=*)
      dataset="${1#*=}"
      shift
      ;;
    --output-dir=*)
      output_dir="${1#*=}"
      shift
      ;;
    --train-preset=*)
      train_preset="${1#*=}"
      shift
      ;;
    --clean=*)
      clean="${1#*=}"
      shift
      ;;
    --max-resolution=*)
      max_resolution="${1#*=}"
      shift
      ;;
    --pairs=*)
      pairs="${1#*=}"
      shift
      ;;
    --matcher=*)
      matcher="${1#*=}"
      shift
      ;;
    --metric-gps=*)
      metric_gps="${1#*=}"
      shift
      ;;
    --camera-model=*)
      camera_model="${1#*=}"
      shift
      ;;

    --quality=*)
      quality="${1#*=}"
      shift
      ;;
    --cap-max=*)
      cap_max="${1#*=}"
      shift
      ;;
    --densify-loss-map-mode=*)
      densify_loss_map_mode="${1#*=}"
      shift
      ;;
    --densify-score-mode=*)
      densify_score_mode="${1#*=}"
      shift
      ;;
    --long-axis-split-opacity-k=*)
      split_values="${1#*=}"
      split_values="${split_values//,/ }"
      read -r -a long_axis_split_opacity_k <<< "$split_values"
      shift
      ;;
    --min-init-fraction=*)
      min_init_fraction="${1#*=}"
      shift
      ;;
    --num-iterations=*)
      num_iterations="${1#*=}"
      shift
      ;;
    --train-resolution-divisor=*)
      train_resolution_divisor="${1#*=}"
      shift
      ;;

    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Argument inconnu: $1"
      ;;
  esac
done

[[ -n "$dataset" ]] || die "Tu dois fournir --dataset ou définir DATASET"
[[ -n "$output_dir" ]] || die "Tu dois fournir --output-dir ou définir OUTPUT_DIR"
[[ -d "$dataset" ]] || die "Dataset introuvable: $dataset"

case "$train_preset" in
  3dgs|360-camera|in-the-wild|centered-object|hdr|synthetic|meshing) ;;
  *) die "Preset d'entraînement invalide: $train_preset" ;;
esac

if [[ -n "$max_resolution" ]]; then
  is_positive_integer "$max_resolution" ||
    die "--max-resolution doit être un entier positif: $max_resolution"
  [[ -n "$sfm_max_res_option" ]] ||
    die "Tu utilises --max-resolution mais SFM_MAX_RES_OPTION n'est pas défini"
fi

if [[ -n "$pairs" ]]; then
  case "$pairs" in
    auto|exhaustive|sequential|prefilter|gps) ;;
    *) die "--pairs invalide: $pairs" ;;
  esac
fi

if [[ -n "$matcher" ]]; then
  case "$matcher" in
    bruteforce|lightglue|loma-b128|loma-b|loma-r|loma-l|loma-g) ;;
    *) die "--matcher invalide: $matcher" ;;
  esac
fi

if [[ -n "$metric_gps" ]]; then
  case "$metric_gps" in
    horizontal|3d) ;;
    *) die "--metric-gps invalide: $metric_gps" ;;
  esac
fi

if [[ -n "$camera_model" ]]; then
  case "$camera_model" in
    equirect) ;;
    *) die "--camera-model invalide (supporté ici: equirect): $camera_model" ;;
  esac
fi

if [[ -n "$quality" ]]; then
  case "$quality" in
    low|medium|high|ultra) ;;
    *) die "--quality invalide: $quality" ;;
  esac
fi

if [[ -n "$cap_max" ]]; then
  is_positive_integer "$cap_max" ||
    die "--cap-max doit être un entier positif: $cap_max"
fi

if [[ -n "$densify_loss_map_mode" ]]; then
  case "$densify_loss_map_mode" in
    none|loss_full|ssim_full|ssim_cs|ssim_structure|edge_aware|robust_edge_aware|loss_full_nms|ssim_full_nms|ssim_cs_nms|ssim_structure_nms) ;;
    *) die "--densify-loss-map-mode invalide: $densify_loss_map_mode" ;;
  esac
fi

if [[ -n "$densify_score_mode" ]]; then
  case "$densify_score_mode" in
    mean|max|median|geom) ;;
    *) die "--densify-score-mode invalide: $densify_score_mode" ;;
  esac
fi

if [[ ${#long_axis_split_opacity_k[@]} -gt 0 ]]; then
  [[ ${#long_axis_split_opacity_k[@]} -eq 3 ]] ||
    die "--long-axis-split-opacity-k nécessite exactement 3 valeurs"
  for value in "${long_axis_split_opacity_k[@]}"; do
    is_number "$value" ||
      die "Valeur numérique invalide pour --long-axis-split-opacity-k: $value"
  done
fi

if [[ -n "$min_init_fraction" ]]; then
  is_number "$min_init_fraction" ||
    die "--min-init-fraction doit être un nombre positif ou nul: $min_init_fraction"
fi

if [[ -n "$num_iterations" ]]; then
  is_positive_integer "$num_iterations" ||
    die "--num-iterations doit être un entier positif: $num_iterations"
fi

if [[ -n "$train_resolution_divisor" ]]; then
  is_nonnegative_integer "$train_resolution_divisor" ||
    die "--train-resolution-divisor doit être un entier positif ou nul: $train_resolution_divisor"
fi

if [[ "$spirula_bin" == */* ]]; then
  [[ -x "$spirula_bin" ]] ||
    die "Exécutable Spirula introuvable ou non exécutable: $spirula_bin"
else
  command -v "$spirula_bin" >/dev/null 2>&1 ||
    die "Commande Spirula introuvable dans le PATH: $spirula_bin"
fi

clean_requested=false
if is_true "$clean"; then
  clean_requested=true
fi

split_parent_and_name "$output_dir"

if $clean_requested; then
  if [[ -e "$output_dir" ]]; then
    log "Nettoyage du workspace SFM existant: $output_dir"
    rm -rf "$output_dir"
  fi
fi

mkdir -p "$output_dir"
mkdir -p "$output_parent_dir"

echo "Spirula                  : $spirula_bin"
echo "Dataset                  : $dataset"
echo "Sortie SFM               : $output_dir"
echo "Preset train             : $train_preset"
echo "Clean                    : $clean_requested"
echo "Max resolution           : ${max_resolution:-défaut Spirula}"
echo "Pairs                    : ${pairs:-défaut Spirula}"
echo "Matcher                  : ${matcher:-défaut Spirula}"
echo "Metric GPS               : ${metric_gps:-désactivé}"
echo "Camera model             : ${camera_model:-défaut Spirula}"
echo "Quality                  : ${quality:-défaut du preset}"
echo "Cap max                  : ${cap_max:-défaut du preset}"
echo "Densify loss map mode    : ${densify_loss_map_mode:-défaut du preset}"
echo "Densify score mode       : ${densify_score_mode:-défaut du preset}"
echo "Long axis split opacity k: ${long_axis_split_opacity_k[*]:-défaut du preset}"
echo "Min init fraction        : ${min_init_fraction:-défaut du preset}"
echo "Num iterations           : ${num_iterations:-défaut Spirula}"
echo "Train resolution divisor : ${train_resolution_divisor:-défaut Spirula}"

sfm_done="$output_dir/.sfm_done"
train_done="$output_dir/.train_done"

run_sfm() {
  if [[ -f "$sfm_done" && "$clean_requested" == false ]]; then
    log "SFM déjà effectué, étape ignorée."
    return
  fi

  log "Étape 1/2 : reconstruction SFM"

  local -a cmd
  cmd=("$spirula_bin" sfm auto "$dataset" --output "$output_dir" --lang fr)

  if [[ -n "$max_resolution" ]]; then
    cmd+=("$sfm_max_res_option" "$max_resolution")
  fi

  if [[ -n "$pairs" ]]; then
    cmd+=(--pairs "$pairs")
  fi

  if [[ -n "$matcher" ]]; then
    cmd+=(--matcher "$matcher")
  fi

  if [[ -n "$metric_gps" ]]; then
    cmd+=(--metric-gps "$metric_gps")
  fi

  if [[ -n "$camera_model" ]]; then
    cmd+=(--camera-model "$camera_model")
  fi

  if [[ ${#sfm_extra_args[@]} -gt 0 ]]; then
    cmd+=("${sfm_extra_args[@]}")
  fi

  log "Commande SFM : ${cmd[*]}"
  "${cmd[@]}"

  touch "$sfm_done"
}

run_train() {
  if [[ -f "$train_done" && "$clean_requested" == false ]]; then
    log "Entraînement Gaussian Splats déjà effectué, étape ignorée."
    return
  fi

  local images_source
  images_source="$(cd "$dataset" && pwd -P)"

  if [[ ! -e "$output_dir/images" && ! -L "$output_dir/images" ]]; then
    log "Lien vers les images originales : $images_source"
    ln -s "$images_source" "$output_dir/images"
  fi

  [[ -d "$output_dir/images" ]] ||
    die "Le chemin $output_dir/images n'est pas un dossier accessible"

  log "Étape 2/2 : entraînement Gaussian Splats"

  local -a cmd
  cmd=(
    "$spirula_bin" train "$train_preset"
    --data "$output_dir"
    --data-format colmap
    --image-dir images
    --output-dir-prefix "$output_dir"
    --output-dir-name splats
    --lang fr
  )

  if [[ -n "$quality" ]]; then
    cmd+=(--quality "$quality")
  fi

  if [[ -n "$cap_max" ]]; then
    cmd+=(--cap-max "$cap_max")
  fi

  if [[ -n "$densify_loss_map_mode" ]]; then
    cmd+=(--densify-loss-map-mode "$densify_loss_map_mode")
  fi

  if [[ -n "$densify_score_mode" ]]; then
    cmd+=(--densify-score-mode "$densify_score_mode")
  fi

  if [[ ${#long_axis_split_opacity_k[@]} -gt 0 ]]; then
    cmd+=(--long-axis-split-opacity-k "${long_axis_split_opacity_k[@]}")
  fi

  if [[ -n "$min_init_fraction" ]]; then
    cmd+=(--min-init-fraction "$min_init_fraction")
  fi

  if [[ -n "$num_iterations" ]]; then
    cmd+=(--num-iterations "$num_iterations")
  fi

  if [[ -n "$train_resolution_divisor" ]]; then
    cmd+=(--train-resolution-divisor "$train_resolution_divisor")
  fi

  log "Commande entraînement : ${cmd[*]}"
  stdbuf -oL -eL "${cmd[@]}"

  touch "$train_done"
}

run_sfm
run_train

log "Pipeline terminé."
