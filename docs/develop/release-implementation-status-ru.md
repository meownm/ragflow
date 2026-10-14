# Исправление CI и выпуска: состояние на 12 октября 2026

## Исходники и статус

Основа: `db81651c7898933e712f47890314523b8730b412`, текущий checkout dirty.
Объединённые исходники и изолированный CI/release candidate проверены отдельно. В дереве есть
соседние изменения продукта; они не включены автоматически в выпуск.
Commit, push, публикация RAGFlow и deployment его runtime не выполнялись.
Инфраструктура CI и Nexus изменены по согласованному плану; фактические результаты
перечислены ниже. Полная релизная приёмка: **INCOMPLETE**.

## Изменённое поведение

- `tests.yml` владеет квалификацией: ограниченный `targeted` и полный `release`.
  `release.yml` управляет событиями и публикацией. `sep-tests.yml` оставлен тонким
  временным caller до проверки required checks и пяти содержательных запусков.
- Образ собирается один раз, фиксируется по digest и используется обеими engine
  lane. Браузерная матрица получает SPA из этого образа. Production build использует
  `npm ci`; native Go проверки используют репозиторные build wrappers.
- Qualification packet связывает SHA/run/attempt, API/browser inventories,
  образ, native dependencies, модели, CLI, wheels и отчёты. Publisher проверяет
  живые GitHub jobs, происхождение producer-архивов и заново собирает их пакет
  доверенным кодом. Browser JUnit повторно сверяется с inventory исходного SHA.
- Schema 2 qualification, publication и deployment имеют отдельные результаты.
  Проверки origin встроены в entrypoint publisher до внешних записей. Локальный
  Release проверяет опубликованные GitHub assets и загружает образ по digest.
- Публикация использует отдельную VM, защищённый Environment и готовые артефакты.
  Immutable assets, manifest/checksums, долговечный журнал и атомарный nightly ref
  позволяют распознать частичную попытку и выполнить повтор. Stable latest не
  меняется nightly. Только publisher сериализуется.
- Hosted readiness и jobs требуют `ragflow-isolated-vm` вместе с role/Linux
  labels. Старый host runner не удовлетворяет новой readiness. Watchdog читает
  все страницы активных runs/jobs и проверяет offline/занятую очередь.
- Исправлены тестовые контракты каталога, owner UI, navigation/constructor,
  WCAG prerequisites, API helper/parse state и CLI personas. API пагинация и
  некорректные chunk/session payloads дают проверяемые ошибки до storage/model
  операций. SDK корректно передаёт несколько document IDs.
- В конкурентных SDK/Web тестах проверяются результаты всех futures. Ошибка
  запроса не превращается в успешную проверку по числу завершившихся futures.
  Изменённые chunk сценарии проверяют точные ID и видимое сохранённое содержимое.
- В SQL query domain исправлены компиляция NOT IN, finite Decimal validation,
  отмена принадлежащих batch задач и закрытые входные типы. Adapter tests запускают
  приватные event loops в том же worker context, что и исполняемый сценарий;
  незакрытые pytest policy loops больше не теряются.

## Проверки на объединённых исходниках

| Граница | Результат | Артефакт |
|---|---|---|
| Business Documents domain, API adapters и REST contract; финальный GC | 1069 PASS, exit 0, без warnings; 1068 сценариев и одна внешняя GC проверка | `Y:/DevCache/temp/user/ragflow-release-bd-loop-fixed.log` |
| Branch coverage тех же двух owning packages | 2931/3698 = **79,259%**; combined coverage 87,33%; порог 79% сохранён | `Y:/DevCache/temp/user/ragflow-release-bd-loop-fixed.xml` |
| Release tooling, provenance, bootstrap, publication recovery, selector, CLI/native/upgrade policy и local deploy process | **436 PASS**, exit 0 | `Y:/DevCache/temp/user/ragflow-bootstrap-final-release-tools.log` |
| List/chat/session/chunk API и SDK contracts, HTTP encoding, concurrent fault injection, attachment consumer | **347 PASS**, exit 0 | `Y:/DevCache/temp/user/ragflow-final-api-contracts.log` |
| Actionlint 1.7.12 и независимая проверка publication queue | 6 workflows PASS | `python -m tools.quality.check_release_workflows` |
| Проверка синтаксиса | 16 Bash runner scripts; 2 PowerShell scripts — PASS | `bash -n`; PowerShell AST parser |
| Ruff затронутых release/API/SDK boundaries и новых integration tests; diff whitespace | PASS | `ruff check`; `git diff --check` |

Общий Business Documents прогон использует:

```text
pytest --noconftest test/unit_test/business_documents
  test/unit_test/api/apps/business_documents
  test/unit_test/api/apps/restful_apis/test_business_document_api_contract.py
  <external final-GC check>
  --cov=api/apps/business_documents --cov=business_documents
  --cov-branch --cov-fail-under=79 --cov-report=xml:<external proof path>
```

Тесты policy/publication используют synthetic transport и disposable fixtures.
Они не доказывают работу настоящего registry, GitHub scheduler, native toolchain
шести платформ или реальных live моделей.

Независимая p3/local collection сверена с checked-in API inventory для каждого
движка: SDK **1105**, REST **545**, Web **624**, admin **24**; суммарно **2298**
selected cases на engine. Missing/extra/duplicates: 0. Это проверка состава,
не выполнение live сценариев. Необоснованный skip блокируется валидатором;
утверждённая applicability политика не расширялась для сокрытия падений.

## Изолированный CI/release candidate и интегрированное ревью

Подготовлен detached worktree от той же базы с 166 выбранными снимками:
164 изменённых файла и две неизменённые browser policy/runner границы.
Соседние SQL revisions/split UI, ASR/translator и EVA feature hunks исключены;
исходная независимая browser inventory содержит 62 сценария. Основная inventory
в dirty checkout сохраняет свои дополнительные сценарии.

| Граница candidate | Свежий результат |
|---|---|
| Business Documents owning packages + REST contract | 1021 PASS, без warnings; strict branches 2702/3406 = 79,331%, минимум 79% сохранён |
| Tools/API/SDK contracts | 954 PASS + 42 subtests; 9 существующих Linux selector tests пропущены на Windows |
| Те же 9 selectors в test VM | 9 PASS, реально исполнены через Linux Bash |
| Bootstrap/source guard | 17 PASS |
| Browser/API collection | 62/62 browser; 2298 API cases на engine; missing/extra/duplicates 0 |
| Actionlint/Bash/PowerShell/Ruff/diff | 6 workflows, 16 scripts, 2 scripts; PASS |

Owning Python modules загружены из candidate; посторонних source modules нет.
Исходные snapshots, выбранные hunks и хеши сохранены вне репозитория.
Проверки объединённого дерева не использовались как доказательство candidate.

Интегрированное ревью выявило missing identity/inventory в завершающем вызове
`verify_api_reports` engine lane: p3 упал бы после успешных suites. Исправлен
реальный вызов. Завершающий Python-блок исполняется тестом на валидном и чужом SHA
для обоих engines: 14 PASS на main и 14 PASS на candidate; неуспех не перезаписывает
engine evidence. Семь imported quality calls в Python heredocs согласованы с
сигнатурами. Inline privileged C++ builder удалён: C++/Go используют существующий
`build-go-linux.sh`. Build/native contracts после изменения: 35 PASS, actionlint PASS.

В main перенесены только независимо согласованные build-identity checks и две
pytest marker declarations. Fresh focused bootstrap/source/build-identity: 44 PASS.
Candidate ещё не закоммичен; релизного SHA и receipt нет. Диагностический ресурсный
пилот не считается квалификацией выпуска.

## Утверждённая начальная точка восстановления

Пользователь разрешил разовый bootstrap с найденным историческим QA-образом:

```text
192.168.1.175:5443/ragflow@sha256:1c25531cf7e6b9e54b2e3ea1c46eb899b978db1fe804aebddddf4422498b6c63
source: 3d168d5bc503f87f32eb255b8a21dad509f7b0db
```

SHA образа проверен в существующем QA runtime, Git history и GitHub commit API.
Отчёта полной квалификации этого исторического образа нет. Политика фиксирует
точную пару image/SHA в `tools/quality/release-bootstrap-baseline.json`.
Local runtime с `SOURCE_REVISION=unverified` не выбран как доверенная точка.

Bootstrap проверяет actual old/new image model initializers на synthetic
PostgreSQL: seed, backup, migration, повтор initializer, восстановление нового
backup и возврат старого backup/образа, cleanup только собственных ресурсов.
Receipt прямо сохраняет ограниченный recovery scope и `historical-unqualified`.
Весь новый комплект проходит обязательные release lane без послаблений.

Publisher разрешает первый bootstrap только при отсутствии опубликованного
квалифицированного комплекта. Последующая попытка блокируется; точный повтор того
же tag/qualification hash/bundle разрешён для восстановления после обрыва.
После первого выпуска отключить `RELEASE_BOOTSTRAP_ENABLED` и указать предыдущий
квалифицированный tag. Простая historical diagnostic остаётся INCOMPLETE даже
при успешных migration probes и не заменяет bootstrap policy.

## Оставшаяся инфраструктурная и live приёмка

Пользователь разрешил две VM на management host Nexus `192.168.1.175` и
меньший пробный профиль с измерением полноценности квалификации. Реализованы:

| Узел | Пробный профиль | Изоляция |
|---|---|---|
| `ragflow-test-vm`, `172.30.80.10` | 6 vCPU, 10 GiB RAM, 210 GiB qcow2 | Собственный диск/daemon/account; AppArmor и fixed IP/MAC filter |
| `ragflow-release-vm`, `172.30.81.10` | 2 vCPU, 2 GiB RAM, 40 GiB qcow2 | Другой диск/daemon/account; publisher без sudo/socket/SSH keys |
| Management/Nexus host | 30,6 GiB RAM, один SSD | Новый management administrator; CI accounts не управляют host |

VM используют проверенный Ubuntu 24.04 cloud image, самостоятельные диски,
разные сети и guest administrator keys. Shared folders, host devices/sockets,
SSH agent и management private keys в guests отсутствуют. Libvirt memory hard
limits включают overhead: 11/3 GiB. Autostart VM пока выключен.

Старый host runner остановлен/disabled, registration удалена из GitHub,
локальные runner credentials удалены. Account `apt` лишён sudo/groups и SSH;
проверены реальные отказы sudo и SSH. Старый host GH credential уже отвечал 401,
его файл удалён. Перезагрузка management host и независимая аттестация его
исторического root-состояния не выполнялись.

Освобождён неиспользуемый BuildKit cache выведенного runner и точные локальные
копии staged/Feature images после проверки remote digest и отсутствия любых
container consumers. Рабочий bootstrap image, QA/Nexus containers и volumes
сохранены. После очистки root имел около 306 GiB свободно; это результат операции,
а не сумма предварительных reclaimable оценок Docker.

Nexus admin password и PostgreSQL credential заменены после отзыва старого CI
доступа. Перед изменениями Nexus остановлен на время согласованного backup:
DB dump, globals и 25,2 GB blob/config archive сохранены у management administrator.
Dump восстановлен в одноразовый PostgreSQL: все 204 таблицы схемы `nexus` есть.
Полное восстановление Nexus приложения из blob archive ещё не проверено.
Обнаруженный connection starvation устранён: max_connections 100 → 150 при
пуле Nexus 100; сохранён резерв административных подключений.
Причина подтверждается [документацией Sonatype](https://help.sonatype.com/en/postgresql-max-connections.html).

Созданы Nexus `docker-staging` и `docker-releases`: strict content validation,
`ALLOW_ONCE`, anonymous disabled. Шесть data-plane identities имеют только
`browse/read` либо `browse/read/add/edit` своего repository. Новые TLS endpoints
`8444/8445` доступны через отдельный gateway: registry paths и точный token path
своего repository, остальные Nexus REST/UI paths закрыты. Новый публичный CA
установлен в guests; private CA/server keys остаются на host.

Реальные infrastructure проверки:

- Обе VM: host SSH/8081/8443/9090 и соседняя VM недоступны; GitHub HTTPS доступен.
  Publisher также не достигает legacy 5443 и model server.
- Test VM видит обязательные qwen3.8/qwen3.6/bge-m3 и фиксирует их digest.
- Staging push/read проходят; reader push, test write в release, publisher write
  в staging и delete отклоняются 403. Changed tag отклоняется 409.
- Publisher `skopeo copy --preserve-digests` переносит минимальный ACL probe;
  readback release равен исходному manifest digest. Runtime reader pull проходит,
  его push запрещён. Probe namespace не является выпуском RAGFlow.
- Publisher user действительно не имеет sudo и записи Docker socket. Systemd
  sandbox исполняет gh/skopeo/Python, запрещает socket/system writes. Настоящий
  runner ID29 был online/idle; после проверки снова остановлен. Test runner ID28
  зарегистрирован, но service остановлен для ресурсного пилота.
- Ограниченная memory нагрузка: test 8 GiB + release 1 GiB, 35 samples;
  host MemAvailable минимум 10 499 743 744 bytes, Nexus writable всегда 200,
  максимум latency 9,6 ms. Максимально занятый host swap около 44 MB; это
  предварительная capacity проверка, не полная квалификация.
- Go builder построен с BuildKit limit 8 GiB/3 CPU/max-parallelism 1.
  HEAD native C++/Go и parser test binary собраны за 198 seconds с limit
  6 GiB/2 CPU, exit 0, OOMKilled=false. Шесть native PDF/DOCX scenarios проходят
  в builder. Candidate C++/Go и CLI tests также прошли в VM; общий
  `build-go-linux.sh --cpp` / `build-go-linux.sh` исполнен от guest CI account,
  exit 0. Девять Linux-only selector checks, пропущенных на Windows, прошли в VM.
- Полный диагностический образ candidate построен через ограниченный BuildKit.
  Первая попытка завершилась OOM client unit при Docker load с лимитом 512 MiB;
  BuildKit/VM OOM не было. Повтор с client limit 2 GiB и теми же cached layers
  загрузил образ, exit 0. Manifest: `sha256:331af2964f2d4610241a69e9c9ef70b43dcb02731691d5387f7225ed46e96029`.
  Он содержит `SOURCE_REVISION=unverified` и не является release candidate.
- Внутри этого образа без source/native overlays проходят шесть native PDF/DOCX
  scenarios и CLI извлечение ожидаемого текста из PDF/DOCX. Отдельный diagnostic
  report сохраняет hashes и cleanup; release native receipt не создавался.
- Image build/retry: 134/20 samples; host MemAvailable минимум 10,30/10,59 GB,
  guest MemAvailable минимум 4,54/8,76 GB. Nexus всегда отвечал 200; максимальная
  latency при повторном Docker import — 0,782 s. Минимум свободного диска host
  после import — 218,99 GB, guest — 142,13 GB. Эти значения не доказывают
  достаточность профиля для полного disposable stack и live model/browser suites.
- Test bootstrap и runner installer отклоняют физический host и retired
  `apt`/root identities до mutation. Publisher readiness повторяется на hosted job
  после qualification; queued jobs обеих ролей охвачены watchdog. Approval и
  concurrency waits имеют отдельные состояния, без безусловного publisher exemption.
- DeepDOC prerequisite собран из baseline source snapshot и проверен отдельным
  disposable container: health, DLA, OCR detection/recognition, TSR — PASS;
  OCR возвращает `RAGFlow 2026`. Hashes пяти model files записаны. Образ сохранён
  в staging с readback того же manifest digest
  `sha256:9863910183bd142991a27ae2fd18c77abe92285c0bb1649a2b676e6bc2dbaaf9`;
  `RAGFLOW_CI_DEEPDOC_IMAGE` фиксирует этот digest. Это prerequisite artifact,
  а не опубликованный выпуск RAGFlow; container cleanup — PASS.
- Полный disposable stack readiness probe выполнен последовательно на обоих
  engines с тем же unverified image digest. API возвращает `pong`, SPA — HTTP200,
  Go CLI — SUCCESS; TEI отдаёт embedding размерности384. По восемь сервисов на
  engine; containers, volumes и networks удалены. Счётчики cgroup OOM/OOM kill
  и Docker OOMKilled не показывают OOM. Первое diagnostic ожидание JSON на ping
  исправлено на реальный text contract; прерванный proof сохранён отдельно.
  Elasticsearch:23 samples, min guest/host MemAvailable2,65/11,56GB;
  Infinity:25 samples,3,58/11,52GB. Nexus всегда200, max latency13,0/21,4ms.
  Это отдельный пробный resource override: search2GiB/ES heap512MiB,
  application4GiB, DeepDOC2GiB, TEI1,5GiB; он пока не принят как CI profile.
  Application cgroup достигал4GiB с memory reclaim без OOM. Достаточность для
  parsing/concurrent API/live browser нагрузок должна проверяться отдельно;
  readiness не заменяет2298 API cases на engine или полной browser inventory.

GitHub Environment `internal-release` разрешает только main и version tag pattern;
repository variables и узкие registry secrets настроены. Release writer и publisher
staging reader находятся только в Environment. `RELEASE_NIGHTLY_ENABLED=false`;
старый release workflow выключен на время переноса. Trusted publisher SHA пока
не задан. Hosted readiness/watchdog tokens запрошены отдельно: административный
CLI token не переносится в CI.

Для завершения необходимы:

1. Подтвердить resource profile на полном p3 API/model/browser workload и закрепить
   его в CI после измерений. Image/native/DeepDOC и оба stack readiness diagnostics
   завершены; guest runners зарегистрированы, sandbox проверен. Меньший профиль
   не признаётся достаточным для release по readiness smoke.
2. Завершить runtime/image проверку подготовленного отдельного candidate;
   зафиксировать окончательный manifest. Затем согласовать commit/push комплекта
   с уже проверенными source inventory и coverage.
3. Настроить runner read/watchdog credentials, pinned publisher SHA и
   digest-pinned DeepDOC/resource images. Выполнить две полные квалификации
   одного committed SHA/комплекта через replay на обоих engines.
4. Исполнить реальные native/platform/model/browser проверки, PostgreSQL race,
   bootstrap migration/restore и негативные provenance/publication операции.
5. Выпустить внутренний nightly и проверить его readback/rollback. Затем включить
   ежедневное продвижение; после пяти запусков обновить required checks и удалить
   временный caller. До этого автоматический nightly остаётся выключен.

Оставшиеся WebKit/runner connection/Docker pull дефекты требуют воспроизведения
в live среде. Unit и collection evidence не снимают этот acceptance gap.

Связанные документы: [архитектура](release-architecture-ru.md),
[изоляция VM](ci-runner-isolation-ru.md),
[runner tooling](../../deployment/runner/README.md),
[регрессия](../../test/REGRESSION.md),
[локальный deploy](../../deployment/local/README.md).
