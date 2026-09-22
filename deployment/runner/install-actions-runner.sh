#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="${REPOSITORY:-meownm/ragflow}"
RUNNER_NAME="${RUNNER_NAME:-ragflow-test-meow}"
RUNNER_LABELS="${RUNNER_LABELS:-ragflow-test}"
RUNNER_USER="${RUNNER_USER:-apt}"
RUNNER_DIR="${RUNNER_DIR:-/opt/actions-runner}"

IFS= read -r RUNNER_TOKEN
RUNNER_TOKEN="${RUNNER_TOKEN%$'\r'}"
if [[ -z "${RUNNER_TOKEN}" ]]; then
  echo "A GitHub Actions registration token is required on stdin" >&2
  exit 1
fi
trap 'RUNNER_TOKEN=' EXIT

release_json="$(curl -fsSL --retry 3 https://api.github.com/repos/actions/runner/releases/latest)"
version="$(jq -r '.tag_name | ltrimstr("v")' <<<"${release_json}")"
asset="actions-runner-linux-x64-${version}.tar.gz"
asset_url="$(jq -r --arg asset "${asset}" '.assets[] | select(.name == $asset) | .browser_download_url' <<<"${release_json}")"
asset_digest="$(jq -r --arg asset "${asset}" '.assets[] | select(.name == $asset) | .digest | ltrimstr("sha256:")' <<<"${release_json}")"

if [[ -z "${asset_url}" || -z "${asset_digest}" || "${asset_url}" == null || "${asset_digest}" == null ]]; then
  echo "Could not resolve the current Linux x64 runner asset" >&2
  exit 1
fi

sudo install -d -o "${RUNNER_USER}" -g "${RUNNER_USER}" -m 0755 "${RUNNER_DIR}"
cd "${RUNNER_DIR}"

if [[ ! -x ./config.sh ]]; then
  curl -fL --retry 3 -o "${asset}" "${asset_url}"
  printf '%s  %s\n' "${asset_digest}" "${asset}" | sha256sum -c -
  tar xzf "${asset}"
  rm -f "${asset}"
fi

if [[ ! -f .runner ]]; then
  ./config.sh \
    --unattended \
    --replace \
    --url "https://github.com/${REPOSITORY}" \
    --token "${RUNNER_TOKEN}" \
    --name "${RUNNER_NAME}" \
    --labels "${RUNNER_LABELS}" \
    --work _work
fi

service_name="actions.runner.${REPOSITORY//\//-}.${RUNNER_NAME}.service"
if ! systemctl list-unit-files "${service_name}" --no-legend | grep -q "${service_name}"; then
  sudo ./svc.sh install "${RUNNER_USER}"
fi
runner_home="$(getent passwd "${RUNNER_USER}" | cut -d: -f6)"
dropin_dir="/etc/systemd/system/${service_name}.d"
sudo install -d -m 0755 "${dropin_dir}"
printf '[Service]\nEnvironment=RUNNER_WORKSPACE_PREFIX=%s\n' "${runner_home}" \
  | sudo tee "${dropin_dir}/ragflow.conf" >/dev/null
sudo systemctl daemon-reload
sudo ./svc.sh stop || true
sudo ./svc.sh start
sudo ./svc.sh status
