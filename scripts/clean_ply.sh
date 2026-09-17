#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./clean_ply.sh --dataset-dir <DATASET_DIR> --output-dir <OUTPUT_DIR>

Example:
  ./clean_ply.sh \
    --dataset-dir /my/dataset/dir \
    --output-dir /my_output/dir
EOF
}

DATASET_DIR=""
OUTPUT_DIR=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset-dir)
      DATASET_DIR="$2"
      shift 2
      ;;
    --output-dir|-o)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$DATASET_DIR" ]]; then
  echo "Missing --dataset-dir" >&2
  usage >&2
  exit 1
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  echo "Missing --output-dir" >&2
  usage >&2
  exit 1
fi

DATASET_DIR="$(cd "$DATASET_DIR" && pwd)"
OUTPUT_DIR="$(mkdir -p "$OUTPUT_DIR" && cd "$OUTPUT_DIR" && pwd)"

COLMAP_SPARSE_DIR="$DATASET_DIR/colmap/sparse/0"
TRANSFORM_FILE="$DATASET_DIR/transforms.json"
COLMAP_POINTS="$COLMAP_SPARSE_DIR/points3D.bin"

if [[ ! -f "$TRANSFORM_FILE" ]]; then
  echo "Missing transforms.json: $TRANSFORM_FILE" >&2
  exit 1
fi

if [[ ! -f "$COLMAP_POINTS" ]]; then
  echo "Missing COLMAP points3D.bin: $COLMAP_POINTS" >&2
  exit 1
fi

CLEANER_SCRIPT="$SCRIPT_DIR/../python/clean_ply.py"
if [[ ! -f "$CLEANER_SCRIPT" ]]; then
  echo "Missing cleaner script: $CLEANER_SCRIPT" >&2
  exit 1
fi

# Search in output dir for the exported .ply
PLY_FILE=""
for candidate in \
  "$OUTPUT_DIR"/*.ply \
  "$OUTPUT_DIR"/model3d/*.ply \
  "$OUTPUT_DIR"/exports/*.ply
do
  if [[ -f "$candidate" ]]; then
    PLY_FILE="$candidate"
    break
  fi
done

if [[ -z "$PLY_FILE" ]]; then
  echo "No .ply found under output dir: $OUTPUT_DIR" >&2
  echo "Looked in: $OUTPUT_DIR, $OUTPUT_DIR/model3d, $OUTPUT_DIR/exports" >&2
  exit 1
fi

OUT_DIR="$OUTPUT_DIR/cleaned"
mkdir -p "$OUT_DIR"

BASENAME="$(basename "${PLY_FILE%.ply}")"
OUT_PLY="$OUT_DIR/${BASENAME}_cleaned.ply"

echo "Using input PLY: $PLY_FILE"
echo "Using COLMAP points: $COLMAP_POINTS"
echo "Using transform: $TRANSFORM_FILE"
echo "Writing cleaned PLY to: $OUT_PLY"

python "$CLEANER_SCRIPT" \
  --in-ply "$PLY_FILE" \
  --points "$COLMAP_POINTS" \
  --out-ply "$OUT_PLY" \
  --transform "$TRANSFORM_FILE"

echo "✅ Done"
