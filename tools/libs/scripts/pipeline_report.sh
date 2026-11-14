#!/usr/bin/env bash

pipeline_report_start() {
  PIPELINE_REPORT_WORKSPACE="$1"
  PIPELINE_REPORT_PYTHON="${PYTHON_BIN:-${python_bin:-python3}}"
  PIPELINE_REPORT_RUN_ID="$("$PIPELINE_REPORT_PYTHON" \
    "$repo_root/tools/libs/python/pipeline_report.py" start \
    --workspace "$1" --name "$2" --parent "${PIPELINE_REPORT_PARENT_ID:-}")" || return
  export PIPELINE_REPORT_PARENT_ID="$PIPELINE_REPORT_RUN_ID"
}

pipeline_report_phase() {
  "$PIPELINE_REPORT_PYTHON" "$repo_root/tools/libs/python/pipeline_report.py" phase \
    --workspace "$PIPELINE_REPORT_WORKSPACE" --id "$PIPELINE_REPORT_RUN_ID" --phase "$1"
}

pipeline_report_finish() {
  "$PIPELINE_REPORT_PYTHON" "$repo_root/tools/libs/python/pipeline_report.py" finish \
    --workspace "$PIPELINE_REPORT_WORKSPACE" --id "$PIPELINE_REPORT_RUN_ID" --exit-code "$1"
}
