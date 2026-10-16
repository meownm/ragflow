import { Button } from '@/components/ui/button';
import { CheckCircle2, X } from 'lucide-react';
import type { BusinessDocumentChangePreview } from '../types';
import { EvidenceRefsDiff } from './evidence-refs-diff';

type FocusSource = (
  kind: 'question' | 'proposal' | 'comment',
  id: string,
) => void;

function ChangeSources({
  sources,
  onFocusSource,
}: {
  sources: BusinessDocumentChangePreview['acknowledged_no_change_sources'];
  onFocusSource?: FocusSource;
}) {
  return sources?.map((source) =>
    source.entity_id && source.kind !== 'eva' ? (
      <button
        key={source.event_id}
        type="button"
        className="block text-left text-accent-primary hover:underline"
        onClick={() =>
          onFocusSource?.(
            source.kind as 'question' | 'proposal' | 'comment',
            source.entity_id!,
          )
        }
      >
        {source.label}
        {source.text ? `: ${source.text}` : ''} · показать в обсуждении
      </button>
    ) : (
      <p key={source.event_id}>
        {source.label}
        {source.text ? `: ${source.text}` : ''}
      </p>
    ),
  );
}

export function DocumentSectionChange({
  change,
  onFocusSource,
}: {
  change: BusinessDocumentChangePreview['sections'][number];
  onFocusSource?: FocusSource;
}) {
  return (
    <details className="mb-5 text-xs text-text-secondary">
      <summary className="cursor-pointer py-1 font-medium text-accent-primary">
        Прежний текст и основания · {change.source_event_ids.length}
      </summary>
      <pre className="my-2 whitespace-pre-wrap break-words border-s-2 border-border-button ps-3 font-sans text-sm leading-6">
        {change.before || 'Требования отсутствуют'}
      </pre>
      <EvidenceRefsDiff
        before={change.before_evidence_refs}
        after={change.after_evidence_refs}
      />
      <ChangeSources sources={change.sources} onFocusSource={onFocusSource} />
    </details>
  );
}

export function ChangePreviewPanel({
  preview,
  pending,
  canConfirm,
  canDiscard,
  onFocusSource,
  onConfirm,
  onDiscard,
  preliminary = false,
}: {
  preview: BusinessDocumentChangePreview;
  pending: boolean;
  canConfirm: boolean;
  canDiscard: boolean;
  onFocusSource?: FocusSource;
  onConfirm?: () => void;
  onDiscard?: () => void;
  preliminary?: boolean;
}) {
  return (
    <section
      className="shrink-0 border-b border-accent-primary/30 bg-accent-primary/5 px-5 py-3"
      data-testid="business-document-change-preview"
      aria-label={
        preliminary ? 'Предварительные изменения' : 'Предпросмотр исправлений'
      }
    >
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-text-primary">
            {preliminary
              ? 'Предварительные изменения · проверка продолжается'
              : 'Предпросмотр исправлений · ещё не применено'}
          </h2>
          <p className="mt-1 text-xs text-text-secondary">
            {preview.sections.length
              ? preliminary
                ? `Обновлено разделов в документе: ${preview.sections.length}. Текст предварительный; подтвердить можно после полной проверки.`
                : `Изменения показаны в документе. Разделов: ${preview.sections.length}. Проверьте текст до подтверждения.`
              : 'Изменений текста нет. Подтверждение завершит текущий цикл согласования.'}
          </p>
        </div>
        {!preliminary && canDiscard && onDiscard && (
          <Button
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={onDiscard}
          >
            <X className="size-4" />
            Отказаться
          </Button>
        )}
        {!preliminary && canConfirm && onConfirm && (
          <Button
            size="sm"
            variant="accent"
            disabled={pending}
            onClick={onConfirm}
          >
            <CheckCircle2 className="size-4" />
            Подтвердить применение
          </Button>
        )}
      </div>
      {preview.acknowledged_no_change_event_ids.length > 0 && (
        <div className="mt-3 space-y-1 text-xs text-text-secondary">
          <h3 className="font-semibold">
            Учтено без изменения текста:{' '}
            {preview.acknowledged_no_change_event_ids.length}
          </h3>
          <ChangeSources
            sources={preview.acknowledged_no_change_sources}
            onFocusSource={onFocusSource}
          />
        </div>
      )}
    </section>
  );
}
