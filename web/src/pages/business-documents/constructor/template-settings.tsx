import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { FileCog, Hash, Languages, ListTree } from 'lucide-react';
import {
  MAX_HEADING_BASE_LEVEL,
  MIN_HEADING_BASE_LEVEL,
  type DocumentTemplateDraft,
} from './model';

interface TemplateSettingsProps {
  draft: DocumentTemplateDraft;
  onChange: (patch: Partial<DocumentTemplateDraft>) => void;
}

const selectClassName =
  'h-8 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm text-text-primary outline-none transition-colors focus-visible:ring-1 focus-visible:ring-accent-primary';

export function TemplateSettings({ draft, onChange }: TemplateSettingsProps) {
  const requiredCount = draft.sections.filter(
    (section) => section.required,
  ).length;
  const deepestLevel = draft.sections.reduce(
    (maximum, section) => Math.max(maximum, section.depth + 1),
    0,
  );

  return (
    <aside
      className="min-h-0 border-e border-border-button bg-bg-base xl:overflow-y-auto"
      aria-label="Параметры шаблона"
    >
      <div className="flex h-12 items-center gap-2 border-b border-border-button px-5 text-sm font-medium">
        <FileCog className="size-4 text-text-secondary" />
        Параметры шаблона
      </div>

      <div className="space-y-5 px-5 py-5">
        <label className="block space-y-2 text-xs font-medium text-text-secondary">
          <span>Название</span>
          <Input
            value={draft.name}
            maxLength={120}
            onChange={(event) => onChange({ name: event.target.value })}
            aria-label="Название шаблона"
          />
        </label>

        <label className="block space-y-2 text-xs font-medium text-text-secondary">
          <span>Описание</span>
          <Textarea
            value={draft.description}
            maxLength={1000}
            rows={4}
            resize="vertical"
            className="min-h-24 text-sm"
            onChange={(event) => onChange({ description: event.target.value })}
            aria-label="Описание шаблона"
          />
        </label>

        <div className="grid grid-cols-[minmax(0,1fr)_88px] gap-3">
          <label className="block space-y-2 text-xs font-medium text-text-secondary">
            <span>Технический код</span>
            <Input
              value={draft.documentType}
              maxLength={64}
              className="font-mono text-xs"
              onChange={(event) =>
                onChange({ documentType: event.target.value.toLowerCase() })
              }
              aria-label="Технический код шаблона"
            />
          </label>
          <label className="block space-y-2 text-xs font-medium text-text-secondary">
            <span>Версия</span>
            <Input
              value={draft.version}
              maxLength={32}
              className="font-mono text-xs"
              onChange={(event) => onChange({ version: event.target.value })}
              aria-label="Версия шаблона"
            />
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block space-y-2 text-xs font-medium text-text-secondary">
            <span>Язык</span>
            <select
              value={draft.language}
              onChange={(event) =>
                onChange({ language: event.target.value as 'ru' | 'en' })
              }
              className={selectClassName}
              aria-label="Язык шаблона"
            >
              <option value="ru">Русский</option>
              <option value="en">English</option>
            </select>
          </label>
          <label className="block space-y-2 text-xs font-medium text-text-secondary">
            <span>Уровень заголовка</span>
            <select
              value={draft.headingBaseLevel}
              onChange={(event) =>
                onChange({ headingBaseLevel: Number(event.target.value) })
              }
              className={selectClassName}
              aria-label="Базовый уровень заголовка"
            >
              {Array.from(
                {
                  length: MAX_HEADING_BASE_LEVEL - MIN_HEADING_BASE_LEVEL + 1,
                },
                (_, index) => MIN_HEADING_BASE_LEVEL + index,
              ).map((level) => (
                <option key={level} value={level}>
                  H{level}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="border-y border-border-button py-1">
          <label className="flex items-center justify-between gap-4 py-3 text-sm">
            <span>
              <span className="block font-medium text-text-primary">
                Нумерация разделов
              </span>
              <span className="mt-0.5 block text-xs leading-5 text-text-secondary">
                Сохранять номера в итоговом документе
              </span>
            </span>
            <Switch
              checked={draft.preserveSectionNumbers}
              onCheckedChange={(checked) =>
                onChange({ preserveSectionNumbers: checked })
              }
              aria-label="Сохранять нумерацию разделов"
            />
          </label>
          <label className="flex items-center justify-between gap-4 border-t border-border-button py-3 text-sm">
            <span>
              <span className="block font-medium text-text-primary">
                Протокол в тексте
              </span>
              <span className="mt-0.5 block text-xs leading-5 text-text-secondary">
                Включать историю согласования в документ
              </span>
            </span>
            <Switch
              checked={draft.protocolInBody}
              onCheckedChange={(checked) =>
                onChange({ protocolInBody: checked })
              }
              aria-label="Включать протокол в документ"
            />
          </label>
        </div>

        <dl className="grid grid-cols-3 gap-3 pt-1 text-center">
          <div>
            <dt className="flex justify-center text-text-disabled">
              <ListTree className="size-4" />
            </dt>
            <dd className="mt-1 text-lg font-semibold text-text-primary">
              {draft.sections.length}
            </dd>
            <dd className="text-[11px] text-text-secondary">разделов</dd>
          </div>
          <div>
            <dt className="flex justify-center text-text-disabled">
              <Hash className="size-4" />
            </dt>
            <dd className="mt-1 text-lg font-semibold text-text-primary">
              {requiredCount}
            </dd>
            <dd className="text-[11px] text-text-secondary">обязательных</dd>
          </div>
          <div>
            <dt className="flex justify-center text-text-disabled">
              <Languages className="size-4" />
            </dt>
            <dd className="mt-1 text-lg font-semibold text-text-primary">
              {deepestLevel}
            </dd>
            <dd className="text-[11px] text-text-secondary">уровней</dd>
          </div>
        </dl>
      </div>
    </aside>
  );
}
