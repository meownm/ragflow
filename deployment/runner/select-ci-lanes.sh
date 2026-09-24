#!/usr/bin/env bash
# Select separated regression lanes from the complete event change set.
set -euo pipefail

has_go=false
has_python=false
has_web=false
all=false

case "${GITHUB_EVENT_NAME}:${GITHUB_REF}" in
  pull_request:*)
    base=${CI_PR_BASE:-}
    ;;
  push:refs/heads/*)
    base=${CI_BEFORE:-}
    ;;
  *)
    all=true
    ;;
esac

if [[ "$all" == false ]]; then
  if [[ ! "${base}" =~ ^[0-9a-f]{40}$ || "${base}" == 0000000000000000000000000000000000000000 ]] \
    || ! git cat-file -e "${base}^{commit}"; then
    all=true
  else
    changed_files=$(mktemp)
    trap 'rm -f "$changed_files"' EXIT
    git diff --name-only -z "$base" HEAD > "$changed_files"
    while IFS= read -r -d '' file; do
      case "$file" in
        web/*) has_web=true ;;
        *.go|go.mod|go.sum) has_go=true ;;
        *.py|pyproject.toml|requirements*.txt) has_python=true ;;
        *) all=true ;;
      esac
    done < "$changed_files"
    if [[ ! -s "$changed_files" ]]; then
      all=true
    fi
  fi
fi

if [[ "$all" == true ]]; then
  has_go=true
  has_python=true
  has_web=true
fi

{
  echo "has_go_changes=$has_go"
  echo "has_python_changes=$has_python"
  echo "has_web_changes=$has_web"
} >> "$GITHUB_OUTPUT"
echo "Go: $has_go, Python: $has_python, Web: $has_web"
