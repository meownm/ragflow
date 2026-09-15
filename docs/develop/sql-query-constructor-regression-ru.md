# Регрессия конструктора и создания SQL-запроса

Дата: 2026-09-14. Реализован безопасный вертикальный срез
`требования → каталог → tenant LLM → подтверждения пользователя → SQL → SQLGuard → execution profile`
без выполнения запроса. Он закреплён четырьмя browser-golden. Снимок
OpenMetadata теперь разрешается через центральный реестр в отдельный
версионированный read-only execution profile. Полный runtime
`SQL → БД → ResultGate → Python → документ` остаётся явным пробелом: DB-port
ещё не подключён и ни один экран конструктора не выполняет SQL.

Целевые роли, единый `QueryProject`, шесть этапов основного UX, атомарная модель
требований, правила инвалидации, режимы выполнения, сборка документа и
продуктовые DoR/DoD определены в
[продуктовой спецификации конструктора](sql-query-constructor-product-ru.md).
Целевые экраны, состояния, тексты и UX-регрессия UX-01…16 определены в
[UI/UX/CX-спецификации](sql-query-constructor-ui-ux-cx-ru.md). Целевые слои,
модули, persistence, транзакционные границы и этапы схлопывания текущего пути
описаны в [архитектуре конструктора](sql-query-constructor-architecture-ru.md).

## Что именно доказывают тесты

Регрессия разделена на независимые уровни. Прохождение более дешёвого уровня не
считается доказательством следующего.

| Уровень | Назначение | Оракул | Состояние |
| --- | --- | --- | --- |
| C0 — модель и codec | Структура, нумерация, валидация и JSON round-trip | Канонический JSON шаблона | covered |
| C1 — browser-golden | Реальные браузерные пути конструктора: lifecycle шаблона, разрешение схемы, execution profile, ручная спецификация и LLM-план с human confirmation | Точные wire-контракты и JSON, SQL только после решения всех gates, восстановление после reload и отсутствие browser errors | covered, 4 сценария |
| G1 — deterministic query golden | Полный сценарий разрешения сущностей, согласования, компиляции SQL, выполнения, Python-постобработки и сборки документа | Production owners, SQL AST, параметры, ограниченные данные и итоговый document AST | compiler/planner covered; execution/assembly gap |
| I1 — runtime integration | Read-only выполнение в одноразовой PostgreSQL-схеме и изолированном Python sandbox | Фактические запросы, ResultGate, лимиты, уничтожение sandbox и отсутствие остаточных данных | gap |
| L1 — live model | Качество выбора сущностей и уточняющих вопросов реальной LLM | Рубрика и точность на закреплённом schema snapshot | gap, opt-in |

## Живой контракт OpenMetadata → PostgreSQL

На 2026-09-14 для теста закреплён отдельный набор
`agent/business_requirements/golden_dialogs/sql_query_omd.v1.json`. Это не
синтетическая схема: entity ID, FQN, колонки, ограничения и наличие строк
проверены одновременно через OpenMetadata 1.12.10 и физическую PostgreSQL БД
`bot`.

Правило исполнения точное и проверяемое:

```text
OpenMetadata service.database.schema.table
                         ↓ exact catalog binding
PostgreSQL                  schema.table
```

Префиксы каталога `docker_postgres_bot.bot` не являются частью PostgreSQL
relation. Компилятор принимает двух-, трёх- и четырёхчастный catalog FQN, но в
SQL для четырёхчастной сущности выводит только проверенные
`schema.technical_name`. Несовпадение FQN с отдельными полями
`service/database/schema/technical_name` блокирует запрос.

| OMD FQN | PostgreSQL relation | Entity ID / OMD version | Строк при проверке |
| --- | --- | --- | ---: |
| `docker_postgres_bot.bot.public.search_queries` | `public.search_queries` | `2bebfd51-ea82-4b8f-8b7b-eeec1d930b08` / 0.5 | 639253 |
| `docker_postgres_bot.bot.public.search_results` | `public.search_results` | `c4757cc8-3934-480c-8203-ac4f719b18a5` / 0.5 | 639253 |
| `docker_postgres_bot.bot.public.answer_context_metrics` | `public.answer_context_metrics` | `d170a5e5-5686-4c4e-a1d2-4442f03d93d0` / 0.3 | 905 |
| `docker_postgres_bot.bot.public.llm_requests_log` | `public.llm_requests_log` | `491922b2-66ca-4c3d-9c7d-651a68d491b9` / 0.6 | 27017 |
| `docker_postgres_bot.bot.imoex.moex_orders` | `imoex.moex_orders` | `1df615ec-5065-46d5-87fa-8a85a7558d4b` / 0.5 | 87 |
| `docker_postgres_bot.bot.imoex.moex_events` | `imoex.moex_events` | `e32658b7-cd2a-456d-817c-f3731c119e24` / 0.4 | 30455 |

Полные наборы колонок каждой из шести таблиц совпали между OMD и
`information_schema`. Для основного кейса OMD также является источником двух
FK `search_results.query_id → search_queries.id` и
`answer_context_metrics.query_id → search_queries.id`; для MOEX — FK
`moex_orders.event_id → moex_events.event_id`.

### Пошаговый P0-диалог

Основной кейс начинается с намеренно расплывчатой формулировки «Покажи, какие
поисковые ответы были медленными и плохо использовали контекст» и проходит
одинаковые обязательные ворота:

1. Уточняется гранулярность: одна строка — один поисковый запрос.
2. Уточняется приватность: не выводить query, answer, user/tenant и технические
   payload-поля.
3. Термины «медленно» и «плохо» превращаются в проверяемые пороги
   `latency_ms >= 1500` и `noise_char_share >= 0.25`.
4. Период фиксируется полуинтервалом `[2026-08-01, 2026-09-01)`.
5. OMD подтверждает три таблицы, их колонки и два FK; до этого SQL отсутствует.
6. Пользователь отдельно подтверждает оба `INNER JOIN` и смысл исключения
   неполных строк.
7. Согласуются SELECT, `ORDER BY latency_ms DESC, query_id ASC` и `LIMIT 50`.
8. Для каждого из 44 разделов шаблона сохраняется итоговая улучшенная
   формулировка и статус `accepted` либо осмысленный `not_applicable`.
9. Snapshot разрешается exact binding-ом в один профиль выполнения; UI
   показывает каждое соответствие catalog FQN → physical relation.
10. Только после всех решений строится parameterized SQL и требуется
    `SQLGuard.status=PASS`; текущий UI по-прежнему не выполняет запрос.

Контрольная read-only выборка основного кейса вернула 35 строк. Два соседних
кейса нужны против переобучения теста на одну схему:

| ID | Последовательные уточнения | Живой результат |
| --- | --- | ---: |
| `GSQL_OMD_02_LLM_LATENCY` | отдельные успешные LLM-вызовы, порог 60 секунд, фиксированный период, LIMIT 20, исключение prompt/raw response/error | 2 строки до LIMIT |
| `GSQL_OMD_03_MOEX_ORDERS` | заявка вместо сделки, `submitted=true`, период, FK к событию, исключение account/payload, LIMIT 100 | 84 строки до LIMIT |

### Настроенный execution registry

- PostgreSQL connector: `OMD bot PostgreSQL`, без ingestion query и со статусом
  `UNSTART`.
- Профиль: `OMD bot PostgreSQL RO`; dialect `postgres`, схемы
  `public`, `imoex`, `rtts`, statement timeout 15 секунд, максимум 1000 строк и
  5 МБ.
- Физическая роль: `ragflow_sql_reader`, без superuser/create/replication/
  bypass-RLS и без DML-привилегий; `default_transaction_read_only=on`,
  `search_path=pg_catalog`.
- Exact bindings: `docker_postgres_bot.bot.public`,
  `docker_postgres_bot.bot.imoex`, `docker_postgres_bot.bot.rtts` → один профиль.
- Идемпотентная настройка выполняется
  `tools/scripts/provision_sql_query_omd_mapping.py`; пароль принимается только
  через `RAGFLOW_SQL_READER_PASSWORD` и не сериализуется в результат.

Во время подготовки OpenMetadata search возвращал HTTP 500, потому что
`openmetadata_elasticsearch` был остановлен. Контейнер запущен, сейчас он
healthy, `/api/v1/system/version` и `table_search_index` отвечают 200. Это
операционная зависимость каталога, а не разрешение подменять OMD прямым чтением
БД.

PostgreSQL при каждом соединении предупреждает о несовпадении версии collation:
БД создана с 2.36, текущая ОС предоставляет 2.41. В рамках SQL-конструктора
collation не обновлялась: такой ремонт требует отдельной оценки индексов и окна
обслуживания.

## Эпики и текущий статус

| ID | Эпик и граница | Статус | Зависимости |
| --- | --- | --- | --- |
| E1 | Конструктор структуры: шаблон v1.2.0 из 44 разделов, редактирование, preview, tenant/user-scoped draft, JSON import/export | **DONE** | — |
| E2 | Python как отдельная глава документа: требования, план, код, sandbox-политика, результат и fallback в разделе 6 | **DONE в шаблоне**, runtime отсутствует | E1 |
| E3 | Изолированные browser-golden lifecycle, schema workspace, ручной SQL, LLM-assisted SQL и агентский MVP от требования до документа | **DONE: 5 сценариев; новый MVP golden проверен в Chromium, прежние 4 — в 3/3 браузерах** | E1, E2, E4–E6, E13–E14 |
| E4 | Knowledge/schema resolution: до 8 терминов, до 5 компактных таблиц-кандидатов на термин, отдельная пакетная загрузка до 500 полей только выбранных таблиц, freshness/source evidence, один bounded-вызов tenant LLM, fallback, отдельный schema snapshot и tenant/user-scoped восстановление | **DONE для server-backed vertical slice** | Действующий OpenMetadata Copilot и Dataset ACL; chat-модель арендатора опциональна, при её недоступности работает детерминированный fallback |
| E5 | Decision workflow: варианты при неоднозначности, явный выбор таблиц/полей, обязательное подтверждение каждого JOIN/WHERE и общий no-SQL gate | **MVP DONE / full traceability IN PROGRESS**: guided UI подтверждает вопросы, таблицы, поля и query proposal; agent proposals и решения долговечны; атомарные DEC IDs и полный invalidation graph ещё отсутствуют | E4 |
| E6 | Query specification v1: SELECT, агрегаты/date bucket, INNER/LEFT JOIN, отдельные WHERE-карточки, typed parameters, ORDER BY, LIMIT, PostgreSQL compiler и независимый read-only SQLGuard | **DONE для flat-query v1** | E4, E5 |
| E7 | Выполнение и контроль результата: центральный реестр, отдельный execution profile, явная datasource binding, DB-port, лимиты, cancel, ResultGate и сохранение исходного результата | **IN PROGRESS**: реестр/profile/exact binding/resolver готовы; DB-port, read-only transaction, cancel, ResultGate и raw-result отсутствуют | E6; управляемый PostgreSQL connector |
| E8 | ID-справочники и дополнительные данные: отдельный раздел 2.4.3; lookup через подтверждённый JOIN поддержан planner/compiler | **IN PROGRESS**: post-result `additional_data_request` отсутствует | E4–E7 |
| E9 | Python-runtime: default-off sandbox, контракт `df → result_df`, ресурсы, аудит и fallback | **DONE в документном контракте / runtime BLOCKED** | E7 |
| E10 | Deterministic/integration golden и негативная матрица GSQL-01…06 | **IN PROGRESS**: planner/compiler и GSQL-02/06 покрыты; DB/Python части GSQL-01/03/04/05 заблокированы E7–E9 | E4–E9 |
| E11 | LLM query planner: один bounded tenant-вызов, закрытая JSON Schema, только принятые catalog IDs, PK/FK/description evidence, максимум 400 колонок, fallback и запрет self-approval | **DONE для flat-query v1** | E4–E6 |
| E12 | Advanced SQL: подзапросы, CTE, HAVING, оконные функции, UNION и pagination | **GAP**, шаблон требований есть, исполняемого контракта нет | E6 |
| E13 | Единый QueryProject: серверное хранение требований, решений, snapshot, query spec, compilation, binding, runs и document revisions с optimistic concurrency | **IN PROGRESS**: persisted Requirements/Schema/Query cycle, lease jobs, human proposals, immutable accepted artifacts, optimistic concurrency и idempotency готовы; compilation, binding, runs и document revisions ещё вне агрегата | E4–E7; схема и миграция Business Documents |
| E14 | Основной продуктовый workspace: список проектов, шесть этапов, серверный autosave, blockers и capability-aware UI; template/registry вынесены в административные режимы | **MVP DONE для 3 этапов**: server-backed список проектов, Requirements → Schema → Query, polling, blockers, review/decision, compile и Markdown download находятся на одной странице с режимом шаблонов; Execution/Result/Python остаются GAP | E13, E15 |
| E15 | Атомарный реестр требований и решений: REQ/DEC IDs, evidence, coverage, invalidation graph и автоматически собираемая traceability matrix | **DESIGNED / GAP** | E4–E6, E13 |
| E16 | DocumentAssembler: compile-only и runtime document revisions, статусы разделов, канонические read-only блоки, annotations и честные NOT_EXECUTED/DEGRADED состояния | **MVP compile-only output**: UI собирает и выгружает Markdown с требованиями, таблицами/полями, SELECT/JOIN/WHERE, лимитами, SQL, параметрами и отдельной Python-главой; server-owned revisions и runtime-ветка остаются GAP | E1, E2, E6, E13, E15; для runtime-ветки E7–E9 |

`DONE` здесь означает наличие исполняемого продукта и автоматической проверки.
`DONE для server-backed vertical slice` означает, что поведение доступно в
конструкторе, имеет application-сценарий и API на сервере и использует
production-владельцев каталога и tenant LLM, но ещё не входит в будущий полный
query aggregate. `DONE для flat-query v1` не включает CTE, подзапросы, HAVING
или выполнение в БД. `IN PROGRESS` означает
наличие проверенного подмножества эпика с явно перечисленным остатком.
`DESIGNED / GAP` означает, что контракт описан, но production-владельца и
исполняемого пути ещё нет. Такой эпик не считается частично пройденным только
из-за наличия шаблона, fixture или route mock.

Tenant/user в ключе localStorage предотвращает случайное восстановление чужого
черновика, но не является границей авторизации или шифрованием на общем
устройстве. Серверный сценарий проверяет право создания Business Documents и
Dataset ACL до чтения каталога; будущий полный query aggregate должен владеть
долговременным хранением и аудитом решений.

Скриншот не является golden-оракулом: он чувствителен к шрифтам и движку и плохо
показывает смысловой дрейф. Скриншоты и trace сохраняются как диагностика при
падении. Основной оракул — структурированные контракты.

## Обязательные browser-golden C1

### C1a — lifecycle шаблона

Исполняемый сценарий:
`test/playwright/e2e/test_document_constructor_ui.py::test_document_constructor_build_preview_persist_export_import_golden`.

Он выполняется в Chromium, Firefox и WebKit над одним production build и одним
проверенным digest артефакта:

1. Устанавливает синтетическую авторизованную сессию без реальных реквизитов.
2. Возвращает через API-stub роль `AUTHOR_CREATOR` и
   `capabilities.create=true`; записи в БД не создаются.
3. Открывает список документов и переходит по видимой ссылке «Конструктор».
4. Проверяет исходный SQL-шаблон, точное число секций и отдельную обязательную
   главу 6 «Постобработка результатов на Python».
5. Меняет название проекта и через UI добавляет обязательную главу 7 с
   назначением, требованием и инструкцией генератору.
6. Ждёт tenant/user-scoped автосохранение в `localStorage`.
7. Открывает предпросмотр и проверяет новые данные и Python-главу.
8. Скачивает JSON через браузер и проверяет имя файла.
9. Сравнивает распарсенный экспорт с каноническим шаблоном плюс точными
   изменениями пользователя. Порядок, номера, типы блоков и требования входят в
   сравнение.
10. Сбрасывает проект с подтверждением и проверяет восстановление исходного
    шаблона.
11. Импортирует только что выгруженный JSON, перезагружает страницу и проверяет
    восстановление проекта из локального хранилища.
12. Проверяет отсутствие API-мутаций, `pageerror`, console errors и значимых
    failed requests. Игнорируется только точная отмена повторной загрузки
    `/app-icon.png` самим Firefox при reload (`NS_BINDING_ABORTED`).

Этот тест доказывает полный жизненный цикл конструктора. Он не утверждает, что
SQL был сгенерирован или выполнен.

### C1b — schema workspace и решение неоднозначности

Исполняемый сценарий:
`test/playwright/e2e/test_document_constructor_ui.py::test_document_constructor_resolves_schema_ambiguity_and_exports_snapshot`.

1. Открывает schema workspace из реального production build.
2. Отправляет исходные требования и отдельный термин «Заказ» одним запросом в server-side сценарий
   `POST /api/v1/business-documents/sql-query/schema/resolve`. Он проверяет роль
   Business Documents и Dataset ACL, читает существующий OpenMetadata Copilot и
   делает не более одного bounded-вызова tenant LLM по всему набору терминов.
3. Получает `dwh.order_fact` и `dwh.order_event_fact`, LLM-рекомендацию только
   среди каталоговых ID, проверяет состояние
   `NEEDS_CLARIFICATION` и отсутствие неявного выбора первого fuzzy-кандидата.
4. Явно выбирает `dwh.order_fact`. UI отдельным пакетным запросом
   `POST /api/v1/business-documents/sql-query/schema/entities` загружает полную
   схему только этой таблицы. До завершения загрузки snapshot остаётся
   `NEEDS_CLARIFICATION`.
5. Выбирает поля `status_id` и `paid_amount_rub`; только при непустых исходных
   требованиях, после явного выбора хотя бы одного поля, наличия версии и
   schema fingerprint, а также известной неустаревшей freshness snapshot
   получает `READY`.
6. Ждёт отдельное tenant/user-scoped автосохранение, не смешанное с JSON
   шаблона документа.
7. Скачивает `sql-schema-snapshot.v1.json` и целиком сравнивает его с
   `test/playwright/golden/sql-schema-snapshot.v1.json`: кандидаты, решение,
   исходные требования, LLM-интерпретация и точные name/version/content hash
   prompt, таблица, все поля и флаги выбора,
   constraints, entity version, freshness, retrieval и source references входят
   в оракул.
8. Перезагружает страницу и проверяет восстановление требований, таблицы и полей.
9. Проверяет ровно один batch-запрос поиска и один запрос полной схемы выбранной
   таблицы, отсутствие прочих
   API-мутаций,
   `pageerror`, console errors и значимых failed requests.

Если каталог сообщает больше 500 полей таблицы, клиент не создаёт несохраняемый
неограниченный state: snapshot фиксирует фактическое и загруженное количество,
ставит `columns_truncated=true`, показывает предупреждение и остаётся
`NEEDS_CLARIFICATION`. Это проверено отдельным model-тестом.

Четыре C1-сценария входят в обязательный реестр из 56 browser cases. Каждый
проверен в Chromium, Firefox и WebKit; весь реестр дополнительно проходит в
Chromium. Они доказывают компиляцию, но не выполнение SQL в БД.

### C1c — ручная спецификация и компиляция

Исполняемый сценарий:
`test/playwright/e2e/test_document_constructor_ui.py::test_document_constructor_confirms_query_decisions_and_compiles_golden`.

Он восстанавливает READY snapshot с двумя таблицами и сначала отправляет его
в `POST /api/v1/business-documents/sql-query/execution-binding/resolve`.
Golden проверяет точный accepted snapshot, отсутствие заранее выбранного
profile и безопасный ответ `BOUND` с `Warehouse RO`: автор не получает
connector ID, host, username, password или ingestion query. Затем сценарий
проверяет видимое соответствие catalog FQN → PostgreSQL relation. Затем он
подтверждает JOIN, создаёт отдельную WHERE-карточку с integer-параметром и
проверяет, что кнопка компиляции заблокирована до каждого решения. После этого
он сравнивает весь request к
`POST /api/v1/business-documents/sql-query/compile`, проверяет SQL, отдельные
параметры и `SQLGuard.status=PASS`. До закрытия gates запросов компиляции нет.

### C1d — LLM-план и обязательное human confirmation

Исполняемый сценарий:
`test/playwright/e2e/test_document_constructor_ui.py::test_document_constructor_applies_llm_plan_then_requires_human_confirmation`.

Он отправляет точный принятый snapshot в
`POST /api/v1/business-documents/sql-query/plan`, применяет catalog-bound
предложение tenant LLM и проверяет, что JOIN/WHERE всё равно приходят
неподтверждёнными. SQL отсутствует, пока пользователь не подтвердит оба решения.
После подтверждения весь compile request совпадает с golden, SQL отображается,
а SQLGuard имеет статус PASS. Ни LLM, ни браузер не обращаются к БД.

## Реализованная архитектура безопасного SQL vertical slice

- Чистый owner разрешения схемы —
  `business_documents/sql_query/schema_resolution.py`: закрытый command,
  закрытые catalog DTO, двухфазная загрузка, ограниченные concurrency и общий
  deadline, каталоговый authority, проверка ответа LLM и fail-closed fallback.
- Адаптер `api/apps/business_documents/sql_query_schema.py` связывает сценарий с
  Dataset ACL, общим process-local экземпляром OpenMetadata Copilot и default
  chat-моделью текущего арендатора. Таймаут LLM — 90 секунд, provider retries
  отключены существующим LLM-адаптером.
- System prompts и закрытые JSON Schema ответов хранятся отдельными
  версионированными assets. В LLM payload передаются версия и content hash
  prompt; ответ сначала проверяется JSON Schema, затем application owner
  проверяет термины, таблицы и column ID. В контракте v1 честно поддержаны
  только `entity`, `field` и `unknown`.
- Чистый owner планирования —
  `business_documents/sql_query/query_planning.py`. Tenant LLM получает только
  согласованный snapshot, описания, glossary и constraints без реквизитов БД.
  Контекст ограничен 400 колонками: превышение даёт ручной fallback, а не
  молчаливое усечение. Ответ повторно проверяется по catalog IDs, типам,
  последовательности JOIN и alias; все JOIN/WHERE принудительно получают
  `confirmed=false`.
- Чистый owner компиляции —
  `business_documents/sql_query/query_specification.py`. Он принимает только
  закрытую структурированную спецификацию, сверяет требования, версии и
  fingerprint snapshot, строит parameterized PostgreSQL SELECT и независимо
  разбирает результат через sqlglot SQLGuard. Любой нерешённый пункт возвращает
  `sql=null` и `guard=NOT_RUN`.
- Чистый owner центрального реестра —
  `business_documents/sql_query/execution_registry.py`. Он принимает закрытые
  versioned-команды, группирует таблицы снимка по точной тройке
  `(catalog service, database, schema)` и разрешает все группы только в один
  общий активный PostgreSQL profile. Неоднозначность возвращает
  `NEEDS_SELECTION`, отсутствие mapping или несовместимые профили —
  fail-closed `UNAVAILABLE`.
- Адаптер `api/apps/business_documents/sql_execution_registry.py` хранит
  profiles и mappings в отдельных таблицах центрального managed tenant.
  Отсутствие настроенного managed owner делает реестр недоступным с 503: fallback
  к tenant текущего actor запрещён. Owner задаётся существующим системным
  параметром `managed_resources.owner_email` или переменной
  `MANAGED_RESOURCE_OWNER_EMAIL`. Управление доступно только superuser; автор с
  правом создания документа может только разрешить принятый снимок и получает
  безопасную проекцию политики без реквизитов коннектора и полного allowlist
  соседних схем.
- До разбора client snapshot и чтения реестра resolver повторно проверяет ACL
  Dataset, который управляет OpenMetadata catalog. Поэтому сфабрикованный
  snapshot нельзя использовать для перебора profiles; недоступность ACL backend
  даёт 503, отказ в доступе — 403.
- Execution profile отдельно задаёт connector, диалект, allowlist схем,
  statement timeout, максимальные строки и байты. Он имеет optimistic version и
  fingerprint политики. Пароль не входит в identity fingerprint, поэтому его
  штатная ротация не ломает profile; смена host, port, database или username
  блокирует разрешение до явного admin update и новой версии.
- Catalog binding является отдельной точной записью, а не полем snapshot или
  ingestion connector. Активную запись нельзя направить в запрещённую profile
  schema. Удалённый/повреждённый connector и невалидная сохранённая политика не
  участвуют в разрешении; аварийное отключение таких profile/binding остаётся
  доступно администратору.
- Администратор на странице конструктора получает отдельный диалог реестра для
  создания, изменения и отключения profiles/bindings. Авторы видят отдельную
  панель проверки и явный выбор только при нескольких совместимых profiles.
- Адаптеры `sql_query_schema.py`, `sql_query_planner.py` и
  `sql_query_lifecycle.py` владеют соответственно OpenMetadata/LLM,
  tenant-LLM и HTTP/ACL интеграциями. HTTP-слой только передаёт
  actor/tenant/role.
- UI разделён на чистую модель спецификации, controller-диалог и отдельные
  панели LLM/компилятора; серверный ответ не может перенести self-approval в
  браузер.
- UI не обращается к OpenMetadata или LLM напрямую.
- LLM не может добавить таблицу или поле, рекомендовать ID вне ответа каталога
  либо закрыть неоднозначность. Невалидный JSON, выдуманный ID, timeout или
  ошибка модели дают `FALLBACK`; каталоговые кандидаты и необходимость ручного
  выбора сохраняются.
- Ошибка или timeout отдельного catalog lookup возвращается как `DEGRADED` с
  `lookup.status=ERROR` и retryable-кодом; UI не подменяет её состоянием
  `NOT_FOUND`.
- UI связывает ответы с generation исходного поиска: изменение требований или
  списка сущностей отменяет применимость старых решений. Detail-загрузки
  независимы для разных таблиц, а запоздавший ответ по повторно выбранной
  таблице не может перезаписать более новый результат.
- Schema/planner/registry owners не выполняют SQL, не читают произвольную БД и
  не запускают Python. Компилятор создаёт текст SQL, resolver выбирает только
  безопасный profile, но DB-port намеренно ещё не подключён. Эти полномочия
  остаются за незавершённой частью E7–E9.

## Golden GSQL-01: запрос от требований до документа

### Закреплённый вход

Запрос пользователя:

> Покажи по месяцам 2026 года сумму оплаченных завершённых заказов
> корпоративных клиентов. Статус выведи названием. После выборки отметь
> аномальные месяцы по z-score.

Версионированный синтетический schema snapshot:

| Таблица | Гранулярность | Поля |
| --- | --- | --- |
| `dwh.order_fact` | одна строка на заказ | `order_id`, `customer_id`, `status_id`, `created_at`, `gross_amount_rub`, `paid_amount_rub` |
| `dwh.order_event_fact` | одна строка на смену статуса | `order_id`, `status_id`, `event_at` |
| `dwh.customer_dim` | одна строка на клиента | `customer_id`, `customer_type_code` |
| `ref.order_status` | одна строка на статус | `status_id`, `status_code`, `status_name_ru` |

Все DDL, описания, ключи, кардинальности и словарные значения принадлежат
fixture. Golden не зависит от общей тестовой или production-БД.

### Обязательный диалог решений

1. Модель находит две таблицы-кандидата для сущности «заказ» и не генерирует
   SQL.
2. Пользователь выбирает `dwh.order_fact`: нужен текущий итог заказа, а не
   история переходов.
3. Модель показывает `gross_amount_rub` и `paid_amount_rub`; пользователь
   выбирает оплаченную сумму.
4. `status_id` разрешается через подтверждённый справочник
   `ref.order_status`; модель не выдаёт ID за пользовательское название.
5. Только после закрытия решений состояние становится `READY`.

### Каноническая спецификация запроса

- гранулярность: одна строка на месяц и название статуса;
- таблицы: `order_fact`, `customer_dim`, `order_status`;
- JOIN:
  `c.customer_id = o.customer_id`, `s.status_id = o.status_id`;
- WHERE-карточки: полуинтервал дат, тип клиента, код завершённого статуса;
- параметры отделены от SQL;
- read-only, одна инструкция, явный SELECT, детерминированный ORDER BY и LIMIT;
- раздел 6 содержит отдельный Python-план и фактический статус выполнения.

Ожидаемый SQL для PostgreSQL:

```sql
SELECT
    date_trunc('month', o.created_at)::date AS month,
    s.status_name_ru AS status_name,
    SUM(o.paid_amount_rub) AS paid_amount_rub
FROM dwh.order_fact AS o
JOIN dwh.customer_dim AS c
    ON c.customer_id = o.customer_id
JOIN ref.order_status AS s
    ON s.status_id = o.status_id
WHERE o.created_at >= :date_from
  AND o.created_at < :date_to
  AND c.customer_type_code = :customer_type_code
  AND s.status_code = :status_code
GROUP BY
    date_trunc('month', o.created_at)::date,
    s.status_name_ru
ORDER BY month, status_name
LIMIT :row_limit
```

Параметры: `date_from=2026-01-01`, `date_to=2027-01-01`,
`customer_type_code=CORPORATE`, `status_code=COMPLETED`, `row_limit=1000`.
SQL сравнивается после разбора и нормализации AST; форматирование и регистр не
должны давать ложное падение. Имена объектов, JOIN/WHERE, параметры и LIMIT
сравниваются строго.

### Python-постобработка

Вход — только копия прошедшего ResultGate DataFrame с 12 закреплёнными строками
и колонками `month`, `status_name`, `paid_amount_rub`. Код получает `df` и
возвращает `result_df`, добавляя `z_score` и `is_anomaly`. Для нулевого
стандартного отклонения z-score равен `0.0`.

Golden обязан проверить:

- executor запускается только при `enabled=true` и `execution_mode=sandbox`;
- sandbox не получает сеть, БД, секреты или постоянную файловую систему;
- `python_execution.status=ok`, строк остаётся 12, исходные колонки сохранены;
- результат проходит схему, типы, NULL, finite-number и сериализационные
  проверки;
- исходный SQL-result сохранён отдельно;
- в итоговом document AST присутствуют ровно корневые главы 1–6, а
  постобработка находится только в главе 6.

### Сквозные жёсткие утверждения

- При открытой неоднозначности отсутствуют executable SQL и DB-вызовы.
- Каждая сущность, таблица, колонка, JOIN и фильтр имеет ссылку на snapshot.
- SQLGuard наблюдает одну read-only инструкцию без `SELECT *` и без
  пользовательских литералов.
- DB-adapter получает точный SQL и отдельный объект параметров.
- ResultGate отклоняет лишние колонки, превышение лимита, неожиданные типы и
  несериализуемые значения.
- Итоговый документ содержит исходный запрос, ER-контур, SELECT/JOIN, отдельные
  WHERE-карточки, лимиты, итоговый SQL и отдельную Python-главу.
- Ни один секрет, DSN или токен не попадает в document AST, события, логи и
  browser artifacts.

## Обязательные соседние golden-кейсы

| ID | Риск | Ожидаемый результат | Статус |
| --- | --- | --- | --- |
| GSQL-01 | Основной запрос по заказам, клиентам и справочнику статусов | Точный parameterized SQL и SQLGuard PASS; затем DB/ResultGate/Python/document | **compiler covered**, runtime gap |
| GSQL-02 | Пользователь не выбрал одну из таблиц-кандидатов или не подтвердил JOIN | `NEEDS_CLARIFICATION`, SQL и DB-вызовы отсутствуют | **covered** pure + browser |
| GSQL-03 | После результата нужны названия для неизвестных ID | создаётся `additional_data_request`; отдельный bounded SQL проходит подтверждение и SQLGuard, Python к БД не обращается | **gap**; lookup в основном запросе covered |
| GSQL-04 | Sandbox выключен политикой | `python_execution.status=disabled`, исходный SQL-result остаётся успешным | **gap**, нет runtime |
| GSQL-05 | Python завершился ошибкой или по timeout | pipeline `degraded`, исходный результат возвращён, sandbox уничтожен | **gap**, нет runtime |
| GSQL-06 | Schema snapshot изменился после согласования | запрос блокируется как stale, требуется повторное сопоставление | **covered** pure compiler/planner |

## Архитектура оставшейся части deterministic runner

Runner должен вызывать production-владельцев сценария, а не повторять алгоритм
в тесте:

1. Уже реализованные scripted catalog/LLM и production owners возвращают
   закреплённые неоднозначности, catalog-bound план и SQL.
2. Будущий production query aggregate сохраняет решения и версии snapshot.
3. Уже реализованный compiler строит спецификацию и SQL, SQLGuard проверяет
   разрешённый read-only контракт.
4. Уже реализованный центральный registry resolver по точным catalog scopes
   выбирает один разрешённый execution profile; будущий executor повторно
   проверяет Dataset ACL и snapshot/profile/binding versions перед открытием
   DB-port.
5. Записывающий spy вокруг DB-port подтверждает отсутствие ранних или лишних
   запросов; disposable PostgreSQL возвращает закреплённые строки.
6. Production ResultGate валидирует и ограничивает результат.
7. Production Python-port вызывает одноразовый sandbox или детерминированный
   контрактный double; отдельный integration-кейс проверяет настоящий sandbox.
8. Production assembler создаёт document AST, который сравнивается с golden.

Route mocks достаточны для C1, но не дают покрытие G1/I1. Live LLM никогда не
входит в детерминированный release gate: она запускается отдельно и не может
превратить skip в pass.

## DoR для G1/I1

- Есть один production-владелец query lifecycle; Knowledge, SQLGuard и
  execution-profile resolution реализованы, для DB, ResultGate, Python sandbox
  и DocumentAssembler определены явные порты.
- Состояния, решения пользователя, snapshot/version и execution metadata
  сериализуются закрытыми схемами.
- API и worker вызывают один application scenario.
- Есть disposable PostgreSQL fixture и принудительное удаление схемы.
- Sandbox default-off, поддерживает timeout/cancel и гарантированное удаление.
- UI имеет стабильные role/label/testid для каждого шага и статуса.

## DoD полной регрессии

- C0, C1, G1 и I1 проходят без skip; C1 проходит во всех трёх браузерах.
- GSQL-01 и обязательные негативные кейсы имеют явные знаменатели и выполняют
  каждое утверждение через production boundary.
- Ошибка любого gate не оставляет DB-запрос, частичный документ, новый revision
  или живой sandbox.
- На падении сохранены безопасные JUnit, trace, screenshot и очищенные event
  metadata; исходные данные и секреты отсутствуют.
- Coverage matrix содержит только `covered`, `covered_by_lower_level`,
  `not_applicable` или честный `gap`; fixture без runner не считается pass.
