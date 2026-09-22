#!/usr/bin/env bash
set -euo pipefail

registry_host="${REGISTRY_HOST:-192.168.1.175}"
registry_port="${REGISTRY_PORT:-5443}"
registry_root="${REGISTRY_ROOT:-/opt/ragflow-registry}"
client_cidr="${REGISTRY_CLIENT_CIDR:-192.168.1.0/24}"
cert_dir="${registry_root}/certs"
data_dir="${registry_root}/data"
docker_cert_dir="/etc/docker/certs.d/${registry_host}:${registry_port}"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

install -d -m 0755 "${cert_dir}" "${data_dir}" "${docker_cert_dir}"

if [[ ! -s "${cert_dir}/registry.crt" || ! -s "${cert_dir}/registry.key" ]]; then
  openssl req -x509 -newkey rsa:4096 -sha256 -nodes -days 825 \
    -keyout "${cert_dir}/registry.key" \
    -out "${cert_dir}/registry.crt" \
    -subj "/CN=${registry_host}" \
    -addext "subjectAltName=IP:${registry_host},DNS:localhost"
fi

chmod 0600 "${cert_dir}/registry.key"
chmod 0644 "${cert_dir}/registry.crt"
install -m 0644 "${cert_dir}/registry.crt" "${docker_cert_dir}/ca.crt"

docker rm -f ragflow-candidate-registry >/dev/null 2>&1 || true
docker run -d \
  --name ragflow-candidate-registry \
  --restart unless-stopped \
  -p "${registry_port}:5000" \
  -e REGISTRY_HTTP_ADDR=0.0.0.0:5000 \
  -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.crt \
  -e REGISTRY_HTTP_TLS_KEY=/certs/registry.key \
  -e REGISTRY_STORAGE_DELETE_ENABLED=true \
  -v "${cert_dir}:/certs:ro" \
  -v "${data_dir}:/var/lib/registry" \
  registry:2 >/dev/null

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow from "${client_cidr}" to any port "${registry_port}" proto tcp >/dev/null
fi

echo "Candidate registry: https://${registry_host}:${registry_port}/v2/"
echo "Client CA: ${cert_dir}/registry.crt"
