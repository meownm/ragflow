#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOURCE_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
INSTALL_DIR=${INSTALL_DIR:-/opt/ragflow-pg}
PROJECT_NAME=${PROJECT_NAME:-ragflow-pg}
RAGFLOW_PORT=${RAGFLOW_PORT:-9380}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@ragflow.local}
ADMIN_NICKNAME=${ADMIN_NICKNAME:-RAGFlow Admin}
SECRETS_DIR=${SECRETS_DIR:-/etc/ragflow-pg}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-}

if [[ $(uname -m) != "x86_64" ]]; then
  echo "Only x86_64 Linux is supported by this bundle." >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "/etc/os-release is required." >&2
  exit 1
fi
. /etc/os-release
if [[ ${ID:-} != "ubuntu" && ${ID:-} != "debian" ]]; then
  echo "Supported distributions: Ubuntu and Debian; found ${ID:-unknown}." >&2
  exit 1
fi

sudo -v
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl jq openssl rsync tar docker.io docker-compose-v2
sudo systemctl enable --now docker

printf 'vm.max_map_count=262144\n' | sudo tee /etc/sysctl.d/99-ragflow-pg.conf >/dev/null
sudo sysctl --system >/dev/null
test "$(sysctl -n vm.max_map_count)" -ge 262144

if [[ ${SOURCE_ROOT} != ${INSTALL_DIR} ]]; then
  sudo install -d -m 0755 "${INSTALL_DIR}"
  sudo rsync -a --delete \
    --exclude .git --exclude .venv --exclude .codex_tmp --exclude .playwright-cli \
    --exclude node_modules --exclude output --exclude docker/ragflow-logs \
    "${SOURCE_ROOT}/" "${INSTALL_DIR}/"
fi
sudo chown -R root:root "${INSTALL_DIR}"
sudo find "${INSTALL_DIR}" -type d -exec chmod 0755 {} +
sudo find "${INSTALL_DIR}" -type f -name '*.sh' -exec chmod 0755 {} +

ENV_FILE=${INSTALL_DIR}/docker/.env
sudo install -m 0600 "${INSTALL_DIR}/deployment/linux-pg/env.template" "${ENV_FILE}"

random_secret() { openssl rand -hex 32; }
set_env() {
  local key=$1 value=$2 escaped
  escaped=$(printf '%s' "${value}" | sed 's/[&|]/\\&/g')
  sudo sed -i "s|^${key}=.*|${key}=${escaped}|" "${ENV_FILE}"
}
set_env POSTGRES_PASSWORD "$(random_secret)"
set_env ELASTIC_PASSWORD "$(random_secret)"
set_env OPENSEARCH_PASSWORD "$(random_secret)"
set_env MINIO_PASSWORD "$(random_secret)"
set_env REDIS_PASSWORD "$(random_secret)"
set_env SVR_WEB_HTTP_PORT "${RAGFLOW_PORT}"

sudo install -d -m 0700 "${SECRETS_DIR}"
if [[ -z ${ADMIN_PASSWORD} ]]; then
  ADMIN_PASSWORD=$(random_secret)
fi
sudo install -m 0600 /dev/null "${SECRETS_DIR}/admin.env"
sudo tee "${SECRETS_DIR}/admin.env" >/dev/null <<EOF
ADMIN_EMAIL=${ADMIN_EMAIL}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
EOF

cd "${INSTALL_DIR}/docker"
COMPOSE=(sudo docker compose --env-file .env -p "${PROJECT_NAME}" \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.linux.local.yml \
  -f ../deployment/linux-pg/docker-compose.release.yml)

"${COMPOSE[@]}" config --quiet
services=$("${COMPOSE[@]}" config --services)
if grep -qx mysql <<<"${services}"; then
  echo "Refusing to deploy: MySQL is active in the merged Compose config." >&2
  exit 1
fi
grep -qx postgres <<<"${services}"
grep -qx t-one-asr <<<"${services}"

"${COMPOSE[@]}" up -d --build

deadline=$((SECONDS + 600))
until curl -fsS --max-time 10 "http://127.0.0.1:${RAGFLOW_PORT}/api/v1/system/healthz" | jq -e '.status == "ok"' >/dev/null; do
  if (( SECONDS >= deadline )); then
    "${COMPOSE[@]}" ps
    sudo docker logs --tail 200 "${PROJECT_NAME}-ragflow-cpu-1" || true
    echo "RAGFlow did not become healthy within 600 seconds." >&2
    exit 1
  fi
  sleep 5
done

RAGFLOW_CONTAINER=$("${COMPOSE[@]}" ps -q ragflow-cpu)
ASR_CONTAINER=$("${COMPOSE[@]}" ps -q t-one-asr)
test -n "${RAGFLOW_CONTAINER}"
test -n "${ASR_CONTAINER}"

sudo docker exec "${ASR_CONTAINER}" python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9011/health/ready', timeout=10)"
sudo docker exec "${RAGFLOW_CONTAINER}" curl -fsS --max-time 10 http://t-one-asr:9011/v1/models | jq -e \
  '.data | map(.id) | index("t-one") != null' >/dev/null

sudo docker exec -i \
  -e BOOTSTRAP_ADMIN_EMAIL="${ADMIN_EMAIL}" \
  -e BOOTSTRAP_ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
  -e BOOTSTRAP_ADMIN_NICKNAME="${ADMIN_NICKNAME}" \
  "${RAGFLOW_CONTAINER}" python - < "${INSTALL_DIR}/deployment/linux-pg/seed_admin_asr.py"

sudo docker exec "${RAGFLOW_CONTAINER}" printenv DB_TYPE | grep -qx postgres
sudo docker exec -i \
  -e BOOTSTRAP_ADMIN_EMAIL="${ADMIN_EMAIL}" \
  "${RAGFLOW_CONTAINER}" python - <<'PY'
from common import settings
settings.init_settings()
from api.db.services import UserService
from api.db.services.user_service import TenantService
import os
email = os.environ["BOOTSTRAP_ADMIN_EMAIL"]
users = list(UserService.query(email=email))
assert len(users) == 1 and users[0].is_superuser
exists, tenant = TenantService.get_by_id(users[0].id)
assert exists and tenant.asr_id == "t-one@t-one-local@New API"
PY

"${COMPOSE[@]}" ps
echo
echo "RAGFlow installation completed."
echo "URL: http://127.0.0.1:${RAGFLOW_PORT}/"
echo "Admin email: ${ADMIN_EMAIL}"
echo "Admin password is stored in ${SECRETS_DIR}/admin.env (mode 0600)."
echo "Database: PostgreSQL; pgvector is not required because DOC_ENGINE=elasticsearch."
