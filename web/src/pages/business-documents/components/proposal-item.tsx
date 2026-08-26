import { Button } from '@/components/ui/button';
import { Check, Lightbulb, X } from 'lucide-react';
import type { BusinessDocumentProposal } from '../types';

interface ProposalItemProps {
  proposal: BusinessDocumentProposal;
  canDecide: boolean;
  pending: boolean;
  onDecide: (payload: Record<string, unknown>) => void;
}

const statusText: Record<BusinessDocumentProposal['decision'], string> = {
  PENDING: 'Ожидает решения',
  ACCEPTED: 'Принято',
  REJECTED: 'Отклонено',
};

export function ProposalItem({
  proposal,
  canDecide,
  pending,
  onDecide,
}: ProposalItemProps) {
  const isPending = proposal.decision === 'PENDING';

  return (
    <article
      className="border-b border-border-button px-5 py-5 last:border-b-0"
      data-testid="business-document-proposal"
    >
      <div className="flex items-start gap-3">
        <Lightbulb className="mt-0.5 size-4 shrink-0 text-state-warning" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              Предложение
            </span>
            <span className="text-[11px] text-text-secondary">
              {statusText[proposal.decision]}
            </span>
          </div>
          <p className="mt-2 text-sm leading-6 text-text-primary">
            {proposal.text}
          </p>
          {proposal.rationale && (
            <p className="mt-2 text-xs leading-5 text-text-secondary">
              {proposal.rationale}
            </p>
          )}
          {isPending && (
            <div className="mt-4 flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={!canDecide || pending}
                data-testid={`reject-proposal-${proposal.proposal_id}`}
                onClick={() =>
                  onDecide({
                    proposal_id: proposal.proposal_id,
                    decision: 'REJECTED',
                  })
                }
              >
                <X className="size-3.5" />
                Отклонить
              </Button>
              <Button
                size="sm"
                variant="accent"
                disabled={!canDecide || pending}
                loading={pending}
                data-testid={`accept-proposal-${proposal.proposal_id}`}
                onClick={() =>
                  onDecide({
                    proposal_id: proposal.proposal_id,
                    decision: 'ACCEPTED',
                  })
                }
              >
                <Check className="size-3.5" />
                Принять
              </Button>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
