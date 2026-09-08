#!/usr/bin/env bash
set -euo pipefail

GIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GSPLAT_PROFILE="balanced"
OUTPUT_DIR=$GIT_ROOT/example/output

echo  GIT_ROOT=$GIT_ROOT GSPLAT_PROFILE=$GSPLAT_PROFILE bash $GIT_ROOT/environment/ign.slurm/launch.sh --dataset_dir $GIT_ROOT/example --output_dir $OUTPUT_DIR
GIT_ROOT=$GIT_ROOT GSPLAT_PROFILE=$GSPLAT_PROFILE bash $GIT_ROOT/environment/ign.slurm/launch.sh --dataset_dir $GIT_ROOT/example --output_dir $OUTPUT_DIR
