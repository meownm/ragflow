#!/usr/bin/env bash
# Source once from a producer; each Docker step delegates to the same owner run.
artifact_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
artifact_driver="${artifact_repo_root}/tools/quality/artifact_lifecycle.py"
if [[ "${GITHUB_ACTIONS:-}" == true ]]; then export RAGFLOW_ARTIFACT_DOCKER_SUDO=1; fi
artifact_managed_entry() {
  local producer="$1"; shift
  if [[ -z "${RAGFLOW_ARTIFACT_RUN_ID:-}" || "${RAGFLOW_ARTIFACT_SHELL_OWNER:-}" != "$producer" ]]; then
    export RAGFLOW_ARTIFACT_SHELL_OWNER="$producer"
    exec python3 -B "$artifact_driver" exec --producer "$producer" --category "${RAGFLOW_ARTIFACT_CATEGORY:-disposable}" --keep-days "${RAGFLOW_ARTIFACT_KEEP_DAYS:-0}" -- bash "$0" "$@"
  fi
}
docker() {
  python3 -B "$artifact_driver" docker -- docker "$@"
}
