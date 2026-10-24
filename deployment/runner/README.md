# Runner квалификации и публикации

Целевая схема: **test VM + release VM**, Nexus и управление hypervisor на
host вне CI. Порядок переноса и acceptance:
[runbook изоляции](../../docs/develop/ci-runner-isolation-ru.md).

| Узел | Доступ CI | Назначение |
|---|---|---|
| Management host | Нет runner/CI credentials | Hypervisor, Nexus, CA и backup |
| Test VM, `ragflow-test` | Sudo/Docker только своего guest | Native build, regression, staging write |
| Release VM, `ragflow-release` | Без sudo и Docker socket | Evidence, skopeo, staging read и release write |

Guests имеют самостоятельные Docker daemon. Host filesystem, daemon/libvirt
socket, SSH agent и management credentials в guests не передаются. Publisher
использует `skopeo`; Docker release guest остаётся у его администратора.

## Test VM bootstrap

На свежем test guest Ubuntu x86_64/ext4 выполните bootstrap как администратор.
Он даёт sudo и расширяет guest LVM: **не запускайте его на management host**.

```bash
sudo TEST_VM_CONFIRMED=yes RUNNER_USER=ragflow-test bash deployment/runner/bootstrap-ubuntu.sh
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

Регистрация test runner выполняется внутри test VM. Передавайте отдельный
ephemeral registration token на stdin, не копируйте installation/credentials
старого host runner:

```bash
printf '%s\n' "$RUNNER_TOKEN" | \
  TEST_VM_CONFIRMED=yes REPOSITORY=meownm/ragflow \
  RUNNER_NAME=ragflow-test-vm \
  RUNNER_LABELS=ragflow-test,ragflow-isolated-vm \
  bash deployment/runner/install-actions-runner.sh
```

The installer creates an enabled systemd service and sets
`RUNNER_WORKSPACE_PREFIX` to the runner user's home. Artifact/resource identity
includes repository/run/attempt/lane/engine.

The resulting labels are `self-hosted`, `Linux`, `X64`, `ragflow-test`, and
`ragflow-isolated-vm`. Этот дополнительный label назначается только после проверки
границы VM; старый host runner с одним role label не проходит hosted readiness.
Не добавляйте `ragflow-release` test guest. Release registration выполняется
администратором отдельного guest по runbook, без sudo у publisher account.

## Release VM

```bash
sudo RELEASE_VM_CONFIRMED=yes bash deployment/runner/bootstrap-release.sh
```

Оператор предварительно проверяет topology: host не выполняет CI, guest не
имеет host mounts/socket/credentials. Bootstrap не может доказать права на
hypervisor. Он устанавливает tools/guest Docker, создаёт publisher без
additional groups/sudo, закрывает password login, готовит systemd hardening.
Он не регистрирует runner и не создаёт token, secrets или Nexus repositories.
Не используйте test installer для непривилегированного publisher.

Публичный CA можно передать через `REGISTRY_CA_FILE` и независимо проверенный
`REGISTRY_CA_SHA256`. Private keys остаются на management host. Не отключайте
TLS verification. Сначала выполните две полные dry qualification одного SHA,
negative ACL/provenance/retry checks и восстановление; только затем запускайте
публикацию. Полная квалификация выполняется на версионном теге `vX.Y.Z` или
вручную через `release.yml` с `dry_run=true`; ежедневного расписания нет.
При ручном `dry_run=false` сохраняется публикация во внутренний nightly-канал.

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

## Incremental application builds

`Dockerfile` builds Python dependencies, browser resources, frontend and release
identity independently. `runtime-base` contains the system packages, virtualenv
and offline browsers, without application source, SPA or release identity. The
production target always starts from that stage, so repeated releases do not
inherit old application layers. It can also be exported explicitly:

```bash
docker build --target runtime-base -t local/ragflow-runtime-base .
```

The Python build normalizes only the virtual root project's version in temporary
manifest copies. Dependency versions, hashes and sources remain unchanged; the
image receives the original `pyproject.toml` and `uv.lock`. Release-only changes
therefore retain the dependency layer. `RAGFLOW_BUILD_JOBS` (default `2`) bounds
Python source builds, including NumPy's Meson compiler processes, to avoid
exhausting memory on a builder that shares a machine with running services.

Feature and qualified CI builds use `build-go-linux.sh --artifacts`. On a miss,
it compiles C++ and stripped Go binaries through `build.sh`; on a hit, it verifies
and restores the two binaries and tokenizer static library. The cache lives in
`~/.cache/ragflow-go-builder/artifacts`, outside source snapshots. Its key includes
Go/C++ sources and embedded resources, library bytes, immutable builder image ID,
`build.sh`, the native verification/profile files, and the actual build commands
and flags. The commands/flags used for execution also define the cache recipe;
changes to their meaning require updating that recipe (or its identity schema).
Launcher, cache-handler and Docker orchestration bytes are excluded: logging or
wrapper edits reuse artifacts when the toolchain and compilation inputs match.
The Dockerfile is represented by the resulting immutable builder image ID.
Python, frontend and release-version changes reuse it. Identity schema 2 starts
a new artifact namespace; old entries are retained without automatic promotion.
The incremental Go compiler cache is namespaced by the actual C++ static library,
native library bytes and builder identity: Go itself does not track changes in
external cgo libraries. Ordinary Go edits retain that incremental compiler cache.
Go binaries use `-trimpath -buildvcs=false`; source identity is supplied by the
image's checked VERSION/SOURCE_REVISION contract. Corrupt cache entries rebuild;
missing libraries and invalid toolchain identity fail the build. Per-key locks
serialize cache publication; output hashes are verified before restoration.
Existing native final-image tests and release qualification still run.

For comparable rebuild timings, create two separate managed disposable source
trees (no `.git`), populate their native binaries, and select the same immutable
base-image digests in both environments. Record the digests: mutable tags may
change while preparing a long benchmark and invalidate unrelated layers. Then
run from the checkout:

```bash
python3 deployment/runner/benchmark-build.py --before /task/before \
  --after /task/after --output /task/evidence
```

The benchmark completes each source variant's initial build and selected probes
before moving to the other variant. This avoids alternating two large cache
working sets; record the builder's GC policy with `docker buildx inspect` when
interpreting cache misses. It builds on the same Docker builder, with no cache
pruning, push or deployment. Select relevant repeats with
`--scenarios unchanged python version`; both initial builds always run.
It restores its temporary probes and saves each
command, exit code, elapsed time, image ID, Docker-accounted size, layer count and
raw build log.
Initial runs use the existing cache and must not be called cold builds. Compare
warm runs after initial builds complete, without other concurrent builds. Docker
image accounting is not a measurement of compressed registry transfer size.

## Candidate registry

Provision the LAN-only TLS registry once:

```bash
sudo REGISTRY_HOST=192.168.1.175 bash deployment/runner/setup-candidate-registry.sh
```

`setup-candidate-registry.sh` создаёт TLS registry без authentication/roles.
Ограниченные staging read/write roles требуют Nexus или token authority;
этот legacy setup сам по себе не выполняет целевую модель доступа.

`tests.yml` владеет qualification. Image собирается один раз в staging под
уникальным SHA/run/attempt; engine lane проверяют его digest. Test VM пишет
только staging, publisher читает staging и пишет только `docker-releases`.
Immutable release tags используют `ALLOW_ONCE`; nightly pointer разрешается
через проверенный Git tag/manifest. Endpoints задают
`STAGING_IMAGE_REPOSITORY` и `RELEASE_IMAGE_REPOSITORY`.

Staging auth создаётся только в job-scoped `RUNNER_TEMP` через
`staging-docker-auth.sh`; `sudo docker` получает явный `--config`. Builder
получает `NEXUS_STAGING_BUILD_USER/PASSWORD` с staging write, engine lane —
`NEXUS_STAGING_READ_USER/PASSWORD` только с staging read. Publisher использует
отдельный `NEXUS_STAGING_USER/PASSWORD` с read. Secrets не копируются в `.env`,
workspace, artifacts или постоянные `~/.docker`/`/root/.docker`. Пароль передаётся
на stdin; login output подавляется. `CI_DOCKER_CONFIG`, `DOCKER_CONFIG` и
`CI_DOCKER_AUTH_SCOPE` передаются следующим steps через `GITHUB_ENV`.
После atomic Docker login helper возвращает только проверенный `config.json`
runner UID/GID и mode 600. Поэтому raw Docker/upgrade consumer читает
`DOCKER_CONFIG`, а sudo commands используют явный `--config`; чужие файлы и
постоянный root config не меняются. Config содержит file-based auth; пустая
initial auth entry предотвращает auto-detection внешнего credential store.
Persistent `credsStore`/`credHelpers` и symlink `config.json` отклоняются до
login. Inherited `DOCKER_AUTH_CONFIG` очищается.

Для проверки upgrade предыдущий квалифицированный image читается из
`docker-releases` отдельной identity `NEXUS_RELEASE_READ_USER/PASSWORD` с
`browse/read`. Эти secrets передаются только upgrade step. В этом же shell
выполните `source deployment/runner/staging-docker-auth.sh`, затем
`authenticate_previous_release_read`: credentials будут unset до запуска
probe. CLI `previous-read` доступен для отдельного auth step. Helper добавляет
read auth в существующий проверенный builder config, не заменяет staging
entry. `RELEASE_IMAGE_REPOSITORY` должен иметь **другой host[:port]**, чем
staging: Docker auth keys ограничены authority, repository path не разделяет
identities. Release write identity в test VM не выдаётся.

Builder config живёт до завершения runtime lock/native/upgrade steps. Workflow
обязан вызвать `bash deployment/runner/staging-docker-auth.sh cleanup` с
`if: always()`. `cleanup-engine.sh` удаляет auth после cleanup stack, включая
отказ до создания Compose project. Cleanup проверяет temp root, repository,
run, attempt, scope и ownership marker; чужой путь или symlink отклоняется.
Удаление этого verified job directory выполняется через guest sudo, поскольку
BuildKit может создать root-owned subdirectories.
После принудительного завершения guest оператор удаляет только подтверждённые
остатки закончившегося job, никогда общий Docker config.

`RAGFLOW_CI_DEEPDOC_IMAGE` — обязательный repository variable с опубликованным
Linux-совместимым DeepDOC image reference (`repository@sha256:...` рекомендуется).
Workflow передаёт его как `DEEPDOC_IMAGE`. Локальный `deepdoc_oss:latest` без
registry manifest/RepoDigest не квалифицируется. Build job фиксирует supporting
images обоих engines в `runtime-images.json`; engine использует проверенный
override и `docker compose up --no-build`, без повторной DeepDOC сборки.

Для Nexus gateway разрешите registry `/v2/` paths и **точный** Bearer token URL
из `WWW-Authenticate` (например `/service/rest/v1/security/docker/token`) на
отдельном auth endpoint/allowlist. Остальной Nexus `/service/rest/`, UI и
administrative endpoints недоступны CI subnets. Доступ к token endpoint не даёт
management API доступа. Login authority — host[:port] из staging repository,
без repository path, userinfo или URL scheme; TLS/CA проверяется для обоих
registry/auth endpoints.

Hosted readiness использует `RUNNER_READ_TOKEN`; ручной queue watchdog —
`RUNNER_WATCHDOG_TOKEN` с runner metadata read и Actions write только нужного
repository. Tokens management/GitHub CLI accounts в guest не переносятся.
`runner-watchdog.yml` запускается только через `workflow_dispatch`, когда нужно
проверить зависшую очередь; периодического опроса каждые десять минут нет.
Перед публикацией hosted job повторно проверяет online publisher после полной
qualification. Watchdog контролирует `queued` jobs обеих ролей: offline более
15 минут и busy queue более двух часов. `waiting` (Environment approval) и
`pending` (concurrency) не считаются ожиданием назначения runner; см.
[GitHub deployment states](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments).

`release` profile всегда выполняет обе engine lane. `targeted` выбирает их
по подтверждённому baseline/path policy и не получает право publication.
Недоступный baseline выбирает обе lane. Обязательный skipped/failure/cancelled
никогда не считается релизным pass.

### Первоначальный baseline

`RAGFLOW_PREVIOUS_QUALIFIED_RELEASE` указывает опубликованный комплект schema 2
с проверяемыми GitHub assets и digest образа. Исторический образ без этого proof
не удовлетворяет обычному prerequisite. Для первого выпуска отдельно разрешён
разовый bootstrap с фиксированной утверждённой политикой
`tools/quality/release-bootstrap-baseline.json`: установить
`RELEASE_BOOTSTRAP_ENABLED=true` при пустом `RAGFLOW_PREVIOUS_QUALIFIED_RELEASE`.
Workflow запускает те же проверки с `--bootstrap`. Доказательство восстановления
явно ограничено историческим образом; сам этот образ остаётся неквалифицированным.
Publisher блокирует новый bootstrap после первого опубликованного комплекта,
разрешая только идемпотентный повтор его точного тега и qualification hash.
После публикации выключить bootstrap и задать предыдущий квалифицированный тег.
Отсутствие успешной migration/restore/cleanup проверки блокирует первый выпуск.

Для отдельной диагностики существующего образа используйте тот же upgrade probe:

```bash
RAGFLOW_TEST_DISPOSABLE=1 python3 deployment/runner/test-qualified-upgrade.py \
  --historical-image "$HISTORICAL_IMAGE_DIGEST" --historical-sha "$HISTORICAL_SHA" \
  --baseline-repository meownm/ragflow \
  --image "$CANDIDATE_IMAGE_DIGEST" --sha "$SOURCE_SHA" \
  --postgres-image "$POSTGRES_IMAGE_DIGEST" --output "$DISPOSABLE_PROOF_DIR"
```

Оба образа и PostgreSQL задаются по digest; SHA полные. Probe проверяет GitHub
commit/ancestry и фактический `SOURCE_REVISION` внутри каждого образа, затем
создаёт собственные PostgreSQL, synthetic rows и backups. Успешная диагностика
имеет `diagnostic_status=PASS`, но общий `status=INCOMPLETE`, exit 2: она доказывает
только проверенную миграцию и восстановление данных, не квалификацию исторического
выпуска. Она не используется как publisher proof. Shared runtime/DB не затрагивать.

The live browser jobs use the repository variable `RAGFLOW_CI_OLLAMA_URL` as
the Ollama URL reachable from their RAGFlow containers. The endpoint must have
`qwen3.8:latest`, `qwen3.6:27b`, and `bge-m3:latest` installed.
The fixture validates the real models and configures them only in the disposable
CI database; it fails when the endpoint or a model is unavailable.

The Go regression also pulls a digest-pinned image from this registry:
`ragflow-go-test-resources`. It contains the `rag/`, `wordnet/`, and `opencc/`
directories from `infiniflow/resource` commit
`0937399b60f1949267388548e33ea0d5c0cc25f7`. The test script verifies all
Git blob hashes against `go-test-resource-blobs.txt` before running Go tests.
To rebuild the image, copy these directories from that commit, verify their
hashes against the manifest, build a `FROM scratch` image with separate
`COPY rag /resource/rag`, `COPY wordnet /resource/wordnet`, and
`COPY opencc /resource/opencc` steps, and push it to the LAN registry. Update
the digest in the test script only after verifying the new image by digest.

## Verification

```bash
systemctl status actions.runner.meownm-ragflow.ragflow-test-vm.service
docker info
bash build.sh --cpp
bash deployment/runner/build-go-linux.sh
```

GitHub should report the runner as `online` and idle before a workflow is
queued. The local build commands must produce `bin/ragflow_server` and
`bin/ragflow-cli` from the intended Git SHA.
