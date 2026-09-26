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
    # Include both sides of a rename: removing a .go file must still select Go.
    git diff --name-only --no-renames -z "$base" HEAD > "$changed_files"
    while IFS= read -r -d '' file; do
      case "$file" in
        docs/references/http_api_reference.md) has_web=true ;;
        README.md|docs/*.md|docs/*.mdx) : ;;
        web/*) has_web=true ;;
        *.go|go.mod|go.sum) has_go=true ;;
        test/evals/source_workbench/*|agent/business_requirements/golden_model_quality/*.json|agent/business_requirements/golden_dialogs/*.json|agent/business_requirements/evals/*.json|agent/business_requirements/prompts/*.md)
          has_python=true
          ;;
        ragflow_deps/*.py|tools/quality/select_go_test_packages.py|test/unit_test/tools/quality/test_go_package_selection.py)
          has_go=true
          has_python=true
          ;;
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
