# E1: число записей LLM по модели

Задача пользователя: минимальный E1 по PR №50, PostgreSQL bot, `COUNT(log_id)` по `model_name` за заданный период; общий SQL runtime, исправление UNKNOWN/отмены, права и полнота; без deployment.

База начала: `5637343b4566ce004d7d9d29b75a04ce1e7a7aca`, `main`, shared dirty checkout. Чужие tracked/untracked изменения SQL Assistant, администрирования и UI сохраняются. Контекст и исходные пробелы: [E0](e0-assessment-20261028-ru.md). Этот файл — единый checkpoint реализации/проверок E1.

## Принятые исходы и проверки

- Структурированный запрос с двумя абсолютными датами со смещением создаёт текущие SQL artifacts без LLM и без неявного выполнения; отдельная команда execute создаёт общий job. Одна запись журнала — одна попытка, включая ошибки; фильтр по началу, `[from,to)`, timezone UTC, NULL model — отдельная группа. Проверка: независимые fixture/SQL и пустой результат, границы, разные offsets.
- Один ключ/тело команды — один run; новое тело с прежним ключом конфликтует. До сетевой отправки есть durable attempt/dispatch journal; после неопределённой отправки нет resend. Проверка: повтор, конкурентный claim, потеря ACK/lease и callback persistence.
- Отмена до отправки не выполняет SELECT; подтверждённая остановка отличается от UNKNOWN. Поздняя отмена не уничтожает успешно завершившийся расчёт. Проверка: реальный PostgreSQL cancel/разрыв и service state transitions.
- Только разрешённый actor/profile/entity/DB-role получает пилот; роль не имеет доступа к закрытым колонкам. Смена политики или отзыв grants блокируют запуск и чтение/экспорт сохранённых строк. Проверка: отрицательные service и реальные PostgreSQL grants.
- Полнота источника отдельно от исполнения и усечения: period-scoped, dated evidence; expired/missing coverage → UNKNOWN, явные warn/block. Проверка: complete/partial/stale/unknown, границы покрытия и отзыв.
- `/sql-projects`, история, export, retention и общий worker сохраняют свои действующие функции. Проверка: focused consumers и migration на disposable БД; UI получает честный статус UNKNOWN.

Изменения — локальные расширения текущего fork; второй executor, каталог и frontend не создаются. Развёртывание, изменение промышленной bot и model qualification не выполняются. Runtime default policy fail-closed; реальное включение пилота требует конфигурации роли/прав и свидетельств полноты.

## Реализованный срез

Точка входа — `POST /api/v1/sql-assistant/llm-log-count`:

```json
{"client_request_id":"llm-august-1","from":"2026-08-01T00:00:00+03:00","to":"2026-09-01T00:00:00+03:00"}
```

Период нормализуется в UTC, требует явного offset и `from < to`. Сервер создаёт обычные REQUIREMENTS/SCHEMA/QUERY/COMPILATION и SQL Assistant session. Возвращаемый `id` открывается существующим SQL Assistant. Выполнение — его обычная команда `execute` с текущими revisions; повтор исходного тела/ключа возвращает существующий run. Новый ключ во время UNKNOWN не разрешает новый SELECT.

Метрика `llm-log-count/1`: `model_name, COUNT(log_id) AS request_count` из `bot.public.llm_requests_log`, `request_timestamp_start >= :period_from AND request_timestamp_start < :period_to`, группировка и сортировка по модели. NULL — отдельная группа. Считаются записи, включая ошибки и повторные попытки; фильтра успешности, JOIN, текстового SQL и inference нет. Лимит 1000 групп проходит через существующие проверки результата и усечения. Пустой период возвращает 0 групп. Это техническая версия метрики; session остаётся EXPLORATORY, глобальное бизнес-согласование не объявляется состоявшимся.

В snapshot входят только `log_id`, `model_name`, `request_timestamp_start`; prompt/raw response, пользовательские идентификаторы и описания закрытых колонок туда не копируются. Существующие compiler, catalog binding, execution profile, queue/lease, PostgreSQL adapter, validator, history, export и retention переиспользуются. Нового executor или приложения нет. Новая форма выбора периода не добавлялась: минимальный вход — API, просмотр/запуск — существующий интерфейс.

### Исполнение и отмена

В существующем `BusinessDocumentSqlQueryRun.execution` сохраняются attempt, фаза и точная идентичность backend (`pid`, `backend_start`, `application_name`) **до** отправки основного SQL. Callback после ответа PostgreSQL фиксирует SUCCEEDED до обработки/сохранения строк. Метаданные результата по-прежнему отделены от SQL-исхода.

- CAS `QUEUED → RUNNING` и durable `PREPARED → DISPATCHING` допускают одну отправку. SQL jobs не возвращаются в автоматический RETRY, включая старые записи с `max_attempts > 1`.
- Потеря ACK/lease после dispatch означает UNKNOWN. Запрос отмены сам по себе не доказывает CANCELLED. UNKNOWN блокирует следующий запуск, редактирование, смену источника и завершение проекта.
- Подтверждённый ответ `QueryCanceled` либо отмена в известной фазе REGISTERED/PREPARED дают CANCELLED. Повторная доставка CANCEL_REQUESTED после dispatch, после ACK или без фазового журнала старого формата не объявляет остановку. Если SQL успел завершиться, поздняя отмена сохраняет успешный результат. Если подтверждение завершения есть, но строки не удалось сохранить, статус SUCCEEDED означает «SQL завершён, строки недоступны».
- Поздний completion callback может закрыть UNKNOWN только для исходного attempt; он не публикует строки с потерянной lease.
- `GET /api/v1/sql-assistant/sessions/{id}/runs/{run_id}/execution` наблюдает исходный backend; UNKNOWN cancel сигнализирует только совпадающему backend. Отсутствие процесса не считается доказательством успеха/отмены. Повтор SQL не выполняется. HTTP recovery для UNKNOWN — RECONCILE, `retryable=false`.
- `POST /api/v1/business-documents/sql-query/projects/{id}/runs/{run_id}/cancel` сохраняет готовые строки; отдельное `/discard` с тем же префиксом удаляет только READY-результат и его производные. Старый UI передаёт discard при удалении готового результата. Оба интерфейса различают UNKNOWN/SUCCEEDED/CANCELLED; неоднозначный исход не называется выполняющимся запросом.

Миграция добавляет JSON `execution` с DB default `{}`: старые записи и INSERT старого бинарника сохраняются, повтор миграции безопасен. Она проверена на disposable PostgreSQL; текущая служебная БД не мигрировалась.

### Права и полнота

Доверенная серверная переменная `SQL_LLM_LOGS_POLICY` обязательна. Без неё пилот закрыт. Пример структуры (профиль, роль и участники требуют реальной административной настройки):

```json
{"version":"1","profile_id":"<existing-profile-id>","entity_id":"491922b2-66ca-4c3d-9c7d-651a68d491b9","db_role":"ragflow_llm_count","actors":["<allowed-actor-id>"],"unknown_action":"warn","incomplete_action":"block"}
```

Нет admin bypass списка actors. Помимо него действуют текущие project/tenant/role/catalog ACL и registry binding. До отправки, в самом соединении и перед чтением/экспортом сохранённых строк проверяются policy, исходный профиль/его версия, текущая DB-role и реальные column grants. Роль должна быть без superuser/BYPASSRLS и иметь SELECT **только** на трёх разрешённых колонках этой таблицы. Отсутствующие grants и расширение SELECT на закрытые колонки блокируют пилот. Смена access policy или версии профиля также блокирует прежний результат. Привилегированный общий connector для пилота не допускается.

Разрешение здесь означает доступ к агрегатам **всех** логов bot за период. В исходной таблице не обнаружен RLS; фильтр по пользовательскому/организационному идентификатору в эту метрику не добавлялся. Состав группы пилота должен соответствовать именно такому доступу. Промышленные grants, connector и policy в этой задаче не менялись.

`source_completeness` независима от успешного SQL и от `checks.completeness` (усечение вывода). Необязательное поле policy `completeness` содержит `status` (COMPLETE/PARTIAL/STALE), `evidence_ref`, `observed_at`, `valid_until`, `coverage_from`, `coverage_to`. Принимается только свидетельство, покрывающее обе границы периода, не из будущего, возрастом ≤300 секунд и ещё не истёкшее. Иначе UNKNOWN. Для UNKNOWN и PARTIAL/STALE администратор явно выбирает warn/block; разрешить их как COMPLETE нельзя. Текущая полнота пересчитывается перед запуском и выдачей. Свидетельство источника не выводится из количества строк или успешного ответа БД.

## Проверки и границы доказательств

Все команды, exit codes и JUnit находятся в указанных receipts; повторные наборы пересекаются и **не суммируются** как независимые проверки.

| Исход / потребитель | Проверка и результат |
| --- | --- |
| Общий run lifecycle, runtime, метрика, assistant, access, export, роли, history, HTTP | `.venv/Scripts/python.exe -B S:/tmp/ragflow/pr50-e1-llm-logs/qa_e1.py cancellation-review <13 focused modules>` — **319 passed**, exit 0; [точный argv](../../.local/archives/pr50-e1-llm-logs/cancellation-review/receipt.json), [JUnit](../../.local/archives/pr50-e1-llm-logs/cancellation-review/results.xml). После исправления повторной доставки CANCEL_REQUESTED |
| Метрика, поздний ACK, UNKNOWN/lease, отзыв actor/policy/profile/grants/coverage, текущие права/экспорт и HTTP | `qa_e1.py units-review <5 modules>` — **154 passed**, exit 0; [argv](../../.local/archives/pr50-e1-llm-logs/units-review/receipt.json), [JUnit](../../.local/archives/pr50-e1-llm-logs/units-review/results.xml). Catalog/driver для unit-границы контролируются |
| Quart body/auth-decorator/actor binding, idempotent create, no implicit dispatch; cancel/discard transport | `qa_e1.py http-2 <2 modules>` — **27 passed**, exit 0; [argv](../../.local/archives/pr50-e1-llm-logs/http-2/receipt.json), [JUnit](../../.local/archives/pr50-e1-llm-logs/http-2/results.xml). Использована реальная Quart route и service, контролируемый auth transport; это не проверка промышленного login middleware |
| PostgreSQL: независимый COUNT oracle, ошибки/NULL/границы, пустой период/offsets, grants revoke/widen, реальный cancel/disconnect, два конкурентных worker, миграция | `qa_e1.py postgres-cancellation-review --postgres test/integration/test_sql_llm_logs_e1_postgres.py` — **8 passed**, exit 0; [argv](../../.local/archives/pr50-e1-llm-logs/postgres-cancellation-review/receipt.json), [JUnit](../../.local/archives/pr50-e1-llm-logs/postgres-cancellation-review/results.xml). И источник, и служебные таблицы на PostgreSQL. Собственный контейнер/схемы удалены; общий bot не затрагивался |
| Старые UI consumers: блокировки UNKNOWN, явные статусы, профиль, ревизии | Три focused Jest suites — **89 passed**, exit 0; [log](../../.local/archives/pr50-e1-llm-logs/final-validation/jest.log), [JSON](../../.local/archives/pr50-e1-llm-logs/final-validation/jest.json). Компонентная проверка, без браузера |
| Статика затронутых файлов | Ruff check + format check, Prettier check, ESLint без auto-fix — exit 0; [команды, source hashes и drift](../../.local/archives/pr50-e1-llm-logs/final-validation/checks.json) |
| Типы всего текущего frontend | `node node_modules/typescript/bin/tsc --noEmit` из `web/` — **exit 0**, [log](../../.local/archives/pr50-e1-llm-logs/final-validation/type-check.log) |

Во время final-validation хэши 33 выбранных файлов source/tests до и после проверок совпали (`drift=[]`). После него изменены только две проверки фазы в `sql_query_runs.py` и добавлены три соответствующих regression cases в `test_sql_llm_logs_e1.py`; на этом коде повторены 319 backend/HTTP и 8 PostgreSQL проверок. Итоговый source snapshot, сравнение с final-validation и повтор Ruff для двух файлов — [post-review](../../.local/archives/pr50-e1-llm-logs/post-review/verification.json). Frontend с момента его проверок не менялся. Ранний TypeScript прогон выявлял ошибки в параллельно меняющихся `sql-assistant/index.tsx` и `sql-assistant/result-dialogue.tsx`; последний прогон текущего checkout прошёл. Browser/live bot/live catalog acceptance, LLM quality, build/release qualification и deployment не выполнялись.

## Review и оставшиеся условия включения

Проверены требования → owning path → callers/worker/read/export и ошибки, затем фактический diff. Найденные при повторном проходе пробелы — доступ к старому результату после смены версии профиля и ложное подтверждение отмены при повторной доставке CANCEL_REQUESTED — закрыты и включены в отрицательные проверки. Новые чистые правила находятся в `business_documents/sql_query/{llm_logs,execution}.py`; их parent imports не создают infrastructure. Сетевая проверка и durable callbacks остаются в существующем API/adapter/runtime. Подтверждённого нового мёртвого пути для удаления не найдено; сохранённые SQL contracts и поддерживаемые upstream backends не удалялись.

Для реального включения остаются настройка отдельной роли/connector и списка участников, утверждение семантики «все записи — попытки» и политики полноты, действительное свидетельство загрузки и приёмка на целевом bot. Автоматического producer свидетельств полноты в этом срезе нет. При потерянном ACK и исчезнувшем backend, без позднего подтверждения исходного attempt, UNKNOWN остаётся защищённым: PostgreSQL `pg_stat_activity` не даёт истории завершённых запросов. Это ограничение явно сохраняется; обхода блокировки или выдуманного терминального исхода нет.

Весь PR50 и общие G0/G1 не объявляются закрытыми: общая governance метрик, workload budgets, полные manifests/SLO/holdout и Greenplum не входят в минимальный E1.

## Checkpoint

Локальный E1 реализован, проверки перечислены выше. HEAD остаётся `5637343b4566ce004d7d9d29b75a04ce1e7a7aca`; исходные dirty файлы для 14 затронутых путей сохранены в [baseline manifest](../../.local/archives/pr50-e1-llm-logs/baseline/manifest.json), финальный снимок и hashes — в post-review (предыдущий — final-validation). В shared checkout имеются параллельные изменения, принадлежность всех dirty файлов задаче E1 не заявляется. TypeScript и scoped checks прошли; после отдельного review отмены backend/HTTP/DB перепроверены. Следующий шаг — отдельно подготовить реальное включение после проверки policy/прав/полноты и целевой приёмки. Commit, push и deployment не выполнялись.

Учёт пилота процесса: substantive E1, риск — права/исполнение/отмена/сохранённые данные; один основной агент, без делегирования. Известные длительности checks сохранены в receipts; общее время до пользовательской приёмки, human time, model usage/cached usage/cost и последующий repair/rollback неизвестны. Изменения согласованы с текущим shared checkout; интеграционные merge conflicts не создавались. Пользовательская приёмка ещё не получена.
