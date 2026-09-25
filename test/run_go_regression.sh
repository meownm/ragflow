#!/usr/bin/env bash
# Invoke after the native build. All Go packages run against disposable storage.
set -euo pipefail
cd "$(dirname "$0")/.."

resource_dir=$(mktemp -d "${RUNNER_TEMP:-/tmp}/ragflow-test-resources.XXXXXX")
minio_id=""
resource_container=""
cleanup() {
  if [[ -n "$minio_id" ]]; then
    sudo docker rm -f -v "$minio_id"
  fi
  if [[ -n "$resource_container" ]]; then
    sudo docker rm -f "$resource_container"
  fi
  rm -rf -- "$resource_dir"
}
trap cleanup EXIT
# The image contains rag/* from infiniflow/resource commit
# 0937399b60f1949267388548e33ea0d5c0cc25f7. Verify every blob below.
resource_image="192.168.1.175:5443/ragflow-go-test-resources@sha256:50d3e3e14d4434ebc7edca8f0f5f5433b8292b0980750f47e8427e2cf65e53d0"
echo "Pulling pinned Go regression resources from the LAN registry"
sudo docker pull "$resource_image"
resource_container=$(sudo docker create "$resource_image" /noop)
sudo docker cp "$resource_container:/resource/rag" - | tar -xf - -C "$resource_dir"
sudo docker rm "$resource_container"
resource_container=""
test "$(git hash-object "$resource_dir/rag/huqie.trie")" = 818eb369dde299fa468d6bd991bbb5d6b853f219
test "$(git hash-object "$resource_dir/rag/huqie.txt")" = d6e097122e59b17e7705356d26fe10f0e4a08671
test "$(git hash-object "$resource_dir/rag/pos-id.def")" = 0c206403844a4018594bd11677933694983eba69
export RAGFLOW_DICT_PATH="$resource_dir"
export RAGFLOW_TEST_MINIO_USER=regression
export RAGFLOW_TEST_MINIO_PASSWORD=regression-only-minio
echo "Starting disposable Go regression MinIO"
minio_id=$(sudo docker run -d --label ragflow.regression=go \
  -p "${RAGFLOW_TEST_BIND_ADDRESS:-127.0.0.1}::9000" \
  -e MINIO_ROOT_USER="$RAGFLOW_TEST_MINIO_USER" \
  -e MINIO_ROOT_PASSWORD="$RAGFLOW_TEST_MINIO_PASSWORD" \
  pgsty/minio:RELEASE.2026-03-25T00-00-00Z server /data)
export RAGFLOW_TEST_MINIO_ENDPOINT
minio_port=$(sudo docker inspect --format '{{(index (index .NetworkSettings.Ports "9000/tcp") 0).HostPort}}' "$minio_id")
RAGFLOW_TEST_MINIO_ENDPOINT="${RAGFLOW_TEST_DOCKER_HOST:-127.0.0.1}:$minio_port"
ready=0
for attempt in $(seq 1 60); do
  if curl --noproxy '*' -fsS "http://$RAGFLOW_TEST_MINIO_ENDPOINT/minio/health/ready" >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
test "$ready" -eq 1
echo "Running Go package tests"
timeout --signal=TERM --kill-after=15s 900s ./build.sh --test ./... || {
  status=$?
  echo "Go package tests failed (exit $status)" >&2
  exit "$status"
}
