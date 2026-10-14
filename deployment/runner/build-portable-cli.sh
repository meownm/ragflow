#!/usr/bin/env bash
# The portable distribution deliberately selects the supported no-CGO profile.
set -euo pipefail
test "${CGO_ENABLED:?}" = 0
test "$#" = 1
output="$1"
mkdir -p "$(dirname "$output")"
go test ./internal/parser/parser -run 'NoCGO' -count=1
go test ./internal/cli/... -count=1
go build -trimpath -ldflags='-s -w' -o "$output" cmd/ragflow-cli.go
