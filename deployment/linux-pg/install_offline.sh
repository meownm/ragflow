#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_MANIFEST=${SCRIPT_DIR}/OFFLINE-PACKAGE.env
CHECKSUMS=${SCRIPT_DIR}/SHA256SUMS
PAYLOAD_DIR=${SCRIPT_DIR}/payload

if [[ ! -r ${PACKAGE_MANIFEST} || ! -r ${CHECKSUMS} ]]; then
  echo "Offline package manifest or checksums are missing." >&2
  exit 1
fi

manifest_value() {
  local key=$1
  awk -F= -v key="${key}" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "${PACKAGE_MANIFEST}"
}

RELEASE_VERSION=$(manifest_value RELEASE_VERSION)
TARGET_OS=$(manifest_value TARGET_OS)
TARGET_VERSION=$(manifest_value TARGET_VERSION)
TARGET_ARCH=$(manifest_value TARGET_ARCH)
DOCKER_DNF_REPO=$(manifest_value DOCKER_DNF_REPO)
SOURCE_ARCHIVE=$(manifest_value SOURCE_ARCHIVE)
FRONTEND_ARCHIVE=$(manifest_value FRONTEND_ARCHIVE)
DOCKER_IMAGES_ARCHIVE=$(manifest_value DOCKER_IMAGES_ARCHIVE)

if [[ -z ${RELEASE_VERSION} || ${TARGET_OS} != "rocky" || ${TARGET_VERSION} != "9" || ${TARGET_ARCH} != "amd64" || -z ${DOCKER_DNF_REPO} ]]; then
  echo "Unsupported or incomplete offline package manifest." >&2
  exit 1
fi

if [[ $(uname -m) != "x86_64" ]]; then
  echo "The offline package supports x86_64 only." >&2
  exit 1
fi
. /etc/os-release
if [[ ${ID:-} != "rocky" || (${VERSION_ID:-} != 9 && ${VERSION_ID:-} != 9.*) ]]; then
  echo "The offline package requires Rocky Linux 9.x; found ${ID:-unknown} ${VERSION_ID:-unknown}." >&2
  exit 1
fi

cd "${SCRIPT_DIR}"
sha256sum -c "${CHECKSUMS}"

sudo -v
ENABLED_DNF_REPOS=$(sudo dnf -q repolist --enabled | awk 'NR > 1 { print $1 }')
if ! grep -Fxq "${DOCKER_DNF_REPO}" <<<"${ENABLED_DNF_REPOS}"; then
  echo "Required Docker DNF repository is not enabled: ${DOCKER_DNF_REPO}" >&2
  exit 1
fi
sudo dnf install -y --enablerepo="${DOCKER_DNF_REPO}" \
  ca-certificates curl jq openssl rsync tar \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker

sudo docker load -i "${PAYLOAD_DIR}/${DOCKER_IMAGES_ARCHIVE}"
while IFS= read -r image_name; do
  [[ -z ${image_name} ]] && continue
  sudo docker image inspect "${image_name}" >/dev/null
done < "${PAYLOAD_DIR}/docker-images.txt"

SOURCE_DIR=${SOURCE_DIR:-/srv/ragflow-linux-pg-${RELEASE_VERSION}}
INSTALL_DIR=${INSTALL_DIR:-/opt/ragflow-pg}
PROJECT_NAME=${PROJECT_NAME:-ragflow-pg}
RAGFLOW_PORT=${RAGFLOW_PORT:-80}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@ragflow.local}
ADMIN_NICKNAME=${ADMIN_NICKNAME:-RAGFlow Admin}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-}

if sudo test -e "${SOURCE_DIR}" && [[ -n $(sudo find "${SOURCE_DIR}" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
  echo "SOURCE_DIR is not empty: ${SOURCE_DIR}" >&2
  exit 1
fi
sudo install -d -m 0755 "${SOURCE_DIR}"
sudo tar -xzf "${PAYLOAD_DIR}/${SOURCE_ARCHIVE}" -C "${SOURCE_DIR}"
sudo install -d -m 0755 "${SOURCE_DIR}/web"
sudo tar -xzf "${PAYLOAD_DIR}/${FRONTEND_ARCHIVE}" -C "${SOURCE_DIR}/web"
sudo find "${SOURCE_DIR}" -type f -name '*.sh' -exec chmod 0755 {} +
sudo test -s "${SOURCE_DIR}/web/dist/index.html"

cd "${SOURCE_DIR}"
sudo env \
  OFFLINE_INSTALL=1 \
  INSTALL_DIR="${INSTALL_DIR}" \
  PROJECT_NAME="${PROJECT_NAME}" \
  RAGFLOW_PORT="${RAGFLOW_PORT}" \
  ADMIN_EMAIL="${ADMIN_EMAIL}" \
  ADMIN_NICKNAME="${ADMIN_NICKNAME}" \
  ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
  DOCKER_DNF_REPO="${DOCKER_DNF_REPO}" \
  bash deployment/linux-pg/install.sh
