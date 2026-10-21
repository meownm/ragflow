import { DocumentMarkdown } from '@/pages/business-documents/components/document-markdown';
import { RevisionTextDiff } from '@/pages/business-documents/components/revision-text-diff';
import { headings, type Revision, type Specification } from './model';

export function RevisionPreview({
  current,
  previous,
}: {
  current: Specification;
  previous: Revision;
}) {
  if (!previous.sections) return null;
  const changed = headings.filter(
    (_, index) =>
      previous.sections?.[String(index + 1)] !==
      current.sections[String(index + 1)],
  );
  return (
    <div className="space-y-4">
      <p className="text-sm text-text-secondary">
        Редакция {previous.number} → редакция {current.revision}. Удалённое
        зачёркнуто, добавленное подчёркнуто.
      </p>
      {previous.title !== current.title && (
        <section aria-label="Изменение названия">
          <h4 className="font-medium">Название</h4>
          <RevisionTextDiff before={previous.title} after={current.title} />
        </section>
      )}
      {changed.length === 0 && previous.title === current.title && (
        <p>Содержимое редакций совпадает.</p>
      )}
      {headings.map((heading, index) => {
        const key = String(index + 1);
        const before = previous.sections?.[key] ?? '';
        const after = current.sections[key];
        return before !== after ? (
          <details key={key} open>
            <summary>
              {key}. {heading}
            </summary>
            <RevisionTextDiff before={before} after={after} />
          </details>
        ) : null;
      })}
      <details>
        <summary>Полный текст редакции {previous.number}</summary>
        <h4 className="my-3 font-medium">{previous.title}</h4>
        {headings.map((heading, index) => (
          <section key={index} className="my-4 space-y-2">
            <h5 className="font-medium">
              {index + 1}. {heading}
            </h5>
            <DocumentMarkdown>
              {previous.sections?.[String(index + 1)] || 'Раздел пуст'}
            </DocumentMarkdown>
          </section>
        ))}
      </details>
    </div>
  );
}
