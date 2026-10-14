import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type {
  SqlAgentProject,
  SqlQueryCompileResponse,
} from '@/pages/business-documents/types';
import {
  fetchBusinessDocumentSqlRevisionWorkspace,
  saveBusinessDocumentSqlManualQuery,
  saveBusinessDocumentSqlRevisionDraft,
  validateBusinessDocumentSqlManualQuery,
} from '@/services/business-document-service';
import { Check, Code2, LoaderCircle, Plus } from 'lucide-react';
import {
  type ComponentProps,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';

type Parameter = { name: string; type: string; value: unknown };
type DraftStep = {
  id: string;
  title: string;
  sql: string;
  parameters: string;
  verified?: SqlQueryCompileResponse;
};

const DEFAULT_PARAMETERS =
  '[{"name":"row_limit","type":"integer","value":100}]';
const STEP_HINTS = [
  'Базовая выборка: один источник и явные имена выходных полей.',
  'Соединения: добавьте JOIN и проверьте ключи и кратность строк.',
  'Агрегация: добавьте GROUP BY, агрегаты и при необходимости HAVING.',
  'Подзапрос или CTE: уточните расчёт и фильтрацию.',
  'Финальная версия: проверьте параметры, сортировку и LIMIT :row_limit.',
];

function starterSql(project: SqlAgentProject): string {
  const schema = project.artifacts?.schema;
  const accepted = schema?.accepted_schema as
    | Array<{ entity_id: string }>
    | undefined;
  const snapshot = schema?.schema_snapshot as
    | {
        requirements?: Array<{
          selected_table?: {
            id: string;
            schema: string;
            technical_name: string;
            columns: Array<{ name: string; selected?: boolean }>;
          };
        }>;
      }
    | undefined;
  const acceptedIds = new Set(accepted?.map((item) => item.entity_id) || []);
  const table = snapshot?.requirements
    ?.map((item) => item.selected_table)
    .find((item) => item && acceptedIds.has(item.id) && item.columns.length);
  const column =
    table?.columns.find((item) => item.selected) || table?.columns[0];
  if (
    !table ||
    !column ||
    !table.schema ||
    !table.technical_name ||
    !column.name
  )
    return '';
  const quote = (value: string) => `"${value.replaceAll('"', '""')}"`;
  return `SELECT t.${quote(column.name)} AS ${quote(column.name)} FROM ${quote(table.schema)}.${quote(table.technical_name)} AS t LIMIT :row_limit`;
}

function parseParameters(source: string): Parameter[] {
  const parsed: unknown = JSON.parse(source);
  if (
    !Array.isArray(parsed) ||
    parsed.some(
      (item) =>
        !item ||
        typeof item !== 'object' ||
        typeof item.name !== 'string' ||
        typeof item.type !== 'string' ||
        !('value' in item),
    )
  ) {
    throw new Error(
      'Параметры должны быть массивом объектов с name, type и value.',
    );
  }
  return parsed as Parameter[];
}

function initialParameters(
  project: SqlAgentProject,
  compiledParameters?: Record<string, unknown>,
): string {
  const manual = project.artifacts?.query?.parameters;
  if (Array.isArray(manual)) return JSON.stringify(manual, null, 2);
  if (!compiledParameters || Object.keys(compiledParameters).length === 0)
    return DEFAULT_PARAMETERS;
  return JSON.stringify(
    Object.entries(compiledParameters).map(([name, value]) => ({
      name,
      type:
        name === 'row_limit' || name === 'offset' || Number.isInteger(value)
          ? 'integer'
          : typeof value === 'number'
            ? 'decimal'
            : typeof value === 'boolean'
              ? 'boolean'
              : Array.isArray(value)
                ? value.every(Number.isInteger)
                  ? 'integer_list'
                  : 'text_list'
                : 'text',
      value,
    })),
    null,
    2,
  );
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Проверка не выполнена.';
}

export function GuidedSqlForm(
  props: ComponentProps<typeof GuidedSqlFormEditor>,
) {
  const { project } = props;
  return (
    <GuidedSqlFormEditor
      key={`${project.id}:${project.artifact_ids.schema}:${project.artifact_ids.query}`}
      {...props}
    />
  );
}

function GuidedSqlFormEditor({
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
  const startingSql = initialSql || starterSql(project);
  const startingParameters = initialParameters(project, compiledParameters);
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState<DraftStep[]>(() => [
    {
      id: 'step-1',
      title: initialSql ? 'Финальный SQL' : 'Базовая выборка',
      sql: startingSql,
      parameters: startingParameters,
    },
  ]);
  const [draftLoaded, setDraftLoaded] = useState(false);
  const draftVersion = useRef<number | null>(null);
  const lastSavedDraft = useRef('');
  const latestDraft = useRef('');
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const pendingSaves = useRef(0);
  const mustSaveRecoveredDraft = useRef(false);
  const [draftSaveState, setDraftSaveState] = useState<
    'saved' | 'pending' | 'saving' | 'error'
  >('saved');
  const [current, setCurrent] = useState(0);
  const [aligned, setAligned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<DraftStep[] | null>(null);
  const [loadVersion, setLoadVersion] = useState(0);
  const pendingKey = `sql-pending:${project.id}:${project.artifact_ids.schema}:${project.artifact_ids.query}`;

  useEffect(() => {
    let cancelled = false;
    setDraftLoaded(false);
    mustSaveRecoveredDraft.current = false;
    void fetchBusinessDocumentSqlRevisionWorkspace(project.id)
      .then((workspace) => {
        if (cancelled) return;
        const saved = workspace.draft;
        const compatible =
          saved?.base_query_artifact_id ===
          (project.artifact_ids.query || null);
        let loaded =
          compatible && saved?.steps.length
            ? saved.steps.map((item, index) => ({
                ...item,
                id: `step-${index + 1}`,
              }))
            : [
                {
                  id: 'step-1',
                  title: initialSql ? 'Финальный SQL' : 'Базовая выборка',
                  sql: startingSql,
                  parameters: startingParameters,
                },
              ];
        lastSavedDraft.current =
          compatible && saved?.steps.length ? JSON.stringify(saved.steps) : '';
        draftVersion.current = compatible && saved ? saved.version : null;
        try {
          const pending = JSON.parse(
            window.sessionStorage.getItem(pendingKey) || 'null',
          );
          if (
            pending?.steps?.length &&
            pending.steps.every(
              (item: Record<string, unknown>) =>
                typeof item.title === 'string' &&
                typeof item.sql === 'string' &&
                typeof item.parameters === 'string',
            )
          ) {
            const recovered = pending.steps.map(
              (item: Omit<DraftStep, 'id'>, index: number) => ({
                ...item,
                id: `step-${index + 1}`,
              }),
            );
            if (pending.version === draftVersion.current) {
              loaded = recovered;
              mustSaveRecoveredDraft.current = true;
            } else setRecovery(recovered);
          }
        } catch {
          setError(
            'Не удалось прочитать несохранённые изменения этой вкладки.',
          );
        }
        latestDraft.current = JSON.stringify(
          loaded.map(({ title, sql, parameters }) => ({
            title,
            sql,
            parameters,
          })),
        );
        setDraftSaveState(
          compatible && saved?.steps.length ? 'saved' : 'pending',
        );
        setSteps(loaded);
        setCurrent(0);
        setDraftLoaded(true);
      })
      .catch((nextError) => {
        if (!cancelled)
          setError(`Серверный черновик недоступен: ${errorText(nextError)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [
    project.id,
    project.artifact_ids.query,
    project.artifact_ids.schema,
    initialSql,
    startingSql,
    startingParameters,
    pendingKey,
    loadVersion,
  ]);
  const persistDraft = useCallback(
    (payload: Array<{ title: string; sql: string; parameters: string }>) => {
      const serialized = JSON.stringify(payload);
      pendingSaves.current += 1;
      const queued = saveQueue.current
        .catch(() => undefined)
        .then(async () => {
          if (
            serialized === lastSavedDraft.current &&
            !mustSaveRecoveredDraft.current
          ) {
            if (latestDraft.current === serialized) {
              const buffered = JSON.parse(
                window.sessionStorage.getItem(pendingKey) || 'null',
              );
              if (buffered && JSON.stringify(buffered.steps) === serialized)
                window.sessionStorage.removeItem(pendingKey);
              setDraftSaveState('saved');
            }
            return;
          }
          setDraftSaveState('saving');
          try {
            const saved = await saveBusinessDocumentSqlRevisionDraft(
              project.id,
              {
                schema_version: '1',
                expected_state_version: project.state_version,
                expected_draft_version: draftVersion.current,
                steps: payload,
              },
            );
            draftVersion.current = saved.version;
            lastSavedDraft.current = serialized;
            mustSaveRecoveredDraft.current = false;
            try {
              const buffered = JSON.parse(
                window.sessionStorage.getItem(pendingKey) || 'null',
              );
              if (buffered && JSON.stringify(buffered.steps) === serialized)
                window.sessionStorage.removeItem(pendingKey);
              else if (buffered)
                window.sessionStorage.setItem(
                  pendingKey,
                  JSON.stringify({ ...buffered, version: saved.version }),
                );
            } catch {
              setError(
                'Черновик сохранён на сервере, но не удалось обновить копию изменений этой вкладки.',
              );
            }
            setDraftSaveState(
              latestDraft.current === serialized ? 'saved' : 'pending',
            );
            setError((currentError) =>
              currentError?.startsWith('Черновик не сохранён:')
                ? null
                : currentError,
            );
          } catch (nextError) {
            setDraftSaveState('error');
            setError(`Черновик не сохранён: ${errorText(nextError)}`);
            throw nextError;
          }
        })
        .finally(() => {
          pendingSaves.current -= 1;
        });
      saveQueue.current = queued.catch(() => undefined);
      return queued;
    },
    [project.id, project.state_version, pendingKey],
  );
  useEffect(() => {
    if (!draftLoaded || recovery) return;
    const payload = steps.map(({ title, sql, parameters }) => ({
      title,
      sql,
      parameters,
    }));
    const serialized = JSON.stringify(payload);
    latestDraft.current = serialized;
    if (
      serialized === lastSavedDraft.current &&
      pendingSaves.current === 0 &&
      !mustSaveRecoveredDraft.current
    ) {
      window.sessionStorage.removeItem(pendingKey);
      setDraftSaveState('saved');
      return;
    }
    setDraftSaveState('pending');
    try {
      window.sessionStorage.setItem(
        pendingKey,
        JSON.stringify({ version: draftVersion.current, steps: payload }),
      );
    } catch {
      setError(
        'Не удалось сохранить изменения в этой вкладке. Сохраните черновик перед переходом.',
      );
    }
    const timer = window.setTimeout(() => {
      void persistDraft(payload).catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(timer);
  }, [draftLoaded, persistDraft, steps, pendingKey, recovery]);
  useEffect(() => {
    if (draftSaveState === 'saved' && !recovery) return;
    const warnUnsaved = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnUnsaved);
    return () => window.removeEventListener('beforeunload', warnUnsaved);
  }, [draftSaveState, recovery]);
  useEffect(() => {
    setSteps((items) =>
      items.map((item) => ({ ...item, verified: undefined })),
    );
    setAligned(false);
  }, [project.state_version]);

  const step = steps[current];
  const finalStep = current === steps.length - 1;
  const previousVerified = steps
    .slice(0, current)
    .every((item) => Boolean(item.verified));
  const allVerified = previousVerified && Boolean(step.verified);
  const update = (patch: Partial<DraftStep>) => {
    setSteps((items) =>
      items.map((item, index) =>
        index === current
          ? { ...item, ...patch, verified: undefined }
          : index > current
            ? { ...item, verified: undefined }
            : item,
      ),
    );
    setAligned(false);
    setError(null);
  };
  const validate = async () => {
    if (!previousVerified || recovery) return;
    setBusy(true);
    setError(null);
    try {
      const result = await validateBusinessDocumentSqlManualQuery(
        project.id,
        project.state_version,
        step.sql,
        parseParameters(step.parameters),
      );
      if (result.status !== 'READY' || result.guard.status !== 'PASS') {
        throw new Error(
          result.blocking_issues.map((issue) => issue.message).join('; ') ||
            'Схема требует уточнения.',
        );
      }
      setSteps((items) =>
        items.map((item, index) =>
          index === current ? { ...item, verified: result } : item,
        ),
      );
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setBusy(false);
    }
  };
  const addStep = () => {
    if (!allVerified || steps.length >= 12) return;
    const next = steps.length;
    setSteps((items) => [
      ...items,
      {
        id: `step-${Date.now()}-${next}`,
        title: STEP_HINTS[next]?.split(':')[0] || `Уточнение ${next}`,
        sql: step.sql,
        parameters: step.parameters,
      },
    ]);
    setCurrent(next);
    setAligned(false);
  };
  const save = async () => {
    if (
      !finalStep ||
      !allVerified ||
      !aligned ||
      saveDisabledReason ||
      recovery
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await persistDraft(
        steps.map(({ title, sql, parameters }) => ({ title, sql, parameters })),
      );
      await saveBusinessDocumentSqlManualQuery(
        project.id,
        project.state_version,
        `manual-sql-${typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`,
        step.sql,
        parseParameters(step.parameters),
      );
      setOpen(false);
      await onSaved();
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      id="sql-check-query"
      className="mt-6 border-t border-border-button pt-4"
      data-testid="guided-sql-form"
    >
      <Button variant="ghost" onClick={() => setOpen((value) => !value)}>
        <Code2 className="size-4" />
        {open
          ? 'Скрыть редактор SQL'
          : initialSql
            ? 'Редактировать финальный SQL'
            : 'Собрать SQL по шагам'}
      </Button>
      {open && !draftLoaded && (
        <p className="mt-3 text-xs" role="status">
          {error || 'Загружаем серверный черновик…'}
        </p>
      )}
      {open && draftLoaded && (
        <div className="mt-4 max-w-3xl space-y-4">
          {recovery && (
            <div role="alert" className="space-y-2 text-xs">
              <p>
                В этой вкладке остались несохранённые изменения, а серверный
                черновик уже изменился. Выберите, с какой версией продолжить.
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  mustSaveRecoveredDraft.current = true;
                  setSteps(recovery);
                  setCurrent(0);
                  setRecovery(null);
                }}
              >
                Восстановить изменения вкладки
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  window.sessionStorage.removeItem(pendingKey);
                  setRecovery(null);
                }}
              >
                Использовать серверный черновик
              </Button>
            </div>
          )}
          <p className="text-xs leading-5 text-text-secondary">
            Каждый шаг — полный PostgreSQL SELECT с явными алиасами и LIMIT
            :row_limit. Проверка подтверждает синтаксис, разрешённые таблицы и
            поля, параметры и режим чтения. Строки и значения данных проверяются
            после запуска финального запроса.
          </p>
          <nav className="flex flex-wrap gap-2" aria-label="Шаги SQL">
            {steps.map((item, index) => (
              <Button
                key={item.id}
                type="button"
                size="sm"
                variant={index === current ? 'default' : 'outline'}
                disabled={busy}
                onClick={() => {
                  setCurrent(index);
                  setError(null);
                }}
              >
                {item.verified && <Check className="size-3" />}
                {index + 1}. {item.title}
              </Button>
            ))}
          </nav>
          <p className="text-xs text-text-secondary">
            {STEP_HINTS[current] || 'Уточните запрос и проверьте новую версию.'}
          </p>
          <label className="block text-xs">
            Название шага
            <Input
              aria-label="Название шага"
              className="mt-1"
              value={step.title}
              maxLength={80}
              disabled={busy || Boolean(recovery)}
              onChange={(event) => update({ title: event.target.value })}
            />
          </label>
          <label className="block text-xs">
            SQL шага {current + 1}
            <Textarea
              aria-label={`SQL шага ${current + 1}`}
              value={step.sql}
              disabled={busy || Boolean(recovery)}
              onChange={(event) => update({ sql: event.target.value })}
              className="mt-1 min-h-52 font-mono text-xs"
            />
          </label>
          <label className="block text-xs">
            Параметры JSON шага {current + 1}
            <Textarea
              aria-label={`Параметры JSON шага ${current + 1}`}
              value={step.parameters}
              disabled={busy || Boolean(recovery)}
              onChange={(event) => update({ parameters: event.target.value })}
              className="mt-1 min-h-20 font-mono text-xs"
            />
          </label>
          {step.verified && (
            <div
              className="border-s-2 border-state-success ps-3 text-xs"
              role="status"
            >
              <p>
                Шаг проверен · таблицы:{' '}
                {step.verified.guard.status === 'PASS'
                  ? step.verified.guard.tables.join(', ')
                  : '—'}
              </p>
              <p>
                Выходные поля:{' '}
                {step.verified.guard.status === 'PASS'
                  ? step.verified.guard.output_columns?.join(', ') || '—'
                  : '—'}
              </p>
              <p>
                Параметры: {Object.keys(step.verified.parameters).join(', ')}
              </p>
            </div>
          )}
          {error && (
            <p className="text-xs text-state-error" role="alert">
              {error}
            </p>
          )}
          <div className="flex items-center gap-2 text-xs" role="status">
            <span>
              {draftSaveState === 'saved'
                ? 'Черновик сохранён на сервере'
                : draftSaveState === 'saving'
                  ? 'Сохраняем черновик…'
                  : draftSaveState === 'error'
                    ? 'Черновик не сохранён'
                    : 'Есть несохранённые изменения'}
            </span>
            {draftSaveState === 'error' && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  setRecovery(null);
                  setLoadVersion((value) => value + 1);
                }}
              >
                Обновить серверный черновик
              </Button>
            )}
            {draftSaveState !== 'saved' && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={draftSaveState === 'saving' || Boolean(recovery)}
                onClick={() =>
                  void persistDraft(
                    steps.map(({ title, sql, parameters }) => ({
                      title,
                      sql,
                      parameters,
                    })),
                  ).catch(() => undefined)
                }
              >
                Сохранить черновик
              </Button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={
                busy ||
                Boolean(recovery) ||
                !previousVerified ||
                !step.sql.trim()
              }
              onClick={() => void validate()}
            >
              {busy && <LoaderCircle className="size-4 animate-spin" />}
              Проверить шаг
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={
                busy || Boolean(recovery) || !allVerified || steps.length >= 12
              }
              onClick={addStep}
            >
              <Plus className="size-4" />
              Добавить уточнение
            </Button>
            {finalStep && steps.length > 1 && (
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setSteps((items) => items.slice(0, -1));
                  setCurrent(current - 1);
                  setAligned(false);
                }}
              >
                Удалить текущий шаг
              </Button>
            )}
          </div>
          {finalStep && (
            <div className="space-y-3 border-t border-border-button pt-4">
              {saveDisabledReason && (
                <p className="text-xs text-text-secondary">
                  {saveDisabledReason}
                </p>
              )}
              <label className="flex items-center gap-2 text-xs">
                <Checkbox
                  checked={aligned}
                  disabled={!allVerified || busy}
                  onCheckedChange={(value) => setAligned(value === true)}
                />
                Финальный SQL соответствует принятой задаче и выбранным данным
              </label>
              <Button
                disabled={
                  busy ||
                  Boolean(recovery) ||
                  !aligned ||
                  !allVerified ||
                  Boolean(saveDisabledReason)
                }
                onClick={() => void save()}
              >
                Сохранить проверенный SQL
              </Button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
