# Архитектура конструктора документов и SQL-запросов

Дата: 2026-09-20. Статус: целевая архитектура, не заявление о полноте
реализации.

Этот документ фиксирует только технические границы и инварианты. Продуктовое
поведение описано в [продуктовой спецификации](sql-query-constructor-product-ru.md),
интерфейс — в [UI/UX/CX-спецификации](sql-query-constructor-ui-ux-cx-ru.md),
фактическое покрытие — в [регрессии](sql-query-constructor-regression-ru.md).

## 1. Результат и границы

Конструктор — локальное расширение Business Documents в составе модульного
монолита RAGFlow. Он хранит единый `SqlQueryProject`, а не синхронизирует набор
независимых форм:

```text
requirements → schema snapshot → query specification → compilation
             → execution result → Python result → document revision
```

Каждый производный результат immutable и содержит версии входов. LLM предлагает,
детерминированный код проверяет, пользователь подтверждает. UI и worker не имеют
собственной реализации бизнес-правил.

Не создаются:

- отдельный plugin framework или второй планировщик;
- параллельная доменная модель во frontend;
- прямой путь от LLM к выполнению SQL или Python;
- второй lifecycle опубликованного Business Document;
- постоянные compatibility wrappers после переноса потребителей.

## 2. Владение и зависимости

| Поверхность | Владелец | Назначение |
|---|---|---|
| `business_documents/document_constructor/` | локальное расширение | шаблоны, Document AST, сборка |
| `business_documents/sql_query/` | локальное расширение | проект, SQL-семантика и сценарии |
| `api/apps/business_documents/` | локальные адаптеры | Peewee, OpenMetadata, LLM, registry, storage |
| `api/apps/restful_apis/` | минимальная core-интеграция | HTTP и регистрация маршрутов |
| `web/src/pages/business-documents/constructor/` | frontend-расширение | server-backed UI |
| Business Documents worker | локальная интеграция | долгие операции через существующую очередь |

Зависимости направлены внутрь:

```text
React → HTTP → application → domain
worker ────────────────→ application
adapters → application ports
composition root → HTTP + application + adapters
```

- Domain зависит только от стандартной библиотеки и чистых owned types.
- Application зависит от domain и абстрактных портов.
- ORM, SDK, HTTP-клиенты, request context и pandas принадлежат adapters.
- Transport разбирает запрос, вызывает один сценарий и формирует envelope.
- API и worker используют одну application-реализацию.
- `sql_query` может использовать публичные типы `document_constructor`; обратный
  импорт запрещён.

## 3. Компоненты

```text
business_documents/
  document_constructor/
    template_model.py
    document_model.py
    assembly.py
    application.py
    ports.py
  sql_query/
    domain/          # project, requirements, decisions, artifacts
    application/     # use cases, DTO и ports
    schema/          # snapshot и resolution
    specification/   # typed query, compiler и SQLGuard
    execution/       # registry, ResultGate и enrichment
    postprocessing/  # Python plan contract
    document/        # projection и traceability
```

Разбиение выполняется по capability, а не по техническим суффиксам. Сценарии не
собираются в god service. При переносе существующего пути его потребители и тесты
переводятся вместе, после чего старый путь удаляется.

## 4. Модель проекта

`SqlQueryProject` — consistency boundary одного создаваемого результата. Root
содержит текущую сводку и ссылки на immutable artifacts:

```text
id, tenant_id, owner_id
title, template_id, template_version, mode
current_stage
specification_gate, execution_gate, postprocessing_gate
current_*_artifact_id
state_version
created_at, updated_at
```

Состояние разделено на stage, capability gates и статусы отдельных
requirements/decisions/artifacts/jobs. Одна комбинированная enum не используется.
Эффективные capabilities вычисляются сервером; версия execution profile
фиксируется только в artifact конкретного запуска.

Основные инварианты:

1. Current artifact принадлежит проекту и имеет ожидаемый kind.
2. Значимое изменение увеличивает `state_version`.
3. Решение ссылается на существующий вопрос и подтверждённое значение.
4. Compilation закрепляет schema snapshot и query specification.
5. Run закрепляет compilation, binding и execution profile version.
6. Python run закрепляет immutable result и plan.
7. Document revision содержит точные source artifact IDs.
8. Незавершённая job или предложение LLM не повышают capability gate.
9. Изменение artifact инвалидирует только downstream-потомков.
10. Tenant/ACL проверяются до чтения и перед внешним действием.

## 5. Requirements, решения и artifacts

Requirement items имеют устойчивые IDs, normalized intent, source span, status
и связи с decisions/artifacts. Неоднозначность представляется отдельным
`DecisionRequest` со статусом `OPEN | RESOLVED | OBSOLETE`; обязательное открытое
решение блокирует продвижение сценария.

Все производные данные используют общий envelope:

```text
SqlQueryArtifact
  id, tenant_id, project_id
  kind, schema_version, revision
  payload | object_ref
  content_hash, byte_size
  source_artifact_ids[]
  source_requirement_ids[]
  producer, producer_version
  created_by, created_at
```

Payload каждого kind закрыт версионированной схемой. В domain/application он
typed; произвольный `dict` допустим только на границе сериализации. Event log
служит аудитом, но root не восстанавливается replay при обычном чтении.

Граф lineage определяет инвалидацию:

```text
requirements + schema → specification → compilation
compilation + binding → SQL result → enrichment → Python result
все принятые artifacts → document revision
```

История сохраняется; current pointers очищаются только для затронутых потомков.

## 6. Application и порты

Mutating command получает `actor`, `tenant`, `project_id`,
`expected_state_version`, `idempotency_key` и typed payload. Query-side может
читать оптимизированные projections, но не содержит второй набор правил.

Минимальные порты:

```text
UnitOfWork
QueryProjectRepository
ArtifactRepository
TemplateRepository
AccessPolicyPort
CatalogPort
SchemaInspectionPort
LlmInterpretationPort
ExecutionRegistryPort
SqlExecutionPort
ResultStorePort
PythonSandboxPort
JobRepository
AuditPort
BusinessDocumentPublisherPort
Clock / IdGenerator
```

Сценарий получает только нужные порты. Адаптер переводит внешние DTO и ошибки в
owned contract, получает secret непосредственно перед вызовом и задаёт deadline
и cancellation. Отсутствующий adapter означает capability `false`.

## 7. Хранение и конкурентность

Центральная БД хранит root, immutable artifact metadata, events, idempotency
ledger, jobs, template versions и document revisions. Большие SQL/Python results
лежат в object storage; БД хранит schema, размеры, checksum, object reference и
lineage. Preview ограничен и маскирован.

Короткая команда выполняет в одной DB-транзакции:

1. tenant/ACL check;
2. optimistic check `expected_state_version`;
3. проверку инвариантов;
4. запись artifact/decision/event;
5. обновление current pointers и версии;
6. запись idempotent response reference.

Сетевые вызовы, LLM, datasource, object storage и sandbox не выполняются внутри
этой транзакции.

Долгая операция сначала атомарно создаёт job, затем worker получает lease и
fencing token, выполняет bounded external call и в новой транзакции повторно
проверяет source version, cancellation и fence. Stale worker не может повысить
current pointer. Временные объекты удаляет reconciliation.

Повтор idempotency key с тем же request hash возвращает исходный результат, с
другим payload — conflict.

## 8. SQL lifecycle

`SchemaSnapshot` содержит только нужные проекту catalog identities, типы, связи,
fingerprint и evidence. Он не содержит DSN и не копирует всю БД. Основной источник
— OpenMetadata; bounded introspection через read-only profile используется только
при разрешённой capability. Расхождение источников создаёт blocking decision.

`QuerySpecification` — единственный semantic source SQL:

```text
sources, select_items, joins, filters
group_by, having, order_by, limit
subqueries/CTEs, requirement_links
```

Compiler строит SQL только из specification. Импортированный ручной SQL сначала
парсится в specification и показывает semantic diff. Неподдерживаемая конструкция
требует явного advanced/manual mode.

`SQLGuard` запускается после compilation и перед execution. Он запрещает DDL/DML,
multiple statements, недопустимые schemas/functions и конструкции вне profile.
Физическая БД дополнительно применяет read-only role, statement timeout,
row/byte limits, schema allowlist и concurrency quota.

`ResultGate` проверяет result schema, размер, ожидаемые поля, masking и warnings.
Только принятый результат становится входом enrichment, Python и документа.

Дополнительная расшифровка IDs проходит тот же lifecycle: явный request,
подтверждённое mapping, compilation, SQLGuard, execution и deterministic join.
Скрытого SQL-канала у LLM нет.

## 9. Python и документ

Application видит только `ResultHandle`, `FrameSchema`, `PythonPlan` и
`PythonRunResult`. Pandas/Polars находятся внутри disposable sandbox adapter.
План закрепляет code, entrypoint, inputs/outputs, image/version, разрешённые
packages и resource limits.

Sandbox не имеет application credentials и произвольной сети, читает только
заявленный input и пишет в отдельный output namespace. Результат проверяется по
schema/size до создания immutable artifact. In-process `exec` не допускается.

Document assembler строит нейтральный AST, сохраняет source requirement/artifact
IDs и исходный Mermaid/PlantUML при ошибке render. Сборка не выполняет SQL или
Python. Публикация идёт через `BusinessDocumentPublisherPort`, который повторно
проверяет tenant ACL, `capabilities.create === true`, L5 catalog, обязательные
sections и актуальность source hashes.

## 10. HTTP, frontend и runtime

HTTP остаётся project-scoped. Mutations требуют optimistic version и
`Idempotency-Key`; долгие операции возвращают `202` и operation ID. Ошибка имеет
стабильные `code` и `category`; traceback, prompt, credentials и raw connector
errors наружу не передаются.

Frontend хранит:

- server state — в query cache;
- navigation state — в URL;
- незаписанные поля и dirty flag — локально.

Полный проект не хранится в `localStorage`. Frontend не компилирует SQL, не
вычисляет gates и не содержит credentials. Действия доступны только при точном
server capability `=== true`; loading/unknown обрабатываются fail-closed.

HTTP и worker собираются одним composition root из одинаковых портов. Долгие
операции используют существующий Business Documents worker с его
claim/heartbeat/retry/cancel/cleanup contract. Отдельный сервис появляется только
при измеренной независимой нагрузке или обязательной изоляции.

## 11. Безопасность и наблюдаемость

Недоверенны browser payload, LLM output, catalog metadata, datasource rows,
импортированный SQL и Python code. Обязательны:

- tenant/ACL на root lookup и перед внешним действием;
- closed DTO и bounds;
- secret references вместо credentials в проекте;
- SQLGuard, read-only DB role и server-side limits;
- masking и запрет логирования rows/SQL literals;
- disposable Python sandbox без application identity;
- immutable lineage и safe audit;
- сквозные cancellation/deadline;
- fencing от публикации stale result.

Каждая операция имеет correlation/project/operation IDs и версии входов.
Метрики покрывают latency/error, queue wait, cancellation, rejection reasons,
aggregated rows/bytes и sandbox limits без пользовательских данных. Audit отвечает
«кто и что подтвердил», technical log — «почему операция сломалась».

## 12. Проверка и текущий разрыв

Проверки выбираются по изменённому поведению:

- domain/application unit — transitions, invalidation, ACL, idempotency, fence;
- adapter contract — mapping ошибок, deadline и cancellation;
- persistence integration — constraints, races и tenant isolation;
- API/frontend/browser — envelopes, capabilities и полный пользовательский путь;
- disposable PostgreSQL/sandbox — реальные execution boundaries;
- live LLM — только opt-in, не обязательный flaky gate.

Сейчас реализованы persisted project, accepted Requirements/Schema/Query artifacts,
human-gated proposals и server-backed guided UI. Ещё отсутствуют полный execution
result lifecycle, ResultGate, object result, Python sandbox и server-owned
DocumentAssembler с governed publication. Детальный статус и обязательные кейсы
ведутся только в [регрессионном документе](sql-query-constructor-regression-ru.md),
чтобы архитектура не превращалась в дублирующий журнал проекта.
