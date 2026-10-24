#!/bin/sh
# Use prepared tools directly; package-manager fallbacks can misquote Windows args.
set -eu

root=$(git rev-parse --show-toplevel)
root=$(cd "$root" && pwd)
if [ -x "$root/.venv/Scripts/python.exe" ]; then
  PATH="$root/.venv/Scripts:$PATH"
elif [ -x "$root/.venv/bin/python" ]; then
  PATH="$root/.venv/bin:$PATH"
else
  echo "Git hooks require the repository's prepared .venv." >&2
  exit 1
fi
RAGFLOW_HOOK_PYTHON=python
export PATH RAGFLOW_HOOK_PYTHON

if [ -x "$root/.local/tools/lefthook.exe" ]; then
  exec "$root/.local/tools/lefthook.exe" "$@"
elif command -v lefthook >/dev/null 2>&1; then
  exec lefthook "$@"
elif command -v lefthook.exe >/dev/null 2>&1; then
  exec lefthook.exe "$@"
elif [ -f "$root/web/node_modules/lefthook/bin/index.js" ]; then
  exec node "$root/web/node_modules/lefthook/bin/index.js" "$@"
fi

echo "Lefthook is missing. Install it explicitly, then run 'lefthook install'." >&2
exit 1
