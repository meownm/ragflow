# Переход к архитектуре расширений RAGFlow

Дата обновления: 2026-09-14. Статус: T0 реализован для снимка из [отчёта инвентаризации](t0-provenance-report-ru.md); T1 зафиксирован как `BASELINE_COMPLETE_WITH_KNOWN_FAILURES`; T2 принят как `REPORT_ONLY_COMPLETE_WITH_CLASSIFIED_LIMITS`, а не как архитектурный/dead-code/build PASS. T3 control PR #4, #12 и #31 последовательно слиты в `main`; PR #31 интегрирован exact merge-коммитом `7843651969329f34accf537e902fa77c7720a4ff`. Первый post-integration `opened` PR #32 доказал исправление merge-ref race: run `34777290557` при пустом payload без retrigger принял synthetic merge `7d24b626030517461d81d45de3292474f64b6bf3` с точными base/head parents. Тот же run затем выявил отдельный trusted fixture-harness regression: шесть source-mutation cases не нашли protected resolver dependency, поэтому analysis дал `6 failed, 112 passed, 118 subtests passed`, а final остался fail-closed. PR #32 закрыт без merge. Candidate PR #33 содержит closure fix `2eba9b2c69204da713a26b5acaaa96c6d7f64512`, cache-clean follow-up `4ae9c53a0` и JUnit follow-up `0296ac478`: дочерний pytest больше не создаёт `.pytest_cache`, а параметризованные `name[param]` cases сопоставляются с exact logical inventory без ослабления missing/unexpected checks. Windows subset завершился `132 passed, 1 skipped`; полный non-root Linux quality-suite — `485 passed` без skip. После публикации exact head `02bbe07842bf2d6e53e34e966dcc604bec75465f` run `34797814944` выявил следующий blocker: `synchronize` payload сохранил предыдущий test merge SHA, хотя stable `R1/F/R2=21d15986…` уже имел exact текущие parents. Follow-up `7b8bb6c21241453a99169fb61d4263abb72d51df` оставляет payload валидируемым подписанным advisory-наблюдением receipt v2 и выводит candidate только из stable fetched object и exact parents; focused Python 3.13.12 matrix дала `35 passed`, полный isolated Linux Python 3.13.11 quality-suite — `487 passed in 85.80 s`, без skip. Исправления ещё не интегрированы; после интеграции требуется полное повторение post-integration матрицы. Репозиторий принадлежит личному аккаунту, Required Workflow и merge queue недоступны в этой topology, ruleset отсутствует, независимый control owner не назначен. Поэтому T3 остаётся `IN_PROGRESS / REPORT_ONLY / NOT_ENABLED`, а T4–T7 по этой последовательности не приняты.

Цель: уменьшать стоимость обновлений upstream, сохраняя собственные функции и данные, и предотвращать новые нарушения архитектуры. Основание — [целевая архитектура](architecture-and-code-quality-ru.md). Проверки и критерии отказа определены в [каталоге правил](architecture-checks-ru.md). Операционные инструкции для агентов находятся в [AGENTS.md](../../AGENTS.md).

Постоянный цикл после изменений описан в [очистке кода и упрощении алгоритмов](continuous-code-simplification-ru.md): scan → доказательства → ограниченный patch → проверки → повторный анализ. Он применяется внутри текущего объёма задачи и не запускает глобальный рефакторинг upstream.

T2 включает T0 provenance, ограниченные Python ARC-01/02/03 и DEAD-01 scan/plan, 19 exact runtime probes/contracts, полный `web/src` TypeScript observer, два Linux Go package graphs, runtime-graph classifier и Go build-profile planner. TypeScript computed import остаётся исходным `INCOMPLETE`; Go observer и свежий build plan остаются `OBSERVED / NOT_EVALUATED`; classifier возвращает только `COMPLETE / REPORT_ONLY / REVIEW_REQUIRED`; DEAD-01 сохраняет `NOT_CONFIRMED`. T3 теперь выполняет выбранные lanes в base-authority workflow и изолирует configured Python runtime probes на уровне Docker, но пока остаётся report-only, потому что repository enforcement не включён. Широкий архитектурный/dead-code/native-build verdict, исторические saved DSL и продуктовые regression checks этим не заменяются.

## 1. Организация перехода

Переход выполняется небольшими законченными изменениями. Один PR имеет один проверяемый результат: граница, исправление, удаление либо подключение проверки. Рефакторинг стандартного ядра не является самостоятельным этапом.

Порядок зависимостей: T0 → T1 → T2 → T3 → T4 → T5 → T6 → T7. Настройку анализаторов можно исследовать раньше, но блокирующий gate включается только после проверки точности. Новые функции могут разрабатываться во время перехода: они сразу следуют инструкциям, а старый долг не переносится на них автоматически.

До автоматизации применимые правила проверяются вручную в ревью с записью доказательств. Это процессное правило, не технический запрет merge. После подключения required checks тот же набор получает машинную проверку там, где она достоверна.

Роли определяются для конкретного изменения, а не выдумываются в документе:

| Роль | Ответственность |
| --- | --- |
| Владелец модуля | Бизнес-инварианты, публичные контракты и устранение долга |
| Исполнитель | Изменение, доказательства, исправление новых нарушений |
| Ревьюер | Границы, необходимость core-diff, полнота проверок и удаления |
| Владелец выпуска | Upstream-база, миграции, готовность кандидата и восстановление |

В небольшой команде роли могут совмещаться. Это не разрешает анализатору автоматически одобрять собственное расширение baseline. При внедрении CODEOWNERS используются существующие аккаунты и доступные правила репозитория. Дополнительных разрешений на уже согласованный объём работы этот документ не требует.

## 2. Этапы и критерии завершения

### T0. Зафиксировать исходное состояние и границу upstream

**Действия.** Проверить рабочее дерево, собственный commit и remote refs. Определить последний принятый upstream commit по истории интеграций; если достоверно не установлен — записать неизвестность и разобрать её, не подставлять HEAD форка или текущий `upstream/main`. Классифицировать собственные файлы, изменения стандартных файлов, генерацию и внешние ресурсы.

**Артефакты при реализации.** `tools/quality/upstream-base.json`, `module-map.yaml`, `core-changes.yaml` и перечень сохраняемых возможностей с именованными проверками. Форматы — в каталоге правил.

**Результат T0.** [Отчёт, подтверждение upstream и ограничения снимка](t0-provenance-report-ru.md). Августовский merge сохранил дерево первого родителя; поэтому база содержимого установлена отдельно от графового `merge-base`. При изменении рабочего дерева основной снимок обновляется через `--write`; после новых commit или генерации ignored release/runtime ресурсов используется `--write --refresh-supporting`, не выполняющий сетевой fetch и не заменяющий официальную проверку новой upstream-базы.

**Приёмка.** Любой участок diff относительно принятой базы классифицирован; неизвестных участков нет. У каждого собственного модуля есть владелец. Реестр не исключает целиком `api`, `rag`, `internal` или `web`. Подтверждено, что рабочие незакоммиченные изменения не потерялись при формировании снимка.

**Если этап не завершён.** Продолжать изучение и точечные задачи можно; нельзя объявлять автоматическую классификацию происхождения достоверной или запускать массовое удаление по ней.

### T1. Защитить текущие функции до рефакторинга

**Текущий результат.** [Прогоны, добавленные проверки и известные baseline failures](t1-regression-report-ru.md), [машинные результаты с test IDs](t1-regression-results.json). Помимо исторического кандидата C, на локальном Docker-контуре выполнены реальные EVA/OpenMetadata read/write/retry/recovery, согласованный PostgreSQL+MinIO restore, T-One inference/cancellation и current/historical saved-DSL roundtrip. Отмена ASR исправлена и подтверждена live. MRZ Image2Text и часть существующих persisted DSL воспроизводимо не проходят; причины локализованы по границам, их рефакторинг заблокирован до отдельного исправления или миграции. Поэтому T1 завершён как baseline, а не как заявление об отсутствии продуктовых дефектов или готовности T6 release/recovery.

**Действия.** Привязать каждую собственную возможность к наблюдаемым результатам и существующим тестам. Запустить нужные lanes на подготовленном окружении, зафиксировать исходные результаты и пропуски. Дописать только недостающие проверки рисков; не добавлять тесты на расположение функций или текст исходника.

| Возможность | Минимальное доказательство |
| --- | --- |
| Business documents | Создание/ревизия, роли, конфликт версий, повтор команды, worker/export, сохранение evidence |
| EVA-интеграции | Авторизация, корректный выбор источника/назначения, повтор запроса без дублирования, отображение ошибки |
| ASR и другие сервисные расширения | Контракт вызова, тайм-аут/отмена, результат в пользовательском сценарии |
| Audit и observability | Значимое действие связано с actor/tenant/operation; ошибка наблюдаемости не создаёт ложный бизнес-успех |
| Общие зависимости от RAGFlow | Dataset access, поиск/цитаты, маршруты, сохранённые DSL и модельные настройки |

Это стартовый перечень, не утверждение полного покрытия. Его полнота уточняется по T0. Для ASR и других найденных возможностей нельзя считать тест существующим только из-за наличия строки в таблице.

**Приёмка.** У каждой обязательной функции есть выполненная проверка с ожидаемым результатом. Имеющиеся сбои воспроизведены и классифицированы. Рефакторинг функции не начинается с неразобранного сбоя её базовой проверки: сначала исправить или локализовать причину. Процент coverage фиксируется по фактическим источникам; scope не сокращается ради порога.

### T2. Ввести проверку границ без ложных блокировок

**Текущий результат.** [Python-пилот и инструкции](t2-python-analysis-ru.md): AST-граф с импортируемыми symbols, неявными parent initializers, диагностикой неизвестной динамики, provenance и сигналами сложности. Report-only runner различает `PASS`, `FAIL` и `INCOMPLETE` по настроенным ARC-01/02/03 slices для корневого Python-профиля и отдельного `src`-layout ASR-профиля. Для ASR он использует выделенное contract-окружение из tracked hash-locked requirements, проверяет 31 модуль на явные циклы, четыре контрактных модуля на чистый import, точный inventory из 18 FastAPI routes, lifespan cleanup и bounded worker thread lifecycle; отсутствие интерпретатора остаётся `INCOMPLETE`. Остальные обязательные contracts закрепляют все 15 локально изменённых/добавленных REST API loaders (248 routes), реальные agent registries и generated Canvas DSL round-trip, production adapters, frontend routes/navigation, application bootstrap, оба звена provider/router composition и обе HTTP service factories, Python API и Flask admin bootstraps, task-executor registry/CLI/run-mode/unacked replay/special fan-out hydration/operation-log/heartbeat/error/cancellation/cleanup/shutdown semantics, Business Documents worker lifecycle, sync worker process root и connector dispatch registry. DEAD-01 scan/plan охватывает чистый домен и ASR, но принципиально возвращает `NOT_CONFIRMED / NO_AUTOMATIC_PATCH`. [TypeScript observer](t2-typescript-analysis-ru.md) охватывает весь `web/src`, обратных потребителей, browser-entrypoint reachability, eager cycles и dynamic-loading gaps; текущий computed import сохраняет исходный `INCOMPLETE`. [Go observer](t2-go-analysis-ru.md) охватывает все Go sources, включая полную legacy build-constraint semantics, два Linux package graphs, server/CLI entrypoints, reverse consumers и directives без compiler; 0 неполных разрешений и 0 package cycles означают `OBSERVED`, не policy/build PASS. [Runtime-graph classifier](t2-runtime-graph-classification-ru.md) доказательно отделяет exact inherited upstream cycles от нового локального ребра по source/target/specifier и `kind/phase/type_only`, классифицирует все текущие owned unreachable paths, production/auxiliary roots, dynamic gaps и Go directives без wildcard baseline или удаления. [Go planner](t2-go-build-profiles-ru.md) отдельно проверяет полноту owner/target mapping всех T0 `.go`-дельт, запрещает обход `build.sh`, собирает отдельные `cmd` entrypoints и принимает package tests только по структурированным `go test -json` events. Linux/native `PASS` относится к предыдущему согласованному snapshot; свежий host-план текущего snapshot — `READY / NOT_EVALUATED`. T2 принят как полный report-only инструментарий с классифицированными пределами; полного runtime/dead-code verdict, policy gate или CI gate нет. Известные baseline failures T1 не скрываются результатом T2.

**Действия.** Реализованы схема реестров, классификация изменений, import analysis и отчёты. Срез охватывает две локально затронутые agent registries, все локально изменённые/добавленные REST API loaders, production adapters Business Documents, точные frontend routes/navigation, application bootstrap, provider/router composition и HTTP service factories, широкий статический TypeScript-граф `web/src`, широкие Go package graphs для CGO/no-CGO, Python API и Flask admin bootstraps, task-executor registry/CLI/run-mode/unacked replay/special fan-out hydration/operation-log/heartbeat/error/cancellation/cleanup/shutdown semantics, Business Documents worker lifecycle, sync worker process/dispatch registry, ASR route/lifespan/thread-pool lifecycle, cycle/dead-code проверки чистого домена и ASR, полный host build/test plan для текущих `.go`-дельт и exact классификацию TypeScript/Go signals и дополнительных roots. Подготовлены четыре точных report-only lanes T3 — Python architecture, runtime graph, Go build plan и policy fixtures — плюс отдельный provenance lane. Workflow ещё не настроен как required merge gate и инструменты не удаляют код; existing tests продолжают блокировать регресс как раньше.

**Проверки самого инструмента.** Положительные и отрицательные фикстуры из каталога правил: запрещённый импорт, обход через helper, отсутствующий анализатор, истёкшее исключение, подмена upstream-базы, rename/delete стандартного файла, dynamic entrypoint.

**Приёмка.** Каждое включаемое правило обнаруживает свою негативную фикстуру и пропускает допустимую. Нельзя включать rule с систематическими ложными срабатываниями, скрыв их широким ignore. В отчёте разделены найденное нарушение и неполный анализ.

**Фактическая приёмка T2.** Положительные и отрицательные fixtures Python/TypeScript/Go observers, architecture runner, DEAD-01 scanner, build-profile planner и runtime-graph classifier проходят. Classifier отдельно отклоняет локальное ребро цикла, подмену eager module edge на upstream type-only/call-phase edge, отсутствие TypeScript parser, неизвестный/stale unreachable path, ложный type-only verdict, новую регистрацию/source gap/Go main/directive, исчезнувшее root evidence и embed asset. Frontend contract отклоняет разрыв передачи `routers` через wrapper, а Go observer проверяет отрицание и AND/OR legacy `+build` с fail-closed для некорректной записи. Текущие inherited/manual сигналы записаны как evidence и не маскируются `PASS`, baseline или ignore. Поэтому завершён именно report-only этап T2; включение selector/aggregate/required check остаётся T3.

Для автоматической очистки отдельно принять whitelist-преобразования, учёт side effects и защиту fingerprint от конкурирующих изменений. Сначала внедрить `scan/plan/verify`; локальный `apply` включать только после негативных проб. Для сложности откалибровать сигналы на существующих собственных модулях; не делать один порог обязательным основанием переписывания всех функций.

### T3. Включить контроль новых нарушений

**Текущий результат.** Реализованы exact changed-path selector, base/candidate monotonic comparison, protected-source materialization, identity-bound reports и fail-closed aggregate. Candidate checker/workflow остаются `MUST_MATCH`; только declarative policy может пройти `BASE_FIXTURE_REVIEW`, не меняя base evaluator/materialization. Workflow разделяет trusted plan, candidate analysis и fresh final aggregate с единственным стабильным именем `architecture-policy`. Configured Python producer запускается в Docker с read-only candidate/trusted/rootfs, без сети/IPC и только с одним supervisor-owned report file; runtime children имеют UID/GID `65534:65534`, пустые supplementary groups, нулевые effective capabilities и `no_new_privileges`. Перед producer обязательны 12 отрицательных OS checks. Исторические positive/negative runs перечислены в [отчёте T3](t3-architecture-policy-ru.md). PR #4/#12/#31 интегрированы в trusted `main`; PR #31 merge `784365196…` добавил stable exact `read/fetch/read`, fetched object/parent binding, verified plan checkout и cross-job receipt. Первый реальный `opened` после интеграции, PR #32 run [34777290557](https://github.com/meownm/ragflow/actions/runs/34777290557), успешно разрешил пустой payload в exact merge `7d24b626…` с правильными родителями, но полный run выявил fixture dependency-closure defect и остался fail-closed. PR #33 последовательно исправил closure, pytest cache и parameterized JUnit; Windows subset дал `132 passed, 1 skipped`, полный Linux suite — `485 passed` без skip, локальная base-mode имитация — `121 passed` плюс `118` subtests. После push exact head `02bbe078…` новый `synchronize` run [34797814944](https://github.com/meownm/ragflow/actions/runs/34797814944) корректно остановился на новом blocker: event payload содержал предыдущий test merge `2f321e7d…`, хотя stable `R1/F/R2=21d15986…` уже имел exact parents текущих base/head. Follow-up `7b8bb6c21241453a99169fb61d4263abb72d51df` переводит payload в валидируемое подписанное advisory-наблюдение receipt v2; candidate по-прежнему выводится только из stable fetched object и exact parents. Focused Python 3.13.12 matrix дала `35 passed`, полный isolated Linux Python 3.13.11 quality-suite — `487 passed in 85.80 s`, без skip; GitHub post-integration PASS ещё обязателен. Исправление не интегрировано; deploy и repository settings не менялись. Полный контракт и доказательства находятся в разделе [«Нормативный контракт PR merge-ref resolver»](t3-architecture-policy-ru.md#нормативный-контракт-pr-merge-ref-resolver). Результат остаётся `IN_PROGRESS / NOT_ENABLED`; `CLASSIFIED`, `PLANNED`, `OBSERVED` и `NOT_EVALUATED` не становятся `PASS`.

**Действия.** Не создавать пустой baseline вместо недоказанного широкого verdict и сохранить единственный финальный job с устойчивым именем `architecture-policy`. Завершить provenance/review исправления fixture closure, вынести его в отдельный PR и не интегрировать без нового явного разрешения. После его появления в trusted `main` повторить на новом disposable PR весь gate, а не только уже доказанный resolver step: первое `opened`, заполненный payload, `ready_for_review`/`reopened`, одиночный и быстрые последовательные `synchronize`, controlled negative scenarios, fixture/analysis/final и check-run API binding должны быть терминально успешны или fail-closed согласно сценарию. Для personal-account repository organization-level Required Workflow недоступен, поэтому до изменения правил нужно явно выбрать один из доказуемых вариантов: перенести репозиторий в организацию и закрепить Required Workflow либо использовать repository ruleset вместе с проверенным base-owned `pull_request_target`/внешним GitHub App check. Обычный одноимённый status от GitHub Actions не принимается как эквивалент без негативной пробы против candidate duplicate/spoof. Требуется добавить независимого владельца control paths: единственный текущий collaborator и автор PR не может одобрить собственный PR. Gate запускается независимо от метки `ci`; path filters не должны оставлять required job навсегда pending.

**Приёмка.** Исторические негативные PR-пробы выполнены: запрещённая dependency и unclassified core path заблокированы, отсутствие analyzer и failed fixture не превратились в skip, а configured Python runtime code не получил доступа к supervisor-owned evidence/command files в проверенной OS boundary. PR #32 закрыл первичный empty-payload blocker, а последовательные локальные follow-up PR #33 закрыли fixture closure, cache и JUnit defects. Реальный `synchronize` run `34797814944` затем доказал, что immutable event payload может содержать предыдущий test merge при уже актуальном stable ref; этот `PAYLOAD_MISMATCH` exit 2 не засчитан. Follow-up `7b8bb6c21241453a99169fb61d4263abb72d51df` сохраняет payload только как advisory evidence и прошёл focused positive/negative matrix `35 passed` и полный isolated Linux quality-suite `487 passed`, без skip; GitHub post-integration run ещё отсутствует. После интеграции исправления DoD требует повторить primary `opened`, один и серию быстрых `synchronize`, fail-closed stale/changing/missing/wrong-parent cases, один SHA/base во всех job, полный fixture/analysis/final и check-run API binding к exact head/app. Затем нужно на реальном repository enforcement доказать, что candidate duplicate не удовлетворяет правило вместо trusted run, отмена analysis не разрешает merge, изменение/удаление candidate workflow не выключает проверку и отсутствие независимого control-owner approval блокирует merge. Merge-group HEAD отдельно проверяется только в topology с доступной merge queue. До всех этих проверок T3 не получает `VERIFIED`.

**Долг.** Новые нарушения запрещены. Старые устраняются у затронутой границы; при отсутствии безопасного исправления в данном изменении нужна предметная причина и срок по правилам исключений. Baseline не перегенерируется для принятия нового долга.

### T4. Пилот на business_documents

**Текущий результат.** Выполнен первый сквозной срез: правила ролей, активности операции и назначения владельца вынесены в чистые `business_documents.domain`/`business_documents.application`, Peewee-транзакция реализована узким adapter, а HTTP-вход вызывает один application-сценарий; прежний `BusinessDocumentService.assign_document` удалён. В том же кандидате экспорт отделён от конкретного storage backend и получил durable staging/cleanup ledger с lease fencing, проверкой объекта и восстановлением после неоднозначного сбоя. Текущий совмещённый document lane выполняет 251 тест с branch coverage 81,08% при неизменном пороге 79%; отдельные PostgreSQL/MinIO race и interruption contracts выполнены. Exact T4 architecture slice для domain/import, package wiring, cycles, HTTP registration и production adapters возвращает `PASS` без findings. Широкий Python observer остаётся `INCOMPLETE` из-за известных динамических путей, а DEAD-01 — только `OBSERVED` с нулём статических кандидатов; эти результаты не являются полным архитектурным или dead-code verdict.

T4 остаётся в работе: создание документа, lifecycle commands, AI/retrieval/EVA и остальные worker-сценарии ещё не перенесены в отдельные application boundaries. Их следует выделять следующими самостоятельными изменениями после фиксации и ревью текущего кандидата, не расширяя этот пилот и не объявляя завершённым весь этап.

**Границы.** Правила ревизий/состояний/ролей → domain; сценарии → application; Peewee, retrieval, LLM, экспорт и EVA → adapters; HTTP и worker → входы в один application.

**Порядок работ.** Сначала выявить side effects импортов и текущие транзакционные границы. Затем выделять один законченный сценарий за раз вместе с потребителями. Если размещение внутри `api/apps` запускает bootstrap, чистую собственную часть вынести в один корневой пакет, сохранив только транспортные входы в прежних местах. Не создавать новое общее ядро для всех функций RAGFlow.

**Приёмка.** Чистые слои импортируются без Quart app/DB/Redis/модели. Сценарий сохраняет проверку доступа, атомарность, conflict/retry/cancel и внешние envelopes. Старый бизнес-путь удалён. Обновлены package metadata, registry, coverage paths, тесты и worker wiring. Порог 79 для текущего совмещённого покрытия документов не снижается при переносе; новый путь включён в измерение вместо исключения.

**Возврат.** До выпуска можно отменить целиком конкретный PR при сохранении проверенного рабочего сценария. Если изменение включало данные, возврат кода допустим только по проверенному плану миграции, а не обычным revert вслепую.

### T5. Изолировать остальные собственные интеграции и удалить лишнее

**Действия.** Повторить пилотный подход для обнаруженных EVA, ASR, audit и других доработок. В стандартных routes/services/canvas/worker остаются необходимые подключения. Приоритет — места с частыми конфликтами upstream и широкими зависимостями.

**Приёмка.** Каждое обращение к ядру имеет адаптер или учтённую точку подключения. Подтверждённые собственные дубли, неиспользуемые exports, настройки и зависимости удалены вместе. Сохранены тесты поведения, динамические регистрации и данные. Неиспользуемый локально стандартный runtime не удалён под видом собственного dead code.

**Предел.** Не перестраивать `internal/entity`, `rag`, `agent`, все frontend pages или библиотеку компонентов. Стандартный код изменять только при доказанной необходимости.

### T6. Согласовать сборку и выполнить пробное upstream-обновление

**Действия.** Привести собственные install/CI/release-пути к основному поддерживаемому потоку выбранного upstream-релиза. Сверить lockfiles и версии инструментов. В отдельном worktree подготовить обновление по разделу 13 архитектурного документа.

**Приёмка.** Чистая установка воспроизводит сборку. На копии предыдущей собственной поставки пройдены миграции, полные применимые regression lanes, сохранение собственных данных и стандартных сценариев. Измерены конфликты и время адаптации; восстановление проверено. Наличие merge commit само по себе этап не завершает.

**Ограничение.** Пробное обновление не развёртывается в рабочую среду как побочный эффект проектирования или рефакторинга.

### T7. Поддерживать правила в обычной работе

**Действия.** Для каждого PR применять инструкции ниже; для каждого upstream-релиза пересматривать собственные исправления ядра. Полный CI-анализ запускать по принятому расписанию и перед выпуском. Дата следующего разбора долга задаётся конкретной записью, а не обещанием «потом».

**Приёмка.** Required checks реально обязательны, реестры соответствуют коду, просроченных исключений нет, проверки умеют обнаруживать нарушения. Само изменение policy/checker проходит его тесты и предметное ревью. Изменившееся требование обновляется в одном каноническом документе и связанных rule IDs.

## 3. Инструкция для разработчика и агента

### Перед изменением

1. Прочитать AGENTS.md и применимые локальные инструкции. Найти фактического владельца поведения и потребителей.
2. Проверить diff и классифицировать затрагиваемое: собственный модуль, стандартное ядро, интеграция, миграция, сборка. До T0 происхождение явно проверять по истории; не угадывать.
3. Описать наблюдаемый результат и применимые правила из каталога. Для core-diff назвать причину, почему нельзя решить задачу в собственном модуле/адаптере.
4. Выбрать минимальные проверки по области изменения. Если затрагивается shared boundary, добавить тесты её потребителей. Зафиксировать важный исходный сбой до правки.

### При изменении

1. Реализовать один путь поведения у владельца. Domain/application не получают конкретные клиенты ядра, ORM и request/session.
2. Не форматировать соседние стандартные файлы без необходимости, не переносить их ради единообразия и не подменять shared lockfile произвольным новым install-потоком.
3. Сразу переводить потребителей, удалять освобождённые собственные helpers/регистрации. Перед удалением проверить strings DSL, assets, worker names, внешние API и поддерживаемые данные.
4. При изменении policy/baseline не добавлять wildcard-исключение для своей ошибки. Подтвердить допустимость конкретной записи и проверить негативные фикстуры инструмента.

### Перед завершением

1. Повторно проверить достижимость изменённых symbols и связанных helpers; разобрать значимый рост сложности. Выполнить доказанную очистку в текущем объёме задачи и затем запустить применимые проверки. Для отсутствующего окружения указать `INCOMPLETE`, а не `PASS`.
2. Проверить diff: непредусмотренные стандартные изменения, оставшиеся копии, комментарии о старом пути, dependency drift.
3. Обновить реестры после их внедрения, тестовые пути и документацию; до этого записать те же сведения в описание изменения.
4. Представить результат по шаблону PR: поведение, происхождение изменения, применимые правила, команды/результаты и незакрытые ограничения. Не утверждать готовность к выпуску по одному unit-тесту.

## 4. Инструкция для ревьюера

Проверять в следующем порядке:

1. Решён ли пользовательский сценарий без потери функций и данных?
2. Изменяется ли правильный владелец? Обосновано ли каждое изменение ядра?
3. Не обходит ли адаптер права/tenant scope или транзакционные правила RAGFlow?
4. Не добавлены ли новые циклы, публичные поверхности и повторные бизнес-реализации?
5. Удалены ли ненужные собственные части вместе с регистрацией, assets и зависимостями?
6. Соответствуют ли проверки риску? Проверены ли ошибки, конфликт версии, динамическая загрузка и миграции там, где они затронуты?
7. Не ослаблены ли baseline, coverage scope или runner conditions ради зелёного статуса?

Результат замечания содержит rule ID, конкретное место, последствие и необходимое исправление. Размер файла и субъективный стиль сами по себе не являются причиной блокировки архитектурного PR.

## 5. Инструкция для обновления и выпуска

Использовать [процедуру upstream-обновления](architecture-and-code-quality-ru.md#13-процедура-обновления-из-upstream). Начинать с полного собственного снимка; untracked файлы и текущие доработки не исчезают из объёма проверки из-за нового worktree.

Кандидат выпуска содержит собственный SHA, upstream SHA, перечень изменений интеграций, версии миграций и ссылки на доказательства. Обязательные gates `PASS` либо обоснованно неприменимы по проверенному selector; для обязательного gate `FAIL`/`INCOMPLETE` не считается готовностью.

При изменении схемы выполнить восстановление копии предыдущего выпуска, миграцию и сверку собственных данных. Откат образа не заменяет восстановление данных. После проверки кандидата изменение рабочей поставки выполняется отдельным согласованным release-потоком.

## 6. Запись завершения этапа

Для каждого этапа в PR или сохранённом отчёте заполнить:

```text
Этап: T0..T7
Статус: PLANNED / IN_PROGRESS / VERIFIED / INCOMPLETE
Собственный commit и upstream commit:
Владелец и ревьюер:
Изменённые модули и интеграции:
Применимые rule IDs:
Команды, окружение, результаты и ссылки на отчёты:
Проверенные негативные случаи:
Что удалено:
Незакрытые условия и следующий конкретный шаг:
```

`VERIFIED` требует всех критериев соответствующего этапа. Проект документа, существование YAML или отсутствие замечаний без проверки не заменяют доказательства. Список этапов не следует помечать завершённым автоматически после создания инструкций.
