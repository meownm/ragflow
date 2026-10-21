import { Button } from '@/components/ui/button';
import type {
  SqlAgentProject,
  SqlExecutionBindingResponse,
  SqlProjectConnectionCheck,
} from '@/pages/business-documents/types';
import {
  checkBusinessDocumentSqlProjectConnection,
  configureBusinessDocumentSqlProjectConnection,
} from '@/services/business-document-service';
import { type ReactNode, useEffect, useRef, useState } from 'react';

function errorText(error: unknown) {
  return error instanceof Error
    ? error.message
    : 'Не удалось выполнить действие с подключением.';
}

export function useSqlProjectConnection(
  project: SqlAgentProject,
  onRefresh: () => Promise<void>,
  queryBinding?: SqlExecutionBindingResponse,
) {
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(
    project.execution_profile_id || null,
  );
  const [profiles, setProfiles] = useState<Array<{ id: string; name: string }>>(
    [],
  );
  const [health, setHealth] = useState<SqlProjectConnectionCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const attempt = useRef<{ signature: string; key: string } | null>(null);
  const canConfigure =
    !project.archived_at &&
    project.capabilities.execute !== false &&
    project.capabilities.edit !== false;
  const active = (status: string) =>
    ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(status);
  const runsInProgress = Boolean(
    active(project.latest_run?.status || '') ||
    project.runs?.some((run) => active(run.status)) ||
    project.derived_runs?.some((run) => active(run.status)),
  );
  const locked = project.operation_state !== 'IDLE' || runsInProgress;

  useEffect(() => {
    setSelectedProfileId(project.execution_profile_id || null);
    attempt.current = null;
  }, [project.id, project.execution_profile_id, canConfigure]);
  useEffect(() => {
    setProfiles([]);
  }, [project.id, project.artifact_ids.schema, canConfigure]);
  useEffect(() => {
    if (queryBinding) setProfiles(queryBinding.candidates || []);
  }, [queryBinding]);
  useEffect(() => {
    const pending = { generation, controller };
    const invalidate = () => {
      ++generation.current;
      controller.current?.abort();
      setHealth(null);
      setError(null);
      setChecking(false);
      setSaving(false);
    };
    invalidate();
    window.addEventListener('pagehide', invalidate);
    return () => {
      ++pending.generation.current;
      pending.controller.current?.abort();
      window.removeEventListener('pagehide', invalidate);
    };
  }, [
    project.id,
    project.state_version,
    project.artifact_ids.schema,
    selectedProfileId,
    canConfigure,
  ]);

  const binding = queryBinding || health?.binding;
  const dirty = selectedProfileId !== (project.execution_profile_id || null);
  const check = async () => {
    if (!canConfigure || checking || saving) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setChecking(true);
    setHealth(null);
    setError(null);
    try {
      const result = await checkBusinessDocumentSqlProjectConnection(
        project.id,
        selectedProfileId,
        current.signal,
      );
      if (current.signal.aborted || controller.current !== current) return;
      if (
        result.project_id !== project.id ||
        result.state_version !== project.state_version ||
        (result.status === 'AVAILABLE' &&
          selectedProfileId !== null &&
          result.binding.selection?.profile.id !== selectedProfileId)
      ) {
        throw new Error(
          'Проект изменился. Обновите его перед проверкой подключения.',
        );
      }
      setHealth(result);
      setProfiles(result.binding.candidates || []);
    } catch (nextError) {
      if (!current.signal.aborted && controller.current === current)
        setError(errorText(nextError));
    } finally {
      if (!current.signal.aborted && controller.current === current)
        setChecking(false);
    }
  };
  const save = async () => {
    if (!canConfigure || locked || saving || checking || !dirty) return;
    const signature = JSON.stringify([
      project.id,
      project.state_version,
      selectedProfileId,
    ]);
    if (attempt.current?.signature !== signature) {
      attempt.current = {
        signature,
        key: `sql-connection-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`,
      };
    }
    const current = generation.current;
    setSaving(true);
    setError(null);
    try {
      await configureBusinessDocumentSqlProjectConnection(
        project.id,
        project.state_version,
        attempt.current.key,
        selectedProfileId,
      );
      if (generation.current === current) await onRefresh();
    } catch (nextError) {
      if (generation.current === current) setError(errorText(nextError));
    } finally {
      if (generation.current === current) setSaving(false);
    }
  };
  return {
    selectedProfileId,
    setSelectedProfileId,
    profiles,
    health,
    error,
    checking,
    saving,
    binding,
    dirty,
    canConfigure,
    runsInProgress,
    locked,
    check,
    save,
    canUseSelection:
      !dirty &&
      !checking &&
      !saving &&
      !error &&
      (!health || health.status === 'AVAILABLE'),
  };
}

type Connection = ReturnType<typeof useSqlProjectConnection>;

export function SqlProjectConnectionPanel({
  connection,
  busy = false,
  hasResult = false,
  resultSource,
  children,
}: {
  connection: Connection;
  busy?: boolean;
  hasResult?: boolean;
  resultSource?: {
    profile_id: string;
    profile_name?: string | null;
    profile_version?: number;
  };
  children?: ReactNode;
}) {
  const {
    selectedProfileId,
    setSelectedProfileId,
    profiles,
    health,
    error,
    checking,
    saving,
    binding,
    dirty,
    canConfigure,
    locked,
    check,
    save,
  } = connection;
  const disabled = busy || saving;
  const selectedIsBound =
    binding?.status === 'BOUND' &&
    binding.selection?.profile.id === selectedProfileId;
  return (
    <section
      className="mb-6 space-y-3 border-b border-border-button pb-5"
      aria-label="Подключение проекта"
    >
      <div>
        <h4 className="text-sm font-semibold">Подключение</h4>
        <p className="mt-1 text-xs text-text-secondary">
          {hasResult
            ? 'Источник для следующего запуска. Полученный результат сохраняет прежний источник.'
            : 'Выбор сохраняется в проекте. Перед запуском доступ и схема проверяются заново.'}
        </p>
      </div>
      {canConfigure ? (
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-56 flex-1 text-sm">
            Источник данных
            <select
              className="mt-2 block w-full rounded-md border border-border-button bg-bg-base p-2"
              value={selectedProfileId || ''}
              disabled={disabled || locked}
              onChange={(event) =>
                setSelectedProfileId(event.target.value || null)
              }
            >
              <option value="">Автоматически по каталогу</option>
              {selectedProfileId &&
                !profiles.some(
                  (profile) => profile.id === selectedProfileId,
                ) && (
                  <option value={selectedProfileId}>
                    {binding?.selection?.profile.id === selectedProfileId
                      ? binding.selection.profile.name
                      : binding
                        ? 'Сохранённый источник недоступен'
                        : 'Сохранённый источник'}
                  </option>
                )}
              {profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="outline"
            disabled={
              disabled ||
              locked ||
              checking ||
              !dirty ||
              (selectedProfileId !== null && !selectedIsBound)
            }
            onClick={() => void save()}
          >
            {saving ? 'Сохраняем источник…' : 'Сохранить источник'}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-text-secondary">
          Выбор подключения доступен пользователям с правом выполнения запросов.
        </p>
      )}
      {dirty && canConfigure && (
        <p role="status" className="text-xs text-text-secondary">
          Выбор ещё не сохранён. Сохраните источник перед запуском.
        </p>
      )}
      {canConfigure && (
        <div className="space-y-2">
          <Button
            variant="outline"
            disabled={disabled || checking}
            onClick={() => void check()}
          >
            {checking ? 'Проверяем подключение…' : 'Проверить подключение'}
          </Button>
          <p className="text-xs text-text-secondary">
            Проверяется только доступность источника. Проверка SQL выполняется
            отдельно.
          </p>
          {error && (
            <p role="alert" className="text-sm text-state-error">
              {error}
            </p>
          )}
          {health && (
            <p role="status" className="text-xs text-text-secondary">
              {health.status === 'AVAILABLE'
                ? `Подключение доступно: ${health.binding.selection?.profile.name} · версия ${health.binding.selection?.profile.version}. Проверено ${new Date(health.checked_at!).toLocaleTimeString()}.`
                : health.status === 'NEEDS_SELECTION'
                  ? 'Для проверки подключения выберите один источник.'
                  : 'Подключение недоступно. Выберите разрешённый источник.'}
            </p>
          )}
        </div>
      )}
      {children || (
        <p className="text-xs text-text-secondary">
          {binding?.status === 'BOUND'
            ? `Выбран профиль: ${binding.selection?.profile.name}`
            : binding?.status === 'UNAVAILABLE'
              ? 'Доступного подключения нет. Автоматическая подмена источника не выполняется.'
              : 'Подключение можно выбрать и проверить по принятой схеме до создания SQL.'}
        </p>
      )}
      {locked && (
        <p className="text-xs text-text-secondary">
          Источник можно изменить после завершения текущей операции.
        </p>
      )}
      {binding?.selection && (
        <div className="space-y-1 text-xs text-text-secondary">
          <p>
            PostgreSQL · база {binding.selection.profile.target_database} ·
            схемы{' '}
            {[...new Set(binding.catalog_scopes?.map((scope) => scope.schema))]
              .sort()
              .join(', ') || 'не определены'}
          </p>
          <p>
            Только чтение · лимит {binding.selection.profile.max_rows} строк ·
            timeout {binding.selection.profile.statement_timeout_ms} мс
          </p>
        </div>
      )}
      {resultSource && (
        <p className="text-xs text-text-secondary">
          Источник выбранного запуска:{' '}
          {resultSource.profile_name || resultSource.profile_id} · версия{' '}
          {resultSource.profile_version}
        </p>
      )}
    </section>
  );
}

export function PreQueryConnection({
  project,
  onRefresh,
  busy = false,
}: {
  project: SqlAgentProject;
  onRefresh: () => Promise<void>;
  busy?: boolean;
}) {
  const connection = useSqlProjectConnection(project, onRefresh);
  return <SqlProjectConnectionPanel connection={connection} busy={busy} />;
}
