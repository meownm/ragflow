import { Button } from '@/components/ui/button';
import { DocumentMarkdown } from '@/pages/business-documents/components/document-markdown';
import { headings, type Sections } from './model';

export function ProposalPreview({
  local,
  candidate,
  includeUnchanged = false,
  section,
}: {
  local: Sections;
  candidate: Sections;
  includeUnchanged?: boolean;
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
            <details key={key} open={changed}>
              <summary>
                {key}. {heading}
              </summary>
              <div className="mt-3 grid min-w-0 gap-4 md:grid-cols-2">
                <div>
                  <p className="mb-2 text-sm text-text-secondary">Сейчас</p>
                  <DocumentMarkdown>
                    {local[key] || 'Раздел пуст'}
                  </DocumentMarkdown>
                </div>
                <div>
                  <p className="mb-2 text-sm text-text-secondary">
                    После применения
                  </p>
                  <DocumentMarkdown>
                    {candidate[key] || 'Раздел пуст'}
                  </DocumentMarkdown>
                </div>
              </div>
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
