#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
builder_file="${repo_root}/deployment/runner/Dockerfile.go-builder"
builder_image="ragflow-go-builder:go1.26.4-ubuntu22"
native_cache="${RAGFLOW_NATIVE_CACHE:-${HOME}/ragflow-native-libs}"
cache_root="${RAGFLOW_GO_CACHE_ROOT:-${HOME}/.cache/ragflow-go-builder}"

if [[ ! -d "${native_cache}" ]]; then
  echo "Native dependency cache not found: ${native_cache}" >&2
  exit 1
fi

mkdir -p "${cache_root}/build" "${cache_root}/modules"

docker build \
  --file "${builder_file}" \
  --tag "${builder_image}" \
  "${repo_root}/deployment/runner"

docker run --rm \
  --entrypoint /bin/bash \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp/builder-home \
  --env GOCACHE=/tmp/go-build-cache \
  --env GOMODCACHE=/tmp/go-mod-cache \
  --env GIT_CONFIG_COUNT=1 \
  --env GIT_CONFIG_KEY_0=safe.directory \
  --env GIT_CONFIG_VALUE_0='*' \
  --mount "type=bind,src=${repo_root},dst=/ragflow" \
  --mount "type=bind,src=${native_cache},dst=/tmp/builder-home/ragflow-native-libs,readonly" \
  --mount "type=bind,src=${cache_root}/build,dst=/tmp/go-build-cache" \
  --mount "type=bind,src=${cache_root}/modules,dst=/tmp/go-mod-cache" \
  "${builder_image}" \
  -c './build.sh --go'
