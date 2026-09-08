#!/usr/bin/env bash
set -euo pipefail

export GIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GSPLAT_PROFILE="balanced"
export OUTPUT_DIR=$GIT_ROOT/example/output

echo  GIT_ROOT=$GIT_ROOT GSPLAT_PROFILE=$GSPLAT_PROFILE bash $GIT_ROOT/environment/ign.slurm/launch.sh --dataset_dir $GIT_ROOT/example --output_dir $OUTPUT_DIR
GIT_ROOT=$GIT_ROOT GSPLAT_PROFILE=$GSPLAT_PROFILE bash $GIT_ROOT/environment/ign.slurm/launch.sh --dataset_dir $GIT_ROOT/example --output_dir $OUTPUT_DIR
