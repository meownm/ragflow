type Part = { text: string; kind: 'same' | 'added' | 'removed' };

function textParts(before: string, after: string): Part[] {
  const left = before.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) ?? [];
  const right = after.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) ?? [];
  let start = 0;
  while (
    start < left.length &&
    start < right.length &&
    left[start] === right[start]
  )
    start++;
  let end = 0;
  while (
    end < left.length - start &&
    end < right.length - start &&
    left[left.length - 1 - end] === right[right.length - 1 - end]
  )
    end++;
  const a = left.slice(start, left.length - end);
  const b = right.slice(start, right.length - end);
  const parts: Part[] = [{ text: left.slice(0, start).join(''), kind: 'same' }];
  // Bound work for large replaced sections; preserve their exact text in both views.
  if (a.length * b.length > 250_000) {
    parts.push(
      { text: a.join(''), kind: 'removed' },
      { text: b.join(''), kind: 'added' },
    );
  } else {
    const width = b.length + 1;
    const lengths = new Uint32Array((a.length + 1) * width);
    for (let i = a.length - 1; i >= 0; i--) {
      for (let j = b.length - 1; j >= 0; j--) {
        lengths[i * width + j] =
          a[i] === b[j]
            ? 1 + lengths[(i + 1) * width + j + 1]
            : Math.max(
                lengths[(i + 1) * width + j],
                lengths[i * width + j + 1],
              );
      }
    }
    let i = 0;
    let j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) {
        parts.push({ text: a[i++], kind: 'same' });
        j++;
      } else if (
        i < a.length &&
        (j === b.length ||
          lengths[(i + 1) * width + j] >= lengths[i * width + j + 1])
      ) {
        parts.push({ text: a[i++], kind: 'removed' });
      } else {
        parts.push({ text: b[j++], kind: 'added' });
      }
    }
  }
  parts.push({ text: end ? left.slice(-end).join('') : '', kind: 'same' });
  return parts.reduce<Part[]>((result, part) => {
    const previous = result[result.length - 1];
    if (previous?.kind === part.kind) previous.text += part.text;
    else if (part.text) result.push({ ...part });
    return result;
  }, []);
}

export function RevisionTextDiff({
  before,
  after,
  beforeLabel = 'Было · удалённое зачёркнуто',
  afterLabel = 'Стало · добавленное подчёркнуто',
  emptyText = 'Требования отсутствуют',
  className = 'space-y-3 text-sm',
}: {
  before: string;
  after: string;
  beforeLabel?: string;
  afterLabel?: string;
  emptyText?: string;
  className?: string;
}) {
  const parts = textParts(before, after);
  return (
    <div className={className} data-testid="revision-text-diff">
      <div className="min-w-0">
        <p className="font-medium text-state-error">{beforeLabel}</p>
        <pre className="mt-1 whitespace-pre-wrap break-words font-sans leading-6">
          {before
            ? parts
                .filter((part) => part.kind !== 'added')
                .map((part, index) =>
                  part.kind === 'removed' ? (
                    <del
                      key={index}
                      className="bg-state-error/15 text-state-error decoration-2"
                    >
                      {part.text}
                    </del>
                  ) : (
                    part.text
                  ),
                )
            : emptyText}
        </pre>
      </div>
      <div className="min-w-0">
        <p className="font-medium text-state-success">{afterLabel}</p>
        <pre className="mt-1 whitespace-pre-wrap break-words font-sans leading-6">
          {after
            ? parts
                .filter((part) => part.kind !== 'removed')
                .map((part, index) =>
                  part.kind === 'added' ? (
                    <ins
                      key={index}
                      className="bg-state-success/15 text-state-success decoration-2"
                    >
                      {part.text}
                    </ins>
                  ) : (
                    part.text
                  ),
                )
            : emptyText}
        </pre>
      </div>
    </div>
  );
}
