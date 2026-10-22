import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Check, X } from 'lucide-react';
import type { BusinessDocumentProposal } from '../types';
import { BusinessDocumentError } from './document-error';
import { DocumentMarkdown } from './document-markdown';

interface ProposalItemProps {
  proposal: BusinessDocumentProposal;
  proposalDecisionsOpen: boolean;
  canDecide: boolean;
  canCancelAcceptance: boolean;
  pending: boolean;
  onDecide: (payload: Record<string, unknown>) => void;
  onCancelAcceptance: () => void;
}

const proposalStatusPresentation = {
  PENDING: {
    label: 'Ожидает решения',

    badgeClass:
      'border-state-warning/20 bg-state-warning/10 text-state-warning',
  },
  ACCEPTED: {
    label: 'Принято',

    badgeClass:
      'border-state-success/20 bg-state-success/10 text-state-success',
  },
  REJECTED: {
    label: 'Отклонено',

    badgeClass: 'border-state-error/20 bg-state-error/10 text-state-error',
  },
} satisfies Record<
  BusinessDocumentProposal['decision'],
  {
    label: string;

    badgeClass: string;
  }
>;

const closedPendingProposalPresentation = {
  label: 'Не принято',

  badgeClass: 'border-border-button bg-bg-card text-text-disabled',
};

const applicationMessages = {
  QUEUED: 'В очереди. Применим к последней сохранённой ревизии.',
  WAITING: 'Предложение ожидает применения.',
  PENDING: 'Применение ожидает запуска задачи.',
  RUNNING: 'Анализируем и вносим изменения. Можно продолжать обсуждение.',
  RETRY:
    'Попытка применения завершилась ошибкой. Запланирован автоматический повтор.',
  DEAD: 'Не удалось применить предложение. Повторите попытку или отмените принятие.',
  PREPARED:
    'Изменения подготовлены. Для сохранения нажмите «Применить исправления». После отмены принятия потребуется подготовить их заново.',
  PREVIEW_STALE:
    'Подготовленные изменения больше не актуальны или были отклонены. Подготовьте изменения заново или отмените принятие.',
  COMPLETED:
    'Задача завершена, но применение этого предложения не подтверждено. Подготовьте изменения заново или отмените принятие.',
} satisfies Record<
  NonNullable<BusinessDocumentProposal['application']>['status'],
  string
>;

export function ProposalItem({
  proposal,
  proposalDecisionsOpen,
  canDecide,
  canCancelAcceptance,
  pending,
  onDecide,
  onCancelAcceptance,
}: ProposalItemProps) {
  const isPending = proposal.decision === 'PENDING';
  const isAwaitingApplication =
    proposal.decision === 'ACCEPTED' && proposal.applied === false;
  const isClosedWithoutDecision = isPending && !proposalDecisionsOpen;
  const status = isClosedWithoutDecision
    ? closedPendingProposalPresentation
    : proposalStatusPresentation[proposal.decision];

  return (
    <article
      data-protocol-kind="proposal"
      data-protocol-id={proposal.proposal_id}
      className={cn(
        'relative mx-4 my-3 overflow-hidden rounded-lg border bg-bg-base p-3.5 transition-colors duration-200',
        isAwaitingApplication ? 'border-state-warning' : 'border-border-button',
      )}
      data-testid="business-document-proposal"
      data-status={proposal.decision}
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              Предложение
            </span>
            <Badge
              variant="outline"
              className={cn(
                'h-5 px-2 py-0 text-[10px] font-medium leading-none',
                isAwaitingApplication
                  ? 'border-state-warning/20 bg-state-warning/10 text-state-warning'
                  : status.badgeClass,
              )}
              data-testid={`proposal-status-${proposal.proposal_id}`}
            >
              {proposal.applied
                ? 'Применено'
                : isAwaitingApplication
                  ? 'Принято к применению'
                  : status.label}
            </Badge>
          </div>
          <DocumentMarkdown className="mt-2 text-sm leading-6 text-text-primary">
            {proposal.text}
          </DocumentMarkdown>
          {proposal.target_section_id && (
            <p className="mt-1 text-[11px] text-text-secondary">
              § {proposal.target_section_id}
            </p>
          )}
          {proposal.rationale && (
            <DocumentMarkdown className="mt-2 text-xs leading-5 text-text-secondary">
              {proposal.rationale}
            </DocumentMarkdown>
          )}
          {isClosedWithoutDecision && (
            <p
              className="mt-3 text-xs leading-5 text-text-secondary"
              data-testid={`closed-proposal-${proposal.proposal_id}`}
            >
              Решение не было принято до завершения этапа внесения изменений.
            </p>
          )}
          {isAwaitingApplication && (
            <div
              className="mt-3 text-xs leading-5 text-text-secondary"
              data-testid={`proposal-application-${proposal.proposal_id}`}
            >
              <p>
                {applicationMessages[proposal.application?.status ?? 'WAITING']}
              </p>
              {proposal.application?.error && (
                <BusinessDocumentError error={proposal.application.error} />
              )}
            </div>
          )}
          {(isPending || isAwaitingApplication) && proposalDecisionsOpen && (
            <div className="mt-4 flex flex-wrap gap-2">
              {isAwaitingApplication && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    !canCancelAcceptance ||
                    pending ||
                    ['PENDING', 'RUNNING', 'RETRY'].includes(
                      proposal.application?.status ?? '',
                    )
                  }
                  data-testid={`cancel-acceptance-${proposal.proposal_id}`}
                  onClick={onCancelAcceptance}
                >
                  <X className="size-3.5" />
                  Отменить принятие
                </Button>
              )}
              {isPending && (
                <Button
                  size="sm"
                  variant="danger"
                  className="dark:text-[#ff8b8d]"
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
              )}
              {(isPending ||
                (isAwaitingApplication &&
                  ['DEAD', 'COMPLETED', 'WAITING', 'PREVIEW_STALE'].includes(
                    proposal.application?.status ?? 'WAITING',
                  ))) && (
                <Button
                  size="sm"
                  variant="accent"
                  className="text-[#102c2a]"
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
                  {isPending ? 'Применить' : 'Повторить применение'}
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
