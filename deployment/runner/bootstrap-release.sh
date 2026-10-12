#!/usr/bin/env bash
# Administrator provisioning for a dedicated publisher VM; never run from CI.
set -euo pipefail
export LC_ALL=C

fail() { printf '%s\n' "$*" >&2; exit 1; }
_publisher_has_sudo() {
  local policy
  # Root's -U query can exit zero even when the account has no permitted commands.
  policy=$(sudo -n -l -U "$RUNNER_USER" 2>&1) || true
  if grep -q "^User $RUNNER_USER may run the following commands on " <<< "$policy"; then
    return 0
  fi
  if grep -q "^User $RUNNER_USER is not allowed to run sudo on " <<< "$policy"; then
    return 1
  fi
  fail "Cannot determine publisher sudo policy; inspect it as the administrator."
}
[[ ${EUID} -eq 0 ]] || fail "Run bootstrap-release.sh as the VM administrator."
[[ "${RELEASE_VM_CONFIRMED:-}" == yes ]] || fail "Set RELEASE_VM_CONFIRMED=yes only after verifying the VM isolation inventory."
systemd-detect-virt --vm >/dev/null || fail "A dedicated virtual machine is required."
[[ ! -d /opt/nexus && ! -d /opt/ragflow-registry ]] || fail "This machine hosts registry infrastructure; it must not run CI."
if systemctl list-unit-files 'actions.runner*' --no-legend | grep -q 'ragflow-test'; then
  fail "A test runner is registered on this machine."
fi

RUNNER_USER="${RUNNER_USER:-ragflow-release}"
RUNNER_NAME="${RUNNER_NAME:-ragflow-release-vm}"
REPOSITORY="${REPOSITORY:-meownm/ragflow}"
RUNNER_DIR="${RUNNER_DIR:-/opt/actions-runner-release}"
[[ "$RUNNER_USER" =~ ^[a-z][a-z0-9_-]*$ ]] || fail "Invalid publisher user."
[[ "$RUNNER_NAME" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid runner name."
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail "Invalid repository."
[[ "$RUNNER_DIR" == /opt/actions-runner-release && ! -L "$RUNNER_DIR" ]] || fail "Use the dedicated /opt/actions-runner-release directory."
. /etc/os-release
[[ "$ID" == ubuntu ]] || fail "This bootstrap supports Ubuntu only."

# A previous privileged account must be remediated by an administrator first.
if id "$RUNNER_USER" >/dev/null 2>&1; then
  [[ "$(id -u "$RUNNER_USER")" != 0 ]] || fail "Publisher must not be root."
  existing_home="$(getent passwd "$RUNNER_USER" | cut -d: -f6)"
  [[ "$existing_home" == "/home/$RUNNER_USER" && ! -L "$existing_home" ]] || fail "Publisher needs its own /home directory."
  [[ ! -s "$existing_home/.ssh/authorized_keys" ]] || fail "Publisher has SSH login keys; remove them before bootstrap."
  for group in $(id -Gn "$RUNNER_USER"); do
    [[ "$group" == "$RUNNER_USER" ]] || fail "Publisher has an additional group: $group."
  done
  if command -v sudo >/dev/null && _publisher_has_sudo; then
    fail "Publisher has sudo rights; remove them before bootstrap."
  fi
fi

if [[ -n "${REGISTRY_CA_FILE:-}" ]]; then
  [[ -f "$REGISTRY_CA_FILE" && ! -L "$REGISTRY_CA_FILE" ]] || fail "Registry CA must be a regular public certificate."
  [[ "${REGISTRY_CA_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] || fail "Supply the independently verified CA SHA256."
  [[ "$(sha256sum "$REGISTRY_CA_FILE" | cut -d' ' -f1)" == "$REGISTRY_CA_SHA256" ]] || fail "Registry CA checksum mismatch."
  if grep -q 'PRIVATE KEY' "$REGISTRY_CA_FILE"; then fail "A private key must not enter the publisher VM."; fi
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git gh jq python3 skopeo \
  docker.io docker-compose-v2 sudo tar gzip
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Publisher tools need Python 3.11+"'
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --create-home --user-group --shell /bin/bash "$RUNNER_USER"
fi
passwd --lock "$RUNNER_USER" >/dev/null
runner_home="$(getent passwd "$RUNNER_USER" | cut -d: -f6)"
[[ "$runner_home" == "/home/$RUNNER_USER" && ! -L "$runner_home" ]] || fail "Publisher needs its own /home directory."
chmod 0700 "$runner_home"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 "$RUNNER_DIR"

# Docker is owned by this guest's administrator. The publisher uses skopeo,
# receives neither socket access nor membership of the root-equivalent group.
install -d -m 0755 /etc/docker
if [[ ! -e /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'EOF'
{"log-driver":"json-file","log-opts":{"max-size":"50m","max-file":"3"}}
EOF
fi
systemctl enable --now docker
if runuser -u "$RUNNER_USER" -- test -w /var/run/docker.sock; then
  fail "Publisher can write the Docker socket; correct its ACL before registering."
fi
if _publisher_has_sudo; then
  fail "Publisher unexpectedly has sudo rights."
fi

if [[ -n "${REGISTRY_CA_FILE:-}" ]]; then
  install -m 0644 "$REGISTRY_CA_FILE" /usr/local/share/ca-certificates/ragflow-registry.crt
  update-ca-certificates
fi

service_name="actions.runner.${REPOSITORY//\//-}.${RUNNER_NAME}.service"
install -d -m 0755 "/etc/systemd/system/${service_name}.d"
cat > "/etc/systemd/system/${service_name}.d/isolation.conf" <<EOF
[Service]
Environment=RUNNER_WORKSPACE_PREFIX=${runner_home}
UnsetEnvironment=DOCKER_HOST DOCKER_CONTEXT
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${RUNNER_DIR} ${runner_home}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
EOF
systemctl daemon-reload
printf 'Publisher prerequisites prepared. User=%s; runner directory=%s.\n' "$RUNNER_USER" "$RUNNER_DIR"
printf 'Register and install the service from an administrator session; see docs/develop/ci-runner-isolation-ru.md.\n'
