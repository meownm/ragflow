import { Badge } from '@/components/ui/badge';
import { FileText } from 'lucide-react';
import {
  BLOCK_TYPE_OPTIONS,
  numberSections,
  type DocumentTemplateDraft,
} from './model';

export function TemplatePreview({ draft }: { draft: DocumentTemplateDraft }) {
  const sections = numberSections(draft.sections);

  return (
    <div
      className="animate-in mx-auto max-w-[860px] px-6 py-10 duration-300 fade-in slide-in-from-bottom-2 md:px-10"
      data-testid="document-constructor-preview"
    >
      <header className="border-b border-border-button pb-7">
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-accent-primary">
          <FileText className="size-4" />
          Предпросмотр шаблона
        </div>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight text-text-primary">
          {draft.name.trim() || 'Шаблон без названия'}
        </h1>
        {draft.description.trim() && (
          <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">
            {draft.description}
          </p>
        )}
        <div className="mt-5 flex flex-wrap gap-x-4 gap-y-2 font-mono text-xs text-text-secondary">
          <span>{draft.documentType || 'document_type'}</span>
          <span>v{draft.version || '0.0.0'}</span>
          <span>{draft.language.toUpperCase()}</span>
        </div>
      </header>

      {!sections.length ? (
        <div className="py-16 text-center text-sm text-text-secondary">
          Добавьте разделы, чтобы увидеть структуру документа.
        </div>
      ) : (
        <article className="pt-4">
          {sections.map((section) => {
            const blockLabels = section.allowedBlocks
              .map(
                (block) =>
                  BLOCK_TYPE_OPTIONS.find((option) => option.value === block)
                    ?.label,
              )
              .filter(Boolean);
            return (
              <section
                key={section.uid}
                className="border-b border-border-button py-7"
                style={{ marginInlineStart: `${section.depth * 26}px` }}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <h2
                    className="font-semibold tracking-tight text-text-primary"
                    style={{
                      fontSize: `${Math.max(15, 22 - section.depth * 2)}px`,
                    }}
                  >
                    {draft.preserveSectionNumbers && (
                      <span className="me-2 font-mono text-[0.78em] font-normal text-text-disabled">
                        {section.resolvedId}
                      </span>
                    )}
                    {section.title.trim() || 'Раздел без названия'}
                  </h2>
                  <Badge variant={section.required ? 'secondary' : 'outline'}>
                    {section.required ? 'Обязательный' : 'Необязательный'}
                  </Badge>
                </div>

                {section.purpose.trim() && (
                  <p className="mt-3 text-sm leading-6 text-text-secondary">
                    {section.purpose}
                  </p>
                )}

                <div className="mt-4 flex flex-wrap gap-1.5">
                  {blockLabels.map((label) => (
                    <span
                      key={label}
                      className="rounded bg-bg-card px-2 py-1 text-[11px] text-text-secondary"
                    >
                      {label}
                    </span>
                  ))}
                  {(section.minWords || section.maxWords) && (
                    <span className="rounded bg-bg-card px-2 py-1 text-[11px] text-text-secondary">
                      {section.minWords ? `от ${section.minWords}` : ''}
                      {section.minWords && section.maxWords ? ' до ' : ''}
                      {section.maxWords ? `${section.maxWords}` : ''} слов
                    </span>
                  )}
                </div>

                {section.requirements.some((requirement) =>
                  requirement.trim(),
                ) && (
                  <div className="mt-5 border-s-2 border-accent-primary/50 ps-4">
                    <p className="text-xs font-medium uppercase tracking-wide text-text-secondary">
                      Требования
                    </p>
                    <ul className="mt-2 list-disc space-y-1.5 ps-4 text-sm leading-6 text-text-primary">
                      {section.requirements
                        .filter((requirement) => requirement.trim())
                        .map((requirement, index) => (
                          <li key={`${section.uid}-preview-${index}`}>
                            {requirement}
                          </li>
                        ))}
                    </ul>
                  </div>
                )}
              </section>
            );
          })}
        </article>
      )}
    </div>
  );
}
