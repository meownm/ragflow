#!/usr/bin/env bash
set -euo pipefail

RUNNER_USER="${RUNNER_USER:-apt}"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root: sudo RUNNER_USER=${RUNNER_USER} bash $0" >&2
  exit 1
fi

if ! id "${RUNNER_USER}" >/dev/null 2>&1; then
  echo "Runner user does not exist: ${RUNNER_USER}" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git git-lfs gh jq unzip zip tar xz-utils rsync \
  build-essential pkg-config cmake ninja-build clang lld libpcre2-dev \
  libicu-dev libssl-dev zlib1g-dev libkrb5-dev \
  nodejs npm \
  golang-go \
  docker.io docker-buildx docker-compose-v2 \
  lvm2

systemctl enable --now docker
usermod -aG docker "${RUNNER_USER}"

# The repository workflows use sudo for Docker, workspace ownership, and apt.
sudoers_file="/etc/sudoers.d/ragflow-runner-${RUNNER_USER}"
printf '%s ALL=(ALL) NOPASSWD: ALL\n' "${RUNNER_USER}" > "${sudoers_file}"
chmod 0440 "${sudoers_file}"
visudo -cf "${sudoers_file}"

# Elasticsearch requires this value; keeping it persistent also covers Compose lanes.
cat > /etc/sysctl.d/99-ragflow-runner.conf <<'EOF'
vm.max_map_count=262144
EOF
sysctl --system >/dev/null

# The Ubuntu installation may allocate only part of the NVMe disk to the root LV.
root_source="$(findmnt -n -o SOURCE /)"
if [[ "${root_source}" == /dev/mapper/* || "${root_source}" == /dev/*/* ]]; then
  vg_name="$(lvs --noheadings -o vg_name "${root_source}" 2>/dev/null | xargs || true)"
  if [[ -n "${vg_name}" ]]; then
    free_extents="$(vgs --noheadings --units b --nosuffix -o vg_free "${vg_name}" | awk '{printf "%.0f", $1}')"
    if [[ "${free_extents:-0}" -gt 0 ]]; then
      lvextend --resizefs --extents +100%FREE "${root_source}"
    fi
  fi
fi

# Make host-published GitHub Actions service ports reachable by the test suite.
if ! grep -Eq '^[[:space:]]*127\.0\.0\.1[[:space:]].*\bhost\.docker\.internal\b' /etc/hosts; then
  printf '127.0.0.1 host.docker.internal\n' >> /etc/hosts
fi

# Keep long CI builds from filling the disk with unbounded daemon logs.
install -d -m 0755 /etc/docker
if [[ ! -e /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
EOF
  systemctl restart docker
fi

# uv manages the repository's pinned Python 3.13 without replacing system Python.
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Linux deployment and archive contract tests execute PowerShell scripts.
if ! command -v pwsh >/dev/null 2>&1; then
  powershell_release="$(curl -fsSL --retry 3 https://api.github.com/repos/PowerShell/PowerShell/releases/latest)"
  powershell_version="$(jq -r '.tag_name | ltrimstr("v")' <<<"${powershell_release}")"
  powershell_asset="powershell_${powershell_version}-1.deb_amd64.deb"
  powershell_url="$(jq -r --arg asset "${powershell_asset}" '.assets[] | select(.name == $asset) | .browser_download_url' <<<"${powershell_release}")"
  powershell_digest="$(jq -r --arg asset "${powershell_asset}" '.assets[] | select(.name == $asset) | .digest | ltrimstr("sha256:")' <<<"${powershell_release}")"
  curl -fL --retry 3 -o "/tmp/${powershell_asset}" "${powershell_url}"
  printf '%s  %s\n' "${powershell_digest}" "/tmp/${powershell_asset}" | sha256sum -c -
  apt-get install -y "/tmp/${powershell_asset}"
  rm -f "/tmp/${powershell_asset}"
fi

# lefthook is invoked by the preflight job before any web dependencies are installed.
GOBIN=/usr/local/bin go install github.com/evilmartians/lefthook@v1.13.6

git lfs install --system

echo "=== RAGFlow runner bootstrap complete ==="
echo "user=${RUNNER_USER}"
echo "root=$(df -hT / | tail -1)"
docker --version
docker compose version
go version
clang --version | head -1
cmake --version | head -1
uv --version
node --version
pwsh --version
lefthook version
