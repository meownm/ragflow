# Изоляция CI: test VM, release VM и host управления

## Topology и prerequisites

Management host содержит hypervisor, Nexus/его БД, CA и backup; GitHub Actions
runner на нём отсутствует. Test VM и release VM имеют разные service accounts,
диски, Docker daemon и credentials. Root test guest не управляет host, соседним
guest или Nexus. Обе VM можно разместить на одном доверенном hypervisor, когда
CI выполняется только внутри guests и не имеет management доступа.

Историческая проблема: `ragflow-test-meow` на `192.168.1.175` работал от `apt` с
полным sudo, а Nexus находится на том же host. Labels и repository ACL этого
не исправляют. Оператор фиксирует VM names/IP, hypervisor, guest disks,
accounts, network ACL и публичные CA fingerprints. Новые guest addresses в
репозитории не заданы; назначает их инфраструктурный администратор.

Начальная ёмкость: test guest — 8 CPU/24–32 GiB RAM/200 GiB свободного диска;
release guest — 2 CPU/4 GiB RAM/40 GiB диска. Host/Nexus требуют собственного
резерва. Текущие 30 GiB RAM всего host не подтверждают этот профиль: измерить
пик engine/native jobs и capacity до запуска matrix, исключить OOM/ENOSPC.

Запрещены guest-host shared folders, host Docker/libvirt sockets, SSH agent
forwarding, management keys и Nexus administrative credentials в guests.
Publication выполняется через skopeo. Docker release guest доступен только
администратору guest; publisher не входит в root-equivalent Docker group.

## 1. Drain старого runner и отзыв host доступа

1. Оставить `RELEASE_NIGHTLY_ENABLED=false`; записать source SHA, inventory
   сервисов и endpoints. Не записывать secret values в отчёт.
2. Создать отдельный management administrator с SSH key, console и sudo.
   Проверить новую сессию **до** отзыва доступа `apt`: этот account сегодня
   используется также для maintenance. Сохранить Nexus DB/blob backups у
   администратора, проверить их восстановление.
3. Запретить новые assignments, дождаться idle старого runner и остановить:

   ```bash
   sudo systemctl disable --now actions.runner.meownm-ragflow.ragflow-test-meow.service
   ```

4. Проверить отсутствие child CI processes. Завершить только известные CI
   run/attempt Compose projects; сохранить очищенные diagnostic logs. Не
   удалять Nexus, его БД, registry или пользовательские runtime volumes.
5. Удалить старую runner registration в GitHub и отозвать её credentials.
   Не переносить `.credentials*`, SSH/GH/Docker auth, home или environment
   secrets вместе с source/cache. Новый runner регистрируется заново.
6. Из management session удалить `/etc/sudoers.d/ragflow-runner-apt`, другие
   sudoers grants и membership `apt` в sudo/docker/libvirt/lxd/disk; убрать
   его management SSH/socket/API access. Заблокировать obsolete account
   после сохранения нужных данных. Отозвать и заменить ранее доступные ему
   Nexus admin/CI, GitHub tokens и другие administrative credentials.
7. От имени старого account проверить отказ sudo, host Docker/hypervisor,
   SSH management и прежних API credentials. Проверить reboot: старый
   service не стартует и registration отсутствует в GitHub.

Если host выполнял недоверенный код с root, удаления sudoers недостаточно:
восстановить OS из доверенного образа, переносить только проверенные DB/blob
backups, заменить доступные этому коду keys/tokens. Это prerequisite для
независимой management boundary, а не результат unit-тестирования.

## 2. Test VM

Создать свежий Linux x64 guest без management passthrough. Запустить
`deployment/runner/bootstrap-ubuntu.sh` **в guest**: он выдаёт sudo, устанавливает
native tools и расширяет свободные extents guest LVM. Подготовить native cache
по checksum-pinned manifest; не копировать home старого account.

Bootstrap и установка runner требуют `TEST_VM_CONFIRMED=yes`; вне VM и для shared
`apt`/root account они отклоняются до системных изменений, скачивания и регистрации.
Existing чужая registration
не заменяется через `--replace`.

Зарегистрировать `ragflow-test-vm` с labels `ragflow-test,ragflow-isolated-vm` через
`install-actions-runner.sh`, как описано в `deployment/runner/README.md`.
Account управляет только своим guest daemon. Его Docker/Compose должен иметь
guest root directory и не использовать remote `DOCKER_HOST` host.

Выдать staging writer и нужные resource pull roles. Проверить negative ACL:
test identity не пишет release repo, не читает Nexus admin API/publisher
secrets/management filesystem. Затем выполнить unit/native wrappers и
disposable Compose smoke; подтвердить cleanup контейнеров, volumes и port
reservations по repository/run/attempt/lane/engine.

## 3. Release VM: registration без sudo у publisher

Администратор guest запускает:

```bash
sudo RELEASE_VM_CONFIRMED=yes bash deployment/runner/bootstrap-release.sh
```

Перед этим проверяет topology. Bootstrap отказывает на registry host, вне VM
или при привилегированном existing publisher. Создаёт `ragflow-release`, tools,
guest Docker и systemd hardening; не регистрирует runner/секреты/Nexus repo.

Администратор скачивает официальный Linux x64 Actions runner, сверяет SHA256
и распаковывает в `/opt/actions-runner-release`, принадлежащий publisher.
Ephemeral registration token поступает через stdin/hidden prompt в admin
session, не сохраняется в history/файле. Дальнейшие действия администратора:

1. Выполнить `config.sh` через `runuser -u ragflow-release` с repository
   `https://github.com/meownm/ragflow`, name `ragflow-release-vm`, labels
   `ragflow-release,ragflow-isolated-vm`, work `_work`. Дополнительный label ставить
   только после проверки topology; его требуют jobs и hosted readiness.
   Не заменять чужую registration.
   Сбросить token variable после configure.
2. В runner directory выполнить `./svc.sh install ragflow-release`,
   `systemctl daemon-reload`, `./svc.sh start`. Test installer для этой роли
   не подходит: он требует sudo у вызывающего account.
3. Проверить unit
   `actions.runner.meownm-ragflow.ragflow-release-vm.service`: user правильный,
   `isolation.conf` действует, `NoNewPrivileges=true`, capabilities отсутствуют,
   publisher не имеет sudo или записи в Docker socket. Password locked;
   SSH login keys и administrative groups ему не выдаются.
4. Проверить execution gh/skopeo/Python под service sandbox и TLS trust.
   Запись разрешена в home/work/temp guest. Затем выполнить publication
   dry run; installation status не является live acceptance.

При регистрации используйте root-administrator session guest, не давайте
publisher temporary sudo для обхода installer. Общих caches с test VM нет.

## 4. Registry, роли и сеть

Nexus остаётся на management host вне CI. Настроить защищённый staging и
`docker-releases`: Basic auth/DockerToken realm, anonymous disabled.
Release repo: path-enabled Docker hosted, strict content validation,
`ALLOW_ONCE`; immutable SHA/run/attempt tags не перезаписываются.

| Identity | Минимальные privileges |
|---|---|
| Test staging writer | `browse/read/add/edit` только staging |
| Engine staging reader | `browse/read` только staging |
| Upgrade previous-release reader | `browse/read` только `docker-releases` |
| Publisher staging reader | `browse/read` только staging |
| Publisher release writer | `browse/read/add/edit` только `docker-releases` |
| Runtime release reader | `browse/read` только `docker-releases` |

Privilege names: `nx-repository-view-docker-<repository>-<action>`. Не выдавать
wildcard, delete/security-management или `nx-admin`. Проверить pull/push и
negative overwrite/delete/admin API. Staging reader и release writer —
раздельные identities; registry credentials не заменяют management credentials.

Текущий `setup-candidate-registry.sh` на `5443` предоставляет TLS без auth/roles.
Для limited actions перенести staging в Nexus либо использовать проверенный
token authority. Простая Basic auth Distribution не обеспечивает read-only
role. Endpoints и adapters credentials workflow изменяются вместе с переходом.

Firewall guest CI subnets блокирует SSH/hypervisor/admin API host. Разрешены
registry data plane TLS, GitHub/dependency endpoints и test model endpoint.
Path-enabled Nexus gateway ограничивает guest paths registry API; прокси всех
`/service/rest/` для guest networks не является management network boundary.
Разрешить registry `/v2/` и отдельный точный Bearer token endpoint из
`WWW-Authenticate` (у Nexus обычно `/service/rest/v1/security/docker/token`).
Сохранить запрет других `/service/rest/`, UI и административных paths. Auth
endpoint может находиться на другом TLS authority: проверить его allowlist и
CA отдельно; не открывать весь REST prefix ради Docker login.

Публичные CA с проверенным SHA256 устанавливаются в guest/runtime system trust.
Private CA/server keys и Nexus bootstrap credential остаются на host.
Не использовать insecure registry/`--tls-verify=false`. Test VM и RAGFlow
containers должны достигать обязательных Ollama models; release VM model/DB
доступ не требуется.

### Политика сети management host

`deployment/runner/ci-host-network.sh` принадлежит hypervisor administrator.
Он требует root и `CI_HYPERVISOR_CONFIRMED=yes`, отказывает внутри VM/container,
проверяет bridge names и IPv4 адреса. Это provisioning step, никогда CI step.
Он обновляет только собственные `RF_CI_*` chains; общие Docker/host rules не
сбрасываются. Model endpoint разрешён только test bridge; registry ports 8444/8445
разрешены обоим. Legacy 5443 открыт только test для утверждённого bootstrap.
Остальные private/reserved destinations, host management input и IPv6 запрещены;
public HTTPS/HTTP и нужные DNS/DHCP разрешены. Source spoofing блокируется
hypervisor fixed IP/MAC filter; firewall не заменяет этот фильтр.

Пример установки администратором после проверки topology:

```bash
sudo install -m 0755 deployment/runner/ci-host-network.sh /usr/local/sbin/ragflow-ci-network
sudo CI_HYPERVISOR_CONFIRMED=yes TEST_BRIDGE="$TEST_BRIDGE" \
  RELEASE_BRIDGE="$RELEASE_BRIDGE" REGISTRY_IPV4="$REGISTRY_IPV4" \
  MODEL_IPV4="$MODEL_IPV4" /usr/local/sbin/ragflow-ci-network
```

Сохранить проверенные параметры в root-only environment file и включить oneshot
systemd unit после local filesystems, перед libvirt и Docker. Libvirt units требуют
успешной policy до запуска guest networks. Docker/Nexus availability не должна
зависеть от CI policy failure; bridge hooks готовятся до daemon startup. Проверить
повторное применение, daemon/network restart и reboot из management console.
Не выполнять общий flush iptables/nftables и не копировать management environment
в CI. Назначения private package mirrors добавлять узко по фактическим consumers,
с отдельной негативной проверкой доступа к administrative endpoints.

Nexus gateway должен выбирать свой repository для `/v2/` ping; иначе root path
может выдать token realm соседнего repository. Проверить фактический challenge.
Для path-enabled hosted repository token может иметь точный URL
`/repository/<repository>/v2/token` вместо общего REST token URL. Разрешить только
наблюдаемый URL своего repository. Percent-encoded path обходы проверять отдельно;
запросы к административным paths и чужому repository остаются запрещены.

Официальный default Nexus PostgreSQL pool — 100 connections. DB limit должен
оставлять резерв для backup/администрирования; равный 100 limit блокирует `pg_dump`.
Проверять backup восстановлением disposable DB, а blob archive — отдельной
процедурой восстановления Nexus. Смена DB credential согласованно обновляет role,
private environment и Nexus connection settings; после recreation повторно
проверяются writable status и DNS upstream gateway.

## 5. GitHub policy, очередь и включение nightly

- `RELEASE_PUBLISHER_SHA` — полный проверенный immutable commit trusted
  publisher из защищённого main. Job публикации не исполняет source из
  qualification candidate как привилегированный controller.
- `RELEASE_IMAGE_REPOSITORY` и `STAGING_IMAGE_REPOSITORY` содержат реальные
  repository endpoints. Environment `internal-release` разрешает main и
  стабильные version tags по deployment branch/tag rules.
- Rules защищают main и создание stable tags: trusted release bot, проверка
  main ancestry и approved required checks. Наличие похожего tag name само
  по себе не устанавливает доверие.
- Publisher получает `NEXUS_STAGING_USER/PASSWORD` только для staging read,
  отдельные `NEXUS_RELEASE_USER/PASSWORD` — только release write. Test guest
  получает `NEXUS_STAGING_BUILD_USER/PASSWORD` staging writer только build job;
  engine jobs получают `NEXUS_STAGING_READ_USER/PASSWORD` reader. Secrets не
  сохраняются в `.env`, artifacts и постоянном Docker config. Общий helper
  создаёт config с mode 700 в собственном `RUNNER_TEMP`, логинится по stdin без
  вывода auth, передаёт `CI_DOCKER_CONFIG`/`DOCKER_CONFIG`/scope следующим steps.
  Sudo Docker всегда получает явный `--config`; runtime lock использует тот же
  config. После atomic login helper возвращает verified `config.json` runner
  UID/GID с mode 600 для raw Docker consumers. Upgrade step получает только
  `NEXUS_RELEASE_READ_USER/PASSWORD` reader для предыдущего qualified image и
  вызывает sourced `authenticate_previous_release_read` до probe, чтобы unset
  secrets в том же shell. Read auth дополняет существующий builder config;
  staging и release должны иметь разные registry host[:port], иначе Docker
  перезапишет authority auth. Test VM не получает release writer.
  Builder cleanup запускается с `if: always()` после lock/native/upgrade,
  engine cleanup — после stack. Путь и ownership проверяются перед удалением.
  Удаление verified job config через guest sudo охватывает root-owned BuildKit
  directories; постоянные Docker configs не используются.
- `RAGFLOW_CI_DEEPDOC_IMAGE`: опубликованный Linux-совместимый DeepDOC image,
  доступный reader identity; предпочтительно immutable digest. Workflow
  передаёт `DEEPDOC_IMAGE`, lock фиксирует registry digest и image ID. Локальный
  build-only `deepdoc_oss:latest` без RepoDigest блокирует qualification;
  engine запускает pinned Compose override с `--no-build`.
- `RUNNER_READ_TOKEN`: runner metadata read. `RUNNER_WATCHDOG_TOKEN`:
  runner metadata read и Actions write для отмены. Предпочтительны GitHub App
  tokens; maintainer CLI token не копируется в repository secret.
- Qualification независимы; сериализуется publish. `queue: max` сохраняет
  до 100 pending publications, `cancel-in-progress=false`. Watchdog каждые
  десять минут проверяет no online runner более 15 минут и busy queue более
  двух часов. Environment/concurrency wait диагностируются отдельно;
  `timeout-minutes` не ограничивает runner queue.
- Publisher сверяет current-attempt jobs и artifact IDs, API archive digests,
  repository/run/source, attempt/upload interval и закрытый file manifest.
  Обязательные skipped/failure/cancelled блокируют publication.
- Primary nightly pointer — annotated Git tag с проверяемым JSON message;
  immutable nightly release создаётся на run/attempt. Mutable GitHub asset
  `nightly` не используется. Journal — draft release с append-only assets
  для publication tag; retries продолжают зарегистрированные этапы.
- `RELEASE_NIGHTLY_ENABLED=false` сохраняется до двух полных квалификаций одного
  SHA, negative provenance/ACL/retry checks, внутреннего nightly и восстановления
  предыдущего выпуска. Acceptance: оба engine, три browser, шесть CLI platforms,
  wheels, equality проверенного/опубликованного image digest.
- После acceptance разрешить automatic nightly; проверить первые пять
  содержательных запусков, очереди, длительность и infrastructure failures.
  Обновление mutable nightly tag не запускает новую qualification.

Отчёт переноса содержит реальные host/VM identity, source SHA, отозванные
identities без secret values, точные checks/readiness и недостающую acceptance.
До live проверки VM/ACL/Environment статус `INCOMPLETE`; поставленные scripts
и passing unit tests не подтверждают выполненную infrastructure migration.
