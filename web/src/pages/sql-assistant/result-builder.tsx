import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type {
  AssistantResult,
  AssistantResultMetric,
  AssistantView,
} from '@/interfaces/database/sql-assistant';
import { useEffect, useState } from 'react';
import { cellText } from './result-format';

const MetricLabels = {
  value: 'Значение ячейки',
  rows: 'Количество строк',
  sum: 'Сумма',
  avg: 'Среднее',
  min: 'Минимум',
  max: 'Максимум',
  count: 'Непустые значения',
  distinct: 'Уникальные значения',
};
const FilterLabels = {
  eq: 'Равно',
  ne: 'Не равно',
  contains: 'Содержит',
  gt: 'Больше',
  gte: 'Не меньше',
  lt: 'Меньше',
  lte: 'Не больше',
  is_null: 'Нет значения (NULL)',
  is_not_null: 'Есть значение',
};
function initialResultView(result: AssistantResult): AssistantView {
  return {
    column_order: result.columns.map((column) => column.id),
    hidden: [],
    labels: {},
    formats: {},
    filters: [],
    sort: null,
    chart: null,
    appearance: {
      display: 'table',
      density: 'comfortable',
      striped: false,
      wrap: false,
    },
    metrics: [],
  };
}
function normalize(result: AssistantResult): AssistantView {
  return { ...initialResultView(result), ...result.view };
}

export function ResultBuilder({
  result,
  busy,
  onSave,
  onNewMetric,
}: {
  result: AssistantResult;
  busy: boolean;
  onSave: (view: AssistantView, revision: number) => Promise<void>;
  onNewMetric?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState('fields');
  const [draft, setDraft] = useState(() => normalize(result));
  const [revision, setRevision] = useState(result.view_revision);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const conflict = result.view_revision > revision;
  useEffect(() => {
    if (!dirty && result.view_revision >= revision) {
      setDraft(normalize(result));
      setRevision(result.view_revision);
    }
  }, [result, revision, dirty]);
  const change = (next: AssistantView) => {
    setDraft(next);
    setDirty(true);
    setSaved(false);
    setError('');
  };
  const reset = () => {
    setDraft(normalize(result));
    setRevision(result.view_revision);
    setDirty(false);
    setError('');
    setSaved(false);
  };
  const label = (id: string) =>
    draft.labels[id] ||
    result.columns.find((column) => column.id === id)?.name ||
    id;
  const patchMetric = (index: number, patch: Partial<AssistantResultMetric>) =>
    change({
      ...draft,
      metrics: draft.metrics?.map((metric, position) =>
        position === index ? { ...metric, ...patch } : metric,
      ),
    });
  const save = async () => {
    try {
      await onSave(draft, revision);
      setRevision(revision + 1);
      setDirty(false);
      setSaved(true);
      setError('');
    } catch {
      setError(
        'Настройки не сохранены. Проверьте поля, доступ и актуальность результата. Черновик остаётся в конструкторе.',
      );
    }
  };
  return (
    <section className="sa-result-builder" aria-label="Конструктор результата">
      <div className="sa-toolbar">
        <Button
          type="button"
          variant="outline"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? 'Свернуть настройки' : 'Настроить результат'}
        </Button>
      </div>
      {open && (
        <>
          <nav className="sa-builder-tabs" aria-label="Настройки результата">
            {[
              ['fields', 'Поля'],
              ['filters', 'Фильтры и сортировка'],
              ['appearance', 'Внешний вид'],
              ['metrics', 'Метрики'],
            ].map(([key, title]) => (
              <Button
                key={key}
                type="button"
                size="sm"
                variant={tab === key ? 'default' : 'ghost'}
                aria-pressed={tab === key}
                onClick={() => setTab(key)}
              >
                {title}
              </Button>
            ))}
          </nav>
          <fieldset disabled={busy} className="sa-builder-controls">
            {tab === 'fields' && (
              <div className="space-y-3">
                {draft.column_order.map((id, index) => (
                  <div key={id} className="sa-builder-field">
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        aria-label={`Показывать ${result.columns.find((column) => column.id === id)?.name}`}
                        checked={!draft.hidden.includes(id)}
                        disabled={
                          busy ||
                          (!draft.hidden.includes(id) &&
                            draft.hidden.length === result.columns.length - 1)
                        }
                        onChange={() =>
                          change({
                            ...draft,
                            hidden: draft.hidden.includes(id)
                              ? draft.hidden.filter((item) => item !== id)
                              : [...draft.hidden, id],
                          })
                        }
                      />
                      {result.columns.find((column) => column.id === id)?.name}
                    </label>
                    <label>
                      Название
                      <Input
                        aria-label={`Название ${result.columns.find((column) => column.id === id)?.name}`}
                        maxLength={120}
                        value={
                          draft.labels[id] ??
                          result.columns.find((column) => column.id === id)
                            ?.name
                        }
                        onChange={(event) =>
                          change({
                            ...draft,
                            labels: {
                              ...draft.labels,
                              [id]: event.target.value,
                            },
                          })
                        }
                      />
                    </label>
                    <label>
                      Формат
                      <select
                        aria-label={`Формат ${label(id)}`}
                        value={draft.formats[id] || 'raw'}
                        onChange={(event) =>
                          change({
                            ...draft,
                            formats: {
                              ...draft.formats,
                              [id]: event.target
                                .value as AssistantView['formats'][string],
                            },
                          })
                        }
                      >
                        {[
                          ['raw', 'Исходное значение'],
                          ['number', 'Число'],
                          ['currency', 'Рубли (RUB)'],
                          ['percent', 'Доля → проценты'],
                        ].map(([value, title]) => (
                          <option value={value} key={value}>
                            {title}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="flex gap-1">
                      {[-1, 1].map((direction) => (
                        <Button
                          key={direction}
                          type="button"
                          size="sm"
                          variant="outline"
                          aria-label={`${direction < 0 ? 'Раньше' : 'Позже'} ${label(id)}`}
                          disabled={
                            busy ||
                            index + direction < 0 ||
                            index + direction >= draft.column_order.length
                          }
                          onClick={() => {
                            const order = [...draft.column_order];
                            [order[index], order[index + direction]] = [
                              order[index + direction],
                              order[index],
                            ];
                            change({ ...draft, column_order: order });
                          }}
                        >
                          {direction < 0 ? '↑' : '↓'}
                        </Button>
                      ))}
                    </div>
                  </div>
                ))}
                <p className="text-xs text-text-secondary">
                  RUB не конвертирует валюту. Проценты предназначены для долей
                  от 0 до 1. Высокоточные строки остаются исходными.
                </p>
              </div>
            )}
            {tab === 'filters' && (
              <div className="space-y-3">
                <p className="text-xs">
                  Все условия соединяются через И и применяются ко всем
                  сохранённым страницам.
                </p>
                {draft.filters.map((filter, index) => (
                  <div className="sa-builder-filter" key={index}>
                    <label>
                      Поле
                      <select
                        aria-label={`Поле фильтра ${index + 1}`}
                        value={filter.column_id}
                        onChange={(event) =>
                          change({
                            ...draft,
                            filters: draft.filters.map((item, i) =>
                              i === index
                                ? { ...item, column_id: event.target.value }
                                : item,
                            ),
                          })
                        }
                      >
                        {result.columns.map((column) => (
                          <option key={column.id} value={column.id}>
                            {label(column.id)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Условие
                      <select
                        aria-label={`Условие фильтра ${index + 1}`}
                        value={filter.operator}
                        onChange={(event) =>
                          change({
                            ...draft,
                            filters: draft.filters.map((item, i) =>
                              i === index
                                ? {
                                    ...item,
                                    operator: event.target.value,
                                    value: ['is_null', 'is_not_null'].includes(
                                      event.target.value,
                                    )
                                      ? null
                                      : '',
                                  }
                                : item,
                            ),
                          })
                        }
                      >
                        {Object.entries(FilterLabels).map(([value, title]) => (
                          <option key={value} value={value}>
                            {title}
                          </option>
                        ))}
                      </select>
                    </label>
                    {!['is_null', 'is_not_null'].includes(filter.operator) && (
                      <label>
                        Значение
                        <Input
                          aria-label={`Значение фильтра ${index + 1}`}
                          value={String(filter.value ?? '')}
                          maxLength={1000}
                          onChange={(event) =>
                            change({
                              ...draft,
                              filters: draft.filters.map((item, i) =>
                                i === index
                                  ? { ...item, value: event.target.value }
                                  : item,
                              ),
                            })
                          }
                        />
                      </label>
                    )}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Удалить фильтр ${index + 1}`}
                      onClick={() =>
                        change({
                          ...draft,
                          filters: draft.filters.filter((_, i) => i !== index),
                        })
                      }
                    >
                      Удалить
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  disabled={
                    busy || draft.filters.length >= 20 || !result.columns.length
                  }
                  onClick={() =>
                    change({
                      ...draft,
                      filters: [
                        ...draft.filters,
                        {
                          column_id: result.columns[0].id,
                          operator: 'contains',
                          value: '',
                        },
                      ],
                    })
                  }
                >
                  Добавить фильтр
                </Button>
                <div className="sa-result-tools">
                  <label>
                    Сортировка
                    <select
                      aria-label="Поле сортировки"
                      value={draft.sort?.column_id || ''}
                      onChange={(event) =>
                        change({
                          ...draft,
                          sort: event.target.value
                            ? {
                                column_id: event.target.value,
                                direction: draft.sort?.direction || 'ASC',
                              }
                            : null,
                        })
                      }
                    >
                      <option value="">Исходный порядок</option>
                      {result.columns.map((column) => (
                        <option key={column.id} value={column.id}>
                          {label(column.id)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {draft.sort && (
                    <label>
                      Порядок
                      <select
                        aria-label="Порядок сортировки"
                        value={draft.sort.direction}
                        onChange={(event) =>
                          change({
                            ...draft,
                            sort: {
                              column_id: draft.sort!.column_id,
                              direction: event.target.value as 'ASC' | 'DESC',
                            },
                          })
                        }
                      >
                        <option value="ASC">По возрастанию</option>
                        <option value="DESC">По убыванию</option>
                      </select>
                    </label>
                  )}
                </div>
              </div>
            )}
            {tab === 'appearance' && (
              <div className="sa-builder-appearance">
                <label>
                  Отображение
                  <select
                    aria-label="Отображение результата"
                    value={draft.appearance!.display}
                    onChange={(event) => {
                      const display = event.target.value as
                        | 'table'
                        | 'chart'
                        | 'both';
                      change({
                        ...draft,
                        appearance: { ...draft.appearance!, display },
                        chart:
                          display !== 'table'
                            ? draft.chart || {
                                kind: 'bar',
                                x: result.columns[0]?.id || '',
                                y:
                                  result.columns[1]?.id ||
                                  result.columns[0]?.id ||
                                  '',
                              }
                            : draft.chart,
                      });
                    }}
                  >
                    <option value="table">Таблица</option>
                    <option value="chart">График</option>
                    <option value="both">Таблица и график</option>
                  </select>
                </label>
                <label>
                  Плотность
                  <select
                    aria-label="Плотность таблицы"
                    value={draft.appearance!.density}
                    onChange={(event) =>
                      change({
                        ...draft,
                        appearance: {
                          ...draft.appearance!,
                          density: event.target.value as
                            | 'comfortable'
                            | 'compact',
                        },
                      })
                    }
                  >
                    <option value="comfortable">Обычная</option>
                    <option value="compact">Компактная</option>
                  </select>
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={draft.appearance!.striped}
                    onChange={(event) =>
                      change({
                        ...draft,
                        appearance: {
                          ...draft.appearance!,
                          striped: event.target.checked,
                        },
                      })
                    }
                  />{' '}
                  Чередование строк
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={draft.appearance!.wrap}
                    onChange={(event) =>
                      change({
                        ...draft,
                        appearance: {
                          ...draft.appearance!,
                          wrap: event.target.checked,
                        },
                      })
                    }
                  />{' '}
                  Перенос текста в ячейках
                </label>
                {draft.appearance!.display !== 'table' && (
                  <>
                    <label>
                      Тип графика
                      <select
                        aria-label="Тип графика"
                        value={draft.chart?.kind || 'bar'}
                        onChange={(event) =>
                          change({
                            ...draft,
                            chart: {
                              ...draft.chart!,
                              kind: event.target.value as 'bar' | 'line',
                            },
                          })
                        }
                      >
                        <option value="bar">Столбцы</option>
                        <option value="line">Линия</option>
                      </select>
                    </label>
                    {(['x', 'y'] as const).map((axis) => (
                      <label key={axis}>
                        Ось {axis.toUpperCase()}
                        <select
                          aria-label={`Ось ${axis.toUpperCase()}`}
                          value={draft.chart?.[axis]}
                          onChange={(event) =>
                            change({
                              ...draft,
                              chart: {
                                ...draft.chart!,
                                [axis]: event.target.value,
                              },
                            })
                          }
                        >
                          {result.columns.map((column) => (
                            <option key={column.id} value={column.id}>
                              {label(column.id)}
                            </option>
                          ))}
                        </select>
                      </label>
                    ))}
                    <p className="text-xs">
                      График показывает текущую страницу без новой группировки и
                      агрегации.
                    </p>
                  </>
                )}
              </div>
            )}
            {tab === 'metrics' && (
              <div className="space-y-3">
                <p className="text-xs text-text-secondary">
                  Показатели относятся к сохранённому набору после фильтров,
                  включая все страницы. Это не показатели всей БД. Значение
                  ячейки привязано к исходной строке; её номер можно увидеть в
                  основаниях ответа.
                </p>
                {(draft.metrics || []).map((metric, index) => (
                  <div key={metric.id} className="sa-builder-metric">
                    <label>
                      Название
                      <Input
                        aria-label={`Название метрики ${index + 1}`}
                        value={metric.title}
                        maxLength={120}
                        onChange={(event) =>
                          patchMetric(index, { title: event.target.value })
                        }
                      />
                    </label>
                    <label>
                      Показатель
                      <select
                        aria-label={`Тип метрики ${index + 1}`}
                        value={metric.kind}
                        onChange={(event) => {
                          const kind = event.target
                            .value as AssistantResultMetric['kind'];
                          patchMetric(index, {
                            kind,
                            column_id:
                              kind === 'rows'
                                ? null
                                : metric.column_id ||
                                  result.columns[0]?.id ||
                                  '',
                            row_index:
                              kind === 'value'
                                ? (result.rows[0]?.index ?? 0)
                                : null,
                          });
                        }}
                      >
                        {Object.entries(MetricLabels).map(([value, title]) => (
                          <option
                            key={value}
                            value={value}
                            disabled={
                              !result.metric_aggregation_allowed &&
                              !['rows', 'value'].includes(value)
                            }
                          >
                            {title}
                          </option>
                        ))}
                      </select>
                    </label>
                    {metric.kind !== 'rows' && (
                      <label>
                        Поле
                        <select
                          aria-label={`Поле метрики ${index + 1}`}
                          value={metric.column_id || ''}
                          onChange={(event) =>
                            patchMetric(index, {
                              column_id: event.target.value,
                            })
                          }
                        >
                          {result.columns.map((column) => (
                            <option key={column.id} value={column.id}>
                              {label(column.id)}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {metric.kind === 'value' && (
                      <label>
                        Исходная строка (с 1)
                        <Input
                          aria-label={`Строка метрики ${index + 1}`}
                          type="number"
                          min={1}
                          max={result.row_count}
                          value={(metric.row_index ?? 0) + 1}
                          onChange={(event) =>
                            patchMetric(index, {
                              row_index: Number(event.target.value) - 1,
                            })
                          }
                        />
                      </label>
                    )}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        change({
                          ...draft,
                          metrics: draft.metrics?.filter((_, i) => i !== index),
                        })
                      }
                      aria-label={`Удалить метрику ${index + 1}`}
                    >
                      Удалить
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || (draft.metrics?.length || 0) >= 8}
                  onClick={() =>
                    change({
                      ...draft,
                      metrics: [
                        ...(draft.metrics || []),
                        {
                          id: crypto.randomUUID(),
                          title: 'Строки сохранённого набора',
                          kind: 'rows',
                          column_id: null,
                          row_index: null,
                        },
                      ],
                    })
                  }
                >
                  Добавить метрику
                </Button>
                {!result.metric_aggregation_allowed && (
                  <p className="text-xs">
                    Этот SQL уже меняет зерно или агрегирует данные. Повторная
                    агрегация запрещена; доступны число строк и значения ячеек.
                  </p>
                )}
                {onNewMetric && (
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-auto whitespace-normal"
                    onClick={onNewMetric}
                  >
                    Настроить новый расчёт метрики в SQL-конструкторе
                  </Button>
                )}
              </div>
            )}
          </fieldset>
          <details className="my-3">
            <summary>Предпросмотр полей и стиля</summary>
            <p className="text-xs text-text-secondary">
              До 5 строк текущей страницы. Фильтры и метрики применятся после
              сохранения.
            </p>
            <div
              className={`sa-table-scroll sa-result-table ${draft.appearance?.density} ${draft.appearance?.striped ? 'sa-striped' : ''} ${draft.appearance?.wrap ? 'sa-wrap' : ''}`}
            >
              <table aria-label="Предпросмотр результата">
                <thead>
                  <tr>
                    {draft.column_order
                      .filter((id) => !draft.hidden.includes(id))
                      .map((id) => (
                        <th key={id}>{label(id)}</th>
                      ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.slice(0, 5).map((row) => (
                    <tr key={row.index}>
                      {draft.column_order
                        .filter((id) => !draft.hidden.includes(id))
                        .map((id) => (
                          <td key={id}>
                            {cellText(
                              row.values[
                                result.columns.findIndex(
                                  (column) => column.id === id,
                                )
                              ],
                              draft.formats[id],
                            )}
                          </td>
                        ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          {conflict && dirty && (
            <p role="alert">
              Представление изменилось в другой вкладке. Черновик сохранён на
              экране; загрузите актуальные настройки перед повтором.
            </p>
          )}
          {error && <p role="alert">{error}</p>}
          {saved && <p role="status">Настройки результата сохранены</p>}
          <div className="flex flex-wrap gap-2 mt-3">
            <Button
              type="button"
              disabled={busy || !dirty || conflict}
              onClick={() => void save()}
            >
              Сохранить представление
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={reset}
            >
              {conflict ? 'Загрузить сохранённую версию' : 'Отменить изменения'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => change(initialResultView(result))}
            >
              Сбросить настройки
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
