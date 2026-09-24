#!/usr/bin/env bash
# Run inside the disposable CI application container alongside the Python API.
set -euo pipefail
cd /ragflow
test -x bin/ragflow_server
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu/

go_log=$(mktemp)
headers=$(mktemp)
body=$(mktemp)
bin/ragflow_server --api > "$go_log" 2>&1 &
go_pid=$!
cleanup() {
  status=$?
  trap - EXIT
  kill "$go_pid" >/dev/null 2>&1 || true
  wait "$go_pid" >/dev/null 2>&1 || true
  if (( status != 0 )); then
    tail -n 100 "$go_log" || true
  fi
  rm -f "$go_log" "$headers" "$body"
  exit "$status"
}
trap cleanup EXIT

ready=false
for _ in $(seq 1 90); do
  if curl --noproxy '*' -fsS --max-time 2 -D "$headers" -o "$body" \
    http://127.0.0.1:9384/api/v1/system/ping 2>/dev/null; then
    grep -qi '^X-API-Source: go' "$headers"
    test "$(cat "$body")" = pong
    ready=true
    break
  fi
  if ! kill -0 "$go_pid" 2>/dev/null; then
    echo "Go API exited before responding" >&2
    exit 1
  fi
  sleep 1
done
test "$ready" = true

curl --noproxy '*' -fsS --max-time 30 \
  http://127.0.0.1:9384/api/v1/system/healthz \
  | python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["status"] == "ok", data'
echo "Go API ping and dependency health passed"
