import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  importBusinessSource,
  listBusinessSources,
  type BusinessSource,
} from '@/services/linked-document-service';
import { sourceRequestError } from '@/services/source-workbench-service';
import { useEffect, useRef, useState } from 'react';
import type { RunDocumentAction } from './document-settings';

export function BusinessSourcePicker({
  workspaceId,
  disabled,
  run,
}: {
  workspaceId: string;
  disabled: boolean;
  run: RunDocumentAction;
}) {
  const [opened, setOpened] = useState(false);
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<BusinessSource[]>([]);
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  useEffect(() => {
    const requestGuard = generation;
    setOpened(false);
    setItems([]);
    setLoading(false);
    setError('');
    setPage(1);
    return () => {
      requestGuard.current++;
    };
  }, [workspaceId]);
  const search = async (nextPage: number) => {
    const current = ++generation.current;
    setLoading(true);
    setError('');
    try {
      const result = await listBusinessSources(workspaceId, query, nextPage);
      if (current === generation.current) {
        setItems(result.items);
        setMore(result.has_more);
        setPage(nextPage);
      }
    } catch (cause) {
      if (current === generation.current) {
        setItems([]);
        setError(
          sourceRequestError(
            cause,
            'Не удалось получить согласованные источники',
          ),
        );
      }
    } finally {
      if (current === generation.current) setLoading(false);
    }
  };
  return (
    <details
      open={opened}
      className="border-t border-border-button pt-3"
      onToggle={(event) => {
        const value = event.currentTarget.open;
        setOpened(value);
        if (!value) {
          generation.current++;
          setLoading(false);
        }
      }}
    >
      <summary className="cursor-pointer text-sm">
        Подключить Business Documents
      </summary>
      {opened && (
        <div className="mt-3 space-y-2 text-sm">
          <p>
            Доступны только согласованные редакции. Изменения оригинала
            согласовываются в Business Documents.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void search(1);
            }}
          >
            <Input
              aria-label="Найти согласованный бизнес-документ"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              maxLength={255}
              disabled={disabled || loading}
            />
            <Button className="mt-2" disabled={disabled || loading}>
              Найти источники
            </Button>
          </form>
          {loading && <p role="status">Поиск согласованных редакций…</p>}
          {error && <p role="alert">{error}</p>}
          <ul>
            {items.map((item) => (
              <li key={item.document_id} className="my-2">
                <p>
                  {item.title} · согласованная редакция {item.revision_number}
                </p>
                <Button
                  variant="outline"
                  disabled={disabled || loading}
                  onClick={() =>
                    void run(() =>
                      importBusinessSource(workspaceId, item.document_id),
                    )
                  }
                >
                  Подключить {item.title}
                </Button>
              </li>
            ))}
          </ul>
          {!loading && !error && !items.length && (
            <p>
              Нажмите «Найти источники». Несогласованные документы не
              показываются.
            </p>
          )}
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={disabled || loading || page === 1}
              onClick={() => void search(page - 1)}
            >
              Назад
            </Button>
            <Button
              variant="outline"
              disabled={disabled || loading || !more}
              onClick={() => void search(page + 1)}
            >
              Далее
            </Button>
          </div>
        </div>
      )}
    </details>
  );
}
