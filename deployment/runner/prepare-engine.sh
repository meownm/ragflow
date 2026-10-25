#!/usr/bin/env bash
# Source/config are immutable; each lane gets its own env and disposable resources.
set -euo pipefail
source ../deployment/runner/staging-docker-auth.sh
prepare_staging_docker_auth engine
umask 077
# install ss
sudo apt update && sudo apt install -y iproute2
RUNNER_WORKSPACE_PREFIX=${RUNNER_WORKSPACE_PREFIX:-${HOME}}
CI_ENV_FILE="${RUNNER_TEMP}/ragflow-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${DOC_ENGINE}.env"
cp .env "${CI_ENV_FILE}"
echo "CI_ENV_FILE=${CI_ENV_FILE}" >> "${GITHUB_ENV}"
COMPOSE_PROJECT_NAME="rf-${GITHUB_REPOSITORY//\//-}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-engine-${DOC_ENGINE}"
echo "COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME}" >> ${GITHUB_ENV}
echo "RAGFLOW_CONTAINER=${COMPOSE_PROJECT_NAME}-ragflow-cpu-1" >> ${GITHUB_ENV}
ARTIFACTS_DIR=${RUNNER_WORKSPACE_PREFIX}/artifacts/${GITHUB_REPOSITORY}/${GITHUB_RUN_ID}/${GITHUB_RUN_ATTEMPT}/engine/${DOC_ENGINE}
echo "ARTIFACTS_DIR=${ARTIFACTS_DIR}" >> ${GITHUB_ENV}
mkdir -p "${ARTIFACTS_DIR}"

# Determine runner number (default to 1 if not found)
     RUNNER_NUM=$(sudo -n docker --config "$CI_DOCKER_CONFIG" inspect "$(hostname)" --format '{{index .Config.Labels "com.docker.compose.container-number"}}' 2>/dev/null || true)
     RUNNER_NUM=${RUNNER_NUM:-1}

# Engine-specific offset partitions keep concurrent engine jobs from
# choosing the same host ports when they land on the same self-hosted runner.
# A lock plus reservation file closes the check/start race between parallel jobs.
     PORT_BASES=(1200 1201 23817 23820 5432 9000 9001 6379 6380 6601 9380 9381 9382 9384 9383 9385 80 443 4222)
     PARTITION_SIZE=6000
     case "${DOC_ENGINE}" in
       elasticsearch) PARTITION_BASE=1000 ;;
       infinity) PARTITION_BASE=31000 ;;
       *) echo "Unsupported DOC_ENGINE=${DOC_ENGINE}" >&2; exit 1 ;;
     esac
     PORT_LOCK_DIR=${RUNNER_WORKSPACE_PREFIX}/artifacts/${GITHUB_REPOSITORY}/port-locks
     mkdir -p "${PORT_LOCK_DIR}"

     port_offset_available() {
       local offset=$1
       local base port
       for base in "${PORT_BASES[@]}"; do
         port=$((base + offset))
         # The runner is containerized: ss sees its namespace, while Docker
         # publishes ports in the daemon's namespace.
         if ss -ltnH "sport = :${port}" | grep -q . || sudo -n docker --config "$CI_DOCKER_CONFIG" ps --filter "publish=${port}" --format '{{.ID}}' | grep -q .; then
           return 1
         fi
       done
       return 0
     }

     reserve_port_offset() {
       local attempt candidate reservation
       for attempt in $(seq 0 59); do
         candidate=$(( PARTITION_BASE + ((GITHUB_RUN_ID + RUNNER_NUM * 1000 + attempt * 97) % PARTITION_SIZE) ))
         reservation="${PORT_LOCK_DIR}/${candidate}.lock"
         if ( set -o noclobber; echo "${COMPOSE_PROJECT_NAME} ${DOC_ENGINE} $(date -u +%s)" > "${reservation}" ) 2>/dev/null; then
           if port_offset_available "${candidate}"; then
             PORT_OFFSET=${candidate}
             PORT_RESERVATION=${reservation}
             return 0
           fi
           rm -f "${reservation}"
         fi
       done
       return 1
     }

     if ! reserve_port_offset; then
       echo "Failed to reserve a free host port range for ${DOC_ENGINE} docker compose" >&2
       exit 1
     fi
     echo "PORT_RESERVATION=${PORT_RESERVATION}" >> ${GITHUB_ENV}
     echo "Using ${DOC_ENGINE} host port offset ${PORT_OFFSET}"
     ES_PORT=$((1200 + PORT_OFFSET))
     OS_PORT=$((1201 + PORT_OFFSET))
     INFINITY_THRIFT_PORT=$((23817 + PORT_OFFSET))
     INFINITY_HTTP_PORT=$((23820 + PORT_OFFSET))
     INFINITY_PSQL_PORT=$((5432 + PORT_OFFSET))
     MINIO_PORT=$((9000 + PORT_OFFSET))
     MINIO_CONSOLE_PORT=$((9001 + PORT_OFFSET))
     REDIS_PORT=$((6379 + PORT_OFFSET))
     NATS_PORT=$((4222 + PORT_OFFSET))
     TEI_PORT=$((6380 + PORT_OFFSET))
     KIBANA_PORT=$((6601 + PORT_OFFSET))
     SVR_HTTP_PORT=$((9380 + PORT_OFFSET))
     ADMIN_SVR_HTTP_PORT=$((9381 + PORT_OFFSET))
     SVR_MCP_PORT=$((9382 + PORT_OFFSET))
     GO_HTTP_PORT=$((9384 + PORT_OFFSET))
     GO_ADMIN_PORT=$((9383 + PORT_OFFSET))
     SANDBOX_EXECUTOR_MANAGER_PORT=$((9385 + PORT_OFFSET))
     SVR_WEB_HTTP_PORT=$((80 + PORT_OFFSET))
     SVR_WEB_HTTPS_PORT=$((443 + PORT_OFFSET))

# Persist computed ports into .env so docker-compose uses the correct host bindings.
# Remove previous CI overrides first; docker compose uses the last duplicate key.
     sed -i '/^ES_PORT=/d;/^OS_PORT=/d;/^INFINITY_THRIFT_PORT=/d;/^INFINITY_HTTP_PORT=/d;/^INFINITY_PSQL_PORT=/d;/^MINIO_PORT=/d;/^MINIO_CONSOLE_PORT=/d;/^REDIS_PORT=/d;/^TEI_PORT=/d;/^KIBANA_PORT=/d;/^SVR_HTTP_PORT=/d;/^ADMIN_SVR_HTTP_PORT=/d;/^SVR_MCP_PORT=/d;/^GO_HTTP_PORT=/d;/^GO_ADMIN_PORT=/d;/^SANDBOX_EXECUTOR_MANAGER_PORT=/d;/^SVR_WEB_HTTP_PORT=/d;/^SVR_WEB_HTTPS_PORT=/d;/^NATS_PORT=/d;/^COMPOSE_PROFILES=/d;/^TEI_MODEL=/d;/^RAGFLOW_IMAGE=/d;/^DOC_ENGINE=/d' "${CI_ENV_FILE}"
     {
       echo ""
       echo "ES_PORT=${ES_PORT}"
       echo "OS_PORT=${OS_PORT}"
       echo "INFINITY_THRIFT_PORT=${INFINITY_THRIFT_PORT}"
       echo "INFINITY_HTTP_PORT=${INFINITY_HTTP_PORT}"
       echo "INFINITY_PSQL_PORT=${INFINITY_PSQL_PORT}"
       echo "MINIO_PORT=${MINIO_PORT}"
       echo "MINIO_CONSOLE_PORT=${MINIO_CONSOLE_PORT}"
       echo "REDIS_PORT=${REDIS_PORT}"
       echo "NATS_PORT=${NATS_PORT}"
       echo "TEI_PORT=${TEI_PORT}"
       echo "KIBANA_PORT=${KIBANA_PORT}"
       echo "SVR_HTTP_PORT=${SVR_HTTP_PORT}"
       echo "ADMIN_SVR_HTTP_PORT=${ADMIN_SVR_HTTP_PORT}"
       echo "SVR_MCP_PORT=${SVR_MCP_PORT}"
       echo "GO_HTTP_PORT=${GO_HTTP_PORT}"
       echo "GO_ADMIN_PORT=${GO_ADMIN_PORT}"
       echo "SANDBOX_EXECUTOR_MANAGER_PORT=${SANDBOX_EXECUTOR_MANAGER_PORT}"
       echo "SVR_WEB_HTTP_PORT=${SVR_WEB_HTTP_PORT}"
       echo "SVR_WEB_HTTPS_PORT=${SVR_WEB_HTTPS_PORT}"
       echo "COMPOSE_PROFILES=${DOC_ENGINE},cpu,tei-cpu,deepdoc"
       echo "TEI_MODEL=BAAI/bge-small-en-v1.5"
       echo "RAGFLOW_IMAGE=${RAGFLOW_IMAGE}"
       echo "DOC_ENGINE=${DOC_ENGINE}"
     } >> "${CI_ENV_FILE}"
     echo "HOST_ADDRESS=http://host.docker.internal:${SVR_HTTP_PORT}" >> ${GITHUB_ENV}
     echo "RAGFLOW_BASE_URL=http://host.docker.internal:${SVR_WEB_HTTP_PORT}" >> ${GITHUB_ENV}
     echo "ADMIN_HOST_ADDRESS=http://host.docker.internal:${ADMIN_SVR_HTTP_PORT}" >> ${GITHUB_ENV}


CI_LOG_DIR="${ARTIFACTS_DIR}/runtime-logs"
mkdir -p "${CI_LOG_DIR}"
echo "CI_LOG_DIR=${CI_LOG_DIR}" >> "${GITHUB_ENV}"

# Persist the CI generation outside actions/checkout's ignored-file cleanup.
export RAGFLOW_ARTIFACT_DOCKER_SUDO=1
echo 'RAGFLOW_ARTIFACT_DOCKER_SUDO=1' >> "${GITHUB_ENV}"
artifact_owner=$(python3 -B ../tools/quality/artifact_lifecycle.py begin --producer "ci-engine:${DOC_ENGINE}" --keep-days 0)
ARTIFACT_OWNER_JSON="$artifact_owner" python3 - <<'PY'
import json, os
owner = json.loads(os.environ['ARTIFACT_OWNER_JSON'])
print('::add-mask::' + owner['owner_token'])
with open(os.environ['GITHUB_ENV'], 'a') as stream:
    stream.write('RAGFLOW_ARTIFACT_RUN_ID=' + owner['run_id'] + '\n')
    stream.write('RAGFLOW_ARTIFACT_STATE=' + owner['state'] + '\n')
    stream.write('RAGFLOW_ARTIFACT_OWNER_TOKEN=' + owner['owner_token'] + '\n')
PY
