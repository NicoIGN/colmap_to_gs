#!/bin/bash

set -euo pipefail

# Usage:
# GIT_ROOT=/path/to/repo OUTPUT_DIR=/path/to/output CONFIG_SH=/path/to/config.sh ./launch.sh

VERBOSE="${VERBOSE:-false}"

log() {
    echo "$@"
}

is_verbose() {
    case "${VERBOSE:-false}" in
        1|true|TRUE|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

: "${GIT_ROOT:?❌ GIT_ROOT is not set. Example: GIT_ROOT=/path/to/repo ./launch.sh}"

# Source CONFIG_SH first, if it exists
if [ -f "$CONFIG_SH" ]; then
    # shellcheck disable=SC1090
    source "$CONFIG_SH"
    log "✅ Loaded CONFIG_SH: $CONFIG_SH"
else
    log "⚠️ CONFIG_SH not found, continuing without it: $CONFIG_SH"
fi

: "${OUTPUT_DIR:?❌ OUTPUT_DIR is not set.}"

export CONFIG_SH="${CONFIG_SH:-./config.sh}"


LOG_DIR="$OUTPUT_DIR/logs"
SUBMIT_LOG="$LOG_DIR/submit.log"

mkdir -p "$LOG_DIR"

# Slurm stdout/stderr.
# IMPORTANT: %j is interpreted directly by Slurm.
SLURM_STDOUT="$LOG_DIR/gsplat-%j.out"
SLURM_STDERR="$LOG_DIR/gsplat-%j.err"

export SLURM_STDOUT
export SLURM_STDERR

exec > >(tee -a "$SUBMIT_LOG") 2>&1


# Now that CONFIG_SH has been sourced, resolve defaults that may depend on it
LAUNCH_SLURM="${LAUNCH_SLURM:-$GIT_ROOT/environment/ign.slurm/launch.slurm}"
RUN_SH="${RUN_SH:-$GIT_ROOT/scripts/run.sh}"

log "========================"
log "🚀 SUBMIT CHECK"
log "========================"

log "date        : $(date)"
log "host        : $(hostname)"
log "user        : $(whoami)"
log "pwd         : $(pwd)"
log "GIT_ROOT    : $GIT_ROOT"
log "OUTPUT_DIR  : $OUTPUT_DIR"
log "LOG_DIR     : $LOG_DIR"
log "CONFIG_SH   : $CONFIG_SH"
log "RUN_SH      : $RUN_SH"
log "LAUNCH_SLURM: $LAUNCH_SLURM"
log "SLURM_STDOUT: $SLURM_STDOUT"
log "SLURM_STDERR: $SLURM_STDERR"
log "verbose     : $VERBOSE"

[ -d "$GIT_ROOT" ] || {
    log "❌ GIT_ROOT not found: $GIT_ROOT"
    exit 1
}

[ -f "$LAUNCH_SLURM" ] || {
    log "❌ launch.slurm missing: $LAUNCH_SLURM"
    exit 1
}

[ -f "$RUN_SH" ] || {
    log "❌ run.sh missing: $RUN_SH"
    exit 1
}

log

log "========================"
log "📤 SUBMITTING"
log "========================"

log

log "Commande sbatch :"
log "GIT_ROOT=$GIT_ROOT RUN_SH=$RUN_SH CONFIG_SH=$CONFIG_SH SLURM_STDOUT=$SLURM_STDOUT SLURM_STDERR=$SLURM_STDERR sbatch --output=\"$SLURM_STDOUT\" --error=\"$SLURM_STDERR\" \"$LAUNCH_SLURM\""

log

OUT="$(
    sbatch \
        --export=ALL,GIT_ROOT="$GIT_ROOT",OUTPUT_DIR="$OUTPUT_DIR",CONFIG_SH="$CONFIG_SH",RUN_SH="$RUN_SH",SLURM_STDOUT="$SLURM_STDOUT",SLURM_STDERR="$SLURM_STDERR" \
        --output="$SLURM_STDOUT" \
        --error="$SLURM_STDERR" \
        "$LAUNCH_SLURM"
)"

log "$OUT"

JOB_ID="$(
    echo "$OUT" |
        sed -n 's/.*Submitted batch job \([0-9]\+\).*/\1/p'
)"

[ -n "${JOB_ID:-}" ] || {
    log "❌ Could not parse job ID from sbatch output"
    exit 1
}

STDOUT_LOG="$LOG_DIR/gsplat-$JOB_ID.out"
STDERR_LOG="$LOG_DIR/gsplat-$JOB_ID.err"

log "job id    : $JOB_ID"
log "stdout    : $STDOUT_LOG"
log "stderr    : $STDERR_LOG"
log "queue cmd : squeue -j $JOB_ID"

log

log "========================"
log "⏳ WAITING FOR LOG FILES"
log "========================"

for _ in $(seq 1 60); do
    if [ -f "$STDOUT_LOG" ] || [ -f "$STDERR_LOG" ]; then
        break
    fi
    sleep 1
done

touch "$STDOUT_LOG" "$STDERR_LOG"

log

log "========================"
log "📡 STREAMING LOGS"
log "========================"

if is_verbose; then
    log "mode: verbose"

    tail -n0 -F "$STDOUT_LOG" "$STDERR_LOG" &

else
    log "mode: filtered"

    tail -n0 -F "$STDOUT_LOG" "$STDERR_LOG" 2>/dev/null |
        grep -vE \
            'RESOURCE SNAPSHOT|memory\.total|memory\.used|memory\.free|utilization\.gpu|used_gpu_memory|^Mem:|^Swap:|^pid, process_name|^index, name|^==> .* <==|[0-9]+(\.[0-9]+)?it/s|step=[0-9]+|epoch=[0-9]+|loss=' \
        || true &
fi

TAIL_PID=$!

cleanup() {
    kill "$TAIL_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

while squeue -j "$JOB_ID" -h | grep -q .; do
    sleep 2
done

kill "$TAIL_PID" 2>/dev/null || true
wait "$TAIL_PID" 2>/dev/null || true

log

log "========================"
log "🏁 JOB FINISHED"
log "========================"

log "--- stdout (last 30 lines) ---"
tail -n 30 "$STDOUT_LOG" || true

log

log "--- stderr (last 30 lines) ---"
tail -n 30 "$STDERR_LOG" || true

FINAL_STATE="$(
    sacct -j "$JOB_ID" --format=State --noheader 2>/dev/null |
        awk 'NF {print $1; exit}'
)"

EXIT_CODE="$(
    sacct -j "$JOB_ID" --format=ExitCode --noheader 2>/dev/null |
        awk 'NF {print $1; exit}'
)"

log

log "final state : ${FINAL_STATE:-unknown}"
log "exit code   : ${EXIT_CODE:-unknown}"

case "${FINAL_STATE:-}" in
    COMPLETED)
        exit 0
        ;;
    *)
        exit 1
        ;;
esac
