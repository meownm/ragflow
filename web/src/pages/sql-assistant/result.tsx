import { Button } from '@/components/ui/button';
import { useAssistantResult } from '@/hooks/use-sql-assistant-request';
import type {
  AssistantEffect,
  AssistantResult,
  AssistantSession,
  AssistantView,
} from '@/interfaces/database/sql-assistant';
import { SqlExportButton } from '@/pages/business-documents/constructor/sql-export-button';
import { useSqlResultDeadline } from '@/pages/business-documents/constructor/sql-result-deadline';
import dayjs from 'dayjs';
import { useRef, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { DefinitionDetails, DefinitionStatus } from './definitions';
import { ResultBuilder } from './result-builder';
import { cellText } from './result-format';
import type { SendCommand } from './workspace';

const RunLabels: Record<string, string> = {
  QUEUED: 'В очереди',
  RUNNING: 'Выполняется',
  CANCEL_REQUESTED: 'Останавливается',
  READY: 'Готов',
  FAILED: 'Ошибка',
  EXPIRED: 'Срок хранения истёк',
  PURGED: 'Данные удалены',
  UNKNOWN: 'Исход неизвестен',
  SUCCEEDED: 'SQL завершён, строки недоступны',
  CANCELLED: 'Выполнение отменено',
};
export function AssistantResultPanel({
  actor,
  session,
  send,
  busy,
  onDrill,
  onNewMetric,
}: {
  actor: string;
  session: AssistantSession;
  send: SendCommand;
  busy: boolean;
  onNewMetric?: () => void;
  onDrill: (row: {
    row_index: number;
    dimension: string;
    label: string;
  }) => void;
}) {
  const runs =
    session.project.runs ||
    (session.project.latest_run ? [session.project.latest_run] : []);
  return (
    <section className="sa-panel" aria-label="Результаты исследования">
      <h2>Результаты и история</h2>
      {!runs.length ? (
        <p className="text-sm text-text-secondary">
          Результатов пока нет. Подготовьте запрос и выполните расчёт.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2 mb-5">
          {runs.map((run, index) => (
            <Button
              key={run.id}
              variant={
                session.selected_result_id === run.id ? 'default' : 'outline'
              }
              size="sm"
              disabled={busy || run.status !== 'READY'}
              aria-pressed={session.selected_result_id === run.id}
              onClick={() => {
                void send('select_result', { run_id: run.id }).catch(
                  () => undefined,
                );
              }}
            >
              R{runs.length - index} ·{' '}
              {RunLabels[run.status] || 'Исход неизвестен'}
              {run.status === 'READY' ? ` · ${run.row_count} строк` : ''}
            </Button>
          ))}
        </div>
      )}
      {session.selected_result_id ? (
        <SelectedResult
          key={session.selected_result_id}
          actor={actor}
          session={session}
          send={send}
          busy={busy}
          onDrill={onDrill}
          onNewMetric={onNewMetric}
        />
      ) : (
        runs.some((run) => run.status === 'READY') && (
          <p className="text-sm">
            Расчёт завершён. Выберите результат в истории; поздний ответ не
            переключает выбранный контекст.
          </p>
        )
      )}
    </section>
  );
}

function SelectedResult({
  actor,
  session,
  send,
  busy,
  onDrill,
  onNewMetric,
}: {
  actor: string;
  session: AssistantSession;
  send: SendCommand;
  busy: boolean;
  onNewMetric?: () => void;
  onDrill: (row: {
    row_index: number;
    dimension: string;
    label: string;
  }) => void;
}) {
  const [offset, setOffset] = useState(0);
  const result = useAssistantResult(
    actor,
    session.id,
    session.selected_result_id,
    offset,
  );
  const lifetime = useSqlResultDeadline(
    session.selected_result_id || '',
    result.data?.expires_at,
    () => {
      void result.refetch();
    },
  );
  if (result.isPending)
    return (
      <p role="status">Проверяем права и загружаем сохранённый результат…</p>
    );
  if (result.isError || lifetime.expired)
    return (
      <div role="alert">
        <p>
          Результат недоступен: проверьте права, срок хранения или подключение.
          Это не пустая выборка. Автоматический повтор SQL не выполняется.
        </p>
        <Button
          variant="outline"
          className="mt-3"
          onClick={() => void result.refetch()}
        >
          Проверить доступность
        </Button>
      </div>
    );
  return (
    <ResultData
      result={result.data}
      session={session}
      send={send}
      busy={busy}
      offset={offset}
      setOffset={setOffset}
      onDrill={onDrill}
      onNewMetric={onNewMetric}
    />
  );
}

function ResultData({
  result,
  session,
  send,
  busy,
  offset,
  setOffset,
  onDrill,
  onNewMetric,
}: {
  result: AssistantResult;
  session: AssistantSession;
  send: SendCommand;
  busy: boolean;
  offset: number;
  setOffset: (offset: number) => void;
  onNewMetric?: () => void;
  onDrill: (row: {
    row_index: number;
    dimension: string;
    label: string;
  }) => void;
}) {
  const [analysisColumn, setAnalysisColumn] = useState(
    result.columns.find((_, index) =>
      result.rows.some((row) => typeof row.values[index] === 'number'),
    )?.id ||
      result.columns[0]?.id ||
      '',
  );
  const [baselineColumn, setBaselineColumn] = useState(
    result.columns[1]?.id || '',
  );
  const dimensions = result.columns.filter(
    (column) => column.drillable !== false,
  );
  const [dimension, setDimension] = useState(dimensions[0]?.id || '');
  const [effect, setEffect] = useState<AssistantEffect | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const latestAnalysis = useRef(0);
  const view = result.view;
  const positions = new Map(
    result.columns.map((column, index) => [column.id, index]),
  );
  const visible = view.column_order.filter((id) => !view.hidden.includes(id));
  const columnName = (id: string) =>
    view.labels[id] ||
    result.columns.find((column) => column.id === id)?.name ||
    id;
  const update = (next: AssistantView) => {
    void send('update_view', {
      run_id: result.run_id,
      expected_view_revision: result.view_revision,
      view: next,
    })
      .then(() => setOffset(0))
      .catch(() => undefined);
  };
  const analyze = async (kind: 'compare' | 'analyze') => {
    const sequence = ++latestAnalysis.current;
    setAnalysisError(null);
    try {
      const next = await send(kind, {
        run_id: result.run_id,
        columns:
          kind === 'compare'
            ? [analysisColumn, baselineColumn]
            : [analysisColumn],
      });
      if (latestAnalysis.current === sequence) setEffect(next);
    } catch {
      if (latestAnalysis.current === sequence) {
        setEffect(null);
        setAnalysisError(
          'Анализ не выполнен. Нужны числовые колонки; для отклонений — не менее пяти наблюдений.',
        );
      }
    }
  };
  const appearance = view.appearance || {
    display: view.chart ? 'both' : 'table',
    density: 'comfortable',
    striped: false,
    wrap: false,
  };
  const chart = appearance.display === 'table' ? null : view.chart;
  const chartData = chart
    ? result.rows
        .map((row) => ({
          x: String(row.values[positions.get(chart.x)!] ?? 'NULL'),
          y:
            row.values[positions.get(chart.y)!] === null
              ? null
              : Number(row.values[positions.get(chart.y)!]),
        }))
        .filter((row) => row.y === null || Number.isFinite(row.y))
    : [];
  return (
    <div data-testid="sql-assistant-result" className="space-y-4">
      <div className="sa-toolbar">
        <div>
          <p className="text-sm font-medium">
            Сохранённый результат ·{' '}
            {dayjs(result.created_at).format('DD.MM.YYYY HH:mm:ss')}
          </p>
          <p className="text-xs text-text-secondary">
            {result.row_count} строк · {result.duration_ms} мс · хранится до{' '}
            {dayjs(result.expires_at).format('DD.MM.YYYY HH:mm')}
          </p>
        </div>
        <SqlExportButton
          projectId={session.id}
          stateVersion={session.project.state_version}
          command={{
            schema_version: '1',
            kind: 'RESULT',
            format: 'json',
            run_id: result.run_id,
          }}
          label="Скачать результат"
        />
      </div>
      <div className="sa-statuses">
        <div>
          <strong>Выполнение</strong>Завершено
        </div>
        <div>
          <strong>Выдача</strong>
          {result.output_completeness === 'FULL'
            ? 'Сохранена полностью'
            : 'Ограничена лимитом'}
        </div>
        <div>
          <strong>Источник</strong>
          {result.source_completeness === 'COMPLETE'
            ? 'Полнота периода подтверждена'
            : result.source_completeness === 'PARTIAL'
              ? 'Период загружен частично'
              : result.source_completeness === 'STALE'
                ? 'Данные устарели'
                : 'Полнота загрузки неизвестна'}
        </div>
        <div>
          <strong>Определение</strong>
          <DefinitionStatus value={result} />
        </div>
      </div>
      {result.catalog_changed && (
        <p role="status">
          Каталог изменился после расчёта. Показаны сохранённые данные; перед
          новым запуском схема проверяется заново.
        </p>
      )}
      {result.definition && (
        <DefinitionDetails definition={result.definition} />
      )}
      {Boolean(result.view_transfer_removed) && (
        <p role="status">
          Часть настроек представления не перенесена: изменились зависимости
          колонок ({result.view_transfer_removed}). Проверьте видимость, формат
          и фильтры нового результата.
        </p>
      )}
      {result.query_artifact_id !== session.project.artifact_ids.query && (
        <p role="status">
          Это результат предыдущей версии. Текущие изменения расчёта к нему не
          применены.
        </p>
      )}
      <ResultBuilder
        result={result}
        busy={busy}
        onNewMetric={onNewMetric}
        onSave={async (next, revision) => {
          await send('update_view', {
            run_id: result.run_id,
            expected_view_revision: revision,
            view: next,
          });
          setOffset(0);
        }}
      />
      {Boolean(result.metrics?.length) && (
        <section
          className="sa-result-metrics"
          aria-label="Показатели сохранённого набора"
        >
          <p className="text-xs text-text-secondary">
            По сохранённому набору после фильтров · {result.filtered_count}{' '}
            строк.
            {result.output_completeness === 'LIMITED'
              ? ' Набор ограничен лимитом.'
              : ''}{' '}
            Полнота источника отдельно от сохранённой выдачи.
          </p>
          <div className="sa-metric-grid">
            {result.metrics?.map((metric) => (
              <article key={metric.id}>
                <p className="text-sm text-text-secondary">{metric.title}</p>
                <p className="sa-metric-value">
                  {metric.status === 'OK'
                    ? `${metric.rounded ? '≈ ' : ''}${cellText(metric.value)}`
                    : metric.status === 'NULL'
                      ? 'NULL'
                      : metric.status === 'FILTERED_OUT'
                        ? 'Строка скрыта фильтром'
                        : metric.status === 'OUT_OF_RANGE'
                          ? 'Слишком высокая точность для сводки'
                          : metric.status === 'NON_NUMERIC'
                            ? 'Есть нечисловые значения'
                            : 'Требуется новый SQL'}
                </p>
                {metric.rounded && (
                  <p className="text-xs">
                    Среднее округлено до доступной десятичной точности.
                  </p>
                )}
              </article>
            ))}
          </div>
        </section>
      )}
      {appearance.display !== 'chart' && (
        <>
          {result.row_count === 0 ? (
            <p>
              SQL завершился без строк. Условия не расширялись автоматически.
            </p>
          ) : result.filtered_count === 0 ? (
            <p>
              В сохранённом результате нет строк по фильтру отображения. Снимите
              фильтр, чтобы вернуться к набору.
            </p>
          ) : (
            <div
              className={`sa-table-scroll sa-result-table ${appearance.density} ${appearance.striped ? 'sa-striped' : ''} ${appearance.wrap ? 'sa-wrap' : ''}`}
            >
              <table aria-label="Данные выбранного результата">
                <thead>
                  <tr>
                    {visible.map((id) => (
                      <th key={id}>
                        <button
                          disabled={busy}
                          onClick={() =>
                            update({
                              ...view,
                              sort: {
                                column_id: id,
                                direction:
                                  view.sort?.column_id === id &&
                                  view.sort.direction === 'ASC'
                                    ? 'DESC'
                                    : 'ASC',
                              },
                            })
                          }
                        >
                          {columnName(id)}
                          {view.sort?.column_id === id
                            ? view.sort.direction === 'ASC'
                              ? ' ↑'
                              : ' ↓'
                            : ''}
                        </button>
                      </th>
                    ))}
                    <th>Продолжение</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row) => (
                    <tr key={row.index}>
                      {visible.map((id) => (
                        <td key={id}>
                          {cellText(
                            row.values[positions.get(id)!],
                            view.formats[id],
                          )}
                        </td>
                      ))}
                      <td>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={
                            busy ||
                            !dimension ||
                            session.project.capabilities.edit === false
                          }
                          onClick={() =>
                            onDrill({
                              row_index: row.index,
                              dimension,
                              label: `${columnName(dimension)} = ${cellText(row.values[positions.get(dimension)!])}`,
                            })
                          }
                        >
                          Детализировать
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="sa-result-tools">
            <label>
              Измерение для детализации
              <select
                value={dimension}
                onChange={(event) => setDimension(event.target.value)}
              >
                {dimensions.map((column) => (
                  <option key={column.id} value={column.id}>
                    {columnName(column.id)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </>
      )}
      <div className="sa-toolbar">
        <p className="text-xs text-text-secondary">
          Показано{' '}
          {result.rows.length
            ? `${offset + 1}–${offset + result.rows.length}`
            : '0'}{' '}
          из {result.filtered_count} строк сохранённого набора.
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={busy || offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 100))}
          >
            Назад
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || offset + 100 >= result.filtered_count}
            onClick={() => setOffset(offset + 100)}
          >
            Далее
          </Button>
        </div>
      </div>
      {chart && (
        <div className="sa-panel">
          <h3 className="font-medium mb-3">
            График страницы · без новой агрегации
          </h3>
          <p className="mb-4 text-xs text-text-secondary">
            Показана текущая страница ({result.rows.length} строк). Она не
            представляет весь результат при наличии следующих страниц.
          </p>
          <ResponsiveContainer width="100%" height={260}>
            {chart.kind === 'line' ? (
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="x" />
                <YAxis />
                <Tooltip />
                <Line dataKey="y" stroke="rgb(var(--accent-primary))" />
              </LineChart>
            ) : (
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="x" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="y" fill="rgb(var(--accent-primary))" />
              </BarChart>
            )}
          </ResponsiveContainer>
        </div>
      )}
      <details>
        <summary>Сравнение значений и отклонения</summary>
        <p className="text-sm text-text-secondary">
          Числа рассчитываются на сервере по сохранённым строкам. Для сравнения
          периодов сначала получите обе сопоставимые колонки через SQL.
        </p>
        <div className="sa-result-tools">
          <label>
            Числовая колонка
            <select
              value={analysisColumn}
              onChange={(event) => setAnalysisColumn(event.target.value)}
            >
              {result.columns.map((column) => (
                <option key={column.id} value={column.id}>
                  {columnName(column.id)}
                </option>
              ))}
            </select>
          </label>
          <label>
            База сравнения
            <select
              value={baselineColumn}
              onChange={(event) => setBaselineColumn(event.target.value)}
            >
              {result.columns.map((column) => (
                <option key={column.id} value={column.id}>
                  {columnName(column.id)}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="outline"
            disabled={busy || !baselineColumn}
            onClick={() => void analyze('compare')}
          >
            Рассчитать Δ
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void analyze('analyze')}
          >
            Найти отклонения
          </Button>
        </div>
        {analysisError && <p role="alert">{analysisError}</p>}
        {effect && (
          <div>
            <p className="text-xs text-text-secondary">
              Область:{' '}
              {effect.scope === 'LIMITED'
                ? 'ограниченный сохранённый набор'
                : 'весь сохранённый набор'}{' '}
              · полнота источника неизвестна.
            </p>
            <AnalysisOutput effect={effect} />
          </div>
        )}
      </details>
      <details>
        <summary>SQL, параметры и основания выбранного результата</summary>
        <pre>{result.sql}</pre>
        <pre className="mt-3">{JSON.stringify(result.parameters, null, 2)}</pre>
        <p className="text-sm whitespace-pre-wrap my-3">
          {result.history.requirements?.text}
        </p>
        <p className="text-xs text-text-secondary">
          Проверки структуры и границ результата: пройдены. Проверки
          бизнес-формулы и полноты загрузки: не выполнялись.
        </p>
        <ul className="my-3 text-sm">
          {result.history.schema?.snapshot.tables.map((table) => (
            <li key={table.id}>
              {table.fqn} · версия {table.version}
            </li>
          ))}
        </ul>
        <details>
          <summary>Технические сведения</summary>
          <pre>
            {JSON.stringify(
              {
                run_id: result.run_id,
                compilation_id: result.compilation_id,
                query_artifact_id: result.query_artifact_id,
                sql_hash: result.sql_hash,
                checks: result.checks,
              },
              null,
              2,
            )}
          </pre>
        </details>
        <SqlExportButton
          projectId={session.id}
          stateVersion={session.project.state_version}
          command={{
            schema_version: '1',
            kind: 'QUERY',
            format: 'json',
            compilation_id: result.compilation_id,
            query_artifact_id: result.query_artifact_id,
          }}
          label="Скачать SQL и параметры"
        />
      </details>
    </div>
  );
}

function AnalysisOutput({ effect }: { effect: AssistantEffect }) {
  if (effect.kind === 'compare') {
    const rows = effect.data as Array<{
      row_index: number;
      current: string | null;
      baseline: string | null;
      delta: string | null;
      percent: string | null;
      status: string;
    }>;
    return (
      <div className="sa-table-scroll mt-3">
        <table aria-label="Арифметическое сравнение колонок">
          <thead>
            <tr>
              <th>Строка</th>
              <th>Текущее значение</th>
              <th>База</th>
              <th>Изменение</th>
              <th>Изменение, %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.row_index}>
                <td>{row.row_index + 1}</td>
                <td>{row.current ?? 'Нет значения'}</td>
                <td>{row.baseline ?? 'Нет значения'}</td>
                <td>{row.delta ?? 'Не рассчитано'}</td>
                <td>
                  {row.status === 'ZERO_BASELINE'
                    ? 'Нулевая база — процент не определён'
                    : (row.percent ?? 'Нет данных')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  const data = effect.data as {
    median: string;
    mad: string;
    sample_count: number;
    missing_count: number;
    observations: Array<{
      row_index: number;
      value: string;
      score: string | null;
    }>;
    limitations: string[];
  };
  return (
    <section className="mt-3 space-y-3">
      <h3 className="font-medium">Отклонения от медианы</h3>
      <p className="text-sm">
        Наблюдений: {data.sample_count}; пропусков: {data.missing_count}.
        Медиана: {data.median}; медианное абсолютное отклонение: {data.mad}.
      </p>
      {data.observations.length ? (
        <div className="sa-table-scroll">
          <table>
            <thead>
              <tr>
                <th>Строка</th>
                <th>Значение</th>
                <th>Оценка отклонения</th>
              </tr>
            </thead>
            <tbody>
              {data.observations.map((row) => (
                <tr key={row.row_index}>
                  <td>{row.row_index + 1}</td>
                  <td>{row.value}</td>
                  <td>
                    {row.score ??
                      'MAD равен нулю; значение отличается от медианы'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>По выбранному методу отклонений не обнаружено.</p>
      )}
      <ul className="list-disc pl-5 text-sm text-text-secondary">
        {data.limitations.map((limit) => (
          <li key={limit}>{limit}</li>
        ))}
      </ul>
    </section>
  );
}
