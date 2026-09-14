import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { SqlQueryCompileResponse } from '@/pages/business-documents/types';
import { CheckCircle2, LoaderCircle } from 'lucide-react';
import type { QueryDraftIssue } from './query-specification';

export function QueryCompilerPanel({
  issues,
  result,
  error,
  compiling,
  onCompile,
}: {
  issues: QueryDraftIssue[];
  result: SqlQueryCompileResponse | null;
  error: string | null;
  compiling: boolean;
  onCompile: () => void;
}) {
  return (
    <section className="space-y-3 border-t border-border-button pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">
            6. Серверная проверка и SQL
          </h3>
          <p className="mt-1 text-xs text-text-secondary">
            Компилятор принимает только закрытую спецификацию и каталоговые ID.
            SQLGuard повторно разбирает готовый SQL.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            data-testid="query-compile-status"
            className={
              result?.status === 'READY'
                ? 'border-state-success/40 text-state-success'
                : 'border-state-warning/40 text-state-warning'
            }
          >
            {result?.status ||
              (issues.length ? 'NEEDS_CLARIFICATION' : 'READY_TO_COMPILE')}
          </Badge>
          <Button
            type="button"
            onClick={onCompile}
            disabled={compiling || issues.length > 0}
            data-testid="query-compile"
          >
            {compiling ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <CheckCircle2 className="size-4" />
            )}
            {compiling ? 'Проверяем' : 'Собрать SQL'}
          </Button>
        </div>
      </div>

      {issues.length > 0 && (
        <ul className="space-y-1 text-xs text-state-warning">
          {issues.map((issue, index) => (
            <li key={`${issue.code}-${issue.path}-${index}`}>
              {issue.message}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="text-xs text-state-error" role="alert">
          {error}
        </p>
      )}
      {result?.blocking_issues.length ? (
        <ul className="space-y-1 text-xs text-state-warning">
          {result.blocking_issues.map((issue) => (
            <li key={`${issue.code}-${issue.path}`}>{issue.message}</li>
          ))}
        </ul>
      ) : null}
      {result?.status === 'READY' && result.sql && (
        <div className="space-y-3">
          <div>
            <h4 className="text-xs font-semibold">SQL</h4>
            <pre
              className="mt-1 overflow-x-auto rounded-md border border-border-button bg-bg-card p-3 text-xs"
              data-testid="query-sql-output"
            >
              {result.sql}
            </pre>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <h4 className="text-xs font-semibold">Параметры</h4>
              <pre className="mt-1 overflow-x-auto rounded-md border border-border-button bg-bg-card p-3 text-xs">
                {JSON.stringify(result.parameters, null, 2)}
              </pre>
            </div>
            <div>
              <h4 className="text-xs font-semibold">SQLGuard</h4>
              <pre className="mt-1 overflow-x-auto rounded-md border border-border-button bg-bg-card p-3 text-xs">
                {JSON.stringify(result.guard, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
