import { Button } from '@/components/ui/button';
import type { AssistantDialogueOptions } from '@/interfaces/database/sql-assistant';

export function MetricChoice({
  metrics,
  busy,
  canUse,
  onUse,
  exploratory,
  onExplore,
}: {
  metrics: AssistantDialogueOptions['metrics'];
  busy: boolean;
  canUse: boolean;
  onUse: (id: string, version: number) => void;
  exploratory: boolean;
  onExplore: (value: boolean) => void;
}) {
  if (!metrics.length) return null;
  return (
    <section
      className="my-3 space-y-2 rounded-lg border border-border-button p-3"
      aria-label="Определения для вопроса"
    >
      <p className="text-sm font-semibold">Найдены согласованные определения</p>
      <p className="text-xs text-text-secondary">
        Проверьте смысл показателя перед новым расчётом. Совпадение названия не
        подтверждает формулу.
      </p>
      {metrics.map((item) => (
        <div key={item.id} className="space-y-1 break-words">
          <p className="text-sm">
            {item.title} · версия {item.version}
          </p>
          <p className="text-xs text-text-secondary">{item.description}</p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-auto max-w-full whitespace-normal py-2 text-left"
            aria-label={`Выбрать определение «${item.title}»`}
            disabled={busy || !canUse}
            onClick={() => onUse(item.id, item.version)}
          >
            Выбрать определение
          </Button>
        </div>
      ))}
      {!canUse && (
        <p className="text-xs">
          Завершите текущую подготовку и примите исходный запрос, чтобы создать
          черновик по определению.
        </p>
      )}
      <label className="flex items-start gap-2 text-xs">
        <input
          type="checkbox"
          checked={exploratory}
          disabled={busy}
          onChange={(event) => onExplore(event.target.checked)}
        />
        Продолжить как исследовательский расчёт без применения определения
      </label>
    </section>
  );
}
