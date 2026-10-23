import { Button } from '@/components/ui/button';
import { listBusinessDocumentRevisions } from '@/services/business-document-service';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';
import type {
  BusinessDocumentProposal,
  BusinessDocumentQuestion,
  BusinessDocumentReviewCycle,
} from '../types';
import { DocumentMarkdown } from './document-markdown';
import { RevisionTextDiff } from './revision-text-diff';

function savedQuestionAnswer(question: BusinessDocumentQuestion) {
  return (
    question.answer?.custom_answer ||
    question.options.find(
      (option) => option.option_id === question.answer?.selected_option_id,
    )?.label
  );
}

export function ReviewHistory({
  documentId,
  cycles,
  focusId,
}: {
  documentId: string;
  cycles: Array<BusinessDocumentReviewCycle & { review_cycle?: number }>;
  focusId?: string;
}) {
  const hasApplied = cycles.some((cycle) =>
    cycle.proposals.some((proposal) => proposal.applied),
  );
  const revisions = useQuery({
    queryKey: ['business-document-revisions', documentId],
    queryFn: () => listBusinessDocumentRevisions(documentId),
    enabled: hasApplied,
    retry: false,
  });
  const rows = [...(revisions.data ?? [])].sort(
    (a, b) => a.revision_number - b.revision_number,
  );
  const appliedRevision = (proposal: BusinessDocumentProposal) =>
    rows.find(
      (row) =>
        proposal.decision_event_id &&
        (row.source_event_ids?.includes(proposal.decision_event_id) ||
          row.change_basis?.some(
            (basis) => basis.event_id === proposal.decision_event_id,
          )),
    );
  return (
    <div className="px-4 pb-4" data-testid="business-document-review-history">
      {hasApplied && revisions.isLoading && (
        <p className="py-3 text-xs text-text-secondary" role="status">
          Загружаем редакции для сравнения…
        </p>
      )}
      {hasApplied && revisions.error && (
        <div role="alert" className="py-3 text-xs text-state-error">
          Не удалось загрузить редакции.{' '}
          <Button size="sm" variant="ghost" onClick={() => revisions.refetch()}>
            Повторить
          </Button>
        </div>
      )}
      {cycles.map((cycle, index) =>
        cycle.questions.length ||
        cycle.proposals.length ||
        cycle.comments.length ? (
          <section
            key={cycle.review_cycle ?? `current-${index}`}
            className="pt-3"
          >
            <p className="pb-1 text-[11px] text-text-secondary">
              {index === 0
                ? 'Текущий разбор'
                : `Предыдущий разбор${cycle.review_cycle !== undefined ? ` · ${cycle.review_cycle}` : ''}`}
            </p>
            {cycle.questions.map((question) => {
              const answer = savedQuestionAnswer(question);
              return (
                <details
                  key={question.question_id}
                  open={focusId === question.question_id || undefined}
                  className="group border-b border-border-button py-3"
                  data-testid="business-document-question"
                  data-status={question.status}
                  data-protocol-kind="question"
                  data-protocol-id={question.question_id}
                >
                  <summary className="cursor-pointer list-none space-y-1.5 [&::-webkit-details-marker]:hidden">
                    <div className="flex items-center gap-2">
                      <span className="flex-1 text-[10px] uppercase text-text-secondary">
                        Вопрос
                      </span>
                      <span
                        className={`rounded px-2 py-0.5 text-[10px] ${answer ? 'bg-state-success/10 text-state-success' : 'bg-bg-card text-text-secondary'}`}
                      >
                        {question.status === 'ANSWERED'
                          ? 'Ответ зафиксирован'
                          : 'Закрыт'}
                      </span>
                      <ChevronDown className="size-3.5 text-text-secondary transition-transform group-open:rotate-180" />
                    </div>
                    <DocumentMarkdown className="text-[13px] font-medium leading-5">
                      {question.text}
                    </DocumentMarkdown>
                    {answer && (
                      <p className="line-clamp-2 text-xs text-text-secondary">
                        Ответ: {answer}
                      </p>
                    )}
                    <p className="text-[11px] text-text-secondary">
                      {question.target_section_id &&
                        `§ ${question.target_section_id} · `}
                      {answer ? 'Ответ сохранён' : 'Закрыт без ответа'}
                    </p>
                  </summary>
                  <div className="mt-3 rounded-md bg-bg-base p-3 text-xs leading-5">
                    {answer ? (
                      <>
                        <p className="mb-1 text-text-secondary">
                          Сохранённый ответ
                        </p>
                        <DocumentMarkdown>{answer}</DocumentMarkdown>
                      </>
                    ) : (
                      'Разбор завершён без ответа на этот вопрос.'
                    )}
                  </div>
                </details>
              );
            })}
            {cycle.proposals.map((proposal) => {
              const revision = appliedRevision(proposal);
              const previous = revision
                ? rows[rows.indexOf(revision) - 1]
                : undefined;
              const sectionId = proposal.target_section_id;
              const before = sectionId
                ? previous?.section_texts[sectionId]
                : previous?.body_markdown;
              const after = sectionId
                ? revision?.section_texts[sectionId]
                : revision?.body_markdown;
              const label = proposal.applied
                ? 'Применено'
                : proposal.decision === 'ACCEPTED'
                  ? 'Принято к применению'
                  : proposal.decision === 'REJECTED'
                    ? 'Отклонено'
                    : 'Не принято';
              return (
                <details
                  key={proposal.proposal_id}
                  open={focusId === proposal.proposal_id || undefined}
                  className="group border-b border-border-button py-3"
                  data-testid="business-document-proposal"
                  data-status={proposal.decision}
                  data-protocol-kind="proposal"
                  data-protocol-id={proposal.proposal_id}
                >
                  <summary className="cursor-pointer list-none space-y-1.5 [&::-webkit-details-marker]:hidden">
                    <div className="flex items-center gap-2">
                      <span className="flex-1 text-[10px] uppercase text-text-secondary">
                        Предложение
                      </span>
                      <span
                        className={`rounded px-2 py-0.5 text-[10px] ${proposal.applied ? 'bg-state-success/10 text-state-success' : proposal.decision === 'ACCEPTED' ? 'bg-state-warning/10 text-state-warning' : 'bg-bg-card text-text-secondary'}`}
                      >
                        {label}
                      </span>
                      <ChevronDown className="size-3.5 text-text-secondary transition-transform group-open:rotate-180" />
                    </div>
                    <DocumentMarkdown className="text-[13px] font-medium leading-5">
                      {proposal.text}
                    </DocumentMarkdown>
                    <p className="text-[11px] text-text-secondary">
                      {sectionId && `§ ${sectionId} · `}
                      {revision
                        ? `Внесено в редакцию ${revision.revision_number}`
                        : proposal.applied
                          ? 'Правка внесена'
                          : proposal.decision === 'PENDING'
                            ? 'Разбор завершён без решения'
                            : proposal.decision === 'ACCEPTED'
                              ? 'Внесение в документ не подтверждено'
                              : 'Предложение отклонено'}
                    </p>
                  </summary>
                  <div className="mt-3 space-y-3 rounded-md bg-bg-base p-3 text-xs leading-5">
                    {proposal.rationale && (
                      <DocumentMarkdown className="text-text-secondary">
                        {proposal.rationale}
                      </DocumentMarkdown>
                    )}
                    {revision && after !== undefined ? (
                      <>
                        <p className="text-text-secondary">
                          Редакция {previous?.revision_number ?? '—'} →{' '}
                          {revision.revision_number}
                          {sectionId ? ` · § ${sectionId}` : ' · весь документ'}
                        </p>
                        <RevisionTextDiff before={before ?? ''} after={after} />
                      </>
                    ) : (
                      <p className="text-text-secondary">
                        {proposal.applied
                          ? 'Сравнение доступно, когда найдена редакция с источником этого решения.'
                          : 'Сохранённое решение. Повторное принятие недоступно.'}
                      </p>
                    )}
                  </div>
                </details>
              );
            })}
            {cycle.comments.map((comment) => (
              <article
                key={comment.comment_id}
                className="border-b border-border-button py-3"
                data-protocol-kind="comment"
                data-protocol-id={comment.comment_id}
              >
                <p className="text-[10px] uppercase text-text-secondary">
                  Комментарий ·{' '}
                  {comment.section_id
                    ? `§ ${comment.section_id}`
                    : 'весь документ'}
                </p>
                <DocumentMarkdown className="mt-2 text-[13px]">
                  {comment.text}
                </DocumentMarkdown>
              </article>
            ))}
          </section>
        ) : null,
      )}
      {!cycles.some(
        (cycle) =>
          cycle.questions.length ||
          cycle.proposals.length ||
          cycle.comments.length,
      ) && (
        <p className="py-8 text-center text-xs text-text-secondary">
          Завершённых вопросов и предложений пока нет
        </p>
      )}
    </div>
  );
}
