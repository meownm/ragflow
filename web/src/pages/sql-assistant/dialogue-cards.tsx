import { Button } from '@/components/ui/button';
import type { AssistantCard } from '@/interfaces/database/sql-assistant';
import { useSqlResultDeadline } from '@/pages/business-documents/constructor/sql-result-deadline';
import dayjs from 'dayjs';

export function DialogueCards({
  cards,
  busy,
  canEdit,
  onOpenSql,
  onContinue,
}: {
  cards: AssistantCard[];
  busy: boolean;
  canEdit: boolean;
  onOpenSql: () => void;
  onContinue: () => void;
}) {
  const result = cards.find((card) => card.kind === 'RESULT');
  const deadline = useSqlResultDeadline(
    result?.run_id || '',
    result?.expires_at,
    () => undefined,
  );
  return (
    <div className="my-3 space-y-2" aria-label="Объекты ответа">
      {cards.map((card) => {
        if (card.kind === 'RESULT')
          return deadline.expired ? null : (
            <article
              key={card.run_id}
              className="rounded-lg border border-border-button p-3"
            >
              <p className="text-sm font-semibold">
                Сохранённый результат ·{' '}
                {dayjs(card.created_at).format('DD.MM.YYYY HH:mm')}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={onOpenSql}
                >
                  Открыть SQL результата
                </Button>
                {canEdit && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={onContinue}
                  >
                    Продолжить исследование
                  </Button>
                )}
              </div>
            </article>
          );
        if (card.kind === 'SOURCE')
          return deadline.expired ? null : (
            <details key={card.entity_id} className="break-words text-xs">
              <summary>Источник · {card.title}</summary>
              <p>
                Таблица из принятой схемы выбранного запуска. Доступ проверен
                при чтении результата.
              </p>
            </details>
          );
        return (
          <article
            key={card.proposal_id}
            className="rounded-lg border border-border-button p-3"
          >
            <p className="text-sm font-semibold">Предложение SQL</p>
            <p className="break-words text-sm">{card.summary}</p>
            <details className="mt-2">
              <summary>Сравнить изменения SQL</summary>
              <p className="mt-2 text-xs">До изменения</p>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words">
                {card.before_sql || 'Исходный SQL недоступен'}
              </pre>
              <p className="mt-2 text-xs">Предложение</p>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words">
                {card.after_sql}
              </pre>
            </details>
          </article>
        );
      })}
    </div>
  );
}
