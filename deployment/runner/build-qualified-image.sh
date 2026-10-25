#!/usr/bin/env bash
set -euo pipefail
source deployment/runner/artifact-shell.sh
RAGFLOW_ARTIFACT_CATEGORY=cache RAGFLOW_ARTIFACT_KEEP_DAYS=30 artifact_managed_entry qualified-image "$@"
test "$(git rev-parse HEAD)" = "${SOURCE_SHA:?}"
git diff --quiet
git diff --cached --quiet
test -z "$(git ls-files --others --exclude-standard)" || {
  echo 'Untracked source cannot enter a qualified image.' >&2; exit 1;
}
source deployment/runner/staging-docker-auth.sh
prepare_staging_docker_auth build
docker=(python3 -B "$artifact_driver" docker -- sudo -n docker --config "$CI_DOCKER_CONFIG")
version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
python3 -B "$artifact_driver" record --category evidence --evidence output/ci-image
mkdir -p output/ci-image/web/dist
container=""
cleanup() {
  local result=$?
  if [[ -n "$container" ]]; then
    "${docker[@]}" rm -f "$container" >/dev/null || result=1
  fi
  python3 -B "$artifact_driver" record --category evidence --evidence output/ci-image || result=1
  exit "$result"
}
trap cleanup EXIT
if [[ -n "${REPLAY_IMAGE:-}" ]]; then
  [[ "$REPLAY_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]
  "${docker[@]}" pull "$REPLAY_IMAGE"
  digest="$REPLAY_IMAGE"
else
  DOCKER_BUILDKIT=1 "${docker[@]}" build --build-arg "RAGFLOW_BUILD_VERSION=${version}" \
    --build-arg "RAGFLOW_SOURCE_REVISION=${SOURCE_SHA}" --build-arg "NEED_MIRROR=${NEED_MIRROR:-0}" \
    -f Dockerfile -t "${STAGING_IMAGE:?}" .
  "${docker[@]}" push "$STAGING_IMAGE"
  digest=$("${docker[@]}" image inspect "$STAGING_IMAGE" --format '{{json .RepoDigests}}' | \
    python3 -c 'import json,os,sys; repo=os.environ["STAGING_IMAGE"].rsplit(":",1)[0]; d=[d for d in json.load(sys.stdin) if d.startswith(repo+"@")]; assert len(d)==1; print(d[0])')
fi
test "$("${docker[@]}" run --rm --entrypoint cat "$digest" /ragflow/SOURCE_REVISION)" = "$SOURCE_SHA"
"${docker[@]}" run --rm --entrypoint /ragflow/.venv/bin/python "$digest" -c 'import business_documents'
container=$("${docker[@]}" create "$digest")
"${docker[@]}" cp "$container:/ragflow/web/dist/." output/ci-image/web/dist/
sudo chown -R "$(id -un)" output/ci-image
python3 -m tools.quality.release_runtime spa --path output/ci-image/web/dist \
  --sha "$SOURCE_SHA" --image-digest "$digest" --output output/ci-image/browser-build.json
export IMAGE_DIGEST="$digest"
python3 - <<'PY'
import json,os,re,subprocess
from pathlib import Path
docker=['sudo','-n','docker','--config',os.environ['CI_DOCKER_CONFIG']]
from tools.quality.artifact_lifecycle import docker_args
def version(*args):return subprocess.check_output(docker_args(args),text=True).strip()
from tools.quality.release_evidence import file_manifest
native=file_manifest(Path(os.environ.get('RAGFLOW_NATIVE_CACHE',str(Path.home()/'ragflow-native-libs'))))
import hashlib
native_build=json.loads(Path('output/ci-native/artifacts.json').read_text())
builder_id=native_build.get('toolchain')
if native_build.get('status') != 'PASS' or not isinstance(builder_id,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',builder_id):
    raise ValueError('Native build must provide a successful immutable builder ID')
toolchain={'python':version(*docker,'run','--rm','--entrypoint','/ragflow/.venv/bin/python',os.environ['IMAGE_DIGEST'],'--version'),
           'go':version(*docker,'run','--rm','--entrypoint','go',builder_id,'version'),
           'node':version(*docker,'run','--rm','--entrypoint','node',os.environ['IMAGE_DIGEST'],'--version'),'native':hashlib.sha256(json.dumps(native,sort_keys=True).encode()).hexdigest()}
Path('output/ci-image/image-build.json').write_text(json.dumps({'sha':os.environ['SOURCE_SHA'],
    'digest':os.environ['IMAGE_DIGEST'],'toolchain':toolchain,'native_files':native,
    'native_build':native_build}))
PY
printf 'digest=%s\n' "$digest" >> "$GITHUB_OUTPUT"
