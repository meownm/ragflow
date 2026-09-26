export function evidenceRefsChanged(before?: string[], after?: string[]) {
  return JSON.stringify(before ?? []) !== JSON.stringify(after ?? []);
}

export function EvidenceRefsDiff({
  before,
  after,
}: {
  before?: string[];
  after?: string[];
}) {
  if (!evidenceRefsChanged(before, after)) return null;

  return (
    <div className="grid gap-2 border-t border-border-button p-3 text-xs md:grid-cols-2">
      {[
        { title: 'Источники до', refs: before ?? [] },
        { title: 'Источники после', refs: after ?? [] },
      ].map(({ title, refs }) => (
        <div key={title} className="min-w-0">
          <div className="font-semibold text-text-secondary">{title}</div>
          {refs.length ? (
            <ul className="mt-1 space-y-1">
              {refs.map((ref) => (
                <li key={ref} className="break-all font-mono text-text-primary">
                  {ref}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-text-secondary">Нет источников</p>
          )}
        </div>
      ))}
    </div>
  );
}
