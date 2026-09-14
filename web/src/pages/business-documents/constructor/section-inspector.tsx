import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import {
  CircleAlert,
  FileCheck2,
  ListChecks,
  Plus,
  Settings2,
  Trash2,
  X,
} from 'lucide-react';
import {
  BLOCK_TYPE_OPTIONS,
  type ConstructorSection,
  type DocumentBlockType,
  type TemplateValidationIssue,
} from './model';

interface SectionInspectorProps {
  section: ConstructorSection | null;
  resolvedId?: string;
  descendantCount: number;
  issues: TemplateValidationIssue[];
  onChange: (patch: Partial<ConstructorSection>) => void;
  onDelete: () => void;
}

function numericValue(value: string) {
  if (!value.trim()) return null;
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

export function SectionInspector({
  section,
  resolvedId,
  descendantCount,
  issues,
  onChange,
  onDelete,
}: SectionInspectorProps) {
  if (!section) {
    return (
      <aside
        className="flex min-h-72 flex-col items-center justify-center border-s border-border-button px-8 text-center xl:min-h-0"
        aria-label="Настройки раздела"
      >
        <Settings2 className="size-8 stroke-[1.25] text-text-disabled" />
        <h2 className="mt-4 text-base font-medium text-text-primary">
          Выберите раздел
        </h2>
        <p className="mt-2 max-w-xs text-sm leading-6 text-text-secondary">
          Здесь настраиваются назначение, допустимое содержимое и требования к
          генерации.
        </p>
      </aside>
    );
  }

  const toggleContentType = (block: DocumentBlockType) => {
    const allowedBlocks = section.allowedBlocks.includes(block)
      ? section.allowedBlocks.filter((item) => item !== block)
      : [...section.allowedBlocks, block];
    onChange({ allowedBlocks });
  };

  const updateRequirement = (index: number, value: string) => {
    onChange({
      requirements: section.requirements.map((requirement, requirementIndex) =>
        requirementIndex === index ? value : requirement,
      ),
    });
  };

  return (
    <aside
      key={section.uid}
      className="min-h-0 border-s border-border-button bg-bg-base xl:overflow-y-auto"
      aria-label="Настройки раздела"
      data-testid="document-constructor-inspector"
    >
      <div className="flex h-12 items-center justify-between gap-3 border-b border-border-button px-5">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
          <Settings2 className="size-4 shrink-0 text-text-secondary" />
          <span className="truncate">Раздел {resolvedId}</span>
        </div>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              size="icon-sm"
              variant="ghost"
              className="text-text-secondary hover:text-state-error"
              aria-label={`Удалить раздел ${resolvedId}`}
            >
              <Trash2 className="size-4" />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Удалить раздел?</AlertDialogTitle>
              <AlertDialogDescription>
                {descendantCount
                  ? `Вместе с ним будут удалены вложенные разделы: ${descendantCount}.`
                  : 'Раздел будет удалён из структуры шаблона.'}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Отмена</AlertDialogCancel>
              <AlertDialogAction
                className="bg-state-error text-white hover:bg-state-error/90"
                onClick={onDelete}
              >
                Удалить
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      <div className="animate-in space-y-6 px-5 py-5 duration-200 fade-in slide-in-from-right-2">
        {issues.length > 0 && (
          <div
            className="border-s-2 border-state-error py-1 ps-3 text-xs leading-5 text-state-error"
            role="alert"
          >
            {issues.map((issue) => (
              <p key={issue.code}>{issue.message}</p>
            ))}
          </div>
        )}

        <label className="block space-y-2 text-xs font-medium text-text-secondary">
          <span>Название раздела</span>
          <Input
            value={section.title}
            maxLength={500}
            onChange={(event) => onChange({ title: event.target.value })}
            aria-label="Название раздела"
          />
        </label>

        <label className="block space-y-2 text-xs font-medium text-text-secondary">
          <span>Собственный номер</span>
          <Input
            value={section.customId}
            maxLength={16}
            className="font-mono text-xs"
            placeholder={`Автоматически: ${resolvedId}`}
            onChange={(event) => onChange({ customId: event.target.value })}
            aria-label="Собственный номер раздела"
          />
          <span className="block font-normal leading-5 text-text-disabled">
            Оставьте пустым для автоматической нумерации. Допустимы числа и
            точки.
          </span>
        </label>

        <label className="block space-y-2 text-xs font-medium text-text-secondary">
          <span>Назначение раздела</span>
          <Textarea
            value={section.purpose}
            maxLength={2000}
            rows={4}
            resize="vertical"
            className="min-h-24 text-sm"
            placeholder="Что должен раскрыть этот раздел?"
            onChange={(event) => onChange({ purpose: event.target.value })}
            aria-label="Назначение раздела"
          />
        </label>

        <label className="flex items-center justify-between gap-4 border-y border-border-button py-3 text-sm">
          <span>
            <span className="flex items-center gap-2 font-medium text-text-primary">
              <FileCheck2 className="size-4 text-text-secondary" />
              Обязательный раздел
            </span>
            <span className="mt-1 block text-xs leading-5 text-text-secondary">
              Документ нельзя завершить без содержимого
            </span>
          </span>
          <Switch
            checked={section.required}
            onCheckedChange={(checked) => onChange({ required: checked })}
            aria-label="Обязательный раздел"
          />
        </label>

        <fieldset>
          <legend className="flex items-center gap-2 text-xs font-medium text-text-secondary">
            <ListChecks className="size-4" />
            Допустимое содержимое
          </legend>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {BLOCK_TYPE_OPTIONS.map((option) => {
              const selected = section.allowedBlocks.includes(option.value);
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => toggleContentType(option.value)}
                  className={cn(
                    'min-h-9 rounded border px-2.5 py-2 text-start text-xs transition-colors',
                    selected
                      ? 'border-accent-primary/50 bg-accent-primary/10 text-text-primary'
                      : 'border-border-button text-text-secondary hover:border-border-default hover:text-text-primary',
                  )}
                  aria-pressed={selected}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset>
          <legend className="text-xs font-medium text-text-secondary">
            Объём раздела, слов
          </legend>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <label className="space-y-2 text-xs text-text-secondary">
              <span>Минимум</span>
              <Input
                type="number"
                min={1}
                value={section.minWords ?? ''}
                onChange={(event) =>
                  onChange({
                    minWords: numericValue(String(event.target.value)),
                  })
                }
                aria-label="Минимум слов"
              />
            </label>
            <label className="space-y-2 text-xs text-text-secondary">
              <span>Максимум</span>
              <Input
                type="number"
                min={1}
                value={section.maxWords ?? ''}
                onChange={(event) =>
                  onChange({
                    maxWords: numericValue(String(event.target.value)),
                  })
                }
                aria-label="Максимум слов"
              />
            </label>
          </div>
        </fieldset>

        <fieldset>
          <div className="flex items-center justify-between gap-3">
            <legend className="text-xs font-medium text-text-secondary">
              Требования к разделу
            </legend>
            <Button
              size="xs"
              variant="ghost"
              onClick={() =>
                onChange({ requirements: [...section.requirements, ''] })
              }
            >
              <Plus className="size-3.5" />
              Добавить
            </Button>
          </div>
          <div className="mt-3 space-y-2">
            {!section.requirements.length && (
              <p className="border-y border-dashed border-border-button py-4 text-center text-xs leading-5 text-text-disabled">
                Специальных требований пока нет
              </p>
            )}
            {section.requirements.map((requirement, index) => (
              <div
                key={`${section.uid}-requirement-${index}`}
                className="animate-in flex items-start gap-2 duration-150 fade-in zoom-in-95"
              >
                <span className="mt-2 flex size-5 shrink-0 items-center justify-center rounded-full bg-bg-card font-mono text-[10px] text-text-secondary">
                  {index + 1}
                </span>
                <Textarea
                  value={requirement}
                  maxLength={1000}
                  rows={2}
                  resize="vertical"
                  className="min-h-16 text-xs leading-5"
                  placeholder="Например: привести измеримые критерии и источник данных"
                  onChange={(event) =>
                    updateRequirement(index, event.target.value)
                  }
                  aria-label={`Требование ${index + 1}`}
                />
                <Button
                  size="icon-xs"
                  variant="ghost"
                  className="mt-1 text-text-disabled hover:text-state-error"
                  onClick={() =>
                    onChange({
                      requirements: section.requirements.filter(
                        (_, requirementIndex) => requirementIndex !== index,
                      ),
                    })
                  }
                  aria-label={`Удалить требование ${index + 1}`}
                >
                  <X className="size-3.5" />
                </Button>
              </div>
            ))}
          </div>
        </fieldset>

        <label className="block space-y-2 text-xs font-medium text-text-secondary">
          <span className="flex items-center gap-2">
            <CircleAlert className="size-4" />
            Инструкция генератору
          </span>
          <Textarea
            value={section.generationInstructions}
            maxLength={4000}
            rows={5}
            resize="vertical"
            className="min-h-28 text-sm"
            placeholder="Тон, формат, обязательные источники, запреты и другие правила"
            onChange={(event) =>
              onChange({ generationInstructions: event.target.value })
            }
            aria-label="Инструкция генератору"
          />
        </label>
      </div>
    </aside>
  );
}
