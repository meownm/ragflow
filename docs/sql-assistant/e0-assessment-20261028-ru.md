# E0 SQL-ассистента по PR №50

Дата обследования: 2026-10-28 (Europe/Moscow). Владелец обследования и отчёта — основной агент текущей задачи. Статус: обследование кода и focused-проверки завершены; G0 остаётся открытым по перечисленным ниже решениям и пробелам. Это не приёмка реализации v0.5.

Источник требований: [PR №50](https://github.com/meownm/ragflow/pull/50), head `abdee02d777cf80cb328aed53dd650d67f6eba02`, [README](https://github.com/meownm/ragflow/blob/abdee02d777cf80cb328aed53dd650d67f6eba02/docs/sql-assistant/README.md) и [требования v0.5](https://github.com/meownm/ragflow/blob/abdee02d777cf80cb328aed53dd650d67f6eba02/docs/sql-assistant/requirements-v0.5-ru.md). Локальная база — `main`, `ab6f860d40617637280bad5ea2345a088426db53`, с существующими tracked/untracked изменениями, включая SQL Assistant. Переключение ветки, слияние PR, реализация E1 и публикация в эту задачу не входят.

## Сценарий и ожидаемые результаты E0

Аналитик данных хочет получить проверяемый расчёт из существующего приложения. Пользователь выбрал пилот **PostgreSQL `bot` / логи LLM** в текущем чате. Это выбор домена; метрика, таблица, её поля и политика полноты требуют отдельного подтверждения по фактическому каталогу.

1. Найти реальные UI/API/worker/каталог/executor и их потребителей; доказательство — исходники и регистрации.
2. Сопоставить все AC-01–49, BR/SR/ALG/INT с существующими механизмами; различать наличие кода, focused-проверку и сквозную приёмку.
3. Зафиксировать ограничения доступа, ошибки, повтор команд, отмену/UNKNOWN, версии и полноту; доказательство — исходники и применимые детерминированные тесты без промышленного SQL.
4. Предложить минимальный вертикальный E1, его отрицательные случаи и проверки, перечислить блокирующие решения без выдуманного согласования.

Затронутые потребители обследования: `/sql-assistant`, `/sql-projects`, OpenMetadata Copilot, schema/retrieval, SQL worker, чтение истории/экспорт, группы доступа. Исходники приложения не редактируются. Визуальная приёмка Figma, live LLM/SQL, нагрузка и deployment не выполняются.

## Вывод обследования

Продолжать существующий SQL runtime. Новый frontend, второй каталог и отдельный executor не требуются. В dirty checkout уже есть `/sql-assistant`, черновики, серверное представление, локальные версии согласованных определений и интеграция с SQL projects. Эта реализация заметно шире технического E1 по составу экранов, но не закрывает защитные и эксплуатационные условия G1.

Ближайший вертикальный срез: **одна утверждённая метрика числа записей LLM по модели и явному периоду на PostgreSQL bot**, без JOIN и свободного SQL, через существующие команды/компилятор/worker. Сначала нужны исполняемые права пилота, политика полноты, журнал отправки/UNKNOWN и корректная гонка завершения с отменой. Состав среза — предложение; формула ещё не утверждена владельцем данных.

## Исходный код и установленная система

До обследования в checkout находились 49 изменённых tracked-файлов, а также untracked SQL Assistant и тесты. Изменения охватывают также администрирование и другие документы; они не принадлежат E0 и не исправлялись. Общие SQL/OMD-модули — существующие локальные расширения fork (история включает `206750b0b`, `96b4b0cc9`, `5f4931e6f`); новые `sql_assistant`-модули ещё untracked. Обычная upstream-структура сохранена. Изменение E0 добавляет только этот отчёт.

Точная идентичность обследованной области: SHA-256 `0159bebd4f7ef3cc35af705ae3c2b4fd1d586ae022e939a41886f90abe6b5512`, 2561 файл. Это hash отсортированного JSON `{путь: SHA-256 байтов}` для перечисленных в `source-before.json` каталогов/расширений, **не** полный build source-id. Включены API, домен, common/rag, SQL-промпты, unit/eval/browser-тесты и frontend; отдельные конфигурации окружения и секреты в него не входят. После тестов эта область побайтно не изменилась.

Доказательства: [checks.json](../../.local/archives/pr50-e0-20261028/checks.json), [исходный снимок идентичности](../../.local/archives/pr50-e0-20261028/source-before.json), [повторная идентичность](../../.local/archives/pr50-e0-20261028/source-after.json), [версии среды](../../.local/archives/pr50-e0-20261028/versions.json). В JSON записаны точные argv проверок и exit codes; содержимого секретов/строк bot там нет.

**Параллельные изменения после первого прогона.** После стабильных 435 passes другой процесс изменил SQL Assistant/service, worker/agents, типы и UI диалога, несколько тестов; появились `sql_result_answer.py`, `sql_assistant/answer.py`, `result-dialogue.tsx` и их тесты. Они не принадлежат E0. Изменения просмотрены дополнительно (S15 ниже), но единого стабильного финального checkout для всей матрицы в этой задаче не было: повторные fingerprints фиксируют дрейф. Поэтому первый hash и 435 passes относятся к исходному снимку; их нельзя приписывать всем последующим байтам. Отдельные результаты дополнительных проверок и их исходные/конечные manifests сохранены ниже. Перед E1 снова зафиксировать source identity и проверить объединённый diff.

| Компонент | Что проверено 2026-10-28 | Граница доказательства |
| --- | --- | --- |
| Checkout | `main`, HEAD `ab6f860d40617637280bad5ea2345a088426db53`; PR head `abdee02d777cf80cb328aed53dd650d67f6eba02` | PR прочитан через connector; ветка не переключалась, документы PR не выдаются за merged |
| Host Python/драйвер/парсер | Python 3.13.12, psycopg2-binary 2.9.11, sqlglot 28.4.0, Peewee 3.19.0, Quart 0.20.0, pytest 9.0.2 | `importlib.metadata`, подготовленная `.venv` |
| Проверенный green runtime | `/ragflow/VERSION` = `2.0.1-dev.ca694a43368f`; image source-id `ca694a43368f36fc492d5528be4c6af54dd63e18ca362a14f6289a1bad63f3dc`; label `feature-build-only` | В контейнере `ragflow-local-web-green` отсутствуют `api/apps/sql_assistant/service.py` и `definitions.py`. Проверенный контейнер не представляет dirty checkout; активность маршрутизации gateway отдельно не подтверждалась |
| PostgreSQL bot | PostgreSQL 16.14; системные метаданные `public.llm_requests_log` | Чтение через psql внутри локального контейнера `postgres`, транзакции READ ONLY. Это bootstrap-доступ для инвентаризации, не доказательство application DB-role или ACL пользователя |
| OpenMetadata | API `/api/v1/system/version`: 1.12.10, revision `3bc20e698abf222742908c2aa5d0eaa736e7cfbd` | Read-only GET с существующим service credential внутри runtime; секрет не выводился. Документ v0.5 ссылается также на API 2.0.x — совместимость нельзя предполагать |
| OMD-адаптер | UUID/FQN, текущая entity version и `dataTypeDisplay` получены; локальный код имеет exact-FQN lookup | В установленном адаптере метод `get_table_by_name` отсутствует; для паспорта использован GET существующего клиента. Это дополнительное свидетельство отставания runtime |
| Greenplum | В текущем execution registry единственный диалект `postgres`; executor — psycopg2 | Версия, coordinator, лимиты, отмена и корреляция Greenplum не проверены. PostgreSQL-совместимость не считается приёмкой Greenplum; выбранный пользователем первый срез от него не зависит |
| Локальная LLM/retrieval | Используется общий tenant `RAGFlowLLMAdapter`, версии промптов и схемы ответа | Текущие model digest, провайдер, context window, embedding/reranker и поколение индекса в этой задаче не подтверждены. Inference не запускался; будущие model checks — только qwen3.8 |

## Паспорт выбранного домена

Пользователь выбрал PostgreSQL bot / логи LLM. Подтверждена каталоговая сущность `docker_postgres_bot.bot.public.llm_requests_log`, UUID `491922b2-66ca-4c3d-9c7d-651a68d491b9`, OMD version `0.6`, `updatedAt=1793110769706`, `deleted=false`, одна ссылка владельца. Наличие ссылки владельца не является согласованием метрики.

| Поле | Физическая схема / OMD dataTypeDisplay | Значение для E1 |
| --- | --- | --- |
| `log_id` | bigint, NOT NULL, PRIMARY KEY / bigint | Кандидат для `COUNT(log_id)`; не путать запись журнала с уникальным бизнес-запросом |
| `request_timestamp_start` | timestamp with time zone, NOT NULL / тот же тип | Кандидат события времени; нужны явные `[from, to)` и timezone |
| `model_name` | character varying(100), nullable / тот же тип | Разрез; NULL сохраняется отдельной группой, без выдуманного имени модели |
| `duration_seconds` | double precision, nullable / тот же тип | AVG и точность floating-point — следующий срез после согласования совокупности/NULL; не включать автоматически в минимальный E1 |
| `is_success` | boolean, NOT NULL | Нужен выбор владельца: все попытки или только успешные. Предложение E1 — все записи, с явной подписью |
| `request_timestamp_end` | timestamp with time zone, nullable | Не использовать вместо начала запроса без изменения определения |
| Остальные поля | prompt/raw_response/error_message; tokens; tenant/doc/chunk/request/conversation IDs | Не включать в разрешённый контекст и выход первого среза; права определяются отдельно |

В `pg_class` у исходной таблицы `relrowsecurity=false`, `relforcerowsecurity=false`; в `pg_policies` для неё 0 записей. Это не доказывает обход приложения: могут существовать ограниченные роли/представления, но их фактическое связывание с пилотом пока не проверено. При разных правах строк/полей нельзя полагаться на service credential и видимость таблицы в OMD.

Точные метаданные читались следующими запросами внутри `BEGIN READ ONLY`/`COMMIT`: `current_setting('server_version'), current_database()`; `information_schema.columns` с фильтрами `table_schema='public' AND table_name='llm_requests_log'`; `information_schema.table_constraints` + `key_column_usage` для этой таблицы; `pg_class` + `pg_namespace` для флагов RLS; `pg_policies` для числа политик. OMD: GET `/api/v1/tables/name/docker_postgres_bot.bot.public.llm_requests_log?fields=owners,domains,columns,tableConstraints` и отдельное чтение `dataTypeDisplay`. SQL над строками bot, sample data, prompts и ответы моделей не читались.

Существующий live-тест [test_sql_assistant_live.py](../../test/playwright/e2e/test_sql_assistant_live.py) использует **`public.search_queries`**, `created_at` и `COUNT(id)`, а не `llm_requests_log`. Его код — полезный рецепт disposable-проверки, но ни наличие теста, ни его прежний успех не принимают новый домен. В рамках E0 этот тест не запускался.

## Карта реального исполнения и границ модулей

| ID | Владелец поведения и источник | Что уже есть |
| --- | --- | --- |
| S1 | [HTTP](../../api/apps/restful_apis/sql_assistant_api.py), [routes](../../web/src/routes.tsx), [access groups](../../api/db/services/access_group_service.py), [navigation](../../api/db/services/navigation_visibility_service.py) | Login, раздел `sql_assistant`, запрет по группе, отдельные команды сессии. HTTP получает actor из авторизации, не из JSON |
| S2 | [SqlAssistantService](../../api/apps/sql_assistant/service.py), [SqlRuntime](../../api/apps/sql_assistant/runtime.py) | Create/list/get, атомарный CAS session revision, request hash/idempotency, selected result, draft/view; переиспользование Projects/Revisions/Runs |
| S3 | [domain compiler](../../business_documents/sql_query/query_specification.py), [project compilation](../../business_documents/sql_query/project_compilation.py), [assistant query](../../business_documents/sql_assistant_query.py) | Структурированный расчёт, типизированные параметры, фильтры WHERE/HAVING и И/ИЛИ; AST guard, allowlist таблиц/функций; ручной SQL отдельно |
| S4 | [domain definitions](../../business_documents/sql_assistant_definitions.py), [definition adapter](../../api/apps/sql_assistant/definitions.py) | Локальный managed реестр, immutable version/hash, CAS публикации, approved grammar; единый admission для API/worker, явный EXPLORATORY |
| S5 | [runs](../../api/apps/business_documents/sql_query_runs.py), [postgres](../../api/apps/business_documents/sql_query_postgres.py), [worker](../../api/apps/business_documents/worker.py) | Durable job/run и replay, max_attempts=1 для SQL, EXPLAIN без ANALYZE, readonly transaction, timeout, bound rows/bytes, cancel watcher, lease fencing |
| S6 | [execution registry](../../api/apps/business_documents/sql_execution_registry.py), [его domain rules](../../business_documents/sql_query/execution_registry.py), [authorization](../../api/apps/business_documents/authorization.py) | Managed connector/profile, mapping service/database/schema, profile version/fingerprint, allowlist схем, роль приложения и повторная проверка перед SQL |
| S7 | [result access](../../api/apps/business_documents/sql_query_access.py), [exports](../../api/apps/business_documents/sql_query_exports.py), [retention](../../api/apps/business_documents/sql_query_retention.py) | Immutable source chain, tenant/owner/catalog recheck перед rows/export, retention 30 суток без продления чтением |
| S8 | [schema resolver](../../api/apps/business_documents/sql_query_schema.py), [schema domain](../../business_documents/sql_query/schema_resolution.py), [query graph](../../business_documents/sql_query/query_graph.py) | Текущие UUID/FQN/версии/типы OMD, semantic lookup, дозагрузка и bounded relationship paths; типы lineage/FK/SQL различаются |
| S9 | [OMD Copilot](../../api/apps/services/openmetadata_copilot_service.py), [dataset retrieval](../../api/apps/services/openmetadata_dataset_retrieval.py), [runtime provider](../../api/apps/services/openmetadata_runtime_service.py) | Private Dataset ACL, domain scope, проверка актуальной сущности, stale hits отсеиваются; OMD fallback, каталог/lineage/quality |
| S10 | [connector](../../common/data_source/openmetadata_connector.py), [sync](../../rag/svr/sync_data_source.py) | UUID, fingerprint, инкрементальная загрузка/prune, метаданные и источники; существующий upstream sync служит местом небольшой интеграции |
| S11 | [assistant domain](../../business_documents/sql_assistant.py), [result UI](../../web/src/pages/sql-assistant/result.tsx) | output IDs по выражениям, таблица/view, фильтр/сортировка всего сохранённого набора, Decimal comparison/MAD; один x/y график страницы |
| S12 | [workspace](../../web/src/pages/sql-assistant/workspace.tsx), [builder](../../web/src/pages/sql-assistant/builder.tsx), [catalog](../../web/src/pages/sql-assistant/catalog.tsx), [hooks](../../web/src/hooks/use-sql-assistant-request.ts) | Три поверхности, история/черновик/definition UI; запросы через services, query keys с actor, явные команды без mutation auto-retry |
| S13 | [planner](../../api/apps/business_documents/sql_query_planner.py), [agent worker](../../api/apps/business_documents/sql_query_agent_worker.py), [LLM adapter](../../business_documents/adapters/llm.py) | Общая tenant-модель, bounded шаги, структурированные catalog IDs, промпты/provenance; не сквозной бюджет исследования |
| S14 | [models](../../api/db/db_models.py) | SQL project/artifact/run/job и новые session/command/definition/version; используется существующая ORM/БД |
| S15 | [ответ по результату](../../business_documents/sql_result_answer.py), [LLM adapter ответа](../../api/apps/sql_assistant/answer.py), [ResultDialogue](../../web/src/pages/sql-assistant/result-dialogue.tsx), обновлённый S2 | Добавлено параллельно при финализации E0: `answer_result` отдельно от `follow_up`, ограниченный saved-result контекст, cell references и повторная проверка доступа/контекста после inference. Это не новый SQL executor и не доказательство качества ответа |

Домен `business_documents/sql_assistant*.py` и `business_documents/sql_query` не импортирует ORM/HTTP/bootstrap; их `__init__.py` содержат только документацию/константы. Инфраструктура находится в `api/apps/...`. При этом импорт пакета `api.apps` инициализирует settings, Quart, session, observability и маршруты: его нельзя называть изолированным domain API. Focused-тесты используют контролируемые окружения; их результат не доказывает отсутствие bootstrap side effects у произвольного production import.

Есть одна граница для упрощения при будущем E1: общий `sql_query_runs.py:23` импортирует `api.apps.sql_assistant.definitions.require_definition_admission`. Сейчас это общий gate, не второй executor, но базовый runtime зависит от конкретной новой поверхности и её таблиц. Если переносится admission/state machine, логичное место общих правил — существующий SQL domain/runtime; тонкие адаптеры сессии/ORM остаются у владельцев. Не вводить plugin framework или pass-through слой только ради переименования. В E0 переносов и удалений нет.

## Матрица AC → код → доказательство → пробел → инкремент

Обозначения доказательств: **D=108** domain tests, **R=217** service/runtime tests, **C=110** catalog tests (точные команды ниже). **Код** означает source inspection, не live-приёмку. Даже строка с успешным тестом остаётся частичным доказательством AC, если для неё требуется реальный adapter/UI/числовой эталон.

| AC / связанные требования | Существующий модуль | Доказательство сейчас | Пробел v0.5 | Следующий инкремент |
| --- | --- | --- | --- | --- |
| 01; BR-01, SR-12 | S2/S3/S5/S12/S13/S15 | D/R, код; последующий S15 частично проверен отдельно | Нет независимого результата для выбранного LLM-log домена и текущего UI/API/worker | E1 одна метрика через API; диалог E2 |
| 02; BR-02, SR-04–06 | S8/S13, proposals/revisions | Код требований/уточнений | Нет свежей приёмки одноимённых метрик и нескольких изменений одним сообщением | E2 |
| 03; BR-03, SR-07 | S8/S9/S12 | C, код graph/catalog | Полный разрешённый бизнес-путь и зерно пилота не утверждены; graph не утверждает JOIN | E2, без JOIN в первом E1 |
| 04; BR-04, SR-15 | S2 follow_up / S7 | R, привязка к run/row/dimension | Типизированное значение превращается в текст задания модели; наследование семантики долей/distinct не доказано end-to-end | E2 |
| 05; BR-05 | S11 analyze_series | D/R | Только bounded median/MAD; нет Investigation, сезонности, плана и общего бюджета | E5 |
| 06; BR-06/11, SR-13/21 | S5/S7/S14 | R, immutable chain/replay | QuerySpec/Plan mapping есть через artifacts, но нет полного versioned evidence/capability/access admission контракта v0.5 | E1 |
| 07; BR-07, SR-02/19 | S1/S6/S7/S9 | R/C, tenant/owner/catalog revoke | Нет проверенного пользовательского row/field delegation, метаданных по полям и отзыва ограничений строк; fixed connector role | E1 ограниченная группа/представление, затем расширение |
| 08; BR-08, SR-14/16 | S5/S11/S12 | D/R | Нет UNKNOWN; timeout/failure и потеря подтверждения не разделены. UI не проверен | E1 |
| 09; BR-09, SR-03/23 | S8/S9/S10 | C, stale hits/current entities | Неизменяемого поколения и полноты scan перед prune нет | E1 для ALG-00; первый API-срез может использовать exact OMD fallback |
| 10; BR-10, SR-17/22 | S2/S5 | R, ledger/CAS/cancel fencing | Нет persistent dispatch/DB correlation; гонка отмены и завершения расходится с v0.5 | E1 |
| 11; BR-12, SR-20 | S9 GovernanceAgent | C, preview/version check | Нет FeedbackProposal/согласующего; GET перед PATCH не атомарный conditional write | E6, до проверки API только предложение/штатный процесс |
| 12; SR-08–11 | S3/S5/S6 | D/R, AST allowlist/preflight | Реальные grants/функции/стоимость пилотного DB-role не проверены; uniqueness из OMD не доказывает физическое ограничение Greenplum | E1, без JOIN |
| 13; BR-13, SR-05/09/18 | S3/S4 | D/R, formula drift/manual/retirement tests | Нет бизнес-утверждения выбранной метрики; ограниченная грамматика не покрывает произвольные отношения | E1 |
| 14; BR-15, SR-01/13 | S2/S12 | R: stale tab и late run не меняют выбор | Нет отдельного Turn и общей приёмки multi-tab на реальной БД/HTTP | E1 контракт, E2 UI |
| 15; SR-23, INT-08 | S10 | C проверяет connector, не generations | Stable UUID-derived document_id обновляется через sync; нет immutable docs/profile/IndexManifest/CAS active_generation | Отдельный подынкремент E1, не закрывать по fallback |
| 16; SR-17/21/22, INT-05/06 | S5/S14 | R, max_attempts=1 и lease | Нет PREPARED/DISPATCHING/UNKNOWN, DB query/attempt correlation и сверки; exhausted lease → FAILED | Первый E1 |
| 17; BR-09, SR-14/25 | S2/S11 | R: UNKNOWN явно возвращается | Полнота захардкожена UNKNOWN; нет evidence по источнику, периоду, watermark и политики warn/block | Первый E1 |
| 18; SR-02/19, INT-02 | S8/S9 | C: domain ACL и stale filtering | Dataset search не ограничен предварительным document-ID intersection; только последующая фильтрация. Field ACL/empty-intersection capability отсутствует | E1, точный OMD маршрут до capability test |
| 19; BR-14, SR-25 | S11 compare_values | D/R: Decimal/zero base | Сравнивает две колонки готового набора, а не гарантированно две совместимые версии/периоды/срезы | E2 |
| 20; SR-07/24 | S8 query_graph.relationship_paths | Код, bounded max_hops/graph | Не подтверждены approved semantic paths, права всех bridge tables и корректное уточнение альтернатив | E2, не включать JOIN в минимум |
| 21; SR-26 | SQL goldens, S13 provenance | Код существующего корпуса; D/R/C | Независимый holdout выбранного домена, пороги и model digest не зафиксированы; старые успехи не переиспользованы как приёмка | E1 эталоны; E4 выпуск |
| 22; SR-25 | S13 и job timeout | Код bounded шагов | Нет сохраняемого task-wide processing/sql/repair budget и TTL WAITING_USER с паузой | E1 контракты/бюджет; E2 диалог |
| 23/24; BR-17, SR-27/30 | S2/S11 | D/R: no new job, тот же SQL/run/rows | Browser v0.5 не запускался; перенос IDs ограничен формой SQL | E3 |
| 25; BR-16/18, SR-27/32 | S2/S3/S8 | D/R: save/apply создают artifact | Нет полного field ACL/semantic fan-out gate; новое поле не гарантирует согласованную метрику | E3 на общем E1 gate |
| 26; BR-18, SR-29 | S3/S12 | Код compile/grouping | Нет числовой сквозной приёмки изменения зерна и неаддитивных метрик | E3 |
| 27; BR-17/18 | S2/S11 transfer_view | D/R | Скрытие сохраняется по derived output ID; его устойчивость при сложной смене SQL/aliases не общий контракт | E3 |
| 28; SR-28, BR-16 | S3 assistant query/_tree | D: WHERE/HAVING, типы, NULL/trees | Грамматика ограничена; нет полной UI/live DB проверки всех операторов и часовых поясов пилота | E3; нужный минимум времени в E1 |
| 29; SR-11/14 | S2 present_rows/S11 | D/R: фильтр всего saved result до pagination | Нет глобальной операции над всей БД без нового расчёта; UI честно ограничен сохранённым набором | E3 |
| 30; BR-06, SR-21 | S2 result/S7 history/export | R, immutable source chain | Нет новой browser-проверки SQL выбранного result после drill-down | E2/E3 |
| 31/32; BR-19, SR-29/31 | S2 draft/S12 surfaces | R: discard/apply/history | Нет общей контрактной модели QuerySpec/ViewSpec/QueryDraft v0.5, UI не принимался | E3 |
| 33; SR-18/31 | S3/S4/S12 | D/R: explicit exploration, no inherited approval | Поддерживаемая manual grammar уже ограничена; произвольный SQL не преобразуется обратно | E3, ограничения сохранить |
| 34; SR-01/22 | S2 command/session CAS | R: revision conflict, idempotency | Возвращается код/сообщение, но не актуальное состояние и safe diff в error envelope | E1 контракт, E3 UI |
| 35; BR-08/10 | S5/S12 | R: существующие cancel/error states | UNKNOWN и достоверное подтверждение отмены отсутствуют; cancellation wins даже после готового output | Первый E1 |
| 36; BR-15 | S2 select_result | R: completion не меняет selected_result | Нет persisted active_turn/active_spec callback contract; систематическое ручное выделение результата проще целевого auto-select | E2 |
| 37; SR-14 | S2/S4/S11 | R, отдельные definition/freshness/output flags | source UNKNOWN без политики; статус исполнения READY и FULL/LIMITED требуют явного mapping в v0.5 | E1 |
| 38; SR-32 | S4/S5/S7 | R: catalog/schema/definition/role recheck | Отзыв row/column policy не инвалидирует сохранённые результаты по policy version | Первый E1 access gate |
| 39; BR-20 | S3 select aggregates | D, SUM/AVG/COUNT etc | Независимые числовые эталоны AVG(duration)+COUNT для llm_requests_log не созданы; COUNT(*) не следует считать поддержанным при текущем запрете Star | После минимального COUNT E1, полнота E3 |
| 40/42; BR-22 | S11 view/chart | Код: один x/y, column IDs | Нет коллекции series, independent visibility/order/format/color, axis_id | E3 |
| 41; BR-20/21 | S2 draft/apply/S12 | D/R new artifact | Нет полного multi-series preview/предыдущий graph контракта | E3 |
| 43; BR-23 | S11 result UI/chart | Код одного YAxis | Нет нескольких осей с unit compatibility и legend mapping | E3 |
| 44; BR-22 | S11 view.chart | D/R view no SQL, код UI | Таблица и chart одновременно; независимые series/view settings не покрыты v0.5 | E3 |
| 45; BR-24 | S2 session list | Код хранит исследования | Нет отдельного SavedQuestion с title/spec/params/ViewSpec/visibility/revision; история сессий его не заменяет | E3 |
| 46/47; BR-25, ALG-11 | S12 catalog/index | Код `onUse` вставляет FQN в строку вопроса | Нет typed ContextRef, @ поиска по типам и удаления ссылки, версии и повторного допуска контекста | E3 |
| 48; BR-16/20, SR-07/08 | S3/S4/S8 | D: joins/unique right key в agreed grammar | Нет произвольных вычисляемых выражений/отношений; catalog unique — ограниченное доказательство; approvals/bridge paths не приняты | E3; новый JOIN только после E1 gate |
| 49; BR-24 | S2 session CAS | R для session, не SavedQuestion | Нет concurrent SavedQuestion save/diff и собственного контракта сохранения | E3 |

## Контракты и алгоритмы: что переиспользовать

| Требования | Существующее соответствие | Остаток и решение E0 |
| --- | --- | --- |
| ALG-00, INT-02/08 | S9/S10 private Dataset, UUID/fingerprint, stale hit resolution | Нужны immutable documents, index profile, manifest readiness/CAS и exact ID restriction. Для первого API-среза разрешён существующий exact OMD lookup без semantic retrieval; это не закрывает ALG-00/AC-15 |
| ALG-01, INT-01/03/04 | S2/S8/S13 requirements/schema/query proposals, expected revisions | Turn/task budget, semantic clarifications и typed context формализовать в существующей сессии; доступ до LLM проверить на разрешённых полях |
| ALG-02, INT-05/06 | S3/S4/S5 structured compiler, AST, EXPLAIN | Зафиксировать compiler/capability/access policy versions и admission expiry, доказываемую семантику метрики; не переименовывать artifacts без пользы |
| ALG-03 | S2/S5/S6/S7 job/run/command/profile/result | Отправка с устойчивым состоянием/корреляцией; UNKNOWN/reconcile, доступ на выдаче по policy, source completeness, бюджеты |
| ALG-04 | S8/S9 catalog, typed edges | Права поля/актива, approved path и актуальные evidence; не считать lineage утверждённым JOIN |
| ALG-05/08 | S2 follow_up + immutable result; S11 compare | Наследование условий структурированно, независимый пересчёт метрик/периодов и ограничения сравнимости |
| ALG-06, INT-07 | S11 bounded comparison/MAD; старый runtime также имеет Python/lookup | Для нового assistant P1 нужен разрешённый детерминированный analysis contract; не открывать выполнение произвольного Python из старого API |
| ALG-07 | S9 preview/confirm | Требуется proposal/owner/atomic write capability. В OMD 1.12.10 безопасный conditional PATCH не доказан |
| ALG-09/10 | S2/S3/S11/S12 draft/apply/view | Общая модель output IDs, несколько рядов/осей, SavedQuestion и safe diff |
| ALG-11 | S12 FQN → text | Typed ContextRef/search/revoke пока отсутствует |

Модель `QuerySpec → QueryPlan → QueryRun → Result → ViewSpec` частично представлена immutable QUERY/COMPILATION artifacts, Run, session state/views; это полезное переиспользование. Пробел — наблюдаемые контракты, версии, отдельные обязанности и необходимые состояния, а не отсутствие классов с точно такими именами. Не создавать вторую иерархию объектов ради совпадения терминов.

Локальный реестр S4 уже существует. В E0 не установлено, что модель OpenMetadata 1.12.10 не способна хранить нужное определение/версию: перед расширением реестра нужно оценить существующие metric/custom-property возможности и определить владельца источника истины. Не удалять нынешние версии и не создавать второй независимый каталог; решение зафиксировать до промышленной публикации метрик.

## Пробелы, определяющие E1

1. **Неизвестный исход исполнения — блокер G1.** В `sql_query_runs.py:437` QUEUED/RUNNING переводится в RUNNING до физической отправки. В модели Run (`db_models.py:1694`) нет dispatch/attempt/db-correlation полей. `max_attempts=1` предотвращает штатную повторную job-попытку, но после потери ACK/lease состояние БД не сверяется: `worker.py:247–282` вызывает fail_job; `sql_query_runs.py:529` записывает FAILED. READ ONLY не устраняет нагрузку повторного SELECT. Нужны устойчивые состояния до отправки, единственный владелец отправки, наблюдение UNKNOWN и отсутствие resend; отсутствие запроса в active list не доказывает неисполнение.
2. **Гонка завершения/отмены — блокер G1.** `complete_job` (`sql_query_runs.py:498`) уничтожает готовый результат, если уже записан CANCEL_REQUESTED. Текущий `test_cancellation_wins_over_late_valid_result` прямо принимает PURGED/CANCELED после получения валидного output. По v0.5 завершившийся в БД раньше запрос должен сохранить SUCCEEDED, а неподтверждённая остановка — UNKNOWN. Это расхождение существующего контракта, не регрессия E0; менять правило и тест следует вместе с новым executor lifecycle и сохранением права отдельно удалить результат.
3. **Полнота источника — блокер G1.** Методы `get`, `result`, `_apply(compare/analyze)` в `SqlAssistantService` возвращают UNKNOWN без доменной политики; добавленный S15 также сохраняет UNKNOWN. Успешный SQL, end timestamp записи и OMD updatedAt не являются watermark всей загрузки за период. Нужно правило warning/block, scope и возраст свидетельства; до свидетельства честно UNKNOWN, без выдуманных нулей.
4. **Права данных/контекста — блокер G1.** Повторные tenant/owner/table-domain проверки уже есть. S6 использует username из connector, а не AccessContext исполняемой роли пользователя; S7 перепроверяет каталог, но не версию row/column policy. AST guard проверяет allowlist таблиц/функций, а не произвольное соответствие полевых прав. Для узкого пилота допустима согласованная однородная группа с реальным ограниченным DB-role и разрешённым представлением. Raw prompt/response и tenant IDs не должны попадать в модель/выдачу первого среза. Обычный SELECT grant на всю raw-таблицу такого ограничения не обеспечивает.
5. **RAG snapshot и pre-retrieval ACL — отдельный обязательный подынкремент E1.** `openmetadata_dataset_retrieval.py:47` вызывает Dataset search без document-ID allowlist; найденные сущности фильтруются позже в DatasetRetrievalAgent. В `sync_data_source.py:250` ID зависит от kb/connector/entity, не от entity version/content/index profile. Нет атомарного манифеста. Exact-FQN OMD позволяет начать первый срез без этого маршрута, но G1/AC-15 нельзя объявить закрытыми.
6. **Утверждение семантики/бюджеты/контракты — открытый G0 и G1.** Администратор технически может публиковать definition; это не назначение владельца метрики. Политики пилота, определение метрики и holdout пока не утверждены; нет общего persisted budget с паузой WAITING_USER. Новые HTTP-ошибки возвращают error_code/message без полного stage/trace/retryable/safe-diff конверта v0.5. Нужен явный mapping текущих revision/READY/FULL/LIMITED на целевые понятия, совместимый со старым `/sql-projects`.

## Минимальный вертикальный E1

Рабочее название: **«Число записей LLM по модели за указанный период»**, первая API-поставка внутри E1, а не весь G1.

Предлагаемое определение для решения владельца данных:

- Источник — выбранная витрина логов bot с точной OMD identity/version и реальной DB-role. Использовать существующую разрешённую витрину; если её нет, DBA подготавливает минимальное представление над `public.llm_requests_log` с нужными полями/строками и регистрирует его в том же OMD. Создание view/GRANT в E0 не выполнялось.
- Одна запись `log_id` — одна зарегистрированная попытка, включая неуспешную. Метрика `COUNT(log_id)`, а не уникальные `request_id` и не число успешных ответов. Отбор по `request_timestamp_start >= :from AND < :to`; from/to — абсолютные timestamptz со смещением, timezone предлагаем UTC. Никакого неявного текущего месяца.
- Зерно — одна строка на `model_name`; NULL остаётся группой NULL. COUNT — целое, на пустой совокупности группированная таблица пуста; это не ошибка и не повод расширять фильтр. Обозначение «записей журнала/попыток» должно соответствовать утверждённому смыслу.
- Без JOIN, свободных выражений, AVG длительности, чата/LLM, @, SavedQuestion и графиков. Они подключаются к тому же пути после доказательства исполнения. Запрет ручного SQL относится к пилотному срезу, а не к удалению существующей функции SQL projects.
- По source completeness — UNKNOWN с явным предупреждением только если владелец допускает такой пилот; иначе блокировка. Для COMPLETE нужен установленный источник свидетельства, его coverage/возраст и владелец. Наличие строк не заменяет evidence.
- Предлагаемые ограничения до калибровки: 1000 строк результата, 5 MB сохранения, 15 секунд на statement и ограниченная конкурентность. Это кандидат конфигурации, не действующее SLO/согласованные пороги. Все диагностические SQL учитываются в общем сохранённом бюджете; обычный путь — один расчётный SELECT.

Технический план (существующие владельцы файлов, без второго executor):

1. Зафиксировать решение домена/прав/семантики и versioned capability profile; подготовить независимую fixture и ожидаемый результат. Связать утверждённое definition с существующими QUERY/COMPILATION; предусмотреть структурированный API-старт без обязательного вызова LLM. Текущий create всегда ставит REQUIREMENTS job — это узкая доработка S2, не новый frontend.
2. В существующем SQL lifecycle сохранять команду/run/attempt и `PREPARED → DISPATCHING` до сетевой отправки. Перед передачей повторно проверить AccessContext, dependency versions, expiry и остаток бюджета. После DISPATCHING ни повтор HTTP, ни истёкший lease не дают повторно отправить SQL.
3. Доработать PostgreSQL-адаптер: корреляция с отдельным DB backend/attempt, чтение подтверждаемого статуса и адресная отмена. Если установленный PostgreSQL/драйвер не позволяет доказать итог после разрыва, сохранять UNKNOWN и путь сопровождения. Не обещать exactly-once со стороны БД без соответствующей capability.
4. Записывать execution status и output availability независимо; потерянные данные ответа → SUCCEEDED с output unavailable только при подтверждённом завершении. Сохранить историю и явную команду нового расчёта; отсутствие строк отлично от отсутствия результата. Правило cancellation versus completion закрепить атомарным переходом по доказанному событию.
5. В Result добавить policy/evidence source completeness и admission versions; на чтении/экспорте повторять текущие права. Совместимый mapping для SQL projects обязателен; не переносить новые обозначения строкой только в UI.

Наблюдаемые исходы и план проверки:

| Исход E1 | Проверка и ожидаемый результат |
| --- | --- |
| Структурированная спецификация даёт точное число | Disposable PostgreSQL с тем же driver и schema типов; независимый hand-authored SQL и fixture/holdout: две модели, NULL, границы времени, успешные/ошибочные записи. Равенство наборов/целых значений, не только SQL-текста |
| Нет доступа к чужим строкам/полям | Реальный ограниченный DB-role/view + два principals; denied field/source не проходит поиск, LLM context, preflight, run, history/export. Каталоговое право не даёт SELECT |
| Повтор одной команды не создаёт второй запуск | Конкурентные HTTP и PostgreSQL транзакции с тем же ID/hash; один command/run. Другой payload → conflict; rollback между ledger и run не оставляет частичного эффекта |
| Потеря подтверждения не вызывает повтор SQL | Fault injection до отправки, после отправки до ACK, после завершения до сохранения; kill/restart worker, просроченный lease. Проверить число физических отправок и корреляцию: UNKNOWN/reconcile без resend |
| Отмена и завершение имеют доказанный итог | Отмена до отправки без SQL, cancel ACK, ошибка cancel, завершение раньше cancel. Неподтверждённое состояние — UNKNOWN; готовый SQL не становится CANCELLED из-за поздней команды |
| Неполнота не выдаётся за полный период | Evidence complete/partial/stale/unknown и unavailable/expired свидетельство; согласованные warning/block, SQL status отдельно, нет автозамены пропусков нулями |
| Версии и отзыв доступа работают после запуска | Изменение схемы/definition/profile/policy до dispatch и после сохранения. Новый допуск либо отказ, сохранённый результат не раскрывается после ограничения прав |
| Старые потребители сохранены | Focused suites SQL projects/runs/read/export + disposable HTTP/worker после изменения общего lifecycle. Новый assistant использует тот же gate; late result не меняет selected branch |

После этого API-среза остаётся отдельная работа E1 над ALG-00/immutable manifests, полной грамматикой пилота, нагрузочными лимитами и всем набором G1. Только затем E2 подключает вопросы на русском с qwen3.8 и независимой проверкой качества. Такой порядок уменьшает первый срез, не отменяя ни один обязательный AC.

## Открытые решения G0 / DoR

| Решение | Текущее состояние | Кто/чем закрывает | Блокирует |
| --- | --- | --- | --- |
| Пилотный домен (DOR-01/05) | **Выбран пользователем:** PostgreSQL bot / логи LLM; исходная таблица/OMD identity проверены | Не требует повторного выбора | Не блокирует обследование/подготовку E1 |
| Термин и формула метрики, роль ошибок/повторов, время/NULL (DOR-02/06/09) | COUNT(log_id), все попытки, timestamp_start, UTC — предложение | Владелец данных утверждает определение/версию и независимый эталон | Приёмку расчёта E1 |
| DB-role, разрешённая витрина, пилотная группа, field/row/export права (DOR-05/08/10) | Application connector/profile binding не подтверждён; у raw table RLS нет | DBA + владелец доступа: grants/view или доказанный эквивалент, две проверки principals | Любое реальное исполнение/выдачу пилота |
| Полнота периода и ограничения UNKNOWN (DOR-06) | Свидетельство/watermark и допустимый возраст не выбраны | Владелец данных: warning/block, coverage, evidence source/age, owner | Приёмку source completeness |
| Источник истины согласованной семантики (DOR-04/06) | Локальный реестр уже существует; достаточность OMD не исследована до утверждения | Проверить capabilities установленного OMD для метрик/версий; сохранить один owner/source | Расширение/промышленное утверждение реестра |
| Execution recovery/cancel capability (DOR-04/05) | PostgreSQL 16.14 подтверждён, корреляция/сверка ещё отсутствуют | Инженерная spike-проверка на disposable PostgreSQL; контракт UNKNOWN и сопровождения | G1 |
| Хранилище и версии контрактов (DOR-04/10) | Существующая SQL ORM/БД подходит как база; изменения схемы ещё не спроектированы | Узкий дизайн migration/command/run/access evidence в текущем storage; backward compatibility | Реализацию нового dispatch journal |
| Эталоны/holdout/числовые допуски (DOR-02/07) | Существующие goldens полезны, но не выбранный домен | QA + владелец данных: fixture и независимый holdout; COUNT exact, AVG позже | G1 и качество E2 |
| SLO, budgets, concurrency, TTL/custody (DOR-08/10) | Есть per-query timeout/rows/bytes и 30-дневный retention; кандидаты E1 не утверждены | Владелец эксплуатации: нагрузочный профиль, лимиты, восстановление, retention для всех классов | Полный G1/выпуск |
| UI-контракт и UX состояния (DOR-03) | Исходники и тесты есть; текущий runtime отстаёт | Контракт API-среза сейчас; реальный browser/keyboard/long values после сборки соответствующего кода | UI-приёмку E2/E3, не подготовку API E1 |
| Greenplum | Вне выбранного первого E1; целевая совместимость открыта | Паспорт версии/драйвера/прав/cancel/plan и отдельная приёмка перед подключением | Только Greenplum-поставку |

DoR применяется к конкретному срезу. Выбор домена снял один блокер, но не согласовал остальные решения. DOD-01–11 и G1–G6 не считаются выполненными по этому отчёту или по focused unit passes.

## Проверки и пределы результата

Все проверки выполнялись `.venv/Scripts/python.exe -B -m pytest -q ...` в зарезервированной задаче `S:\tmp\ragflow\pr50-e0-20261028`, с внешними `--basetemp`, `cache_dir` и JUnit. Точные команды сохранены в [checks.json](../../.local/archives/pr50-e0-20261028/checks.json); lifecycle run `9993c2d647da465cb29b5ae9a85c35e6` связан с этим отчётом.

| Группа | Конкретные test modules | Результат |
| --- | --- | --- |
| D — domain | `test_sql_assistant.py`, `test_sql_assistant_query.py`, `test_sql_assistant_definitions.py` из `test/unit_test/business_documents`; `test_sql_query_specification.py` из `test/unit_test/api/apps/business_documents` | **108 passed**, exit 0; [JUnit](../../.local/archives/pr50-e0-20261028/domain.xml), [log](../../.local/archives/pr50-e0-20261028/domain.log) |
| R — runtime/services | Из `test/unit_test/api/apps/business_documents`: `test_sql_assistant_service.py`, `test_sql_query_runs.py`, `test_sql_query_runs_boundaries.py`, `test_sql_run_command_boundaries.py`, `test_sql_query_access.py`, `test_sql_reader_authorization.py`, `test_sql_postgres_connection.py` | **217 passed**, exit 0; SQLite/controlled PostgreSQL adapter; [JUnit](../../.local/archives/pr50-e0-20261028/runtime.xml), [log](../../.local/archives/pr50-e0-20261028/runtime.log) |
| C — catalog | `test/unit_test/data_source/test_openmetadata_connector.py`, `test/unit_test/api/apps/services/test_openmetadata_copilot_service.py`, `test/unit_test/api/apps/business_documents/test_sql_live_catalog_lookup.py`, `test/unit_test/test_sql_retrieval_scope.py` | **110 passed**, exit 0; fake catalog/transport; [JUnit](../../.local/archives/pr50-e0-20261028/catalog.xml), [log](../../.local/archives/pr50-e0-20261028/catalog.log) |
| Повтор R после параллельных правок | Семь R modules + `test_sql_query_agents_boundaries.py`, `test_sql_result_answer.py` | **252 passed**, exit 0; [receipt](../../.local/archives/pr50-e0-20261028-recheck/checks.json). Во время прогона изменились service и новый test module: `source_unchanged=false`; итоговая source-аттестация неполная |
| Добавленный диалог S15 | `test_sql_assistant_service.py`, `test_sql_result_dialogue.py`, `test_sql_result_answer.py` | **41 passed, 2 failed**, exit 1; [JUnit](../../.local/archives/pr50-e0-20261028-dialogue/dialogue-final.xml), [receipt](../../.local/archives/pr50-e0-20261028-dialogue/checks.json). Service и тест параллельно менялись; это не стабильная полная приёмка последнего кода |
| Диагностика двух сбоев replay | [временные диагностические тесты](../../.local/archives/pr50-e0-20261028-exact-replay/test_exact_replay.py): неизменное HTTP-тело и повтор после EXPIRED, controlled LLM | **2 passed**, exit 0; [receipt](../../.local/archives/pr50-e0-20261028-exact-replay/checks.json), [JUnit](../../.local/archives/pr50-e0-20261028-exact-replay/exact-replay.xml). Service на этом интервале не менялся; менялся только параллельный test_sql_result_dialogue.py, поэтому общий source_unchanged=false |
| Метаданные окружения | `docker ps`; labels/version/package/file-presence в green; read-only PostgreSQL system catalog; read-only OMD system/table GET | Проверены приведённые выше факты; bootstrap/service principal не является пользовательской ACL-приёмкой |
| Live SQL / independent numeric acceptance / HTTP middleware / PostgreSQL concurrency / LLM / browser / нагрузка | Не запускались | Не проверено этим E0; 435 passes эти границы не заменяют |
| Git publication / deployment | Не запрашивались | Нет commit/push/merge/deploy; PR №50 остаётся исходной передачей требований |

Два сбоя S15 возникли в `test_answer_is_persistent_and_replay_does_not_run_model_or_generate_sql` и `test_expired_answer_is_not_recovered_from_log_or_replay`: test helper пересобирал команду с уже изменённой expected_revision, сохраняя прежний ключ. При hash всего тела это **другой payload**, для которого SQL_COMMAND_CONFLICT соответствует контракту. Отдельная диагностика повторила исходный JSON без изменения обеих revisions: один вызов модели, та же command/session revision; после expiry получен SQL_RESULT_EXPIRED без второго inference. Это подтверждает причину наблюдавшихся сбоев тестов, а не автоматически принимает всю реализацию S15. После окончания параллельных правок её владелец должен повторить свой suite; исходники и тесты E0 не исправлял.

В E0 не добавлялись тесты в repository; два временных диагностических теста сохранены в evidence вне source. Не запускались auto-fix/format, security scans или all-model suites. Подготовленные deterministic-тесты выбраны по реальным контрактам, а не как полная regression/release matrix. Обнаруженные расхождения не исправлялись в чужой dirty реализации. Проверены существование всех локальных ссылок отчёта и покрытие всех 49 номеров AC; новых unit-тестов для документации нет.

## Checkpoint продолжения

- Принятый scope: обследование E0 и предложение E1, пилот bot/логи LLM. Локальная база/dirty identity и PR head приведены выше; перед реализацией перепроверить их и не затирать другие изменения.
- Доказательства: 435 focused passes на первом стабильном снимке; последующие 252 passes и диагностические 2 passes не суммируются с ними как единая приёмка нового checkout. S15 suite дал 41 pass/2 fail на меняющемся source, причина replay-test mismatch проверена отдельно. Физическая таблица/типы/PK/RLS и каталоговая UUID/version подтверждены; installed green не содержит новых модулей. Все AC сопоставлены; ни один полный AC v0.5 не объявлен принятым.
- G0 открыт: semantics/access/completeness/contract/holdout/SLO решения; G1 открыт: dispatch/UNKNOWN/cancel, data-policy admission, budgets, manifests и независимые API/DB проверки.
- Следующее действие после постановки реализации: согласовать предложенное определение и разрешённую витрину/роль, затем спроектировать и выполнить первый API-срез E1 в текущем runtime. Недоступные внешние решения не заменять предположениями; инженерную подготовку/диспетчеризацию проверять на disposable fixtures.
- Учёт пилота процесса: scope/risk — substantive E0, доступ/состояние/SQL; один основной агент, без делегирования. Пользовательское вмешательство — один ответ о выборе домена; его время, стоимость/model usage/cache usage неизвестны. Изменения приложения/интеграционные конфликты — 0 со стороны E0; приёмка пользователем и последующий repair/rollback пока неизвестны. Длительность проверок есть в checks.json; общее время до принятого результата пока неизвестно.
