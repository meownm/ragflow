import { Button } from '@/components/ui/button';
import type { ReactNode } from 'react';

export function StatePanel({
  title,
  children,
  onRetry,
  loading = false,
}: {
  title: string;
  children?: ReactNode;
  onRetry?: () => void;
  loading?: boolean;
}) {
  return (
    <section
      className="rf-state-panel"
      role={loading ? 'status' : 'region'}
      aria-label={title}
      aria-busy={loading}
    >
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {onRetry && (
        <Button variant="outline" onClick={onRetry}>
          Повторить
        </Button>
      )}
    </section>
  );
}
