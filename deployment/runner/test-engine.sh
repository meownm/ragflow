#!/usr/bin/env bash
# All acceptance suites exercise the same digest with disposable state.
set -euo pipefail
[[ -d "${CI_DOCKER_CONFIG:?}" && "${DOCKER_CONFIG:?}" == "$CI_DOCKER_CONFIG" ]] || exit 1
docker=(python3 -B tools/quality/artifact_lifecycle.py docker -- sudo -n docker --config "$CI_DOCKER_CONFIG")
mkdir -p output/ci-engine
set -a
source "${CI_ENV_FILE:?}"
set +a
export http_proxy="" https_proxy="" HTTP_PROXY="" HTTPS_PROXY="" no_proxy="*" NO_PROXY="*"
export HOST_ADDRESS="http://host.docker.internal:${SVR_HTTP_PORT}"
export ADMIN_HOST_ADDRESS="http://host.docker.internal:${ADMIN_SVR_HTTP_PORT}"
export RAGFLOW_BASE_URL="http://host.docker.internal:${SVR_WEB_HTTP_PORT}"
compose=("${docker[@]}" compose --env-file "$CI_ENV_FILE" -f docker/docker-compose.yml \
  -f docker/docker-compose.ci-postgres.yml -f docker/docker-compose.release-test.yml \
  -f "${RUNTIME_IMAGE_OVERRIDE:?}" -p "${COMPOSE_PROJECT_NAME:?}")
"${compose[@]}" config --quiet
"${compose[@]}" up -d --no-build
ready=0
for _ in $(seq 1 60); do
  if "${docker[@]}" exec "$RAGFLOW_CONTAINER" curl -fsS --max-time 5 http://localhost:9380/api/v1/system/ping >/dev/null; then
    ready=1; break
  fi
  sleep 5
done
test "$ready" = 1
actual=$("${docker[@]}" inspect "$RAGFLOW_CONTAINER" --format '{{.Image}}')
expected=$("${docker[@]}" image inspect "$RAGFLOW_IMAGE" --format '{{.Id}}')
test "$actual" = "$expected"
test "$("${docker[@]}" exec "$RAGFLOW_CONTAINER" cat /ragflow/SOURCE_REVISION)" = "$SOURCE_SHA"
source .venv/bin/activate
python -m tools.quality.release_runtime models --output output/ci-engine/models-before.json
export RAGFLOW_TEST_IMAGE_DIGEST="${RAGFLOW_IMAGE##*@}"
export GITHUB_SHA="$SOURCE_SHA"
for suite in test_sdk_api restful_api test_web_api test_admin_api; do
  pytest --model-profile=local --level="${HTTP_API_TEST_LEVEL:?}" \
    --api-case-suite "$suite" --api-case-evidence "output/ci-engine/${suite}-cases.json" \
    --junitxml="output/ci-engine/${suite}.xml" "test/testcases/${suite}" \
    2>&1 | python -u tools/quality/redact_ci_log.py | tee "output/ci-engine/${suite}.log"
  if [[ "$HTTP_API_TEST_LEVEL" == p3 ]]; then
    python -m tools.quality.api_case_evidence "output/ci-engine/${suite}-cases.json" \
      --suite "$suite" --engine "$DOC_ENGINE" --repository "$GITHUB_REPOSITORY" \
      --sha "$SOURCE_SHA" --run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT" \
      --image-digest "$RAGFLOW_TEST_IMAGE_DIGEST" --output "output/ci-engine/${suite}-qualified.json"
  fi
done
bash deployment/runner/test-cli.sh
cp "${DOC_ENGINE}_cli_test.log" output/ci-engine/cli.log
"${docker[@]}" exec "$RAGFLOW_CONTAINER" bin/ragflow-cli --help >/dev/null
export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64
python -m playwright install --with-deps chromium
pytest --junitxml=output/ci-engine/live-browser.xml test/playwright/e2e/test_dataset_upload_parse.py \
  test/playwright/e2e/test_next_apps_chat.py test/playwright/e2e/test_next_apps_search.py \
  2>&1 | python -u tools/quality/redact_ci_log.py | tee output/ci-engine/live-browser.log
python -m tools.quality.release_runtime models --output output/ci-engine/models-after.json
cmp output/ci-engine/models-before.json output/ci-engine/models-after.json
python - <<'PY'
import json,os
from pathlib import Path
from tools.quality.artifact_lifecycle import current,consumption_subject
from tools.quality.release_packet import verify_api_reports
root=Path('output/ci-engine')
if os.environ['HTTP_API_TEST_LEVEL']=='p3':
    identity={'repository':os.environ['GITHUB_REPOSITORY'],'sha':os.environ['SOURCE_SHA'],
              'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT']}
    verify_api_reports(root,os.environ['DOC_ENGINE'],identity=identity,inventory=Path('test/mandatory-api-cases.json'),image_digest=os.environ['RAGFLOW_IMAGE'])
owner=current()
assert owner is not None
(root/('engine-'+os.environ['DOC_ENGINE']+'.json')).write_text(json.dumps({
  'schema':1,'project':'ragflow','kind':'ci-engine','status':'PASS',
  'artifact_run_id':owner.data['run_id'],'artifact_subject':consumption_subject(owner.data),'ci':owner.data.get('ci'),
  'sha':os.environ['SOURCE_SHA'],'image_digest':os.environ['RAGFLOW_IMAGE'],
  'engine':os.environ['DOC_ENGINE'],'models':json.loads((root/'models-after.json').read_text())}))
PY
