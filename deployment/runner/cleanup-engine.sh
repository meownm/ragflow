#!/usr/bin/env bash
set -euo pipefail
source deployment/runner/staging-docker-auth.sh
cleanup_auth_on_exit() {
  local exit_status=$?
  cleanup_staging_docker_auth || exit_status=1
  trap - EXIT
  exit "$exit_status"
}
trap cleanup_auth_on_exit EXIT
[[ -n "${COMPOSE_PROJECT_NAME:-}" && -n "${CI_ENV_FILE:-}" ]] || exit 0
mkdir -p output/ci-engine
docker=(sudo -n docker --config "${CI_DOCKER_CONFIG:?}")
compose=("${docker[@]}" compose --env-file "$CI_ENV_FILE" -f docker/docker-compose.yml \
  -f docker/docker-compose.ci-postgres.yml -f docker/docker-compose.release-test.yml -p "$COMPOSE_PROJECT_NAME")
[[ -z "${RUNTIME_IMAGE_OVERRIDE:-}" ]] || compose+=(-f "$RUNTIME_IMAGE_OVERRIDE")
status=0
"${compose[@]}" logs --no-color 2>&1 | python3 tools/quality/redact_ci_log.py > output/ci-engine/runtime.log || status=1
"${compose[@]}" down -v --remove-orphans || status=1
remaining=$("${docker[@]}" ps -aq --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}") || status=1
volumes=$("${docker[@]}" volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}") || status=1
[[ -z "$remaining" && -z "$volumes" ]] || status=1
if [[ -n "${PORT_RESERVATION:-}" ]]; then
  expected_root="${RUNNER_WORKSPACE_PREFIX:-${HOME}}/artifacts/${GITHUB_REPOSITORY}/port-locks/"
  if [[ "$PORT_RESERVATION" == "$expected_root"* ]] &&
     [[ ! -f "$PORT_RESERVATION" || "$(awk '{print $1}' "$PORT_RESERVATION")" == "$COMPOSE_PROJECT_NAME" ]]; then
    rm -f -- "$PORT_RESERVATION"
  else
    status=1
  fi
fi
rm -f -- "$CI_ENV_FILE"
[[ -z "${RUNTIME_IMAGE_OVERRIDE:-}" ]] || rm -f -- "$RUNTIME_IMAGE_OVERRIDE"
exit "$status"
