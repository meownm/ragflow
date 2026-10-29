import { Button } from '@/components/ui/button';
import type {
  AssistantDialogueOptions,
  AssistantReference,
  AssistantSession,
} from '@/interfaces/database/sql-assistant';
import { useSqlResultDeadline } from '@/pages/business-documents/constructor/sql-result-deadline';
import { ArrowUp, LoaderCircle, Square, X } from 'lucide-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { MentionInput, referenceKey } from './mention-input';
import type { MessageDelivery, SendCommand } from './workspace';

export type DrillRow = { row_index: number; dimension: string; label: string };

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
    <p role="status">
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
  delivery,
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
  delivery?: MessageDelivery;
}) {
  const [question, setQuestion] = useState('');
  const [references, setReferences] = useState<AssistantReference[]>([]);
  const input = useRef<HTMLTextAreaElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const acknowledged = useRef<string | null>(null);
  const result = session.cards?.find(
    (card) =>
      card.kind === 'RESULT' && card.run_id === session.selected_result_id,
  );
  const selected = result?.kind === 'RESULT' ? result : undefined;
  const { expired } = useSqlResultDeadline(
    selected?.run_id || '',
    selected?.expires_at,
    () => undefined,
  );
  const unavailable = Boolean(
    session.selected_result_id && session.cards && !selected,
  );
  const blocked =
    busy ||
    preparingQuery ||
    expired ||
    unavailable ||
    session.capabilities?.chat === false ||
    session.conversation?.phase === 'RECONCILE';
  const last = session.messages[session.messages.length - 1];
  useEffect(() => {
    onQuestionChange?.(question);
  }, [question, onQuestionChange]);
  useEffect(() => {
    if (atBottom.current && history.current)
      history.current.scrollTop = history.current.scrollHeight;
  }, [session.messages.length, last?.answer?.text, preparingQuery]);
  useEffect(() => {
    if (parentRow) {
      setQuestion('Детализируй выбранную группу по ');
      requestAnimationFrame(() => input.current?.focus());
    }
  }, [parentRow]);
  useEffect(() => {
    if (!delivery || acknowledged.current === delivery.key) return;
    acknowledged.current = delivery.key;
    if (
      question.trim() === delivery.payload.text &&
      JSON.stringify(references.map(referenceKey)) ===
        JSON.stringify(delivery.payload.references || []) &&
      (!parentRow ||
        (parentRow.row_index === delivery.payload.row_index &&
          parentRow.dimension === delivery.payload.dimension))
    ) {
      setQuestion('');
      setReferences([]);
      onClearRow();
    }
  }, [delivery, question, references, parentRow, onClearRow]);
  const submit = async (text = question, chosenReferences = references) => {
    if (!text.trim() || blocked) return;
    try {
      await send('message', {
        text: text.trim(),
        ...(session.selected_result_id
          ? { run_id: session.selected_result_id }
          : {}),
        ...(selected ? { context: selected.context } : {}),
        ...(chosenReferences.length
          ? { references: chosenReferences.map(referenceKey) }
          : {}),
        ...(parentRow
          ? { row_index: parentRow.row_index, dimension: parentRow.dimension }
          : {}),
      });
      setQuestion((current) => (current === text ? '' : current));
      setReferences([]);
      onClearRow();
      atBottom.current = true;
    } catch {
      /* The workspace reports failure and retains the unsent draft. */
    }
  };
  const choose = (option: string | { id: string; label: string }) => {
    if (typeof option === 'string') {
      void submit(option, []);
      return;
    }
    const pending = session.conversation?.pending;
    const item = pending?.candidates?.find(
      (candidate) => candidate.id === option.id,
    );
    const selectedReferences: AssistantReference[] =
      item && pending?.kind === 'SOURCE'
        ? [{ kind: 'TABLE', id: item.id, title: option.label }]
        : item &&
            pending?.kind === 'METRIC' &&
            item.version !== undefined &&
            item.content_hash
          ? [
              {
                kind: 'DEFINITION',
                id: item.id,
                title: option.label,
                version: item.version,
                content_hash: item.content_hash,
                freshness: 'CURRENT',
              },
            ]
          : [];
    void submit(option.label, selectedReferences);
  };
  return (
    <>
      <div
        className="sa-chat-history"
        ref={history}
        role="log"
        aria-label="История диалога"
        aria-live="polite"
        onScroll={() => {
          const node = history.current;
          if (node)
            atBottom.current =
              node.scrollHeight - node.scrollTop - node.clientHeight < 60;
        }}
      >
        {session.messages.map((message) => (
          <article
            key={message.id}
            className={
              message.role === 'assistant'
                ? 'sa-chat-assistant'
                : 'sa-chat-user'
            }
          >
            <p className="whitespace-pre-wrap break-words">{message.text}</p>
            {message.answer && (
              <AnswerExpiry
                source={message.parent_result_id || message.id}
                deadline={message.answer.expires_at}
              >
                <div
                  className="sa-chat-answer"
                  aria-label="Ответ по результату"
                >
                  <p className="whitespace-pre-wrap break-words">
                    {message.answer.text}
                  </p>
                  {message.answer.status === 'READY' &&
                    message.answer.kind !== 'CLARIFICATION' && (
                      <p className="sa-answer-scope">
                        {message.answer.scope === 'LIMITED'
                          ? 'По сохранённой части результата. Полнота источника не подтверждена.'
                          : 'По сохранённому результату. Полнота источника не подтверждена.'}
                      </p>
                    )}
                  {Boolean(message.answer.citations?.length) && (
                    <details>
                      <summary>Основания ответа</summary>
                      <ul>
                        {message.answer.citations?.map((citation) => (
                          <li
                            key={citation.row_index + ':' + citation.column_id}
                          >
                            {citation.column}, строка {citation.row_index + 1}:{' '}
                            {citation.value}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </AnswerExpiry>
            )}
          </article>
        ))}
        {session.conversation?.phase === 'WAITING' &&
          Boolean(session.conversation.question?.options.length) && (
            <div className="sa-chat-options">
              {session.conversation.question?.options.map((option, index) => (
                <Button
                  key={typeof option === 'string' ? option : option.id}
                  variant="outline"
                  size="sm"
                  disabled={blocked}
                  onClick={() => choose(option)}
                >
                  {typeof option === 'string'
                    ? option
                    : option.label || String(index + 1)}
                </Button>
              ))}
            </div>
          )}
        {preparingQuery && (
          <p className="sa-chat-progress" role="status">
            <LoaderCircle className="size-4 animate-spin" />
            {session.conversation?.phase === 'EXECUTING'
              ? 'Выполняю запрос…'
              : session.conversation?.phase === 'CANCELLING'
                ? 'Останавливаю…'
                : 'Готовлю ответ…'}
          </p>
        )}
        {session.conversation?.phase === 'CANCELLED' && (
          <p className="sa-answer-scope" role="status">
            Запрос остановлен. Последний успешный результат сохранён.
          </p>
        )}
      </div>
      <form
        className="sa-chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {parentRow && (
          <div className="sa-drill-context">
            <span>{parentRow.label}</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="Убрать выбранную группу"
              onClick={onClearRow}
            >
              <X className="size-3" />
            </Button>
          </div>
        )}
        <label htmlFor="sa-dialogue-input" className="sr-only">
          Сообщение SQL-помощнику
        </label>
        <MentionInput
          ref={input}
          id="sa-dialogue-input"
          value={question}
          onChange={setQuestion}
          onSend={() => void submit()}
          references={references}
          onReferences={setReferences}
          options={options?.references || []}
          loading={optionsLoading}
          error={optionsError}
          disabled={blocked}
          maxLength={10000}
          rows={2}
          placeholder={
            session.selected_result_id
              ? 'Уточните ответ или измените представление…'
              : 'Ответьте на уточнение…'
          }
        />
        <div className="sa-chat-submit">
          <span>
            {session.project.capabilities.edit === false
              ? 'Просмотр и вопросы по результату'
              : 'Условия сохраняются в этом диалоге'}
          </span>
          {preparingQuery ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label="Остановить запрос"
              disabled={busy || session.conversation?.phase === 'CANCELLING'}
              onClick={() => void send('stop').catch(() => undefined)}
            >
              <Square className="size-3" />
            </Button>
          ) : (
            <Button
              type="submit"
              size="sm"
              aria-label="Отправить сообщение"
              disabled={blocked || !question.trim()}
            >
              <ArrowUp className="size-4" />
            </Button>
          )}
        </div>
      </form>
    </>
  );
}
