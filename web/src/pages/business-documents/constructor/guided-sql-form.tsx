import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type { SqlAgentProject, SqlQueryCompileResponse } from '@/pages/business-documents/types';
import {
  saveBusinessDocumentSqlManualQuery,
  validateBusinessDocumentSqlManualQuery,
} from '@/services/business-document-service';
import { Check, Code2, LoaderCircle, Plus } from 'lucide-react';
import { useEffect, useState } from 'react';

type Parameter = { name: string; type: string; value: unknown };
type DraftStep = {
  id: string;
  title: string;
  sql: string;
  parameters: string;
  verified?: SqlQueryCompileResponse;
};

const DEFAULT_PARAMETERS = '[{"name":"row_limit","type":"integer","value":100}]';
const STEP_HINTS = [
  'Базовая выборка: один источник и явные имена выходных полей.',
  'Соединения: добавьте JOIN и проверьте ключи и кратность строк.',
  'Агрегация: добавьте GROUP BY, агрегаты и при необходимости HAVING.',
  'Подзапрос или CTE: уточните расчёт и фильтрацию.',
  'Финальная версия: проверьте параметры, сортировку и LIMIT :row_limit.',
];

function starterSql(project: SqlAgentProject): string {
  const schema = project.artifacts?.schema;
  const accepted = schema?.accepted_schema as Array<{ entity_id: string }> | undefined;
  const snapshot = schema?.schema_snapshot as {
    requirements?: Array<{ selected_table?: {
      id: string; schema: string; technical_name: string;
      columns: Array<{ name: string; selected?: boolean }>;
    } }>;
  } | undefined;
  const acceptedIds = new Set(accepted?.map((item) => item.entity_id) || []);
  const table = snapshot?.requirements?.map((item) => item.selected_table)
    .find((item) => item && acceptedIds.has(item.id) && item.columns.length);
  const column = table?.columns.find((item) => item.selected) || table?.columns[0];
  if (!table || !column || !table.schema || !table.technical_name || !column.name) return '';
  const quote = (value: string) => `"${value.replaceAll('"', '""')}"`;
  return `SELECT t.${quote(column.name)} AS ${quote(column.name)} FROM ${quote(table.schema)}.${quote(table.technical_name)} AS t LIMIT :row_limit`;
}

function parseParameters(source: string): Parameter[] {
  const parsed: unknown = JSON.parse(source);
  if (!Array.isArray(parsed) || parsed.some((item) =>
    !item || typeof item !== 'object' || typeof item.name !== 'string' ||
    typeof item.type !== 'string' || !('value' in item)
  )) {
    throw new Error('Параметры должны быть массивом объектов с name, type и value.');
  }
  return parsed as Parameter[];
}

function initialParameters(project: SqlAgentProject, compiledParameters?: Record<string, unknown>): string {
  const manual = project.artifacts?.query?.parameters;
  if (Array.isArray(manual)) return JSON.stringify(manual, null, 2);
  if (!compiledParameters || Object.keys(compiledParameters).length === 0) return DEFAULT_PARAMETERS;
  return JSON.stringify(Object.entries(compiledParameters).map(([name, value]) => ({
    name,
    type: name === 'row_limit' || name === 'offset' || Number.isInteger(value) ? 'integer' :
      typeof value === 'number' ? 'decimal' : typeof value === 'boolean' ? 'boolean' :
      Array.isArray(value) ? (value.every(Number.isInteger) ? 'integer_list' : 'text_list') : 'text',
    value,
  })), null, 2);
}

function readDraft(key: string, initialSql: string, parameters: string, finalSql: boolean): DraftStep[] {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0 && parsed.length <= 12 && parsed.every((step) =>
        step && typeof step.id === 'string' && typeof step.title === 'string' &&
        typeof step.sql === 'string' && typeof step.parameters === 'string'
      )) {
        return parsed.map((step) => ({
          id: step.id, title: step.title, sql: step.sql, parameters: step.parameters,
        }));
      }
    }
  } catch { /* Browser storage can be unavailable. */ }
  return [{ id: 'step-1', title: finalSql ? 'Финальный SQL' : 'Базовая выборка', sql: initialSql, parameters }];
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Проверка не выполнена.';
}

export function GuidedSqlForm({
  project,
  initialSql = '',
  compiledParameters,
  saveDisabledReason,
  onSaved,
}: {
  project: SqlAgentProject;
  initialSql?: string;
  compiledParameters?: Record<string, unknown>;
  saveDisabledReason?: string;
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const storageKey = `sql-draft:${project.id}:${project.artifact_ids.schema || 'none'}:${project.artifact_ids.query || 'none'}`;
  const [steps, setSteps] = useState<DraftStep[]>(() => readDraft(
    storageKey, initialSql || starterSql(project), initialParameters(project, compiledParameters), Boolean(initialSql),
  ));
  const [current, setCurrent] = useState(0);
  const [aligned, setAligned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(steps.map((item) => ({
        id: item.id, title: item.title, sql: item.sql, parameters: item.parameters,
      }))));
    } catch { /* The editor still works without browser storage. */ }
  }, [storageKey, steps]);
  useEffect(() => {
    setSteps((items) => items.map((item) => ({ ...item, verified: undefined })));
    setAligned(false);
  }, [project.state_version]);

  const step = steps[current];
  const finalStep = current === steps.length - 1;
  const previousVerified = steps.slice(0, current).every((item) => Boolean(item.verified));
  const allVerified = previousVerified && Boolean(step.verified);
  const update = (patch: Partial<DraftStep>) => {
    setSteps((items) => items.map((item, index) =>
      index === current ? { ...item, ...patch, verified: undefined } :
      index > current ? { ...item, verified: undefined } : item,
    ));
    setAligned(false);
    setError(null);
  };
  const validate = async () => {
    if (!previousVerified) return;
    setBusy(true);
    setError(null);
    try {
      const result = await validateBusinessDocumentSqlManualQuery(
        project.id, project.state_version, step.sql, parseParameters(step.parameters),
      );
      if (result.status !== 'READY' || result.guard.status !== 'PASS') {
        throw new Error(result.blocking_issues.map((issue) => issue.message).join('; ') || 'Схема требует уточнения.');
      }
      setSteps((items) => items.map((item, index) => index === current ? { ...item, verified: result } : item));
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setBusy(false);
    }
  };
  const addStep = () => {
    if (!allVerified || steps.length >= 12) return;
    const next = steps.length;
    setSteps((items) => [...items, {
      id: `step-${Date.now()}-${next}`,
      title: STEP_HINTS[next]?.split(':')[0] || `Уточнение ${next}`,
      sql: step.sql,
      parameters: step.parameters,
    }]);
    setCurrent(next);
    setAligned(false);
  };
  const save = async () => {
    if (!finalStep || !allVerified || !aligned || saveDisabledReason) return;
    setBusy(true);
    setError(null);
    try {
      await saveBusinessDocumentSqlManualQuery(
        project.id, project.state_version,
        `manual-sql-${typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`,
        step.sql, parseParameters(step.parameters),
      );
      setOpen(false);
      await onSaved();
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setBusy(false);
    }
  };

  return <section className="mt-6 border-t border-border-button pt-4" data-testid="guided-sql-form">
    <Button variant="ghost" onClick={() => setOpen((value) => !value)}>
      <Code2 className="size-4" />{open ? 'Скрыть редактор SQL' : initialSql ? 'Редактировать финальный SQL' : 'Собрать SQL по шагам'}
    </Button>
    {open && <div className="mt-4 max-w-3xl space-y-4">
      <p className="text-xs leading-5 text-text-secondary">
        Каждый шаг — полный PostgreSQL SELECT с явными алиасами и LIMIT :row_limit.
        Проверка подтверждает синтаксис, разрешённые таблицы и поля, параметры и режим чтения.
        Строки и значения данных проверяются после запуска финального запроса.
      </p>
      <nav className="flex flex-wrap gap-2" aria-label="Шаги SQL">
        {steps.map((item, index) => <Button
          key={item.id} type="button" size="sm" variant={index === current ? 'default' : 'outline'}
          disabled={busy} onClick={() => { setCurrent(index); setError(null); }}
        >{item.verified && <Check className="size-3" />}{index + 1}. {item.title}</Button>)}
      </nav>
      <p className="text-xs text-text-secondary">{STEP_HINTS[current] || 'Уточните запрос и проверьте новую версию.'}</p>
      <label className="block text-xs">Название шага
        <Input aria-label="Название шага" className="mt-1" value={step.title} maxLength={80}
          disabled={busy} onChange={(event) => update({ title: event.target.value })} />
      </label>
      <label className="block text-xs">SQL шага {current + 1}
        <Textarea aria-label={`SQL шага ${current + 1}`} value={step.sql}
          disabled={busy} onChange={(event) => update({ sql: event.target.value })}
          className="mt-1 min-h-52 font-mono text-xs" />
      </label>
      <label className="block text-xs">Параметры JSON шага {current + 1}
        <Textarea aria-label={`Параметры JSON шага ${current + 1}`} value={step.parameters}
          disabled={busy} onChange={(event) => update({ parameters: event.target.value })}
          className="mt-1 min-h-20 font-mono text-xs" />
      </label>
      {step.verified && <div className="border-s-2 border-state-success ps-3 text-xs" role="status">
        <p>Шаг проверен · таблицы: {step.verified.guard.status === 'PASS' ? step.verified.guard.tables.join(', ') : '—'}</p>
        <p>Выходные поля: {step.verified.guard.status === 'PASS' ? step.verified.guard.output_columns?.join(', ') || '—' : '—'}</p>
        <p>Параметры: {Object.keys(step.verified.parameters).join(', ')}</p>
      </div>}
      {error && <p className="text-xs text-state-error" role="alert">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" disabled={busy || !previousVerified || !step.sql.trim()}
          onClick={() => void validate()}>{busy && <LoaderCircle className="size-4 animate-spin" />}Проверить шаг</Button>
        <Button type="button" variant="outline" disabled={busy || !allVerified || steps.length >= 12}
          onClick={addStep}><Plus className="size-4" />Добавить уточнение</Button>
        {finalStep && steps.length > 1 && <Button type="button" variant="ghost" disabled={busy}
          onClick={() => { setSteps((items) => items.slice(0, -1)); setCurrent(current - 1); setAligned(false); }}>
          Удалить текущий шаг
        </Button>}
      </div>
      {finalStep && <div className="space-y-3 border-t border-border-button pt-4">
        {saveDisabledReason && <p className="text-xs text-text-secondary">{saveDisabledReason}</p>}
        <label className="flex items-center gap-2 text-xs"><Checkbox checked={aligned} disabled={!allVerified || busy}
          onCheckedChange={(value) => setAligned(value === true)} />Финальный SQL соответствует принятой задаче и выбранным данным</label>
        <Button disabled={busy || !aligned || !allVerified || Boolean(saveDisabledReason)} onClick={() => void save()}>Сохранить проверенный SQL</Button>
      </div>}
    </div>}
  </section>;
}
