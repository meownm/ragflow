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
result="${root}/feature-result.json"
python3 -B "${source}/deployment/runner/feature_image.py" --snapshot "${root}" "${@:2}" | tee "${root}/feature-build.log"
# The cache may return a build from before a Git commit, with identical source bytes.
tail -n 1 "${root}/feature-build.log" > "${result}"
image_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image_id"])' "${result}")"
source_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_id"])' "${result}")"
image="192.168.1.175:8443/docker-hosted/ragflow:dev-${source_id}"
docker tag "${image_id}" "${image}"

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
