#!/usr/bin/env bash
# Single-allocation DAG for script/*.slurm: prepare -> concurrent blocks -> merge.
# Each block runs as its own srun step bound to one GPU of the allocation.

pipeline_sbatch_gpu_count() {
  local count="${SLURM_GPUS_ON_NODE:-}"
  if [[ ! "$count" =~ ^[1-9][0-9]*$ && -n "${SLURM_JOB_GPUS:-}" ]]; then
    IFS=, read -r -a ids <<< "$SLURM_JOB_GPUS"
    count="${#ids[@]}"
  fi
  [[ "$count" =~ ^[1-9][0-9]*$ ]] || count=1
  printf '%s\n' "$count"
}

# Usage: pipeline_sbatch_run LAUNCHER OUTPUT_DIR NUM_BLOCKS -- LAUNCHER_ARGS_BEFORE_PHASE -- LAUNCHER_ARGS_AFTER_PHASE
pipeline_sbatch_run() {
  local launcher="$1" output_dir="$2" num_blocks="$3"
  shift 3
  [[ "$1" == -- ]] || return 2
  shift
  local -a before=() after=()
  while (($#)) && [[ "$1" != -- ]]; do before+=("$1"); shift; done
  [[ "${1:-}" == -- ]] && shift
  after=("$@")

  local gpus cpus mem_total parallel block_cpus block_mem
  gpus="$(pipeline_sbatch_gpu_count)"
  cpus="${SLURM_CPUS_PER_TASK:-${SLURM_CPUS_ON_NODE:-1}}"
  mem_total="${SLURM_MEM_PER_NODE:-}"
  parallel=$((gpus < num_blocks ? gpus : num_blocks))
  block_cpus=$((cpus / parallel)); ((block_cpus >= 1)) || block_cpus=1

  local -a common=(--exact --ntasks=1 --gres=gpu:1)
  local full_opts="${common[*]} --cpus-per-task=$cpus"
  local block_opts="${common[*]} --cpus-per-task=$block_cpus"
  if [[ "$mem_total" =~ ^[0-9]+$ ]]; then
    block_mem=$((mem_total / parallel))
    full_opts+=" --mem=${mem_total}M"
    block_opts+=" --mem=${block_mem}M"
  fi

  mkdir -p "$output_dir/logs"
  printf '[sbatch] Job=%s GPU=%s CPU=%s blocs=%s parallèles=%s\n' \
    "$SLURM_JOB_ID" "$gpus" "$cpus" "$num_blocks" "$parallel"

  printf '[sbatch] Étape prepare\n'
  PIPELINE_SRUN_OPTIONS="$full_opts" PIPELINE_STEP_CPUS="$cpus" \
    bash "$launcher" ${before[@]+"${before[@]}"} prepare ${after[@]+"${after[@]}"} || {
      printf '[ERREUR] Préparation en échec ; blocs et fusion non lancés.\n' >&2
      return 1
    }

  local index running=0 failed=0 log
  for ((index = 0; index < num_blocks; index++)); do
    if ((running >= parallel)); then
      wait -n || failed=1
      running=$((running - 1))
    fi
    ((failed == 0)) || break
    log="$output_dir/logs/sbatch-block-${SLURM_JOB_ID}_${index}"
    printf '[sbatch] Bloc %s lancé ; journaux : %s.{out,err}\n' "$index" "$log"
    SLURM_ARRAY_TASK_ID="$index" SLURM_ARRAY_JOB_ID="$SLURM_JOB_ID" \
      PIPELINE_SRUN_OPTIONS="$block_opts" PIPELINE_STEP_CPUS="$block_cpus" \
      bash "$launcher" ${before[@]+"${before[@]}"} train-block ${after[@]+"${after[@]}"} \
      >"$log.out" 2>"$log.err" &
    running=$((running + 1))
  done
  while ((running > 0)); do
    wait -n || failed=1
    running=$((running - 1))
  done
  if ((failed)); then
    printf '[ERREUR] Au moins un bloc a échoué ; fusion non lancée. Voir %s/logs/sbatch-block-%s_*.err\n' \
      "$output_dir" "$SLURM_JOB_ID" >&2
    return 1
  fi

  printf '[sbatch] Étape merge\n'
  PIPELINE_SRUN_OPTIONS="$full_opts" PIPELINE_STEP_CPUS="$cpus" \
    bash "$launcher" ${before[@]+"${before[@]}"} merge ${after[@]+"${after[@]}"}
  printf '[sbatch] Pipeline terminé.\n'
}
