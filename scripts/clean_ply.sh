#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./clean_ply.sh \
    --transform <transforms.json> \
    --points <points3D.bin> \
    --in-ply <input.ply> \
    --out-ply <output.ply>
EOF
}

die() {
  echo "Erreur : $*" >&2
  exit 1
}

TRANSFORM_FILE=""
COLMAP_POINTS=""
IN_PLY=""
OUT_PLY=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANER_SCRIPT="$SCRIPT_DIR/../python/clean_ply.py"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --transform|--points|--in-ply|--out-ply|-o)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] \
        || die "Valeur manquante pour $1"

      case "$1" in
        --transform) TRANSFORM_FILE="$2" ;;
        --points)    COLMAP_POINTS="$2" ;;
        --in-ply)    IN_PLY="$2" ;;
        --out-ply|-o) OUT_PLY="$2" ;;
      esac
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "Argument inconnu : $1"
      ;;
  esac
done

[[ -n "$TRANSFORM_FILE" ]] || die "Argument --transform requis"
[[ -n "$COLMAP_POINTS" ]]  || die "Argument --points requis"
[[ -n "$IN_PLY" ]]         || die "Argument --in-ply requis"
[[ -n "$OUT_PLY" ]]        || die "Argument --out-ply requis"

for file in "$TRANSFORM_FILE" "$COLMAP_POINTS" "$IN_PLY" "$CLEANER_SCRIPT"; do
  [[ -f "$file" && -r "$file" ]] \
    || die "Fichier introuvable ou illisible : $file"
done

command -v python >/dev/null 2>&1 || die "Python est introuvable"

# Vérification avant toute création de répertoire ou écriture.
python - "$IN_PLY" "$OUT_PLY" <<'PY'
import os
import sys

source, destination = sys.argv[1:]


def fail(message):
    print(f"Erreur : {message}", file=sys.stderr)
    sys.exit(1)


if os.path.realpath(source) == os.path.realpath(destination):
    fail("Le fichier de sortie doit être différent du fichier d'entrée.")

if os.path.exists(destination) and os.path.samefile(source, destination):
    fail("L'entrée et la sortie désignent le même fichier (lien physique).")

if os.path.isdir(destination):
    fail("Le chemin de sortie désigne un répertoire.")
PY

mkdir -p -- "$(dirname -- "$OUT_PLY")"

printf 'PLY en entrée : %s\n' "$IN_PLY"
printf 'Points COLMAP : %s\n' "$COLMAP_POINTS"
printf 'Transformation : %s\n' "$TRANSFORM_FILE"
printf 'PLY en sortie : %s\n' "$OUT_PLY"

python "$CLEANER_SCRIPT" \
  --in-ply "$IN_PLY" \
  --points "$COLMAP_POINTS" \
  --out-ply "$OUT_PLY" \
  --transform "$TRANSFORM_FILE"

echo "✅ Terminé"
