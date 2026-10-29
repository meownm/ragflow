import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type {
  AssistantQuery,
  AssistantSession,
} from '@/interfaces/database/sql-assistant';
import { useEffect, useState } from 'react';
import { AssistantFilters } from './filters';
import type { SendCommand } from './workspace';

type Column = { id: string; name: string; data_type: string };
type Table = { id: string; technical_name: string; columns: Column[] };

export function assistantTables(session: AssistantSession): Table[] {
  const schema = session.project.artifacts?.schema;
  const snapshot = schema?.schema_snapshot as
    | { requirements?: Array<{ selected_table?: Table }> }
    | undefined;
  const accepted = new Set(
    (schema?.accepted_schema as Array<{ entity_id: string }> | undefined)?.map(
      (item) => item.entity_id,
    ),
  );
  return (
    snapshot?.requirements?.flatMap((item) =>
      item.selected_table && accepted.has(item.selected_table.id)
        ? [item.selected_table]
        : [],
    ) || []
  );
}

const Aggregations = {
  column: 'Поле / группировка',
  sum: 'Сумма',
  avg: 'Среднее',
  min: 'Минимум',
  max: 'Максимум',
  count: 'Количество',
  count_distinct: 'Уникальных значений',
  date_bucket: 'Период',
};
export function AssistantBuilder({
  session,
  surface,
  send,
  busy,
  onDirtyChange,
}: {
  session: AssistantSession;
  surface: 'builder' | 'sql';
  send: SendCommand;
  busy: boolean;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const project = session.project;
  const initialQuery =
    session.draft?.query ||
    session.structured_query ||
    project.artifacts?.query;
  const structured =
    initialQuery &&
    !('mode' in initialQuery) &&
    Array.isArray(initialQuery.select)
      ? (initialQuery as AssistantQuery)
      : null;
  const [query, setQuery] = useState<AssistantQuery | null>(() =>
    structured ? structuredClone(structured) : null,
  );
  const [sql, setSql] = useState(
    session.draft?.compiled.sql || project.compilation?.result.sql || '',
  );
  const [parameters, setParameters] = useState(() =>
    JSON.stringify(
      'parameters' in (initialQuery || {})
        ? (initialQuery as { parameters: unknown }).parameters
        : Object.entries(
            (session.draft?.compiled || project.compilation?.result)
              ?.parameters || {},
          ).map(([name, value]) => ({
            name,
            type:
              [
                ...(structured?.filters || []),
                ...(structured?.aggregate_filters || []),
              ].find((filter) => filter.parameter_name === name)
                ?.parameter_type ||
              (Number.isInteger(value)
                ? 'integer'
                : typeof value === 'number'
                  ? 'decimal'
                  : 'text'),
            value,
          })),
      null,
      2,
    ),
  );
  const [dirty, setDirty] = useState(false);
  const [origin, setOrigin] = useState<'builder' | 'sql' | null>(null);
  const editingOther = dirty && origin !== null && origin !== surface;
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);
  const [error, setError] = useState<string | null>(null);
  const tables = assistantTables(session);
  const columns = tables.flatMap((table) =>
    table.columns.map((column) => ({
      ...column,
      label: `${table.technical_name}.${column.name}`,
    })),
  );
  const [newColumn, setNewColumn] = useState(columns[0]?.id || '');
  const canEdit = project.capabilities.edit !== false && !project.archived_at;
  const agreed = (session.draft?.mode || session.mode) === 'AGREED';
  const mutableParameters = (session.draft?.definition || session.definition)
    ?.contract.mutable_parameters;
  const set = (next: AssistantQuery) => {
    setQuery(next);
    setOrigin('builder');
    setDirty(true);
  };
  const save = async () => {
    setError(null);
    try {
      const draft =
        (origin || surface) === 'sql'
          ? {
              mode: 'manual',
              sql,
              parameters: JSON.parse(parameters) as unknown,
              ...((initialQuery as { timezone?: string } | null)?.timezone ||
              session.execution_timezone
                ? {
                    timezone:
                      (initialQuery as { timezone?: string } | null)
                        ?.timezone || session.execution_timezone,
                  }
                : {}),
            }
          : query;
      await send('save_draft', {
        query: draft,
        base_query_artifact_id: project.artifact_ids.query,
      });
      setDirty(false);
      setOrigin(null);
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : 'Черновик не сохранён.',
      );
    }
  };
  const discard = async () => {
    try {
      await send('discard_draft');
      setDirty(false);
      setOrigin(null);
      setQuery(
        session.structured_query ||
          (project.artifacts?.query && !('mode' in project.artifacts.query)
            ? structuredClone(
                project.artifacts.query as unknown as AssistantQuery,
              )
            : null),
      );
      setSql(project.compilation?.result.sql || '');
    } catch {
      /* Parent renders the operation error. */
    }
  };
  if (
    !project.artifact_ids.schema ||
    (!project.artifact_ids.query && !session.draft)
  )
    return (
      <div className="sa-panel">
        <h2>
          {surface === 'sql'
            ? 'SQL появится после подготовки запроса'
            : 'Конструктор появится после выбора данных'}
        </h2>
        <p>
          Продолжите диалог: примите смысл вопроса, источники и условия расчёта.
        </p>
      </div>
    );
  return (
    <div
      className="sa-panel"
      role="tabpanel"
      id={`sa-panel-${surface}`}
      aria-labelledby={`sa-tab-${surface}`}
    >
      <div className="sa-toolbar">
        <h2>
          {surface === 'sql' ? 'SQL текущего расчёта' : 'Конструктор расчёта'}
        </h2>
        <p className="text-xs text-text-secondary">
          {dirty
            ? 'Есть несохранённые изменения'
            : session.draft
              ? 'Черновик сохранён на сервере'
              : 'Исходная версия'}
        </p>
      </div>
      <p className="mb-5 text-sm text-text-secondary">
        Изменение расчёта создаёт черновик. Предыдущий результат остаётся связан
        со своей версией. Скрытие столбца в таблице не удаляет группировку.
      </p>
      {editingOther && (
        <p className="mb-4 text-sm" role="status">
          Есть изменения {origin === 'builder' ? 'конструктора' : 'SQL'}.
          Проверка и сохранение применит именно их; показанная здесь версия
          обновится после сохранения. Для продолжения ввода вернитесь на
          исходную вкладку.
        </p>
      )}
      {surface === 'builder' ? (
        !query ? (
          <p>
            Текущий SQL содержит исследовательские выражения. Обратимое
            преобразование в конструктор не подтверждено. Продолжите в SQL или в
            чате; сохранённый результат останется доступен.
          </p>
        ) : (
          <div className="space-y-6">
            <fieldset disabled={agreed || !canEdit || busy || editingOther}>
              <h3 className="mb-3 font-medium">Поля и группировка</h3>
              <div className="sa-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Поле</th>
                      <th>Расчёт</th>
                      <th>Название</th>
                      <th>Действие</th>
                    </tr>
                  </thead>
                  <tbody>
                    {query.select.map((item, index) => (
                      <tr key={item.id}>
                        <td>
                          {columns.find(
                            (column) => column.id === item.column_id,
                          )?.label || item.column_id}
                        </td>
                        <td>
                          <select
                            aria-label={`Расчёт поля ${index + 1}`}
                            value={item.kind}
                            disabled={!canEdit || busy}
                            onChange={(event) =>
                              set({
                                ...query,
                                select: query.select.map((value, i) =>
                                  i === index
                                    ? {
                                        ...value,
                                        kind: event.target
                                          .value as typeof item.kind,
                                        grain:
                                          event.target.value === 'date_bucket'
                                            ? 'month'
                                            : null,
                                      }
                                    : value,
                                ),
                              })
                            }
                          >
                            {Object.entries(Aggregations).map(
                              ([value, label]) => (
                                <option key={value} value={value}>
                                  {label}
                                </option>
                              ),
                            )}
                          </select>
                          {item.kind === 'date_bucket' && (
                            <select
                              aria-label={`Период поля ${index + 1}`}
                              value={item.grain || 'month'}
                              disabled={!canEdit || busy}
                              onChange={(event) =>
                                set({
                                  ...query,
                                  select: query.select.map((value, i) =>
                                    i === index
                                      ? {
                                          ...value,
                                          grain: event.target
                                            .value as typeof item.grain,
                                        }
                                      : value,
                                  ),
                                })
                              }
                            >
                              {Object.entries({
                                day: 'День',
                                week: 'Неделя',
                                month: 'Месяц',
                                quarter: 'Квартал',
                                year: 'Год',
                              }).map(([value, label]) => (
                                <option key={value} value={value}>
                                  {label}
                                </option>
                              ))}
                            </select>
                          )}
                        </td>
                        <td>
                          <Input
                            aria-label={`Название поля ${index + 1}`}
                            value={item.alias}
                            disabled={!canEdit || busy}
                            onChange={(event) =>
                              set({
                                ...query,
                                select: query.select.map((value, i) =>
                                  i === index
                                    ? { ...value, alias: event.target.value }
                                    : value,
                                ),
                              })
                            }
                          />
                        </td>
                        <td>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={
                              !canEdit || busy || query.select.length < 2
                            }
                            onClick={() =>
                              set({
                                ...query,
                                select: query.select.filter(
                                  (_, i) => i !== index,
                                ),
                                order_by: query.order_by.filter(
                                  (order) => order.select_item_id !== item.id,
                                ),
                              })
                            }
                          >
                            Исключить из расчёта
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="sa-result-tools">
                <label>
                  Поле источника
                  <select
                    value={newColumn}
                    onChange={(event) => setNewColumn(event.target.value)}
                  >
                    {columns.map((column) => (
                      <option key={column.id} value={column.id}>
                        {column.label}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  variant="outline"
                  disabled={!newColumn || !canEdit || busy}
                  onClick={() => {
                    const column = columns.find(
                      (item) => item.id === newColumn,
                    )!;
                    set({
                      ...query,
                      select: [
                        ...query.select,
                        {
                          id: `field_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`,
                          column_id: column.id,
                          kind: 'column',
                          alias: `${column.name}_${query.select.length + 1}`,
                          grain: null,
                        },
                      ],
                    });
                  }}
                >
                  Добавить поле
                </Button>
              </div>
              <p className="text-xs text-text-secondary">
                Поля без агрегации определяют зерно результата. Удаление такого
                поля изменит группировку и пересчитает показатели по источнику.
              </p>
            </fieldset>
            <AssistantFilters
              query={query}
              columns={columns}
              scope="WHERE"
              disabled={!canEdit || busy || editingOther}
              structureLocked={agreed}
              mutableParameters={mutableParameters}
              onChange={set}
            />
            <AssistantFilters
              query={query}
              columns={columns}
              scope="HAVING"
              disabled={!canEdit || busy || editingOther}
              structureLocked={agreed}
              mutableParameters={[]}
              onChange={set}
            />
            <details>
              <summary>Соединения и их основания</summary>
              {query.joins.length ? (
                query.joins.map((join) => (
                  <p key={join.id} className="my-2 text-sm">
                    {join.join_type}: {join.description} · {join.left_column_id}{' '}
                    = {join.right_column_id}
                  </p>
                ))
              ) : (
                <p>Соединений нет.</p>
              )}
              <p className="mt-2 text-xs text-text-secondary">
                Новый путь соединения обсуждается в чате и проверяется по
                каталогу. Lineage не считается условием JOIN.
              </p>
            </details>
            <label className="block max-w-xs">
              Ограничение выдачи
              <Input
                type="number"
                min={1}
                max={10000}
                value={query.row_limit}
                disabled={!canEdit || busy || editingOther}
                onChange={(event) =>
                  set({ ...query, row_limit: Number(event.target.value) })
                }
              />
              <span className="text-xs text-text-secondary">
                Не ограничивает объём сканирования БД.
              </span>
            </label>
          </div>
        )
      ) : (
        <div className="space-y-4">
          {agreed && (
            <p className="text-sm">
              SQL согласованного расчёта доступен для просмотра. Для ручной
              правки выберите «Перейти к исследовательской правке».
            </p>
          )}
          <label className="block">
            Исследовательский SQL
            <Textarea
              aria-label="Исследовательский SQL"
              value={sql}
              onChange={(event) => {
                setSql(event.target.value);
                setOrigin('sql');
                setDirty(true);
              }}
              rows={12}
              maxLength={20000}
              className="mt-2 font-mono text-xs"
              disabled={agreed || !canEdit || busy || editingOther}
            />
          </label>
          <label className="block">
            Типизированные параметры (JSON)
            <Textarea
              aria-label="Параметры SQL"
              value={parameters}
              onChange={(event) => {
                setParameters(event.target.value);
                setOrigin('sql');
                setDirty(true);
              }}
              rows={5}
              maxLength={10000}
              className="mt-2 font-mono text-xs"
              disabled={agreed || !canEdit || busy || editingOther}
            />
          </label>
          <p className="text-xs text-text-secondary">
            Ручная правка остаётся исследовательским кандидатом. Проверка
            структуры не подтверждает бизнес-формулу.
          </p>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-4">
          {error}
        </p>
      )}
      {canEdit && (
        <div className="mt-5 flex flex-wrap gap-3">
          <Button
            disabled={
              busy ||
              !dirty ||
              ((origin || surface) === 'builder' && !query) ||
              ((origin || surface) === 'sql' && agreed)
            }
            onClick={() => void save()}
          >
            Проверить и сохранить черновик
          </Button>
          <Button
            variant="outline"
            disabled={busy || (!dirty && !session.draft)}
            onClick={() => void discard()}
          >
            Отменить изменения
          </Button>
        </div>
      )}
      {session.draft && (
        <section className="mt-6 border-t border-border-default pt-5">
          <h3 className="font-medium">Изменения перед применением</h3>
          <p className="my-2 text-sm">
            Текущий результат не пересчитан. Изменение группировки может
            изменить число строк и показатели.
          </p>
          <div className="grid gap-3 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-xs">Исполненная / принятая версия</p>
              <pre>
                {project.compilation?.result.sql || 'Ещё не исполнялась'}
              </pre>
            </div>
            <div>
              <p className="mb-2 text-xs">Черновик</p>
              <pre>
                {session.draft.compiled.sql || 'Запрос заблокирован проверками'}
              </pre>
            </div>
          </div>
          <pre className="mt-3">
            {JSON.stringify(
              session.draft.compiled.blocking_issues ||
                session.draft.compiled.parameters,
              null,
              2,
            )}
          </pre>
          {canEdit && (
            <Button
              className="mt-3"
              disabled={
                busy || dirty || session.draft.compiled.status !== 'READY'
              }
              onClick={() => {
                void send('apply_draft', {
                  confirmed: true,
                  execute: project.capabilities.execute !== false,
                }).catch(() => undefined);
              }}
            >
              {project.capabilities.execute === false
                ? 'Применить проверенный запрос'
                : 'Проверить и пересчитать'}
            </Button>
          )}
        </section>
      )}
    </div>
  );
}
