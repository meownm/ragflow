import { Button } from '@/components/ui/button';
import type {
  AssistantDialogueOptions,
  AssistantReference,
  AssistantSession,
} from '@/interfaces/database/sql-assistant';
import { useSqlResultDeadline } from '@/pages/business-documents/constructor/sql-result-deadline';
import dayjs from 'dayjs';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { DialogueCards } from './dialogue-cards';
import { MentionInput, referenceKey } from './mention-input';
import { MetricChoice } from './metric-choice';
import type { SendCommand } from './workspace';

export type DrillRow = {
  row_index: number;
  dimension: string;
  label: string;
};

function AnswerExpiry({
  source,
  deadline,
  children,
}: {
  source: string;
  deadline?: number;
  children: ReactNode;
}) {
  const { expired } = useSqlResultDeadline(source, deadline, () => undefined);
  return expired ? (
    <p className="mt-3 text-sm" role="status">
      Ответ недоступен: срок хранения исходного результата истёк.
    </p>
  ) : (
    children
  );
}

export function ResultDialogue({
  session,
  send,
  busy,
  preparingQuery,
  parentRow,
  onClearRow,
  options,
  optionsLoading,
  optionsError,
  onQuestionChange,
  onOpenSql,
  onUseDefinition,
}: {
  session: AssistantSession;
  send: SendCommand;
  busy: boolean;
  preparingQuery: boolean;
  parentRow: DrillRow | null;
  onClearRow: () => void;
  options?: AssistantDialogueOptions;
  optionsLoading?: boolean;
  optionsError?: boolean;
  onQuestionChange?: (question: string) => void;
  onOpenSql?: () => void;
  onUseDefinition?: (id: string, version: number) => void;
}) {
  const [mode, setMode] = useState<'answer' | 'change'>('answer');
  const [drafts, setDrafts] = useState({ answer: '', change: '' });
  const input = useRef<HTMLTextAreaElement>(null);
  const canEdit = session.project.capabilities.edit !== false;
  const question = drafts[mode];
  const [references, setReferences] = useState<{
    answer: AssistantReference[];
    change: AssistantReference[];
  }>({ answer: [], change: [] });
  const [exploratory, setExploratory] = useState(false);
  const result = session.cards
    ?.filter((card) => card.kind === 'RESULT')
    .find((card) => card.run_id === session.selected_result_id);
  const unavailableContext = Boolean(
    session.selected_result_id && session.cards && !result,
  );
  const { expired } = useSqlResultDeadline(
    result?.run_id || '',
    result?.expires_at,
    () => undefined,
  );
  useEffect(() => {
    onQuestionChange?.(question);
    setExploratory(false);
  }, [question, onQuestionChange]);
  useEffect(() => {
    if (parentRow) {
      setMode('change');
      setDrafts((current) => ({
        ...current,
        change: 'Детализируй выбранную группу по ',
      }));
      input.current?.focus();
    }
  }, [parentRow]);
  const submit = async () => {
    const run = session.selected_result_id;
    if (
      !run ||
      expired ||
      unavailableContext ||
      !question.trim() ||
      busy ||
      (mode === 'change' &&
        (!canEdit ||
          preparingQuery ||
          optionsLoading ||
          optionsError ||
          Boolean(options?.metrics.length && !exploratory)))
    )
      return;
    try {
      await send(mode === 'answer' ? 'answer_result' : 'follow_up', {
        run_id: run,
        text: question,
        ...(result ? { context: result.context } : {}),
        ...(references[mode].length
          ? { references: references[mode].map(referenceKey) }
          : {}),
        ...(mode === 'change' && exploratory
          ? { metric_resolution: 'EXPLORATORY' }
          : {}),
        ...(parentRow
          ? { row_index: parentRow.row_index, dimension: parentRow.dimension }
          : {}),
      });
      setDrafts((current) =>
        current[mode] === question ? { ...current, [mode]: '' } : current,
      );
      setReferences((current) => ({ ...current, [mode]: [] }));
      if (mode === 'change') onClearRow();
    } catch {
      // The workspace displays the error; keep the submitted question available.
    }
  };
  return (
    <>
      <div className="sa-messages">
        {session.messages.map((message) => (
          <div key={message.id} className="sa-message">
            <p className="whitespace-pre-wrap break-words">{message.text}</p>
            {message.answer && (
              <AnswerExpiry
                source={message.parent_result_id || message.id}
                deadline={message.answer.expires_at}
              >
                <div
                  className="mt-3 space-y-2"
                  aria-label="Ответ по результату"
                >
                  <p className="text-xs font-semibold">SQL-помощник</p>
                  <p className="whitespace-pre-wrap break-words">
                    {message.answer.text}
                  </p>
                  {message.answer.status === 'READY' && (
                    <p className="text-xs text-text-secondary">
                      По сохранённому запуску. Полнота источника не
                      подтверждена.
                      {message.answer.scope === 'LIMITED' &&
                        ' Выдача ограничена; это не все данные источника.'}
                      {message.answer.context_limited &&
                        ' Помощнику доступен фрагмент результата.'}
                    </p>
                  )}
                  {Boolean(message.answer.citations?.length) && (
                    <details>
                      <summary className="cursor-pointer text-xs">
                        Основания ответа
                      </summary>
                      <ul className="mt-2 space-y-1 text-xs break-words">
                        {message.answer.citations?.map((citation) => (
                          <li
                            key={`${citation.row_index}:${citation.column_id}`}
                          >
                            Строка {citation.row_index + 1} · {citation.column}:{' '}
                            {citation.value}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {message.answer.kind === 'NEEDS_QUERY' && canEdit && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        setDrafts((current) => ({
                          ...current,
                          change: message.text,
                        }));
                        setMode('change');
                        input.current?.focus();
                      }}
                    >
                      Подготовить изменение запроса
                    </Button>
                  )}
                </div>
              </AnswerExpiry>
            )}
            {message.parent_result_id && (
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => {
                  void send('select_result', {
                    run_id: message.parent_result_id,
                  }).catch(() => undefined);
                }}
              >
                К исходному результату
              </Button>
            )}
          </div>
        ))}
      </div>
      <DialogueCards
        cards={session.cards || []}
        busy={busy}
        canEdit={canEdit}
        onOpenSql={() => onOpenSql?.()}
        onContinue={() => {
          setMode('change');
          input.current?.focus();
        }}
      />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <fieldset className="mb-3 space-y-2" disabled={busy}>
          <legend className="mb-2 text-xs text-text-secondary">
            Действие с выбранным результатом
          </legend>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name={`result-mode-${session.id}`}
              checked={mode === 'answer'}
              onChange={() => setMode('answer')}
            />
            Спросить о результате
          </label>
          {canEdit && (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name={`result-mode-${session.id}`}
                checked={mode === 'change'}
                onChange={() => setMode('change')}
              />
              Изменить запрос
            </label>
          )}
        </fieldset>
        <label htmlFor="sa-follow-up">
          {mode === 'answer'
            ? 'Вопрос по выбранному результату'
            : 'Изменение запроса'}
        </label>
        {result && !expired && (
          <section
            aria-label="Контекст вопроса"
            className="my-2 space-y-1 text-xs text-text-secondary"
          >
            <p>
              По запуску от{' '}
              {dayjs(result.created_at).format('DD.MM.YYYY HH:mm')} · часовой
              пояс: {result.context.timezone || 'не указан'}
            </p>
            <details>
              <summary>Версия SQL и параметры</summary>
              <p className="break-all">
                Версия SQL: {result.context.query_artifact_id}
              </p>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words">
                {JSON.stringify(result.parameters, null, 2)}
              </pre>
            </details>
          </section>
        )}
        {parentRow && !expired && !unavailableContext && (
          <p className="my-2 text-xs">
            Контекст: {parentRow.label}{' '}
            <button type="button" className="underline" onClick={onClearRow}>
              Убрать
            </button>
          </p>
        )}
        <MentionInput
          key={mode}
          ref={input}
          id="sa-follow-up"
          value={question}
          onChange={(text) =>
            setDrafts((current) => ({ ...current, [mode]: text }))
          }
          references={references[mode]}
          onReferences={(items) =>
            setReferences((current) => ({ ...current, [mode]: items }))
          }
          options={options?.references || []}
          loading={optionsLoading}
          error={optionsError}
          maxLength={10000}
          rows={4}
          className="mt-2"
          placeholder={
            mode === 'answer'
              ? 'Кто в первой строке и какое у него значение?'
              : 'Измени период или добавь разрез'
          }
        />
        {mode === 'change' && (
          <MetricChoice
            metrics={options?.metrics || []}
            busy={busy}
            canUse={Boolean(
              onUseDefinition &&
              !preparingQuery &&
              session.project.artifact_ids?.query &&
              !session.draft &&
              !session.project.pending_proposal,
            )}
            onUse={(id, version) => onUseDefinition?.(id, version)}
            exploratory={exploratory}
            onExplore={setExploratory}
          />
        )}
        <p className="mt-2 text-xs text-text-secondary">
          {mode === 'answer'
            ? 'Ответ по сохранённым данным, без генерации и выполнения нового SQL.'
            : 'Будет подготовлено отдельное предложение SQL. Применение и выполнение — отдельные действия.'}
        </p>
        <Button
          type="submit"
          className="mt-3"
          disabled={
            !session.selected_result_id ||
            expired ||
            unavailableContext ||
            !question.trim() ||
            busy ||
            (mode === 'change' &&
              (!canEdit ||
                preparingQuery ||
                optionsLoading ||
                optionsError ||
                Boolean(options?.metrics.length && !exploratory)))
          }
        >
          {busy
            ? 'Обрабатываем…'
            : mode === 'answer'
              ? 'Получить ответ'
              : 'Предложить изменение SQL'}
        </Button>
        {!session.selected_result_id && (
          <p className="mt-2 text-xs text-text-secondary">
            Сначала получите и выберите результат.
          </p>
        )}
        {unavailableContext && (
          <p role="status" className="mt-2 text-xs">
            Контекст выбранного результата недоступен. Обновите состояние и
            проверьте доступ и срок хранения.
          </p>
        )}
      </form>
    </>
  );
}
