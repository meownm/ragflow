import { Button } from '@/components/ui/button';
import type {
  SqlHistoryContext as HistoricalContext,
  SqlHistorySelection,
} from '@/pages/business-documents/types';
import { fetchBusinessDocumentSqlHistoryContext } from '@/services/business-document-service';
import { useCallback, useEffect, useRef, useState } from 'react';

function Identity({
  item,
}: {
  item: NonNullable<
    HistoricalContext['requirements'] | HistoricalContext['schema']
  >;
}) {
  const date = item.created_at == null ? null : new Date(item.created_at);
  return (
    <div className="mt-1 break-words text-xs text-text-secondary">
      <p>
        Принято:{' '}
        {date && Number.isFinite(date.getTime()) ? (
          <time dateTime={date.toISOString()}>{date.toLocaleString()}</time>
        ) : (
          'время не записано'
        )}
      </p>
      <details className="mt-1 break-all">
        <summary className="cursor-pointer">
          Технические сведения об источнике
        </summary>
        <p>Автор: {item.accepted_by || 'не записан'}</p>
        <p>Версия: {item.id}</p>
        {item.source_proposal_id && <p>Основание: {item.source_proposal_id}</p>}
      </details>
    </div>
  );
}

export function SqlHistoryContext({
  projectId,
  source,
  label = 'Требования и схема запроса',
}: {
  projectId: string;
  source: SqlHistorySelection;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<HistoricalContext | null>(null);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const scope = JSON.stringify([projectId, source]);
  const currentScope = useRef(scope);
  currentScope.current = scope;

  const close = useCallback(() => {
    generation.current += 1;
    controller.current?.abort();
    setOpen(false);
    setLoading(false);
    setData(null);
    setError('');
  }, []);

  useEffect(() => {
    close();
    window.addEventListener('pagehide', close);
    return () => {
      generation.current += 1;
      controller.current?.abort();
      window.removeEventListener('pagehide', close);
    };
  }, [scope, close]);

  async function load() {
    const requestGeneration = ++generation.current;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setOpen(true);
    setLoading(true);
    setData(null);
    setError('');
    const current = () =>
      !request.signal.aborted &&
      generation.current === requestGeneration &&
      currentScope.current === scope;
    try {
      const value = await fetchBusinessDocumentSqlHistoryContext(
        projectId,
        source,
        request.signal,
      );
      if (!current()) return;
      if (
        value.project_id !== projectId ||
        ('artifact_id' in source
          ? value.source.artifact_id !== source.artifact_id
          : value.source.compilation_id !== source.compilation_id ||
            value.source.query_artifact_id !== source.query_artifact_id)
      ) {
        throw new Error(
          'Полученный контекст не относится к выбранному запросу. Повторите загрузку.',
        );
      }
      setData(value);
    } catch (cause) {
      if (current())
        setError(
          cause instanceof Error
            ? cause.message
            : 'Не удалось загрузить контекст. Повторите загрузку.',
        );
    } finally {
      if (current()) setLoading(false);
    }
  }

  return (
    <section
      className="mt-3 min-w-0 max-w-full"
      aria-label="Контекст выбранного запроса"
    >
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={loading}
          onClick={() => void load()}
        >
          {open ? 'Обновить контекст запроса' : label}
        </Button>
        {open && (
          <Button size="sm" variant="ghost" onClick={close}>
            Закрыть контекст
          </Button>
        )}
      </div>
      {open && (
        <div className="mt-3 min-w-0 max-w-full space-y-4 rounded-md border border-border-button p-3">
          <p className="text-xs text-text-secondary">
            Сохранённый контекст выбранного запроса. Просмотр не меняет текущий
            проект.
          </p>
          <details className="break-all text-xs text-text-secondary">
            <summary className="cursor-pointer">
              Технические сведения о запросе
            </summary>
            <p>
              {'compilation_id' in source
                ? `Проверка: ${source.compilation_id}`
                : `Версия: ${source.artifact_id}`}
            </p>
          </details>
          {loading && <p role="status">Загружаем требования и схему…</p>}
          {error && (
            <p role="alert" className="text-sm text-state-error">
              {error}
            </p>
          )}
          {data && (
            <>
              {data.catalog_changed && (
                <p className="text-xs text-state-warning">
                  Каталог изменился. Ниже показан прежний принятый снимок;
                  доступ к его таблицам проверен сейчас.
                </p>
              )}
              {data.requirements && (
                <div>
                  <h5 className="text-sm font-semibold">
                    Требования · версия {data.requirements.revision}
                  </h5>
                  <Identity item={data.requirements} />
                  <pre
                    className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words text-sm"
                    data-testid="sql-history-requirements"
                  >
                    {data.requirements.text}
                  </pre>
                  <details className="mt-2 text-xs">
                    <summary>Уточнения, ответы и принятые требования</summary>
                    <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap">
                      {JSON.stringify(
                        {
                          requirements: data.requirements.requirements,
                          questions: data.requirements.questions,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </div>
              )}
              {data.schema && (
                <div>
                  <h5 className="text-sm font-semibold">
                    Схема · версия {data.schema.revision}
                  </h5>
                  <Identity item={data.schema} />
                  <div
                    className="mt-2 max-h-96 space-y-3 overflow-auto"
                    data-testid="sql-history-schema"
                  >
                    {data.schema.snapshot.tables.map((table) => (
                      <details key={table.id}>
                        <summary className="cursor-pointer break-all text-sm font-medium">
                          {table.fqn} · версия {table.version} ·{' '}
                          {table.columns.length} полей
                        </summary>
                        {table.description && (
                          <p className="mt-1 whitespace-pre-wrap text-sm">
                            {table.description}
                          </p>
                        )}
                        <table className="mt-2 w-full text-start text-xs">
                          <thead>
                            <tr>
                              {[
                                'Поле',
                                'Тип',
                                'Описание',
                                'Ограничение',
                                'Выбрано',
                              ].map((label) => (
                                <th key={label} className="p-1 text-start">
                                  {label}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {table.columns.map((column) => (
                              <tr key={column.id}>
                                <td className="p-1">{column.name}</td>
                                <td className="p-1">{column.data_type}</td>
                                <td className="whitespace-pre-wrap p-1">
                                  {column.description}
                                </td>
                                <td className="p-1">
                                  {column.constraint || '—'}
                                </td>
                                <td className="p-1">
                                  {column.selected ? 'Да' : 'Нет'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {table.table_constraints.length > 0 && (
                          <pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs">
                            {JSON.stringify(table.table_constraints, null, 2)}
                          </pre>
                        )}
                      </details>
                    ))}
                  </div>
                  <details className="mt-3 text-xs">
                    <summary>Полные данные принятого снимка</summary>
                    <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap">
                      {JSON.stringify(
                        {
                          accepted_schema: data.schema.accepted_schema,
                          snapshot: data.schema.snapshot,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
