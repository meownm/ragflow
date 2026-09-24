#!/usr/bin/env bash
set -euo pipefail

ci_chat_container="${COMPOSE_PROJECT_NAME}-ci-chat-model"
sudo docker run --detach --rm \
  --name "${ci_chat_container}" \
  --label "com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
  --network "${COMPOSE_PROJECT_NAME}_ragflow" \
  --volume "${PWD}/test/playwright/support/ci_chat_model.py:/app/ci_chat_model.py:ro" \
  python:3.13-alpine python /app/ci_chat_model.py
trap 'sudo docker rm -f "${ci_chat_container}" >/dev/null 2>&1 || true' EXIT

ci_chat_ready=0
for _ in $(seq 1 30); do
  if sudo docker exec "${RAGFLOW_CONTAINER}" curl --fail --silent --max-time 3 "http://${ci_chat_container}:8000/health" >/dev/null; then
    ci_chat_ready=1
    break
  fi
  sleep 1
done
if [[ "${ci_chat_ready}" -ne 1 ]]; then
  sudo docker logs "${ci_chat_container}"
  exit 1
fi
export RAGFLOW_CI_CHAT_MODEL_URL="http://${ci_chat_container}:8000/v1"
export E2E_ADMIN_EMAIL="admin@ragflow.io"
export E2E_ADMIN_PASSWORD="admin"
