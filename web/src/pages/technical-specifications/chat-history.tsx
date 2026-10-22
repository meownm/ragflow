import { Button } from '@/components/ui/button';
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { DocumentMarkdown } from '../business-documents/components/document-markdown';
import {
  contextLabel,
  type Draft,
  type Proposal,
  type Specification,
} from './model';

export function ChatHistory({
  document,
  actor,
  proposals,
  disabled,
  onRetry,
  renderProposal,
}: {
  document: Specification;
  actor: string;
  proposals: Proposal[];
  disabled: boolean;
  onRetry: (draft: Draft) => void;
  renderProposal: (proposal: Proposal) => ReactNode;
}) {
  const history = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const viewport = useRef({ width: 0, height: 0 });
  const [showLatest, setShowLatest] = useState(false);
  const answers = new Map(
    document.messages
      .filter((message) => message.role === 'assistant' && message.reply_to)
      .map((message) => [message.reply_to, message.id]),
  );
  const messageIds = new Set(document.messages.map((message) => message.id));
  const attached = new Map<string, Proposal[]>();
  const unattached: Proposal[] = [];
  for (const proposal of proposals) {
    const request =
      proposal.source === 'chat' ? proposal.message_id : undefined;
    const target = request && (answers.get(request) ?? request);
    if (target && messageIds.has(target)) {
      attached.set(target, [...(attached.get(target) ?? []), proposal]);
    } else {
      unattached.push(proposal);
    }
  }

  useLayoutEffect(() => {
    if (followLatest.current && history.current) {
      history.current.scrollTop = history.current.scrollHeight;
    }
  }, [document.messages, proposals]);

  useLayoutEffect(() => {
    const element = history.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    viewport.current = {
      width: element.clientWidth,
      height: element.clientHeight,
    };
    const observer = new ResizeObserver(() => {
      viewport.current = {
        width: element.clientWidth,
        height: element.clientHeight,
      };
      if (followLatest.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  function scrollLatest() {
    if (history.current)
      history.current.scrollTop = history.current.scrollHeight;
    followLatest.current = true;
    setShowLatest(false);
  }

  return (
    <>
      <div
        ref={history}
        className="tz-chat-history"
        role="region"
        aria-label="История чата"
        tabIndex={0}
        onScroll={(event) => {
          const element = event.currentTarget;
          const sizeChanged =
            element.clientWidth !== viewport.current.width ||
            element.clientHeight !== viewport.current.height;
          viewport.current = {
            width: element.clientWidth,
            height: element.clientHeight,
          };
          if (sizeChanged) {
            // Reflow can emit scroll before ResizeObserver. Preserve the reader's intent.
            if (followLatest.current) element.scrollTop = element.scrollHeight;
            return;
          }
          const nearEnd =
            element.scrollHeight - element.scrollTop - element.clientHeight <
            64;
          followLatest.current = nearEnd;
          setShowLatest(!nearEnd);
        }}
      >
        <div className="tz-chat-heading">
          <h2 className="text-lg font-medium">Чат с помощником</h2>
          <p className="text-sm text-text-secondary">
            Обсуждайте и меняйте это ТЗ.
          </p>
        </div>
        {document.messages.length === 0 && (
          <p className="text-text-secondary">
            Задайте вопрос или попросите подготовить правки ТЗ.
          </p>
        )}
        {document.messages.map((item) => (
          <article
            key={item.id}
            className="space-y-2 border-b border-border-button pb-4"
          >
            <p className="text-sm font-medium">
              {item.role === 'user'
                ? item.author_id === actor
                  ? 'Вы'
                  : (document.people[item.author_id ?? ''] ?? 'Участник')
                : 'Помощник'}
              {item.context && ` · ${contextLabel(item.context)}`}
            </p>
            {item.context?.anchor && (
              <blockquote className="border-l-2 border-border-button pl-3 text-sm text-text-secondary">
                {item.context.anchor.quote}
              </blockquote>
            )}
            <DocumentMarkdown>{item.text}</DocumentMarkdown>
            {item.source_revision &&
              item.source_revision !== document.revision && (
                <p className="text-sm text-text-secondary">
                  Относится к редакции {item.source_revision}. Предложения к
                  прежней редакции не применяются.
                </p>
              )}
            {item.status === 'processing' && (
              <p role="status" className="text-sm text-text-secondary">
                Запрос сохранён · помощник готовит ответ
              </p>
            )}
            {item.status === 'failed' && (
              <p role="alert" className="text-sm text-state-error">
                {item.error?.message ?? 'Не удалось получить ответ'}
              </p>
            )}
            {item.author_id === actor &&
              item.context &&
              (item.status === 'failed' || item.status === 'processing') && (
                <Button
                  variant="outline"
                  disabled={disabled}
                  onClick={() =>
                    onRetry({
                      message_id: item.id,
                      text: item.text,
                      context: item.context!,
                    })
                  }
                >
                  {item.status === 'failed'
                    ? 'Повторить сообщение'
                    : 'Проверить запрос'}
                </Button>
              )}
            {attached.get(item.id)?.map(renderProposal)}
          </article>
        ))}
        {unattached.map(renderProposal)}
      </div>
      {showLatest && (
        <Button
          variant="ghost"
          className="tz-chat-latest"
          onClick={scrollLatest}
        >
          К последнему сообщению
        </Button>
      )}
    </>
  );
}
