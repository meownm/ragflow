# Self-hosted build runner

This directory provisions a dedicated Linux x64 GitHub Actions runner for the
repository's `ragflow-test` jobs. It is a build and test host, not a RAGFlow
runtime or data host.

## Host bootstrap

The tested host is Ubuntu x86_64 with an ext4 root filesystem. Run the
bootstrap once as an administrator:

```bash
sudo RUNNER_USER=apt bash deployment/runner/bootstrap-ubuntu.sh
```

The bootstrap installs Docker, Go, C/C++ tools, Python `uv`, Node.js,
PowerShell, GitHub CLI, and lefthook. It also:

- expands an LVM root volume into currently free extents;
- enables Docker with bounded daemon logs;
- sets the Elasticsearch `vm.max_map_count` requirement;
- grants the runner user passwordless sudo because the checked-in workflows
  invoke `sudo` for Docker, apt, and workspace ownership.

Use this host only for trusted repository workflows. Do not place production
credentials or production databases on it.

## Runner registration

Generate a repository runner registration token in GitHub, then pass it on
standard input so it is not written to disk:

```bash
printf '%s\n' "$RUNNER_TOKEN" | \
  REPOSITORY=meownm/ragflow \
  RUNNER_NAME=ragflow-test-meow \
  RUNNER_LABELS=ragflow-test \
  bash deployment/runner/install-actions-runner.sh
```

The installer creates an enabled systemd service and sets
`RUNNER_WORKSPACE_PREFIX` to the runner user's home. The latter is required by
the preflight artifact paths in `tests.yml` and `sep-tests.yml`.

The resulting labels are `self-hosted`, `Linux`, `X64`, and `ragflow-test`.
Do not add `ragflow-release` unless this machine is explicitly authorized to
run release and publication workflows.

## Native dependency cache

`build.sh` verifies and consumes these pinned Linux x64 resources:

```text
/opt/ragflow-native-libs/office_oxide
/opt/ragflow-native-libs/pdfium-static
/opt/ragflow-native-libs/pdf_oxide
```

Prepare them from the repository's checksum-pinned archives with
`ragflow_deps/prepare_native.py`. A missing or corrupt cache is a build failure,
not a skipped prerequisite.

Go binaries embedded in the Ubuntu 24.04 production image are built through
`build-go-linux.sh` in the same Ubuntu 22.04 toolchain family as the C++
builder. This keeps their C++ ABI and glibc
baseline compatible even when the self-hosted runner itself is newer than
Ubuntu 24.04. The script reuses the runner's verified native cache and Go build
caches; workflows must not build release binaries directly on the host.

The Go module/build caches live under `~/.cache/ragflow-go-builder`, outside the
ephemeral Actions job directory. BuildKit's named apt, uv, and npm caches are also
retained by the Docker daemon between jobs.

## Candidate registry

Provision the LAN-only TLS registry once:

```bash
sudo REGISTRY_HOST=192.168.1.175 bash deployment/runner/setup-candidate-registry.sh
```

After both main-branch engine jobs pass, `tests.yml` builds and publishes
`192.168.1.175:5443/ragflow:<full-git-sha>`. The workflow verifies
`SOURCE_REVISION` and imports `business_documents` before pushing. The registry is
not a release publisher and the runner still has no `ragflow-release` label.

## Verification

```bash
systemctl status actions.runner.meownm-ragflow.ragflow-test-meow.service
docker info
bash build.sh --cpp
bash deployment/runner/build-go-linux.sh
```

GitHub should report the runner as `online` and idle before a workflow is
queued. The local build commands must produce `bin/ragflow_server` and
`bin/ragflow-cli` from the intended Git SHA.
