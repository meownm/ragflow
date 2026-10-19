import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  confirmDocumentImplementation,
  revokeDocumentImplementation,
  type WorkspaceDocument,
} from '@/services/linked-document-service';
import { useEffect, useState } from 'react';
import type { RunDocumentAction } from './document-settings';

export function DocumentImplementation({
  workspaceId,
  document,
  disabled,
  run,
  initiallyOpen = false,
}: {
  workspaceId: string;
  document: WorkspaceDocument;
  disabled: boolean;
  run: RunDocumentAction;
  initiallyOpen?: boolean;
}) {
  const [opened, setOpened] = useState(initiallyOpen);
  const [comment, setComment] = useState('');
  const [evidence, setEvidence] = useState('');
  const [reason, setReason] = useState('');
  useEffect(() => {
    if (initiallyOpen) setOpened(true);
  }, [initiallyOpen]);
  const number = (id: string | null | undefined) =>
    document.revisions.find((revision) => revision.id === id)?.number;
  if (document.source_kind !== 'local') return null;
  return (
    <section
      aria-label="Реализация документа"
      className="my-4 space-y-2 rounded border border-border-button p-3 text-sm"
    >
      <p>
        Согласование:{' '}
        {document.approved_revision_id
          ? `утверждена редакция ${number(document.approved_revision_id)}`
          : 'редакция не утверждена'}
        .
      </p>
      <p>
        Реализация:{' '}
        {document.implemented_revision_id
          ? `подтверждена редакция ${number(document.implemented_revision_id)}`
          : 'не подтверждена'}
        .
      </p>
      {document.implemented_revision_id &&
        document.implemented_revision_id !== document.current_revision_id && (
          <p>Текущая рабочая редакция не наследует подтверждение реализации.</p>
        )}
      {document.can_implement && (
        <>
          <Button
            variant="outline"
            disabled={disabled || !document.approved_revision_id}
            onClick={() => setOpened(!opened)}
          >
            Реализация и история
          </Button>
          {opened && (
            <>
              {document.approved_revision_id !==
                document.implemented_revision_id && (
                <form
                  className="space-y-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void run(async () => {
                      const item = await confirmDocumentImplementation(
                        workspaceId,
                        document,
                        comment,
                        evidence,
                      );
                      setComment('');
                      setEvidence('');
                      return item;
                    });
                  }}
                >
                  <p>
                    Подтвердить реализацию утверждённой редакции{' '}
                    {number(document.approved_revision_id)} целиком.
                  </p>
                  <Input
                    aria-label="Комментарий о реализации"
                    value={comment}
                    disabled={disabled}
                    onChange={(event) => setComment(event.target.value)}
                    maxLength={6000}
                  />
                  <Input
                    aria-label="Ссылка на подтверждающий материал"
                    placeholder="Необязательная ссылка https://…"
                    type="url"
                    value={evidence}
                    disabled={disabled}
                    onChange={(event) => setEvidence(event.target.value)}
                    maxLength={2000}
                  />
                  <Button disabled={disabled || !document.approved_revision_id}>
                    Подтвердить реализацию
                  </Button>
                </form>
              )}
              {document.implementation_event_id && (
                <form
                  className="space-y-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void run(async () => {
                      const item = await revokeDocumentImplementation(
                        workspaceId,
                        document,
                        reason,
                      );
                      setReason('');
                      return item;
                    });
                  }}
                >
                  <Input
                    aria-label="Причина отмены реализации"
                    value={reason}
                    required
                    disabled={disabled}
                    onChange={(event) => setReason(event.target.value)}
                    maxLength={6000}
                  />
                  <Button
                    variant="outline"
                    disabled={disabled || !reason.trim()}
                  >
                    Отменить подтверждение реализации
                  </Button>
                  <p>
                    Текст зависимых документов сохранится. Их основания
                    потребуют повторной проверки.
                  </p>
                </form>
              )}
            </>
          )}
        </>
      )}
      <details>
        <summary className="cursor-pointer">История реализации</summary>
        <ul>
          {document.implementation_history?.map((event) => (
            <li key={event.id} className="mt-2">
              {event.kind === 'implementation_confirmed'
                ? 'Подтверждение'
                : 'Отмена'}{' '}
              · редакция {number(event.payload.revision_id)} ·{' '}
              {new Date(event.created_at).toLocaleString('ru')} ·{' '}
              {event.actor_name}
              <p>{event.payload.comment}</p>
              {event.payload.evidence_url && (
                <a
                  className="underline"
                  href={event.payload.evidence_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Подтверждающий материал
                </a>
              )}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
