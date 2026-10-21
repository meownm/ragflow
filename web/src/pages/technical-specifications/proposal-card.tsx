import { Button } from '@/components/ui/button';
import { DocumentMarkdown } from '../business-documents/components/document-markdown';
import { headings, type Proposal, type Sections } from './model';
import { EvaChoices, ProposalPreview } from './proposal-preview';

export function ProposalCard({
  proposal,
  local,
  section,
  disabled,
  onAction,
  onDiscuss,
  onDismiss,
}: {
  proposal: Proposal;
  local: Sections;
  section?: string;
  disabled: boolean;
  onAction: (name: string, data: object) => void;
  onDiscuss: () => void;
  onDismiss: () => void;
}) {
  const changed = headings
    .map((_, index) => String(index + 1))
    .filter((key) => proposal.sections[key] !== local[key]);
  if (section && !changed.includes(section)) return null;
  const footer = !section || section === changed.at(-1);
  return (
    <section className="tz-proposal" aria-label="Предложенные изменения">
      <div className="tz-proposal-heading">
        <h3>Правки к редакции {proposal.base_revision}</h3>
        <span>Разделы {changed.join(', ') || 'без изменений'}</span>
      </div>
      {footer && !!proposal.issues?.length && (
        <ul role="alert" className="list-disc space-y-1 pl-5 text-state-error">
          {proposal.issues.map((issue, index) => (
            <li key={index}>{issue}</li>
          ))}
        </ul>
      )}
      {proposal.status === 'needs_resolution' && footer && (
        <p>
          {proposal.source === 'eva'
            ? 'Изменены одни и те же требования. Выберите значения для связанных разделов; независимые правки обеих сторон сохраняются.'
            : 'Опишите исправление в чате. Помощнику доступны предложение и найденные противоречия.'}
        </p>
      )}
      <ProposalPreview
        local={local}
        candidate={proposal.sections}
        section={section}
      />
      {proposal.conflicts
        ?.filter((conflict) => !section || conflict.section === section)
        .map((conflict) => (
          <details key={conflict.section} open>
            <summary>Раздел {conflict.section} · конфликт</summary>
            <div className="tz-conflict-pair">
              <div>
                <p>Мои значения</p>
                <DocumentMarkdown>{conflict.local}</DocumentMarkdown>
              </div>
              <div>
                <p>Значения Eva</p>
                <DocumentMarkdown>{conflict.remote}</DocumentMarkdown>
              </div>
            </div>
          </details>
        ))}
      {footer && (
        <div className="tz-proposal-actions">
          {proposal.status === 'ready' && (
            <Button
              disabled={disabled}
              onClick={() => onAction('apply', { proposal_id: proposal.id })}
            >
              Применить изменения
            </Button>
          )}
          {proposal.source === 'eva' ? (
            <EvaChoices
              disabled={disabled}
              onChoose={(choice) =>
                onAction('resolve-eva', { proposal_id: proposal.id, choice })
              }
            />
          ) : (
            <Button variant="outline" onClick={onDiscuss}>
              Обсудить всё ТЗ
            </Button>
          )}
          <Button variant="ghost" onClick={onDismiss}>
            Убрать предложение
          </Button>
        </div>
      )}
    </section>
  );
}
