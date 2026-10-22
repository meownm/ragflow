import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import type { SqlAgentProject } from '@/pages/business-documents/types';
import { loadBusinessDocumentSqlSchemaEntities } from '@/services/business-document-service';
import { LoaderCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import {
  applySchemaCandidateDetails,
  buildSchemaSnapshot,
  chooseSchemaCandidate,
  toggleSchemaColumn,
  type SchemaWorkspaceState,
} from './schema-workspace';
import {
  schemaArtifactFromWorkspace,
  schemaWorkspaceFromProposal,
} from './sql-agent-document';

export function SqlSchemaReview({
  project,
  busy,
  onDecision,
  onPreview,
  onEditQuestion,
  readOnly = false,
}: {
  project: SqlAgentProject;
  busy: boolean;
  readOnly?: boolean;
  onEditQuestion?: () => void;
  onPreview?: (schema: Record<string, unknown>) => void;
  onDecision: (
    decision: 'ACCEPT' | 'REJECT',
    payload: Record<string, unknown> | null,
  ) => Promise<void>;
}) {
  const [workspace, setWorkspace] = useState<SchemaWorkspaceState>(() =>
    schemaWorkspaceFromProposal(project),
  );
  const [activeNeed, setActiveNeed] = useState(0);
  const [showAlternatives, setShowAlternatives] = useState(false);
  const [loadingEntity, setLoadingEntity] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  useEffect(() => {
    setWorkspace(schemaWorkspaceFromProposal(project));
    setLocalError(null);
    setActiveNeed(0);
    setShowAlternatives(false);
  }, [project]);
  const snapshot = useMemo(
    () => buildSchemaSnapshot(workspace.resolutions, workspace.requirements),
    [workspace],
  );
  useEffect(() => {
    onPreview?.({
      schema_snapshot: snapshot,
      accepted_schema: workspace.resolutions
        .filter((item) => item.selectedEntityId)
        .map((item) => ({ entity_id: item.selectedEntityId })),
    });
  }, [snapshot, workspace.resolutions, onPreview]);

  const selectCandidate = async (resolutionIndex: number, entityId: string) => {
    setLocalError(null);
    setWorkspace((current) => ({
      ...current,
      resolutions: current.resolutions.map((resolution, index) =>
        index === resolutionIndex
          ? chooseSchemaCandidate(resolution, entityId)
          : resolution,
      ),
    }));
    setLoadingEntity(entityId);
    try {
      const response = await loadBusinessDocumentSqlSchemaEntities({
        entity_ids: [entityId],
        locale: project.locale,
      });
      const details = response.entities.find(
        (item) => item.entity_id === entityId,
      );
      if (!details?.entity || details.lookup.status === 'ERROR') {
        throw new Error(
          details?.lookup.message || 'Схема выбранной таблицы недоступна.',
        );
      }
      setWorkspace((current) => ({
        ...current,
        resolutions: current.resolutions.map((resolution, index) => {
          if (index !== resolutionIndex) return resolution;
          const applied = applySchemaCandidateDetails(
            chooseSchemaCandidate(resolution, entityId),
            details.entity!,
            {
              freshness: details.freshness,
              retrieval: details.retrieval,
              sources: details.sources,
              warnings: details.warnings,
            },
          );
          const recommended = new Set(
            applied.interpretation?.recommendedColumnIds ?? [],
          );
          return {
            ...applied,
            selectedColumnIds:
              applied.candidates
                .find((candidate) => candidate.id === entityId)
                ?.columns.filter((column) => recommended.has(column.id))
                .map((column) => column.id) ?? [],
          };
        }),
      }));
    } catch (error) {
      setLocalError(
        error instanceof Error
          ? error.message
          : 'Схема выбранной таблицы недоступна.',
      );
    } finally {
      setLoadingEntity(null);
    }
  };

  const toggleColumn = (resolutionIndex: number, columnId: string) => {
    setWorkspace((current) => ({
      ...current,
      resolutions: current.resolutions.map((resolution, index) =>
        index === resolutionIndex
          ? toggleSchemaColumn(resolution, columnId)
          : resolution,
      ),
    }));
  };

  const resolution = workspace.resolutions[activeNeed];
  const selected = resolution?.candidates.find(
    (candidate) => candidate.id === resolution.selectedEntityId,
  );
  const candidate = selected || resolution?.candidates[0];
  const recommendationReason =
    candidate?.id === resolution?.interpretation?.recommendedEntityId
      ? resolution?.interpretation?.reason
      : undefined;
  const confirmed = snapshot.requirements.filter(
    (item) => item.state === 'CONFIRMED',
  ).length;
  const ready = snapshot.status === 'READY';

  return (
    <div className="sql-schema-review" data-testid="sql-agent-schema-review">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h3 className="text-[22px] font-semibold leading-tight xl:text-2xl">
            Подберите данные для запроса
          </h3>
          <Badge
            variant="outline"
            className={
              ready
                ? 'border-state-success text-state-success'
                : 'border-transparent bg-state-warning/10 text-state-warning'
            }
          >
            {workspace.resolutions.length
              ? `Подтверждено ${confirmed} из ${workspace.resolutions.length}`
              : 'Данные не найдены'}
          </Badge>
        </div>
        <p className="mt-2 text-sm leading-6 text-text-secondary">
          {ready
            ? 'Таблицы и поля выбраны. Подтвердите схему, чтобы перейти к проверке SQL.'
            : 'Подходящие таблицы пока не подтверждены. Проверьте соответствие вопросу или запустите поиск заново.'}
        </p>
      </div>
      {localError && (
        <p
          role="alert"
          className="border-s-2 border-state-error ps-3 text-sm text-state-error"
        >
          {localError}
        </p>
      )}
      {!resolution ? (
        <p className="text-sm text-text-secondary">
          Каталог не предложил данные. Повторите поиск или уточните задачу.
        </p>
      ) : (
        <div className="sql-schema-layout">
          <div
            className="sql-schema-needs"
            role="group"
            aria-label="Нужные данные"
          >
            {workspace.resolutions.map((item, index) => (
              <button
                key={`${item.term}-${index}`}
                type="button"
                aria-pressed={activeNeed === index}
                className={`sql-schema-need ${activeNeed === index ? 'border-accent-primary bg-accent-primary/10' : 'border-border-button bg-bg-card hover:border-accent-primary/60'}`}
                onClick={() => {
                  setActiveNeed(index);
                  setShowAlternatives(false);
                  setLocalError(null);
                }}
              >
                <span className="rounded bg-bg-base px-1.5 py-0.5 text-xs text-text-secondary">
                  {index + 1}
                </span>
                <span>
                  <span className="block text-sm font-semibold leading-5">
                    {item.term}
                  </span>
                  <span
                    className={`mt-1 block text-xs leading-5 ${snapshot.requirements[index]?.state === 'CONFIRMED' ? 'text-state-success' : 'text-state-warning'}`}
                  >
                    {snapshot.requirements[index]?.state === 'CONFIRMED'
                      ? 'Таблица и поля выбраны'
                      : item.status === 'not_found'
                        ? 'Таблица не найдена'
                        : 'Таблица не подтверждена'}
                  </span>
                </span>
              </button>
            ))}
          </div>
          <section
            className="sql-schema-candidate border-border-button bg-bg-card"
            aria-label={`Таблица для: ${resolution.term}`}
          >
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-text-secondary">
              <p>Найдено в каталоге</p>
              <Badge
                variant="outline"
                className={`shrink-0 whitespace-nowrap border-transparent ${snapshot.requirements[activeNeed]?.state === 'CONFIRMED' ? 'bg-state-success/10 text-state-success' : 'bg-state-warning/10 text-state-warning'}`}
              >
                {snapshot.requirements[activeNeed]?.state === 'CONFIRMED'
                  ? 'Поля выбраны'
                  : 'Не подтверждено'}
              </Badge>
            </div>
            {candidate ? (
              <>
                <h4 className="mt-4 text-xl font-semibold leading-7">
                  {candidate.displayName || candidate.name}
                </h4>
                <p className="mt-3 text-xs leading-5 text-text-secondary">
                  {[candidate.database, candidate.schema, candidate.name]
                    .filter(Boolean)
                    .join(' / ')}
                </p>
                <div className="mt-3 space-y-3 border-t border-border-button pt-3 text-sm leading-6">
                  <p>
                    {recommendationReason ||
                      candidate.description ||
                      'Описание источника не указано.'}
                  </p>
                  {candidate.description && recommendationReason && (
                    <p className="text-text-secondary">
                      {candidate.description}
                    </p>
                  )}
                  <p className="text-text-secondary">
                    Просмотрите описание и поля перед выбором. Таблица должна
                    отвечать этой части вопроса.
                  </p>
                  {resolution.interpretation?.clarificationQuestion && (
                    <p>{resolution.interpretation.clarificationQuestion}</p>
                  )}
                </div>
                <div className="mt-6 flex flex-wrap gap-3">
                  {candidate.schemaStatus !== 'loaded' && (
                    <Button
                      variant="outline"
                      disabled={busy || loadingEntity !== null}
                      onClick={() =>
                        void selectCandidate(activeNeed, candidate.id)
                      }
                    >
                      {loadingEntity === candidate.id && (
                        <LoaderCircle className="size-4 animate-spin" />
                      )}
                      Просмотреть таблицу
                    </Button>
                  )}
                  {resolution.candidates.length > 1 && (
                    <Button
                      variant="ghost"
                      aria-expanded={showAlternatives}
                      onClick={() => setShowAlternatives(!showAlternatives)}
                    >
                      Другие варианты ({resolution.candidates.length - 1})
                    </Button>
                  )}
                </div>
                {showAlternatives && (
                  <div className="mt-4 grid gap-2 sm:grid-cols-2">
                    {resolution.candidates
                      .filter((item) => item.id !== candidate.id)
                      .map((item) => (
                        <Button
                          key={item.id}
                          variant="outline"
                          className="h-auto min-w-0 justify-start whitespace-normal py-3 text-left"
                          disabled={busy || loadingEntity !== null}
                          onClick={() => {
                            setShowAlternatives(false);
                            void selectCandidate(activeNeed, item.id);
                          }}
                        >
                          <span className="min-w-0 break-words">
                            {item.displayName || item.name}
                            <span className="mt-1 block text-xs text-text-secondary">
                              {item.description || item.fqn}
                            </span>
                          </span>
                        </Button>
                      ))}
                  </div>
                )}
                {selected?.schemaStatus === 'loaded' && (
                  <div className="mt-5 border-t border-border-button pt-4">
                    <p className="text-sm font-medium">
                      Поля для ответа и расчёта · выбрано{' '}
                      {resolution.selectedColumnIds.length}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-text-secondary">
                      Отметьте поля для группировки, расчётов и условий. Столбцы
                      готового ответа можно проверить на следующем шаге.
                    </p>
                    <div className="mt-3 grid max-h-64 gap-x-5 gap-y-3 overflow-y-auto pe-2 sm:grid-cols-2">
                      {selected.columns.map((column) => (
                        <label
                          key={column.id}
                          className="flex cursor-pointer items-start gap-2 text-xs leading-5"
                        >
                          <Checkbox
                            className="mt-0.5"
                            disabled={readOnly || busy}
                            checked={resolution.selectedColumnIds.includes(
                              column.id,
                            )}
                            onCheckedChange={() =>
                              toggleColumn(activeNeed, column.id)
                            }
                          />
                          <span className="min-w-0 break-words">
                            <span className="block font-medium">
                              {column.displayName || column.name}
                            </span>
                            <span className="block text-text-secondary">
                              {column.description ||
                                column.dataType ||
                                'Описание не указано'}
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <p className="mt-4 text-sm">
                {resolution.error ||
                  'Подходящие таблицы не найдены. Уточните задачу или повторите поиск.'}
              </p>
            )}
            {resolution.freshness?.stale !== false && (
              <p className="mt-4 text-sm leading-5 text-state-warning">
                Актуальность каталога не подтверждена. Обновите поиск перед
                подтверждением схемы.
              </p>
            )}
            {resolution.warnings.map((warning, index) => (
              <p
                key={index}
                className="mt-2 text-xs leading-5 text-state-warning"
              >
                {warning}
              </p>
            ))}
            {selected?.columnsTruncated && (
              <p className="mt-3 text-sm leading-5 text-state-warning">
                Каталог вернул не все поля таблицы. Для подтверждения нужна
                полная схема.
              </p>
            )}
            {selected?.schemaStatus === 'loaded' &&
              (!selected.schemaFingerprint || selected.version === null) && (
                <p className="mt-3 text-sm leading-5 text-state-warning">
                  Каталог не подтвердил версию схемы. Повторите поиск перед
                  подтверждением.
                </p>
              )}
          </section>
        </div>
      )}
      <div className="sql-schema-footer border-border-button">
        <div className="sql-schema-actions">
          <Button
            className="bg-accent-primary text-black hover:bg-accent-primary/90"
            disabled={readOnly || busy || loadingEntity !== null}
            onClick={() => void onDecision('REJECT', null)}
          >
            Повторить поиск
          </Button>
          {onEditQuestion && (
            <Button variant="outline" disabled={busy} onClick={onEditQuestion}>
              Изменить задачу
            </Button>
          )}
          <Button
            className="bg-accent-primary text-black hover:bg-accent-primary/90 disabled:bg-bg-component disabled:text-text-secondary disabled:opacity-100"
            disabled={readOnly || busy || loadingEntity !== null || !ready}
            onClick={() =>
              void onDecision(
                'ACCEPT',
                schemaArtifactFromWorkspace(workspace) as Record<
                  string,
                  unknown
                >,
              )
            }
          >
            Подтвердить схему
          </Button>
        </div>
        <p role="status" className="mt-3 text-xs leading-5 text-text-secondary">
          {readOnly
            ? 'Режим просмотра. Подтверждение доступно редактору проекта.'
            : ready
              ? 'Схема готова к подтверждению.'
              : snapshot.status === 'STALE'
                ? 'Для подтверждения обновите устаревшие данные каталога.'
                : 'Для подтверждения нужны подходящие таблицы и выбранные поля для всех частей вопроса.'}
        </p>
      </div>
    </div>
  );
}
