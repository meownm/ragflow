#!/usr/bin/env bash
set -euo pipefail
source .venv/bin/activate

export http_proxy=""; export https_proxy=""; export no_proxy=""; export HTTP_PROXY=""; export HTTPS_PROXY=""; export NO_PROXY=""

EMAIL="ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${DOC_ENGINE}@example.com"
PASS="ci-pass-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
DATASET="ci_dataset_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}"

CLI="python -m ragflow_cli"

LOG_FILE="${DOC_ENGINE}_cli_test.log"
: > "${LOG_FILE}"

ERROR_RE='Traceback|ModuleNotFoundError|ImportError|Parse error|Bad response|Fail to|code:\\s*[1-9]'
run_cli() {
  local logfile="$1"
  shift
  local allow_re=""
  if [[ "${1:-}" == "--allow" ]]; then
    allow_re="$2"
    shift 2
  fi
  local cmd_display="CLI command (credentials redacted)"
  echo "===== $(date -u +\"%Y-%m-%dT%H:%M:%SZ\") CMD: ${cmd_display} =====" | tee -a "${logfile}"
  local tmp_log
  tmp_log="$(mktemp)"
  set +e
  timeout 500s "$@" 2>&1 | python3 -u tools/quality/redact_ci_log.py | tee "${tmp_log}"
  local status=${PIPESTATUS[0]}
  set -e
  cat "${tmp_log}" >> "${logfile}"
  if grep -qiE "${ERROR_RE}" "${tmp_log}"; then
    if [[ -n "${allow_re}" ]] && grep -qiE "${allow_re}" "${tmp_log}"; then
      echo "Allowed CLI error markers in ${logfile}"
      rm -f "${tmp_log}"
      return 0
    fi
    echo "Detected CLI error markers in ${logfile}"
    rm -f "${tmp_log}"
    exit 1
  fi
  rm -f "${tmp_log}"
  return ${status}
}

set -a
source "${CI_ENV_FILE}"
set +a

HOST_ADDRESS="http://host.docker.internal:${SVR_HTTP_PORT}"
USER_HOST="$(echo "${HOST_ADDRESS}" | sed -E 's#^https?://([^:/]+).*#\1#')"
USER_PORT="${SVR_HTTP_PORT}"
ADMIN_HOST="${USER_HOST}"
ADMIN_PORT="${ADMIN_SVR_HTTP_PORT}"

svc_ready=0
for i in $(seq 1 60); do
  if sudo docker exec ${RAGFLOW_CONTAINER} curl -sf --connect-timeout 5 "${HOST_ADDRESS}/api/v1/system/ping" > /dev/null 2>&1; then
    svc_ready=1
    break
  fi
  echo "Waiting for service to be available... ($i/60)"
  sleep 5
done
if [ "$svc_ready" -ne 1 ]; then
  echo "Service did not become ready after 5 minutes. Docker logs:"
  sudo docker logs "${RAGFLOW_CONTAINER}" 2>&1 | python3 tools/quality/redact_ci_log.py
  exit 1
fi

admin_ready=0
for i in $(seq 1 30); do
  if run_cli "${LOG_FILE}" $CLI --type admin --host "$ADMIN_HOST" --port "$ADMIN_PORT" --username "admin@ragflow.io" --password "admin" command "ping"; then
    admin_ready=1
    break
  fi
  sleep 1
done
if [[ "${admin_ready}" -ne 1 ]]; then
  echo "Admin service did not become ready"
  exit 1
fi

run_cli "${LOG_FILE}" $CLI --type admin --host "$ADMIN_HOST" --port "$ADMIN_PORT" --username "admin@ragflow.io" --password "admin" command "show version"
ALLOW_USER_EXISTS_RE='already exists|already exist|duplicate|already.*registered|exist(s)?'
run_cli "${LOG_FILE}" --allow "${ALLOW_USER_EXISTS_RE}" $CLI --type admin --host "$ADMIN_HOST" --port "$ADMIN_PORT" --username "admin@ragflow.io" --password "admin" command "create user '$EMAIL' '$PASS'"
denied_log=$(mktemp)
set +e
$CLI --type user --host "$USER_HOST" --port "$USER_PORT" --username "$EMAIL" --password "$PASS" command "create dataset '$DATASET' with embedding 'BAAI/bge-small-en-v1.5@Builtin' parser 'auto'" 2>&1 | python3 tools/quality/redact_ci_log.py > "$denied_log"
set -e
grep -Fq 'Only admin can perform this operation.' "$denied_log"
cat "$denied_log" >> "$LOG_FILE"
rm -f "$denied_log"
run_cli "${LOG_FILE}" $CLI --type admin --host "$ADMIN_HOST" --port "$ADMIN_PORT" --username "admin@ragflow.io" --password "admin" command "grant admin '$EMAIL'"

user_ready=0
for i in $(seq 1 30); do
  if run_cli "${LOG_FILE}" $CLI --type user --host "$USER_HOST" --port "$USER_PORT" --username "$EMAIL" --password "$PASS" command "ping"; then
    user_ready=1
    break
  fi
  sleep 1
done
if [[ "${user_ready}" -ne 1 ]]; then
  echo "User service did not become ready"
  exit 1
fi

run_cli "${LOG_FILE}" $CLI --type user --host "$USER_HOST" --port "$USER_PORT" --username "$EMAIL" --password "$PASS" command "show version"
run_cli "${LOG_FILE}" $CLI --type user --host "$USER_HOST" --port "$USER_PORT" --username "$EMAIL" --password "$PASS" command "create dataset '$DATASET' with embedding 'BAAI/bge-small-en-v1.5@Builtin' parser 'auto'"
run_cli "${LOG_FILE}" $CLI --type user --host "$USER_HOST" --port "$USER_PORT" --username "$EMAIL" --password "$PASS" command "import 'test/benchmark/test_docs/Doc1.pdf,test/benchmark/test_docs/Doc2.pdf' into dataset '$DATASET'"
run_cli "${LOG_FILE}" $CLI --type user --host "$USER_HOST" --port "$USER_PORT" --username "$EMAIL" --password "$PASS" command "parse dataset '$DATASET' sync"
run_cli "${LOG_FILE}" $CLI --type user --host "$USER_HOST" --port "$USER_PORT" --username "$EMAIL" --password "$PASS" command "Benchmark 16 100 search 'what are these documents about' on datasets '$DATASET'"
