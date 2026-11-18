#!/usr/bin/env bash
# Usage : source "$GIT_ROOT/scripts/helper.sh", puis monparam PARAM... ou monparam --phase PHASE

_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

monparam() {
  python3 "$_HELPER_DIR/monparam.py" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  monparam "$@"
fi
