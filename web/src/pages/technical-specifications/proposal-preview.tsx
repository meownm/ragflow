import { Button } from '@/components/ui/button';
import { DocumentMarkdown } from '@/pages/business-documents/components/document-markdown';
import { RevisionTextDiff } from '@/pages/business-documents/components/revision-text-diff';
import { headings, type Sections } from './model';

export function ProposalPreview({
  local,
  candidate,
  includeUnchanged = false,
  expanded = true,
  section,
}: {
  local: Sections;
  candidate: Sections;
  includeUnchanged?: boolean;
  expanded?: boolean;
  section?: string;
}) {
  return (
    <>
      {headings.map((heading, index) => {
        const key = String(index + 1);
        const changed = candidate[key] !== local[key];
        return (
          (!section || key === section) &&
          (changed || includeUnchanged) && (
            <details key={key} open={changed && expanded}>
              <summary>
                {key}. {heading}
              </summary>
              <p className="mt-3 text-xs text-text-secondary">
                Удалённое зачёркнуто, добавленное подчёркнуто.
              </p>
              <RevisionTextDiff
                before={local[key] ?? ''}
                after={candidate[key] ?? ''}
                beforeLabel="Сейчас"
                afterLabel="После применения"
                emptyText="Раздел пуст"
                className="tz-proposal-comparison mt-3 grid min-w-0 gap-4 text-sm"
              />
              <details className="mt-3">
                <summary>Текст с форматированием</summary>
                <div className="tz-proposal-comparison mt-3 grid min-w-0 gap-4">
                  <div className="min-w-0">
                    <p className="mb-2 text-sm text-text-secondary">
                      Исходный текст
                    </p>
                    <DocumentMarkdown>
                      {local[key] || 'Раздел пуст'}
                    </DocumentMarkdown>
                  </div>
                  <div className="min-w-0">
                    <p className="mb-2 text-sm text-text-secondary">
                      Предлагаемый текст
                    </p>
                    <DocumentMarkdown>
                      {candidate[key] || 'Раздел пуст'}
                    </DocumentMarkdown>
                  </div>
                </div>
              </details>
            </details>
          )
        );
      })}
    </>
  );
}

export function EvaChoices({
  disabled,
  onChoose,
}: {
  disabled: boolean;
  onChoose: (choice: 'local' | 'remote') => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        variant="outline"
        disabled={disabled}
        onClick={() => onChoose('local')}
      >
        Сохранить мои значения
      </Button>
      <Button
        variant="outline"
        disabled={disabled}
        onClick={() => onChoose('remote')}
      >
        Использовать значения Eva
      </Button>
    </div>
  );
}
