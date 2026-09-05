#!/usr/bin/env bash
set -Eeuo pipefail

CHECK_ONLY=0
if [[ ${1:-} == "--check" ]]; then
  CHECK_ONLY=1
  shift
fi
if [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--check]" >&2
  exit 2
fi

PACKAGE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_MANIFEST=${PACKAGE_ROOT}/REGISTRY-PACKAGE.env
CHECKSUMS=${PACKAGE_ROOT}/SHA256SUMS
PAYLOAD_DIR=${PACKAGE_ROOT}/payload
[[ -r ${PACKAGE_MANIFEST} && -r ${CHECKSUMS} ]] || {
  echo "Registry package manifest or checksums are missing." >&2
  exit 1
}

manifest_value() {
  local key=$1
  awk -F= -v key="${key}" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "${PACKAGE_MANIFEST}"
}

RELEASE_VERSION=$(manifest_value RELEASE_VERSION)
PACKAGE_MODE=$(manifest_value PACKAGE_MODE)
SOURCE_ARCHIVE=$(manifest_value SOURCE_ARCHIVE)
FRONTEND_ARCHIVE=$(manifest_value FRONTEND_ARCHIVE)
IMAGES_FILE=$(manifest_value IMAGES_FILE)
GVISOR_BUNDLE=$(manifest_value GVISOR_BUNDLE)
[[ -n ${RELEASE_VERSION} && ${PACKAGE_MODE} == "registry" ]] || {
  echo "REGISTRY-PACKAGE.env does not describe a registry release." >&2
  exit 1
}
for payload_name in "${SOURCE_ARCHIVE}" "${FRONTEND_ARCHIVE}" "${IMAGES_FILE}"; do
  if [[ -z ${payload_name} || ${payload_name} == */* || ! -r ${PAYLOAD_DIR}/${payload_name} ]]; then
    echo "Required registry payload is missing or invalid: ${payload_name}" >&2
    exit 1
  fi
done
[[ ${GVISOR_BUNDLE} == "gvisor" && -d ${PAYLOAD_DIR}/${GVISOR_BUNDLE} ]] || { echo "Pinned gVisor bundle is missing." >&2; exit 1; }
(cd "${PACKAGE_ROOT}" && sha256sum -c SHA256SUMS)

SOURCE_DIR=${SOURCE_DIR:-/srv/ragflow-registry-${RELEASE_VERSION}-upgrade}
INSTALL_DIR=${INSTALL_DIR:-/opt/ragflow-pg}
PROJECT_NAME=${PROJECT_NAME:-ragflow-pg}
RAGFLOW_PORT=${RAGFLOW_PORT:-}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/ragflow-pg}
BACKUP_MINIO=${BACKUP_MINIO:-1}
SECRETS_DIR=${SECRETS_DIR:-/etc/ragflow-pg}
ALLOW_DOWNGRADE=${ALLOW_DOWNGRADE:-0}
ALLOW_UNHEALTHY=${ALLOW_UNHEALTHY:-0}
REGISTRY_PREFIX=${REGISTRY_PREFIX:-}
REGISTRY_HOST=${REGISTRY_HOST:-}
REGISTRY_USERNAME=${REGISTRY_USERNAME:-}
REGISTRY_PASSWORD_FILE=${REGISTRY_PASSWORD_FILE:-}

TEMP_DIR=$(mktemp -d)
cleanup() {
  rm -rf -- "${TEMP_DIR}"
}
trap cleanup EXIT

IMAGE_ENV_FILE=${PAYLOAD_DIR}/${IMAGES_FILE}
if [[ ${IMAGES_FILE} == *.template ]]; then
  if [[ -z ${REGISTRY_PREFIX} || ! ${REGISTRY_PREFIX} =~ ^[A-Za-z0-9._:-]+(/[A-Za-z0-9._-]+)*$ ||
        ${REGISTRY_PREFIX} == *".example"* || ${REGISTRY_PREFIX} == *"example.org"* ]]; then
    echo "REGISTRY_PREFIX must name the real corporate Docker registry without a URL scheme." >&2
    exit 1
  fi
  escaped_registry_prefix=$(printf '%s' "${REGISTRY_PREFIX}" | sed 's/[&|\\]/\\&/g')
  IMAGE_ENV_FILE=${TEMP_DIR}/images.env
  sed "s|__REGISTRY_PREFIX__|${escaped_registry_prefix}|g" "${PAYLOAD_DIR}/${IMAGES_FILE}" > "${IMAGE_ENV_FILE}"
  grep -q '__REGISTRY_PREFIX__' "${IMAGE_ENV_FILE}" && {
    echo "Image template still contains an unresolved registry prefix." >&2
    exit 1
  }
fi
if [[ -z ${REGISTRY_HOST} && -n ${REGISTRY_PREFIX} ]]; then
  REGISTRY_HOST=${REGISTRY_PREFIX%%/*}
fi

declare -A seen_image_keys=()
while IFS='=' read -r image_key image_value; do
  [[ -z ${image_key} || ${image_key} == \#* ]] && continue
  case ${image_key} in
    POSTGRES_IMAGE|RAGFLOW_IMAGE|VALKEY_IMAGE|ELASTICSEARCH_IMAGE|PLANTUML_IMAGE|MINIO_IMAGE|T_ONE_ASR_IMAGE|OTEL_COLLECTOR_IMAGE|TEMPO_IMAGE|LOKI_IMAGE|PROMETHEUS_IMAGE|GRAFANA_IMAGE|SANDBOX_EXECUTOR_MANAGER_IMAGE|SANDBOX_BASE_NODEJS_IMAGE|SANDBOX_BASE_PYTHON_IMAGE)
      [[ -n ${image_value} && ! ${image_value} =~ [[:space:]] ]] || { echo "Invalid image reference for ${image_key}." >&2; exit 1; }
      seen_image_keys["${image_key}"]=1
      ;;
    *) echo "Unsupported key in image environment file: ${image_key}" >&2; exit 1 ;;
  esac
done < "${IMAGE_ENV_FILE}"
[[ ${#seen_image_keys[@]} -eq 15 ]] || { echo "The image environment must define exactly 15 supported images." >&2; exit 1; }

sudo -v
if sudo test -e "${SOURCE_DIR}" && [[ -n $(sudo find "${SOURCE_DIR}" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
  extracted_version=$(sudo awk -F= '$1 == "RELEASE_VERSION" { print $2; exit }' "${SOURCE_DIR}/DEPLOYMENT-SOURCE.env" 2>/dev/null || true)
  if [[ ${extracted_version} != "${RELEASE_VERSION}" || ! -s ${SOURCE_DIR}/web/dist/index.html ]]; then
    echo "SOURCE_DIR contains a different or incomplete release: ${SOURCE_DIR}" >&2
    exit 1
  fi
else
  sudo install -d -m 0755 "${SOURCE_DIR}"
  sudo tar -xzf "${PAYLOAD_DIR}/${SOURCE_ARCHIVE}" -C "${SOURCE_DIR}"
  sudo install -d -m 0755 "${SOURCE_DIR}/web"
  sudo tar -xzf "${PAYLOAD_DIR}/${FRONTEND_ARCHIVE}" -C "${SOURCE_DIR}/web"
fi
sudo find "${SOURCE_DIR}" -type f -name '*.sh' -exec chmod 0755 {} +
sudo test -s "${SOURCE_DIR}/web/dist/index.html"

run_preflight() {
  sudo env \
    INSTALL_DIR="${INSTALL_DIR}" PROJECT_NAME="${PROJECT_NAME}" RAGFLOW_PORT="${RAGFLOW_PORT}" \
    SECRETS_DIR="${SECRETS_DIR}" BACKUP_ROOT="${BACKUP_ROOT}" BACKUP_MINIO="${BACKUP_MINIO}" \
    ALLOW_DOWNGRADE="${ALLOW_DOWNGRADE}" ALLOW_UNHEALTHY="${ALLOW_UNHEALTHY}" IMAGE_ENV_FILE="${IMAGE_ENV_FILE}" \
    bash "${SOURCE_DIR}/deployment/linux-pg/upgrade.sh" --check
}

run_preflight
INSTALLED_VERSION=$(sudo awk -F= '$1 == "RELEASE_VERSION" { print $2; exit }' \
  "${SECRETS_DIR}/deployed-source.env" 2>/dev/null || true)
if [[ ${INSTALLED_VERSION#v} == "${RELEASE_VERSION#v}" ]]; then
  echo "Registry package ${RELEASE_VERSION} is already installed; images were not pulled."
  exit 0
fi

[[ -n ${REGISTRY_HOST} ]] || { echo "REGISTRY_HOST is required for registry upgrade." >&2; exit 1; }
registry_dns_host=${REGISTRY_HOST%%:*}
getent ahosts "${registry_dns_host}" >/dev/null || { echo "Registry host does not resolve: ${registry_dns_host}" >&2; exit 1; }
registry_status=$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 10 "https://${REGISTRY_HOST}/v2/" || true)
case ${registry_status} in 200|401) ;; *) echo "Registry endpoint is unavailable (HTTP ${registry_status:-none})." >&2; exit 1 ;; esac

if [[ ${CHECK_ONLY} == "1" ]]; then
  exit 0
fi

if [[ -n ${REGISTRY_USERNAME} ]]; then
  [[ -n ${REGISTRY_HOST} && -r ${REGISTRY_PASSWORD_FILE} ]] || {
    echo "REGISTRY_HOST and a readable REGISTRY_PASSWORD_FILE are required with REGISTRY_USERNAME." >&2
    exit 1
  }
  sudo docker login "${REGISTRY_HOST}" --username "${REGISTRY_USERNAME}" --password-stdin < "${REGISTRY_PASSWORD_FILE}"
fi

sudo env GVISOR_BUNDLE_DIR="${PAYLOAD_DIR}/${GVISOR_BUNDLE}" bash "${SOURCE_DIR}/deployment/linux-pg/install_gvisor.sh"

sudo env \
  REGISTRY_INSTALL=1 IMAGE_ENV_FILE="${IMAGE_ENV_FILE}" \
  INSTALL_DIR="${INSTALL_DIR}" PROJECT_NAME="${PROJECT_NAME}" RAGFLOW_PORT="${RAGFLOW_PORT}" \
  SECRETS_DIR="${SECRETS_DIR}" BACKUP_ROOT="${BACKUP_ROOT}" BACKUP_MINIO="${BACKUP_MINIO}" \
  ALLOW_DOWNGRADE="${ALLOW_DOWNGRADE}" ALLOW_UNHEALTHY="${ALLOW_UNHEALTHY}" \
  bash "${SOURCE_DIR}/deployment/linux-pg/upgrade.sh"
