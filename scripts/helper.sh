#!/usr/bin/env bash
# Usage : bash "$GIT_ROOT/scripts/helper.sh" [PARAM... | --phase PHASE]

_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

monparam() {
  python3 "$_HELPER_DIR/../tools/libs/python/monparam.py" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  monparam "$@"
fi
