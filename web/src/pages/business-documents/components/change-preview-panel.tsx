import { Button } from '@/components/ui/button';
import { CheckCircle2, X } from 'lucide-react';
import type { BusinessDocumentChangePreview } from '../types';
import { EvidenceRefsDiff } from './evidence-refs-diff';

export function ChangePreviewPanel({
  preview,
  pending,
  canConfirm,
  canDiscard,
  onFocusSection,
  onFocusSource,
  onConfirm,
  onDiscard,
  preliminary = false,
}: {
  preview: BusinessDocumentChangePreview;
  pending: boolean;
  canConfirm: boolean;
  canDiscard: boolean;
  onFocusSection: (sectionId: string) => void;
  onFocusSource?: (
    kind: 'question' | 'proposal' | 'comment',
    id: string,
  ) => void;
  onConfirm?: () => void;
  onDiscard?: () => void;
  preliminary?: boolean;
}) {
  return (
    <section
      className="max-h-[45vh] overflow-y-auto border-b border-accent-primary/30 bg-accent-primary/5 px-5 py-4 scrollbar-auto"
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
                ? `Получено разделов: ${preview.sections.length}. Они могут измениться при повторной попытке; подтвердить можно после полной проверки.`
                : `Будет заменено разделов: ${preview.sections.length}. Проверьте текст до подтверждения.`
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
      <div className="mt-3 space-y-2">
        {preview.sections.map((section) => (
          <details
            key={section.section_id}
            className="rounded-md border border-border-button bg-bg-base"
            data-testid="business-document-change-preview-section"
          >
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-text-primary">
              § {section.section_id} {section.title} ·{' '}
              {preliminary ? 'предварительно' : 'будет изменён'}
            </summary>
            <div className="grid gap-2 border-t border-border-button p-3 md:grid-cols-2">
              <div className="min-w-0 rounded border border-state-error/25 bg-state-error/5 p-3">
                <div className="mb-2 text-xs font-semibold text-state-error">
                  Было
                </div>
                <pre className="overflow-x-auto whitespace-pre-wrap break-words font-sans text-sm leading-6 text-text-primary">
                  {section.before || 'Требования отсутствуют'}
                </pre>
              </div>
              <div className="min-w-0 rounded border border-state-success/25 bg-state-success/5 p-3">
                <div className="mb-2 text-xs font-semibold text-state-success">
                  Станет
                </div>
                <pre className="overflow-x-auto whitespace-pre-wrap break-words font-sans text-sm leading-6 text-text-primary">
                  {section.after || 'Требования отсутствуют'}
                </pre>
              </div>
            </div>
            <EvidenceRefsDiff
              before={section.before_evidence_refs}
              after={section.after_evidence_refs}
            />
            <div className="flex items-center justify-between border-t border-border-button px-3 py-2 text-xs text-text-secondary">
              <span>
                Оснований для правки: {section.source_event_ids.length}
              </span>
              <button
                type="button"
                className="font-medium text-accent-primary hover:underline"
                onClick={() => onFocusSection(section.section_id)}
              >
                Показать в документе
              </button>
            </div>
            {section.sources && section.sources.length > 0 && (
              <div className="space-y-1 border-t border-border-button px-3 py-2 text-xs text-text-secondary">
                {section.sources.map((source) =>
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
                      {source.text ? `: ${source.text}` : ''} · показать в
                      обсуждении
                    </button>
                  ) : (
                    <p key={source.event_id}>
                      {source.label}
                      {source.text ? `: ${source.text}` : ''}
                    </p>
                  ),
                )}
              </div>
            )}
          </details>
        ))}
      </div>
      {preview.acknowledged_no_change_event_ids.length > 0 && (
        <div className="mt-3 space-y-1 text-xs text-text-secondary">
          <h3 className="font-semibold">
            Учтено без изменения текста:{' '}
            {preview.acknowledged_no_change_event_ids.length}
          </h3>
          {preview.acknowledged_no_change_sources?.map((source) =>
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
          )}
        </div>
      )}
    </section>
  );
}
