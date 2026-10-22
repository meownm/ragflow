import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';

export const QualityPageSize = 25;

export function QualityPagination({
  page,
  total,
  onPage,
  label,
  name,
}: {
  page: number;
  total: number;
  onPage: (page: number) => void;
  label: (key: string) => string;
  name: string;
}) {
  const offset = (page - 1) * QualityPageSize;
  return (
    <nav
      aria-label={name}
      className="flex shrink-0 flex-wrap items-center gap-3 text-sm text-text-secondary"
    >
      <Button
        size="sm"
        variant="outline"
        disabled={page === 1}
        onClick={() => onPage(page - 1)}
      >
        {label('previousPage')}
      </Button>
      <span aria-live="polite">
        {total
          ? `${offset + 1}–${Math.min(offset + QualityPageSize, total)}/${total}`
          : '0/0'}
      </span>
      <Button
        size="sm"
        variant="outline"
        disabled={page * QualityPageSize >= total}
        onClick={() => onPage(page + 1)}
      >
        {label('nextPage')}
      </Button>
    </nav>
  );
}

export function QualityPagedBlock<T>({
  items,
  name,
  label,
  children,
}: {
  items: readonly T[];
  name: string;
  label: (key: string) => string;
  children: (rows: readonly T[]) => ReactNode;
}) {
  const [requestedPage, setPage] = useState(1);
  const page = Math.min(
    requestedPage,
    Math.max(1, Math.ceil(items.length / QualityPageSize)),
  );
  const viewport = useRef<HTMLDivElement>(null);
  const previousPage = useRef(page);
  useLayoutEffect(() => {
    if (viewport.current) viewport.current.scrollTop = 0;
    if (previousPage.current !== page) {
      viewport.current?.focus({ preventScroll: true });
      viewport.current?.scrollIntoView?.({ block: 'start', behavior: 'auto' });
    }
    previousPage.current = page;
  }, [page]);
  return (
    <div className="quality-paged-block flex min-h-0 flex-col gap-3">
      <div
        ref={viewport}
        role="region"
        aria-label={name}
        tabIndex={0}
        className="quality-block-scroll"
      >
        {children(
          items.slice((page - 1) * QualityPageSize, page * QualityPageSize),
        )}
      </div>
      {items.length > QualityPageSize && (
        <QualityPagination
          page={page}
          total={items.length}
          onPage={setPage}
          label={label}
          name={name}
        />
      )}
    </div>
  );
}
