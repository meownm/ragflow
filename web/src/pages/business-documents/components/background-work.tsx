import { Button } from '@/components/ui/button';
import type { BusinessDocumentProjection } from '../types';

export function BackgroundWork({
  document,
  onFocusSection,
}: {
  document: BusinessDocumentProjection;
  onFocusSection: (sectionId: string) => void;
}) {
  const work = document.background_work ?? [];
  const queued = work.filter((item) => item.status === 'QUEUED');
  const active = work.some((item) => item.status === 'RUNNING');
  const result = document.last_analysis;
  if (!work.length && !result) return null;
  return (
    <div
      className="space-y-2 border-b border-border-button bg-bg-card px-4 py-3 text-xs"
      data-testid="business-document-background-work"
    >
      {(active || queued.length > 0) && (
        <p className="text-text-secondary">
          Работа идёт в фоне. Можно применять другие предложения и добавлять
          комментарии.
        </p>
      )}
      {queued.length > 0 && (
        <p className="font-medium" role="status">
          В очереди: {queued.length}
        </p>
      )}
      {queued.map((item, index) => {
        const proposal = document.protocol.proposals.find(
          (entry) => entry.proposal_id === item.proposal_id,
        );
        return (
          <div
            key={item.request_id}
            className="flex items-center gap-2"
            data-testid="background-queue-item"
          >
            <span className="text-text-secondary">{index + 1}.</span>
            <span className="min-w-0 flex-1 truncate">
              {proposal?.text ?? 'Анализ новых замечаний'}
            </span>
            {proposal?.target_section_id && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onFocusSection(proposal.target_section_id!)}
              >
                § {proposal.target_section_id}
              </Button>
            )}
          </div>
        );
      })}
      {result && (
        <p
          role="status"
          data-testid="business-document-analysis-result"
          className="text-text-secondary"
        >
          {result.superseded
            ? 'Результат предыдущего анализа устарел после новых замечаний.'
            : result.applied
              ? `Анализ завершён: новых вопросов — ${result.question_count ?? 0}, предложений — ${result.proposal_count ?? 0}.`
              : 'Анализ завершён без применения результата. Проверьте замечания.'}
        </p>
      )}
      {work.some((item) => item.status === 'FAILED') && (
        <p className="text-state-warning">
          Есть незавершённые задачи. Повторите применение в предложении или
          запустите анализ снова.
        </p>
      )}
    </div>
  );
}
