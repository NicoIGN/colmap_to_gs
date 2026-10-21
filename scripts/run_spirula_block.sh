#!/usr/bin/env bash
set -euo pipefail

# Entrées / sortie
spirula_bin="${SPIRULA_BIN:-./build/spirula}"
sparse_dir="${SPARSE_DIR:-}"
images_dir="${IMAGES_DIR:-}"
output_dir="${OUTPUT_DIR:-}"
train_preset="${TRAIN_PRESET:-3dgs}"
clean="${CLEAN:-false}"

# Paramètres d'entraînement :
# vide = laisser Spirula appliquer les valeurs du preset.
quality="${QUALITY:-}"
cap_max="${CAP_MAX:-}"
densify_loss_map_mode="${DENSIFY_LOSS_MAP_MODE:-}"
densify_score_mode="${DENSIFY_SCORE_MODE:-}"
min_init_fraction="${MIN_INIT_FRACTION:-}"
num_iterations="${NUM_ITERATIONS:-}"
train_resolution_divisor="${TRAIN_RESOLUTION_DIVISOR:-}"

long_axis_split_opacity_k=()
if [[ -n "${LONG_AXIS_SPLIT_OPACITY_K:-}" ]]; then
  values="${LONG_AXIS_SPLIT_OPACITY_K//,/ }"
  read -r -a long_axis_split_opacity_k <<< "$values"
fi

usage() {
  cat <<'EOF'
Usage :
  bash run_spirula_block.sh \
    --sparse-dir /data/sparse_grf_blocks/block_000 \
    --images-dir /data/images \
    --output-dir /data/training/block_000 \
    [options]

Entrées :
  --spirula-bin PATH    Exécutable Spirula
                       Env : SPIRULA_BIN
                       Défaut : ./build/spirula

  --sparse-dir PATH     Dossier d'un bloc contenant directement :
                         cameras.bin, images.bin, points3D.bin
                       ou :
                         cameras.txt, images.txt, points3D.txt
                       Env : SPARSE_DIR

  --images-dir PATH     Racine des images originales.
                       Les noms dans images.bin/txt sont relatifs à ce dossier.
                       Env : IMAGES_DIR

  --output-dir PATH     Workspace de sortie propre au bloc.
                       Doit être distinct et non imbriqué avec les entrées.
                       Env : OUTPUT_DIR

Entraînement :
  --train-preset NAME   Défaut : 3dgs
                       Env : TRAIN_PRESET

  --quality LEVEL      low|medium|high|ultra
                       Env : QUALITY

  --cap-max N          Budget maximal de splats
                       Env : CAP_MAX

  --densify-loss-map-mode MODE
                       Env : DENSIFY_LOSS_MAP_MODE

  --densify-score-mode MODE
                       mean|max|median|geom
                       Env : DENSIFY_SCORE_MODE

  --long-axis-split-opacity-k A B C
                       Env : LONG_AXIS_SPLIT_OPACITY_K
                       Forme alternative :
                         --long-axis-split-opacity-k=0.5,0.6,30000

  --min-init-fraction N
                       Nombre entre 0 et 1
                       Env : MIN_INIT_FRACTION

  --num-iterations N   Env : NUM_ITERATIONS

  --train-resolution-divisor N
                       Entier >= 0 ; 0 = automatique
                       Env : TRAIN_RESOLUTION_DIVISOR

  --clean              Supprimer uniquement OUTPUT_DIR/splats avant calcul.
                       Env : CLEAN
                       Ne supprime jamais le modèle COLMAP source.

  -h, --help           Afficher cette aide.

Les options simples acceptent aussi --option=valeur.

Priorité :
  CLI > variables d'environnement > preset Spirula.

Dépendances du wrapper :
  bash, python3 ; stdbuf est utilisé si disponible.

Aucune reconstruction SFM n'est effectuée.
EOF
}

die() {
  echo "[ERREUR] $*" >&2
  exit 1
}

log() {
  echo "[run_spirula_block] $*"
}

need_arg() {
  [[ $# -ge 2 && -n "${2:-}" ]] ||
    die "Valeur manquante pour $1"
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

# Analyse CLI.
while [[ $# -gt 0 ]]; do
  # Transformer --option=valeur en deux arguments.
  if [[ "$1" == --*=* ]]; then
    option="${1%%=*}"
    value="${1#*=}"
    shift

    if [[ "$option" == "--long-axis-split-opacity-k" ]]; then
      value="${value//,/ }"
      read -r -a long_axis_split_opacity_k <<< "$value"
      continue
    fi

    set -- "$option" "$value" "$@"
  fi

  case "$1" in
    --spirula-bin)
      need_arg "$@"
      spirula_bin="$2"
      shift 2
      ;;
    --sparse-dir)
      need_arg "$@"
      sparse_dir="$2"
      shift 2
      ;;
    --images-dir)
      need_arg "$@"
      images_dir="$2"
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
      [[ $# -ge 4 ]] ||
        die "--long-axis-split-opacity-k attend A B C"
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
    --clean)
      # Accepte aussi --clean=true / --clean=false.
      if [[ "${2:-}" == "true" || "${2:-}" == "false" ]]; then
        clean="$2"
        shift 2
      else
        clean=true
        shift
      fi
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Argument inconnu : $1"
      ;;
  esac
done

# Vérifications.
[[ -n "$sparse_dir" ]] || die "--sparse-dir requis"
[[ -n "$images_dir" ]] || die "--images-dir requis"
[[ -n "$output_dir" ]] || die "--output-dir requis"

[[ -d "$sparse_dir" ]] || die "Bloc introuvable : $sparse_dir"
[[ -d "$images_dir" ]] || die "Images introuvables : $images_dir"

command -v python3 >/dev/null 2>&1 ||
  die "python3 est nécessaire pour vérifier les chemins."

case "$clean" in
  true|1|yes|on) clean=true ;;
  false|0|no|off) clean=false ;;
  *) die "CLEAN invalide : $clean" ;;
esac

case "$train_preset" in
  3dgs|360-camera|in-the-wild|centered-object|hdr|synthetic|meshing) ;;
  *) die "Preset invalide : $train_preset" ;;
esac

if [[ -n "$quality" ]]; then
  case "$quality" in
    low|medium|high|ultra) ;;
    *) die "Quality invalide : $quality" ;;
  esac
fi

if [[ -n "$cap_max" ]]; then
  is_positive_integer "$cap_max" ||
    die "--cap-max doit être un entier > 0"
fi

if [[ -n "$densify_loss_map_mode" ]]; then
  case "$densify_loss_map_mode" in
    none|loss_full|ssim_full|ssim_cs|ssim_structure|edge_aware|robust_edge_aware|loss_full_nms|ssim_full_nms|ssim_cs_nms|ssim_structure_nms) ;;
    *) die "Densify loss map mode invalide : $densify_loss_map_mode" ;;
  esac
fi

if [[ -n "$densify_score_mode" ]]; then
  case "$densify_score_mode" in
    mean|max|median|geom) ;;
    *) die "Densify score mode invalide : $densify_score_mode" ;;
  esac
fi

if [[ ${#long_axis_split_opacity_k[@]} -gt 0 ]]; then
  [[ ${#long_axis_split_opacity_k[@]} -eq 3 ]] ||
    die "--long-axis-split-opacity-k attend exactement 3 valeurs"

  for value in "${long_axis_split_opacity_k[@]}"; do
    is_number "$value" ||
      die "Valeur opacity invalide : $value"
  done
fi

if [[ -n "$min_init_fraction" ]]; then
  python3 - "$min_init_fraction" <<'PY'
import math
import sys

try:
    value = float(sys.argv[1])
    assert math.isfinite(value) and 0 <= value <= 1
except (ValueError, AssertionError):
    sys.exit("--min-init-fraction doit être un nombre dans [0,1]")
PY
fi

if [[ -n "$num_iterations" ]]; then
  is_positive_integer "$num_iterations" ||
    die "--num-iterations doit être un entier > 0"
fi

if [[ -n "$train_resolution_divisor" ]]; then
  is_nonnegative_integer "$train_resolution_divisor" ||
    die "--train-resolution-divisor doit être un entier >= 0"
fi

# Canonicaliser les chemins, y compris les liens symboliques.
canonical_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
}

sparse_dir="$(canonical_path "$sparse_dir")"
images_dir="$(canonical_path "$images_dir")"
output_dir="$(canonical_path "$output_dir")"

# Empêcher toute préparation/nettoyage dans les sources.
python3 - "$sparse_dir" "$images_dir" "$output_dir" <<'PY'
from pathlib import Path
import sys

sparse, images, output = map(Path, sys.argv[1:])

for source in (sparse, images):
    if (
        source == output
        or source in output.parents
        or output in source.parents
    ):
        sys.exit(
            f"Sortie et entrée doivent être distinctes et non imbriquées : "
            f"{output} / {source}"
        )
PY

if [[ "$spirula_bin" == */* ]]; then
  spirula_bin="$(canonical_path "$spirula_bin")"
  [[ -x "$spirula_bin" ]] ||
    die "Exécutable introuvable : $spirula_bin"
else
  spirula_bin="$(command -v "$spirula_bin")" ||
    die "Spirula introuvable dans le PATH"
fi

# Détecter un modèle COLMAP complet.
model_ext=""
for ext in bin txt; do
  if [[ -f "$sparse_dir/cameras.$ext" &&
        -f "$sparse_dir/images.$ext" &&
        -f "$sparse_dir/points3D.$ext" ]]; then
    model_ext="$ext"
    break
  fi
done

[[ -n "$model_ext" ]] ||
  die "Le bloc doit contenir cameras/images/points3D en bin ou txt."

# Préparer le workspace.
mkdir -p "$output_dir"

# Verrou simple : empêche deux entraînements sur la même sortie.
lock_dir="$output_dir/.training_lock"
mkdir "$lock_dir" 2>/dev/null ||
  die "Sortie verrouillée : $lock_dir. Vérifier qu'aucun calcul n'est actif."

cleanup() {
  rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT

splats_dir="$output_dir/splats"

# Refuser les sorties existantes sans demande explicite.
if [[ -e "$splats_dir" || -L "$splats_dir" ]]; then
  [[ "$clean" == true ]] ||
    die "$splats_dir existe déjà. Utiliser une autre sortie ou --clean."

  # Ne pas supprimer un lien ni suivre une destination symbolique.
  [[ ! -L "$splats_dir" && -d "$splats_dir" ]] ||
    die "Nettoyage refusé : splats doit être un vrai dossier."

  log "Suppression des anciens splats : $splats_dir"
  rm -rf -- "$splats_dir"
fi

# Le script gère son propre dossier sparse de travail.
# Refuser les liens afin de ne pas modifier les sources.
[[ ! -L "$output_dir/sparse" ]] ||
  die "$output_dir/sparse ne doit pas être un lien symbolique"

mkdir -p "$output_dir/sparse"

[[ ! -L "$output_dir/sparse/0" ]] ||
  die "$output_dir/sparse/0 ne doit pas être un lien symbolique"

mkdir -p "$output_dir/sparse/0"

# Éviter qu'une ancienne version binaire masque une version texte.
for name in cameras images points3D; do
  for ext in bin txt; do
    target="$output_dir/sparse/0/$name.$ext"
    [[ ! -d "$target" ]] || die "Dossier inattendu : $target"
    rm -f -- "$target"
  done

  cp "$sparse_dir/$name.$model_ext" \
     "$output_dir/sparse/0/$name.$model_ext"
done

# Conserver les métadonnées du bloc si disponibles.
if [[ -f "$sparse_dir/image_membership.json" ]]; then
  target="$output_dir/image_membership.json"
  [[ ! -L "$target" ]] || die "Lien inattendu : $target"
  cp "$sparse_dir/image_membership.json" "$target"
fi

# Ne pas ignorer silencieusement les données de rigs.
for ext in bin txt; do
  for name in rigs frames; do
    if [[ -f "$sparse_dir/$name.$ext" ]]; then
      target="$output_dir/sparse/0/$name.$ext"
      [[ ! -L "$target" ]] || die "Lien inattendu : $target"
      cp "$sparse_dir/$name.$ext" "$target"
    elif [[ -e "$output_dir/sparse/0/$name.$ext" ||
            -L "$output_dir/sparse/0/$name.$ext" ]]; then
      die "Ancien fichier $name.$ext dans la sortie : utiliser une nouvelle sortie."
    fi
  done
done

# Images partagées entre tous les blocs : aucun duplicata.
images_link="$output_dir/images"

if [[ -L "$images_link" ]]; then
  existing="$(canonical_path "$images_link")"
  [[ "$existing" == "$images_dir" ]] ||
    die "$images_link pointe vers une autre source : $existing"
elif [[ -e "$images_link" ]]; then
  die "$images_link existe et n'est pas un lien symbolique."
else
  ln -s "$images_dir" "$images_link"
fi

[[ -d "$images_link" ]] || die "Lien images inaccessible"

# Construire la commande d'entraînement.
cmd=(
  "$spirula_bin" train "$train_preset"
  --data "$output_dir"
  --data-format colmap
  --image-dir images
  --output-dir-prefix "$output_dir"
  --output-dir-name splats
  --lang fr
)

[[ -z "$quality" ]] ||
  cmd+=(--quality "$quality")

[[ -z "$cap_max" ]] ||
  cmd+=(--cap-max "$cap_max")

[[ -z "$densify_loss_map_mode" ]] ||
  cmd+=(--densify-loss-map-mode "$densify_loss_map_mode")

[[ -z "$densify_score_mode" ]] ||
  cmd+=(--densify-score-mode "$densify_score_mode")

if [[ ${#long_axis_split_opacity_k[@]} -gt 0 ]]; then
  cmd+=(
    --long-axis-split-opacity-k
    "${long_axis_split_opacity_k[@]}"
  )
fi

[[ -z "$min_init_fraction" ]] ||
  cmd+=(--min-init-fraction "$min_init_fraction")

[[ -z "$num_iterations" ]] ||
  cmd+=(--num-iterations "$num_iterations")

[[ -z "$train_resolution_divisor" ]] ||
  cmd+=(--train-resolution-divisor "$train_resolution_divisor")

log "Spirula : $spirula_bin"
log "Bloc    : $sparse_dir"
log "Images  : $images_dir"
log "Sortie  : $output_dir"
log "Preset  : $train_preset"
log "Aucune reconstruction SFM."

# Enregistrer la commande exacte, avec échappement shell.
printf '%q ' "${cmd[@]}" > "$output_dir/train_command.sh"
printf '\n' >> "$output_dir/train_command.sh"

log "Commande :"
cat "$output_dir/train_command.sh"

# Le marqueur signifie seulement que le processus précédent a réussi.
# Il n'est pas utilisé pour ignorer un nouveau calcul.
rm -f "$output_dir/.train_done"

# stdbuf n'est pas installé par défaut sur macOS.
# pipefail conserve l'échec de Spirula même avec tee.
if command -v stdbuf >/dev/null 2>&1; then
  stdbuf -oL -eL "${cmd[@]}" 2>&1 |
    tee "$output_dir/train.log"
else
  "${cmd[@]}" 2>&1 |
    tee "$output_dir/train.log"
fi

touch "$output_dir/.train_done"

log "Entraînement terminé."
log "Splats : $splats_dir"
log "Log    : $output_dir/train.log"
