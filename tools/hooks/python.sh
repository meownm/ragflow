#!/bin/sh
# Keep shell defaults out of Lefthook's own command-variable expansion.
exec "${RAGFLOW_HOOK_PYTHON:-python3}" "$@"
