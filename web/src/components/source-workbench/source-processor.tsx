import { Button } from '@/components/ui/button';
import {
  processSourceWorkspaceStream,
  sourceRequestError,
  type SourceProcessEvent,
  type SourceWorkspace,
} from '@/services/source-workbench-service';
import { useEffect, useRef, useState, type FormEvent } from 'react';

type Mode = 'all' | 'sequential';

/** Streams a checked source selection through one call or an article-by-article workflow. */
export function SourceProcessor({
  workspace,
  selectionBusy = false,
  onBusyChange,
}: {
  workspace: SourceWorkspace;
  selectionBusy?: boolean;
  onBusyChange?(busy: boolean): void;
}) {
  const [mode, setMode] = useState<Mode>('all');
  const [prompt, setPrompt] = useState('');
  const [draft, setDraft] = useState('');
  const [result, setResult] = useState('');
  const [resultFromCurrentRun, setResultFromCurrentRun] = useState(false);
  const [liveText, setLiveText] = useState('');
  const [resultVersion, setResultVersion] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('');
  const [budget, setBudget] = useState('');
  const [resultUsedNotes, setResultUsedNotes] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stopped, setStopped] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const notesUsed = useRef(false);
  const liveArea = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  useEffect(() => () => controller.current?.abort(), []);

  useEffect(() => {
    if (liveArea.current)
      liveArea.current.scrollTop = liveArea.current.scrollHeight;
  }, [liveText]);

  const run = async (event: FormEvent) => {
    event.preventDefault();
    const task = prompt.trim();
    if (
      !task ||
      busy ||
      selectionBusy ||
      controller.current ||
      !workspace.selected_documents.length
    )
      return;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    onBusyChange?.(true);
    setError('');
    setStopped(false);
    setLiveText('');
    setResultFromCurrentRun(false);
    setProgress(0);
    setElapsedSeconds(0);
    setStatus('Подготовка запроса');
    setBudget('');
    notesUsed.current = false;
    const onEvent = (item: SourceProcessEvent) => {
      if (item.event === 'status') {
        setStatus(item.message);
        if (item.context_tokens && item.input_tokens) {
          setBudget(
            `${item.context_assumed ? 'Контекст модели не указан; для планирования принято' : 'Настроенный контекст модели:'} ${item.context_tokens} токенов; примерно ${item.input_tokens} для входа и ${item.output_tokens} для ответа.`,
          );
        }
        if (item.stage === 'extract') notesUsed.current = true;
        if (item.stage === 'generate') setLiveText('');
      } else if (item.event === 'delta') {
        setLiveText((previous) => previous + item.text);
      } else if (item.event === 'step_done' || item.event === 'done') {
        setResult(item.text);
        setResultFromCurrentRun(true);
        setResultUsedNotes(notesUsed.current);
        setResultVersion(item.version);
        setProgress(item.processed);
        setLiveText('');
        if (item.event === 'done') setStatus('Готово');
      }
    };
    try {
      await processSourceWorkspaceStream(
        workspace,
        { prompt: task, draft, mode },
        onEvent,
        request.signal,
      );
    } catch (cause) {
      if ((cause as Error)?.name !== 'AbortError') {
        setError(sourceRequestError(cause, 'Обработка не выполнена'));
      }
    } finally {
      controller.current = null;
      setBusy(false);
      onBusyChange?.(false);
    }
  };

  return (
    <section className="mt-8 rounded-md border border-border-button p-5">
      <h2 className="text-lg font-semibold">Обработка выбранных статей</h2>
      <p className="mt-1 text-sm text-text-secondary">
        Создайте новый текст или вставьте существующий для правок. Итог можно
        использовать как черновик следующего запроса.
      </p>
      <form className="mt-5 space-y-4" onSubmit={run}>
        <fieldset disabled={busy} className="flex flex-wrap gap-4 text-sm">
          <legend className="mb-2 font-medium">Как передать статьи</legend>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="source-processing-mode"
              checked={mode === 'all'}
              onChange={() => setMode('all')}
            />
            Все одним промптом
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="source-processing-mode"
              checked={mode === 'sequential'}
              onChange={() => setMode('sequential')}
            />
            По одной, последовательно
          </label>
        </fieldset>
        <label
          className="block text-sm font-medium"
          htmlFor="source-processing-prompt"
        >
          Промпт
        </label>
        <textarea
          id="source-processing-prompt"
          className="min-h-28 w-full rounded-md border border-border-button bg-bg-card p-3 text-sm"
          placeholder="Например: составь статью по выбранным источникам"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          disabled={busy}
        />
        <label
          className="block text-sm font-medium"
          htmlFor="source-processing-draft"
        >
          Исходный текст для правок (необязательно)
        </label>
        <textarea
          id="source-processing-draft"
          className="min-h-32 w-full rounded-md border border-border-button bg-bg-card p-3 text-sm"
          placeholder="Вставьте существующий текст или оставьте поле пустым для нового"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={busy}
        />
        <p className="text-xs text-text-secondary">
          {mode === 'all'
            ? 'Один вызов модели по полным текстам. Если они не помещаются в её контекст, переключитесь на последовательный режим.'
            : 'Длинные статьи анализируются по частям. Итог каждой статьи становится черновиком следующей.'}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            disabled={
              busy ||
              selectionBusy ||
              !prompt.trim() ||
              !workspace.selected_documents.length
            }
          >
            {busy ? 'Обработка…' : 'Обработать'}
          </Button>
          {busy && (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                controller.current?.abort();
                setStopped(true);
                setStatus('Остановлено пользователем');
              }}
            >
              Остановить
            </Button>
          )}
          {(busy || progress > 0) && (
            <span className="text-sm text-text-secondary">
              Завершено статей: {progress} из{' '}
              {workspace.selected_documents.length}
            </span>
          )}
        </div>
      </form>
      {(busy || status) && (
        <div role="status" className="mt-4 text-sm text-text-secondary">
          <p>
            {status}
            {busy ? ` · ${elapsedSeconds} с` : ''}
          </p>
          {budget && <p className="mt-1 text-xs">{budget}</p>}
        </div>
      )}
      {error && (
        <p role="alert" className="mt-4 text-sm text-state-error">
          {error}
        </p>
      )}
      {stopped && (
        <p className="mt-2 text-sm text-text-secondary">
          {progress > 0
            ? `Сохранён результат после ${progress} из ${workspace.selected_documents.length} статей.`
            : 'Для этого запуска завершённого результата нет.'}
        </p>
      )}
      {liveText && (
        <div className="mt-6">
          <h3 className="font-semibold">
            {busy ? 'Ответ модели сейчас' : 'Незавершённый ответ модели'}
          </h3>
          {!busy && (
            <p className="mt-1 text-xs text-text-secondary">
              Этот текст не был завершён и не используется как итоговый
              результат.
            </p>
          )}
          <textarea
            ref={liveArea}
            aria-label="Потоковый ответ"
            className="mt-3 min-h-40 w-full rounded-md border border-border-button bg-bg-card p-3 text-sm"
            value={liveText}
            readOnly
          />
        </div>
      )}
      {result && (
        <div className="mt-6">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-semibold">
              {resultFromCurrentRun
                ? 'Последний завершённый результат'
                : 'Результат предыдущего запуска'}
            </h3>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => setDraft(result)}
            >
              Использовать как черновик
            </Button>
          </div>
          <textarea
            aria-label="Результат обработки"
            className="mt-3 min-h-64 w-full rounded-md border border-border-button bg-bg-card p-3 text-sm"
            value={result}
            readOnly
          />
          {resultVersion !== workspace.version && (
            <p className="mt-2 text-sm text-text-secondary">
              Результат создан по предыдущей версии подборки.
            </p>
          )}
          <p className="mt-2 text-xs text-text-secondary">
            Номера источников в тексте соответствуют порядку статей в подборке.
          </p>
          {resultUsedNotes && (
            <p className="mt-2 text-xs text-text-secondary">
              Длинные статьи сначала сведены моделью в заметки по частям.
              Проверьте итог по исходным статьям: такое сведение может упустить
              детали.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
