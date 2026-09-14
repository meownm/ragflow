import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  ArrowDown,
  ArrowLeftToLine,
  ArrowRightToLine,
  ArrowUp,
  CornerDownRight,
  FileText,
  ListTree,
  Plus,
} from 'lucide-react';
import {
  BLOCK_TYPE_OPTIONS,
  numberSections,
  type ConstructorSection,
  type TemplateValidationIssue,
} from './model';

interface StructureEditorProps {
  sections: ConstructorSection[];
  selectedUid: string | null;
  issues: TemplateValidationIssue[];
  mode: 'structure' | 'preview';
  onModeChange: (mode: 'structure' | 'preview') => void;
  onSelect: (uid: string) => void;
  onAddRoot: () => void;
  onAddChild: (uid: string) => void;
  onIndent: (uid: string) => void;
  onOutdent: (uid: string) => void;
  onMove: (uid: string, direction: 'up' | 'down') => void;
  renderPreview: () => React.ReactNode;
}

export function StructureEditor({
  sections,
  selectedUid,
  issues,
  mode,
  onModeChange,
  onSelect,
  onAddRoot,
  onAddChild,
  onIndent,
  onOutdent,
  onMove,
  renderPreview,
}: StructureEditorProps) {
  const numberedSections = numberSections(sections);
  const issueUids = new Set(
    issues.map((issue) => issue.sectionUid).filter(Boolean),
  );

  return (
    <section
      className="flex min-h-0 flex-col bg-bg-base"
      aria-label="Структура документа"
    >
      <div className="flex min-h-12 flex-wrap items-center justify-between gap-2 border-b border-border-button px-4 py-2 sm:px-5">
        <div
          className="flex items-center gap-1"
          role="tablist"
          aria-label="Режим конструктора"
        >
          <Button
            size="sm"
            variant={mode === 'structure' ? 'secondary' : 'ghost'}
            onClick={() => onModeChange('structure')}
            role="tab"
            aria-selected={mode === 'structure'}
          >
            <ListTree className="size-4" />
            Структура
          </Button>
          <Button
            size="sm"
            variant={mode === 'preview' ? 'secondary' : 'ghost'}
            onClick={() => onModeChange('preview')}
            role="tab"
            aria-selected={mode === 'preview'}
          >
            <FileText className="size-4" />
            Предпросмотр
          </Button>
        </div>
        {mode === 'structure' && (
          <Button size="sm" variant="outline" onClick={onAddRoot}>
            <Plus className="size-4" />
            Раздел
          </Button>
        )}
      </div>

      {mode === 'preview' ? (
        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-auto">
          {renderPreview()}
        </div>
      ) : (
        <div
          className="min-h-0 flex-1 overflow-y-auto px-4 py-5 scrollbar-auto sm:px-5"
          data-testid="document-constructor-structure"
        >
          <div className="mx-auto max-w-3xl">
            <div className="mb-3 flex items-center justify-between gap-4 text-xs text-text-secondary">
              <span>Порядок и вложенность будущего документа</span>
              <span>{sections.length} разделов</span>
            </div>

            {!numberedSections.length ? (
              <div className="flex min-h-72 flex-col items-center justify-center border-y border-dashed border-border-button px-6 text-center">
                <ListTree className="size-8 stroke-[1.25] text-text-disabled" />
                <h2 className="mt-4 text-base font-medium text-text-primary">
                  Структура пока пуста
                </h2>
                <p className="mt-2 max-w-sm text-sm leading-6 text-text-secondary">
                  Добавьте первый раздел, затем создавайте подразделы и
                  задавайте им требования.
                </p>
                <Button className="mt-5" onClick={onAddRoot}>
                  <Plus className="size-4" />
                  Добавить раздел
                </Button>
              </div>
            ) : (
              <ol className="border-t border-border-button">
                {numberedSections.map((section) => {
                  const selected = section.uid === selectedUid;
                  const hasIssue = issueUids.has(section.uid);
                  const blockLabels = section.allowedBlocks
                    .map(
                      (block) =>
                        BLOCK_TYPE_OPTIONS.find(
                          (option) => option.value === block,
                        )?.label,
                    )
                    .filter(Boolean);
                  return (
                    <li
                      key={section.uid}
                      className={cn(
                        'group relative border-b border-border-button transition-colors duration-150',
                        selected ? 'bg-bg-card' : 'hover:bg-bg-card/60',
                      )}
                      style={{ paddingInlineStart: `${section.depth * 22}px` }}
                      data-testid="document-constructor-section-row"
                    >
                      <div
                        className={cn(
                          'absolute inset-y-2 start-0 w-0.5 rounded-full bg-accent-primary transition-opacity',
                          selected ? 'opacity-100' : 'opacity-0',
                        )}
                      />
                      <button
                        type="button"
                        className="flex min-h-[72px] w-full items-center gap-3 px-3 py-3 text-start outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-accent-primary"
                        onClick={() => onSelect(section.uid)}
                        aria-current={selected ? 'true' : undefined}
                      >
                        <span
                          className={cn(
                            'flex h-7 min-w-9 shrink-0 items-center justify-center rounded border px-1.5 font-mono text-[11px]',
                            hasIssue
                              ? 'border-state-error/50 text-state-error'
                              : selected
                                ? 'border-accent-primary/40 text-accent-primary'
                                : 'border-border-button text-text-secondary',
                          )}
                        >
                          {section.resolvedId}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span
                            className={cn(
                              'block truncate text-sm font-medium',
                              section.title.trim()
                                ? 'text-text-primary'
                                : 'italic text-state-error',
                            )}
                          >
                            {section.title.trim() || 'Без названия'}
                          </span>
                          <span className="mt-1 block truncate text-xs text-text-secondary">
                            {blockLabels.length
                              ? blockLabels.join(' · ')
                              : 'Тип содержимого не выбран'}
                          </span>
                        </span>
                        {section.required && (
                          <Badge
                            variant="secondary"
                            className="hidden sm:inline-flex"
                          >
                            Обязательный
                          </Badge>
                        )}
                      </button>

                      <div className="absolute end-2 top-1/2 flex -translate-y-1/2 items-center rounded bg-bg-base opacity-0 shadow-sm transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          onClick={() => onMove(section.uid, 'up')}
                          aria-label={`Переместить раздел ${section.resolvedId} выше`}
                        >
                          <ArrowUp className="size-3.5" />
                        </Button>
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          onClick={() => onMove(section.uid, 'down')}
                          aria-label={`Переместить раздел ${section.resolvedId} ниже`}
                        >
                          <ArrowDown className="size-3.5" />
                        </Button>
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          onClick={() => onOutdent(section.uid)}
                          aria-label={`Уменьшить уровень раздела ${section.resolvedId}`}
                        >
                          <ArrowLeftToLine className="size-3.5" />
                        </Button>
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          onClick={() => onIndent(section.uid)}
                          aria-label={`Увеличить уровень раздела ${section.resolvedId}`}
                        >
                          <ArrowRightToLine className="size-3.5" />
                        </Button>
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          onClick={() => onAddChild(section.uid)}
                          aria-label={`Добавить подраздел в раздел ${section.resolvedId}`}
                        >
                          <CornerDownRight className="size-3.5" />
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
