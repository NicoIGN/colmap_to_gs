#!/usr/bin/env bash
# Shared Slurm submission primitive; launchers keep their resource and log policies.

pipeline_submit_job() {
  local stage="$1" status=0 submission
  shift
  submission="$(sbatch "$@")" || status=$?
  PIPELINE_JOB_ID="${submission%%;*}"
  if [[ "$PIPELINE_JOB_ID" =~ ^[0-9]+$ ]]; then
    PIPELINE_SUBMITTED_JOBS+=("$PIPELINE_JOB_ID")
    printf '[slurm] %s : job %s\n' "$stage" "$submission"
  elif [[ "$status" -eq 0 ]]; then
    printf '[ERREUR] Réponse sbatch invalide : %s\n' "$submission" >&2
    status=1
  fi
  if [[ "$status" -ne 0 ]]; then
    printf '[ERREUR] Échec Slurm à l’étape %s (soumission ou exécution), code %s.\n' \
      "$stage" "$status" >&2
    if [[ ${#PIPELINE_SUBMITTED_JOBS[@]} -gt 0 ]]; then
      scancel "${PIPELINE_SUBMITTED_JOBS[@]}" ||
        printf '[AVERTISSEMENT] Annulation incomplète ; vérifier squeue.\n' >&2
    fi
    return "$status"
  fi
}
