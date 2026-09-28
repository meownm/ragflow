#!/usr/bin/env bash
set -euo pipefail

# Build an unqualified developer image from a verified local source snapshot.
snapshot="${1:?usage: build-feature-image.sh SNAPSHOT_DIRECTORY}"
root="$(cd "${snapshot}" && pwd)"
source="${root}/source"
manifest="${root}/candidate.json"
test -f "${manifest}"
test -f "${source}/tools/quality/candidate.py"

source_id="$(python3 -B - "${root}" <<'PY'
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "source"))
from tools.quality.candidate import verify
print(verify(Path(sys.argv[1]))["source_id"])
PY
)"
[[ "${source_id}" =~ ^[0-9a-f]{64}$ ]]
chmod +x "${source}/build.sh"
image="192.168.1.175:8443/docker-hosted/ragflow:dev-${source_id}"
version="$(python3 - "${manifest}" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["identity"]["version"])
PY
)"
cd "${source}"

builder="ragflow_feature_cpp_${source_id:0:12}_$$"
cleanup() { docker rm -f -v "${builder}" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run --privileged -d --name "${builder}" \
  -v "${source}:/ragflow" \
  -v "${source}/internal/binding/cpp/resource:/usr/share/infinity/resource" \
  infiniflow/infinity_builder:ubuntu22_clang20 >/dev/null
docker exec "${builder}" bash -c 'cd /ragflow && ./build.sh --cpp'
cleanup
trap - EXIT

bash deployment/runner/build-go-linux.sh
DOCKER_BUILDKIT=1 docker build \
  --build-arg "RAGFLOW_BUILD_VERSION=${version}-dev.${source_id:0:12}" \
  --build-arg 'RAGFLOW_SOURCE_REVISION=' \
  --label "org.ragflow.source-id=${source_id}" \
  --label 'org.ragflow.validation=feature-build-only' \
  -f Dockerfile -t "${image}" .

actual="$(docker image inspect "${image}" --format '{{index .Config.Labels "org.ragflow.source-id"}}')"
test "${actual}" = "${source_id}"
test "$(docker run --rm --entrypoint cat "${image}" /ragflow/SOURCE_REVISION)" = unverified
docker run --rm --entrypoint /ragflow/.venv/bin/python "${image}" -c 'import business_documents'

# The host owns Nexus. Read the bootstrap credential only into docker login stdin.
# No credential or authenticated Docker config is written to this source snapshot.
docker_config="$(mktemp -d)"
chmod 700 "${docker_config}"
auth_cleanup() { rm -f "${docker_config}/config.json"; rmdir "${docker_config}"; }
trap auth_cleanup EXIT
sudo docker exec nexus cat /nexus-data/admin.password | \
  DOCKER_CONFIG="${docker_config}" docker login 192.168.1.175:8443 -u admin --password-stdin >/dev/null
DOCKER_CONFIG="${docker_config}" docker push "${image}"
digest="$(DOCKER_CONFIG="${docker_config}" docker image inspect "${image}" --format '{{index .RepoDigests 0}}')"
printf 'SOURCE_ID=%s\nIMAGE=%s\nDIGEST=%s\nVALIDATION=feature-build-only\n' "${source_id}" "${image}" "${digest}"
