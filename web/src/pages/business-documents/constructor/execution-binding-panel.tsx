import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type {
  SqlExecutionBindingResponse,
  SqlQueryPlanRequest,
} from '@/pages/business-documents/types';
import { resolveBusinessDocumentSqlExecutionBinding } from '@/services/business-document-service';
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  LoaderCircle,
} from 'lucide-react';
import { useState } from 'react';

const SELECT_CLASS =
  'h-8 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm text-text-primary outline-none focus:ring-1 focus:ring-accent-primary';

const REASON_TEXT: Record<
  Exclude<SqlExecutionBindingResponse['reason'], null>,
  string
> = {
  CATALOG_IDENTITY_MISSING:
    'Снимок создан без service/database/schema. Обновите снимок схемы.',
  CATALOG_BINDING_MISSING:
    'Для одной или нескольких схем нет активной связи в центральном реестре.',
  CROSS_PROFILE_QUERY_UNSUPPORTED:
    'Выбранные таблицы относятся к разным профилям выполнения. В первой версии запрос должен выполняться в одной PostgreSQL-базе.',
  MULTIPLE_EXECUTION_PROFILES:
    'Найдено несколько совместимых профилей — выберите один явно.',
};

export function ExecutionBindingPanel({
  request,
}: {
  request: SqlQueryPlanRequest;
}) {
  const [result, setResult] = useState<SqlExecutionBindingResponse | null>(
    null,
  );
  const [selectedProfileId, setSelectedProfileId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const resolve = async (profileId: string | null) => {
    setChecking(true);
    setError(null);
    try {
      const next = await resolveBusinessDocumentSqlExecutionBinding({
        schema_version: request.schema_version,
        schema_snapshot: request.schema_snapshot,
        accepted_requirements: request.accepted_requirements,
        accepted_schema: request.accepted_schema,
        selected_profile_id: profileId,
      });
      setResult(next);
      if (next.status === 'NEEDS_SELECTION') {
        setSelectedProfileId(profileId || next.candidates[0]?.id || '');
      } else if (next.selection) {
        setSelectedProfileId(next.selection.profile.id);
      }
    } catch (resolveError) {
      setResult(null);
      setError(
        resolveError instanceof Error
          ? resolveError.message
          : 'Не удалось проверить профиль выполнения.',
      );
    } finally {
      setChecking(false);
    }
  };

  const profile = result?.selection?.profile;

  return (
    <section
      className="space-y-3 rounded-md border border-border-button bg-bg-card/30 p-4"
      data-testid="query-execution-binding-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <Database className="mt-0.5 size-5 shrink-0 text-accent-primary" />
          <div>
            <h3 className="text-sm font-semibold">
              Источник выполнения запроса
            </h3>
            <p className="mt-1 text-xs text-text-secondary">
              Сверяем снимок OpenMetadata с центральным реестром. Эта проверка
              не подключается к базе и не выполняет SQL.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            data-testid="query-execution-binding-status"
            className={
              result?.status === 'BOUND'
                ? 'border-state-success/40 text-state-success'
                : result?.status === 'UNAVAILABLE' || error
                  ? 'border-state-error/40 text-state-error'
                  : 'border-border-button text-text-secondary'
            }
          >
            {result?.status || (error ? 'ERROR' : 'NOT_CHECKED')}
          </Badge>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => resolve(null)}
            disabled={checking}
            data-testid="query-execution-binding-resolve"
          >
            {checking ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Database className="size-4" />
            )}
            Проверить профиль
          </Button>
        </div>
      </div>

      {error && (
        <p
          className="flex items-start gap-2 text-xs text-state-error"
          role="alert"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}

      {result?.status === 'UNAVAILABLE' && result.reason && (
        <div
          className="flex items-start gap-2 rounded-md border border-state-error/30 bg-state-error/5 p-3 text-xs text-state-error"
          role="alert"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div>
            <p className="font-medium">Профиль не найден</p>
            <p className="mt-1 text-text-secondary">
              {REASON_TEXT[result.reason]}
            </p>
          </div>
        </div>
      )}

      {result?.status === 'NEEDS_SELECTION' && (
        <div className="grid gap-3 rounded-md border border-state-warning/30 bg-state-warning/5 p-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <label className="space-y-1 text-xs font-medium">
            <span>Совместимый профиль</span>
            <select
              className={SELECT_CLASS}
              aria-label="Профиль выполнения SQL"
              value={selectedProfileId}
              onChange={(event) => setSelectedProfileId(event.target.value)}
            >
              {result.candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name} · {candidate.target_database}
                </option>
              ))}
            </select>
          </label>
          <Button
            type="button"
            size="sm"
            onClick={() => resolve(selectedProfileId || null)}
            disabled={checking || !selectedProfileId}
            data-testid="query-execution-binding-select"
          >
            Подтвердить профиль
          </Button>
        </div>
      )}

      {result?.status === 'BOUND' && profile && (
        <div
          className="grid gap-3 rounded-md border border-state-success/30 bg-state-success/5 p-3 text-xs sm:grid-cols-2 lg:grid-cols-4"
          data-testid="query-execution-binding-selection"
        >
          <div className="flex items-start gap-2 sm:col-span-2 lg:col-span-1">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-state-success" />
            <div>
              <p className="font-medium text-text-primary">{profile.name}</p>
              <p className="text-text-secondary">
                {profile.target_database} · {profile.dialect}
              </p>
            </div>
          </div>
          <div>
            <p className="text-text-secondary">Statement timeout</p>
            <p className="font-medium">{profile.statement_timeout_ms} мс</p>
          </div>
          <div>
            <p className="text-text-secondary">Максимум строк</p>
            <p className="font-medium">{profile.max_rows}</p>
          </div>
          <div>
            <p className="text-text-secondary">Максимум результата</p>
            <p className="font-medium">
              {(profile.max_result_bytes / 1_000_000).toLocaleString('ru-RU')}{' '}
              МБ
            </p>
          </div>
          <p
            className="break-all text-[11px] text-text-secondary sm:col-span-2 lg:col-span-4"
            title={profile.policy_fingerprint}
          >
            Политика v{profile.version}: {profile.policy_fingerprint}
          </p>
          {result.selection?.relations?.length ? (
            <div
              className="space-y-1 border-t border-state-success/20 pt-2 sm:col-span-2 lg:col-span-4"
              data-testid="query-execution-relation-mappings"
            >
              <p className="font-medium text-text-primary">
                Соответствие каталога и PostgreSQL
              </p>
              {result.selection.relations.map((relation) => (
                <p
                  key={relation.entity_id}
                  className="break-all font-mono text-[11px] text-text-secondary"
                >
                  {relation.catalog_fqn} → {relation.physical_relation}
                </p>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
