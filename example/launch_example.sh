
export  GIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GSPLAT_PROFILE="balanced"

bash $GIT_ROOT/environment/ign.slurm/launch.sh --dataset_dir $GIT_ROOT/example --output_dir $GIT_ROOT/example/output
