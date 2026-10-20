import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { EvaPage } from '@/interfaces/database/eva-page';
import { sourceRequestError } from '@/services/source-workbench-service';
import {
  cancelEvaImport,
  getEvaImport,
  importEvaSpecification,
  searchEvaPages,
  type EvaImportOperation,
} from '@/services/technical-specification-service';
import { useEffect, useRef, useState } from 'react';
import {
  forgetImport,
  importPrefix,
  readImport,
  rememberImport,
  type ImportAttempt,
} from './import-attempt';
import { messageId, type Specification } from './model';

export function EvaImport({
  workspace,
  actor,
  busy,
  onImported,
}: {
  workspace: string;
  actor: string;
  busy: boolean;
  onImported: (document: Specification) => void;
}) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<EvaPage[] | null>(null);
  const [selected, setSelected] = useState<EvaPage | null>(null);
  const [copy, setCopy] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const prefix = importPrefix(actor, workspace);
  function load() {
    try {
      return { pending: readImport(prefix), unreadable: false };
    } catch {
      return { pending: null, unreadable: true };
    }
  }
  const [saved, setSaved] = useState(load);
  const [activity, setActivity] = useState<'import' | 'cancel' | null>(null);
  const working = activity !== null;
  const running = useRef<'import' | 'cancel' | null>(null);
  const operation = useRef(0);
  const mounted = useRef(true);
  const request = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);

  function finished(attempt: ImportAttempt, result: EvaImportOperation) {
    const unavailable =
      result.state === 'completed' && result.document_unavailable === true;
    if (
      result.id !== attempt.id ||
      (result.state !== 'completed' && result.state !== 'canceled') ||
      (result.state === 'completed' &&
        !unavailable &&
        (!result.document?.id || !result.document.workspace_id)) ||
      (result.state === 'canceled' && result.document)
    )
      throw new Error('Не удалось подтвердить результат импорта');
    try {
      forgetImport(prefix, attempt.id);
    } catch {
      throw new Error(
        'Результат получен, но не удалось удалить локальную запись импорта. Повторите проверку.',
      );
    }
    if (mounted.current) {
      setSaved(load());
      if (unavailable)
        setError('Импорт завершён, но доступ к результату сейчас отсутствует.');
      else if (result.document) onImported(result.document);
    }
  }

  async function execute(
    attempt: ImportAttempt,
    action: 'import' | 'resume' | 'cancel' = 'import',
  ) {
    const cancel = action === 'cancel';
    if (busy || running.current === 'cancel' || (!cancel && running.current))
      return;
    const current = ++operation.current;
    running.current = cancel ? 'cancel' : 'import';
    setActivity(running.current);
    setError('');
    try {
      if (cancel) {
        const result = await cancelEvaImport(
          workspace,
          attempt.page,
          attempt.copy,
          attempt.id,
        );
        if (operation.current !== current) return;
        finished(attempt, result);
      } else {
        const previous =
          action === 'resume'
            ? await getEvaImport(workspace, attempt.id)
            : null;
        if (operation.current !== current) return;
        if (previous?.state === 'completed' || previous?.state === 'canceled') {
          finished(attempt, previous);
        } else {
          const result = await importEvaSpecification(
            workspace,
            attempt.page.web_url,
            attempt.copy,
            attempt.page,
            attempt.id,
          );
          if (operation.current !== current) return;
          finished(attempt, {
            id: attempt.id,
            state: 'completed',
            document: result,
          });
        }
      }
    } catch (cause) {
      if (mounted.current && operation.current === current)
        setError(
          sourceRequestError(
            cause,
            'Импорт не завершён. Запрос сохранён; продолжите или отмените его.',
          ),
        );
    } finally {
      if (operation.current === current) {
        running.current = null;
        if (mounted.current) setActivity(null);
      }
    }
  }

  function begin() {
    if (!selected || running.current || busy) return;
    try {
      const id = messageId();
      const attempt = rememberImport(prefix, { id, page: selected, copy });
      setSaved({ pending: attempt, unreadable: false });
      void execute(attempt, attempt.id !== id ? 'resume' : 'import');
    } catch {
      setError(
        'Не удалось сохранить запрос импорта на устройстве. Импорт не отправлен; повторите попытку.',
      );
    }
  }

  if (saved.unreadable)
    return (
      <section
        aria-label="Импорт из Eva"
        className="space-y-3 border-t border-border-button pt-4"
      >
        <p role="alert">
          Не удалось прочитать незавершённый импорт. Новый запрос не отправлен.
        </p>
        <Button variant="outline" onClick={() => setSaved(load())}>
          Повторить чтение
        </Button>
      </section>
    );

  if (saved.pending)
    return (
      <section
        aria-label="Импорт из Eva"
        className="space-y-3 border-t border-border-button pt-4"
      >
        <h2 className="font-medium">Импорт из Eva</h2>
        <p>
          {saved.pending.page.name}
          {saved.pending.copy ? ' — независимая копия' : ''}
        </p>
        <p className="text-sm text-text-secondary">
          Продолжение использует тот же запрос и не создаёт вторую копию.
        </p>
        {error && (
          <p role="alert" className="text-state-error">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={busy || working}
            onClick={() => void execute(saved.pending!, 'resume')}
          >
            Продолжить импорт
          </Button>
          <Button
            variant="outline"
            disabled={busy || activity === 'cancel'}
            onClick={() => void execute(saved.pending!, 'cancel')}
          >
            Отменить импорт
          </Button>
        </div>
        {working && <p role="status">Проверяем результат импорта…</p>}
      </section>
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
      if (mounted.current && request.current === attempt) setItems(result);
    } catch (cause) {
      if (mounted.current && request.current === attempt)
        setError(sourceRequestError(cause, 'Не удалось найти страницы Eva'));
    } finally {
      if (mounted.current && request.current === attempt) setSearching(false);
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
                      : 'Страница уже связана с другим документом. Можно создать независимую копию.'}
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
            onClick={begin}
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
