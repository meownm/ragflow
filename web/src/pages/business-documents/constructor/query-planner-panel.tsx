import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { SqlQueryPlanResponse } from '@/pages/business-documents/types';
import { LoaderCircle, Sparkles } from 'lucide-react';

export function QueryPlannerPanel({
  result,
  error,
  planning,
  onPlan,
}: {
  result: SqlQueryPlanResponse | null;
  error: string | null;
  planning: boolean;
  onPlan: () => void;
}) {
  return (
    <section
      className="space-y-3 rounded-md border border-accent-primary/30 bg-accent-primary/5 p-4"
      data-testid="query-llm-planner"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <Sparkles className="size-4 text-accent-primary" />
            Черновик через LLM
          </h3>
          <p className="mt-1 max-w-3xl text-xs text-text-secondary">
            Tenant-модель предложит только структуру из подтверждённых catalog
            ID. Она не создаёт SQL и не может подтвердить свои JOIN или WHERE.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {result && (
            <Badge
              variant="outline"
              data-testid="query-plan-status"
              className={
                result.status === 'PROPOSED'
                  ? 'border-state-success/40 text-state-success'
                  : 'border-state-warning/40 text-state-warning'
              }
            >
              {result.status}
            </Badge>
          )}
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onPlan}
            disabled={planning}
            data-testid="query-plan"
          >
            {planning ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Sparkles className="size-4" />
            )}
            {planning ? 'Планируем' : 'Предложить через LLM'}
          </Button>
        </div>
      </div>
      {result?.proposal && (
        <p className="text-xs text-text-secondary">
          Черновик применён. Проверьте поля и явно подтвердите каждое
          предложенное соединение и условие фильтрации.
        </p>
      )}
      {result?.clarification_questions.length ? (
        <div className="space-y-1 text-xs text-state-warning">
          <p className="font-semibold">Нужно уточнить:</p>
          <ul className="list-disc space-y-1 ps-5">
            {result.clarification_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {(result?.warning || error) && (
        <p className="text-xs text-state-warning" role="alert">
          {error || result?.warning}
        </p>
      )}
      {result?.llm.prompt && (
        <p className="text-[11px] text-text-secondary">
          Prompt: {result.llm.prompt.name} v{result.llm.prompt.version} ·{' '}
          {result.llm.prompt.content_hash}
        </p>
      )}
    </section>
  );
}
