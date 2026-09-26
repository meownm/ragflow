import type { BusinessDocumentReviewCycle } from '../types';

export function ReviewActivity({
  reviewCycle,
  onFocusSection,
}: {
  reviewCycle: BusinessDocumentReviewCycle;
  onFocusSection: (sectionId: string) => void;
}) {
  const counts = new Map<string, number>();
  let documentWide = 0;
  for (const question of reviewCycle.questions) {
    if (question.status === 'CANCELLED') continue;
    if (question.target_section_id)
      counts.set(
        question.target_section_id,
        (counts.get(question.target_section_id) ?? 0) + 1,
      );
  }
  for (const proposal of reviewCycle.proposals) {
    if (proposal.decision === 'REJECTED') continue;
    if (proposal.target_section_id)
      counts.set(
        proposal.target_section_id,
        (counts.get(proposal.target_section_id) ?? 0) + 1,
      );
  }
  for (const comment of reviewCycle.comments) {
    if (comment.section_id)
      counts.set(comment.section_id, (counts.get(comment.section_id) ?? 0) + 1);
    else documentWide += 1;
  }
  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b border-border-button bg-bg-card/40 px-5 py-2 text-xs"
      data-testid="business-document-review-activity"
    >
      <span className="font-medium text-text-secondary">
        Замечания анализируются:
      </span>
      {[...counts.entries()].map(([sectionId, count]) => (
        <button
          key={sectionId}
          type="button"
          className="rounded-full border border-accent-primary/30 bg-accent-primary/5 px-2.5 py-1 text-accent-primary hover:bg-accent-primary/10"
          onClick={() => onFocusSection(sectionId)}
        >
          § {sectionId} · {count}
        </button>
      ))}
      {documentWide > 0 && (
        <span className="rounded-full border border-border-button px-2.5 py-1 text-text-secondary">
          Весь документ · {documentWide}
        </span>
      )}
      {counts.size === 0 && documentWide === 0 && (
        <span className="text-text-disabled">
          Обрабатываем вводные и связи между разделами
        </span>
      )}
    </div>
  );
}
