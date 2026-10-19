import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { EvaPage } from '@/interfaces/database/eva-page';
import { sourceRequestError } from '@/services/source-workbench-service';
import { searchEvaPages } from '@/services/technical-specification-service';
import { useEffect, useRef, useState } from 'react';

export function EvaImport({
  workspace,
  busy,
  onImport,
}: {
  workspace: string;
  busy: boolean;
  onImport: (page: EvaPage, copy: boolean) => Promise<void>;
}) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<EvaPage[] | null>(null);
  const [selected, setSelected] = useState<EvaPage | null>(null);
  const [copy, setCopy] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const request = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      request.current++;
      controller.current?.abort();
    },
    [],
  );

  async function search() {
    const attempt = ++request.current;
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    setSelected(null);
    setItems(null);
    setError('');
    setSearching(true);
    try {
      const result = await searchEvaPages(
        workspace,
        query.trim(),
        pending.signal,
      );
      if (request.current === attempt) setItems(result);
    } catch (cause) {
      if (request.current === attempt)
        setError(sourceRequestError(cause, 'Не удалось найти страницы Eva'));
    } finally {
      if (request.current === attempt) setSearching(false);
    }
  }

  return (
    <section
      aria-label="Импорт из Eva"
      className="space-y-3 border-t border-border-button pt-4"
    >
      <h2 className="font-medium">Создать ТЗ из Eva</h2>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <Input
          aria-label="Название или ссылка на страницу Eva"
          placeholder="Название или ссылка на страницу Eva"
          className="min-w-48 flex-1"
          value={query}
          maxLength={2000}
          disabled={busy}
          onChange={(event) => {
            request.current++;
            controller.current?.abort();
            setQuery(event.target.value);
            setItems(null);
            setSelected(null);
            setSearching(false);
            setError('');
          }}
        />
        <Button variant="outline" disabled={busy || searching || !query.trim()}>
          Найти в Eva
        </Button>
      </form>
      {searching && <p role="status">Поиск в Eva…</p>}
      {error && (
        <p role="alert" className="text-state-error">
          {error}
        </p>
      )}
      {items?.length === 0 && (
        <p role="status">
          Страницы не найдены. Уточните название или вставьте ссылку.
        </p>
      )}
      {items && items.length > 0 && (
        <fieldset disabled={busy} className="min-w-0 space-y-1">
          <legend className="text-sm text-text-secondary">
            Выберите страницу
          </legend>
          {items.map((page) => (
            <label
              key={page.id}
              className="flex cursor-pointer items-start gap-3 border-b border-border-button py-3"
            >
              <input
                type="radio"
                name="eva-page"
                className="mt-1"
                checked={selected?.id === page.id}
                onChange={() => setSelected(page)}
              />
              <span className="min-w-0 space-y-1">
                <span className="block font-medium">{page.name}</span>
                <span className="block break-words text-sm text-text-secondary">
                  {page.hierarchy || page.code} · {page.project_id}
                </span>
                <span className="block break-all text-xs text-text-secondary">
                  {page.id} · Версия {page.version}
                </span>
                {!page.binding_available && (
                  <span className="block text-sm">
                    {page.linked_document
                      ? 'Уже связано с ТЗ — откроется существующий документ'
                      : 'Страница уже связана с недоступным ТЗ. Можно создать независимую копию.'}
                  </span>
                )}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      {selected && (
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              disabled={busy}
              checked={copy}
              onChange={(event) => setCopy(event.target.checked)}
            />
            Создать независимую копию без связи с Eva
          </label>
          <Button
            variant="outline"
            disabled={
              busy ||
              (!copy &&
                !selected.binding_available &&
                !selected.linked_document)
            }
            onClick={() => void onImport(selected, copy)}
          >
            {copy
              ? 'Создать копию'
              : selected.linked_document
                ? 'Открыть ТЗ'
                : 'Импортировать выбранную страницу'}
          </Button>
        </div>
      )}
    </section>
  );
}
