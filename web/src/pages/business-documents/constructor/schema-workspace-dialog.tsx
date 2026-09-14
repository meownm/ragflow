import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  loadBusinessDocumentSqlSchemaEntities,
  resolveBusinessDocumentSqlSchema,
} from '@/services/business-document-service';
import {
  Database,
  Download,
  ExternalLink,
  LoaderCircle,
  Search,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { DocumentConstructorStorageScope } from './draft-storage';
import {
  applySchemaCandidateDetails,
  buildSchemaSnapshot,
  chooseSchemaCandidate,
  createSchemaResolution,
  createSchemaResolutionError,
  markSchemaCandidateError,
  markSchemaCandidateLoading,
  parseSchemaTerms,
  toggleSchemaColumn,
  type SchemaTermResolution,
  type SchemaWorkspaceState,
} from './schema-workspace';
import {
  createSchemaWorkspaceStorageKey,
  emptySchemaWorkspace,
  loadSchemaWorkspace,
  saveSchemaWorkspace,
} from './schema-workspace-storage';

function browserStorage() {
  try {
    return typeof window === 'undefined' ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}

function resolutionLabel(resolution: SchemaTermResolution) {
  if (resolution.status === 'confirmed') return 'Таблица подтверждена';
  if (resolution.status === 'needs_clarification') return 'Требуется выбор';
  if (resolution.status === 'not_found') return 'Таблица не найдена';
  return 'Ошибка каталога';
}

function snapshotLabel(
  status: ReturnType<typeof buildSchemaSnapshot>['status'],
) {
  if (status === 'READY') return 'Снимок готов';
  if (status === 'STALE') return 'Снимок устарел';
  return 'Нужно уточнение';
}

function updateResolution(
  workspace: SchemaWorkspaceState,
  index: number,
  update: (resolution: SchemaTermResolution) => SchemaTermResolution,
) {
  return {
    ...workspace,
    resolutions: workspace.resolutions.map((resolution, currentIndex) =>
      currentIndex === index ? update(resolution) : resolution,
    ),
  };
}

export function SchemaWorkspaceDialog({
  scope,
}: {
  scope: DocumentConstructorStorageScope;
}) {
  const storageKey = useMemo(
    () => createSchemaWorkspaceStorageKey(scope),
    [scope],
  );
  const [workspace, setWorkspace] = useState<SchemaWorkspaceState>(() =>
    emptySchemaWorkspace(),
  );
  const [loadedStorageKey, setLoadedStorageKey] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const searchSequence = useRef(0);
  const detailsGeneration = useRef(0);
  const detailsRequestSequence = useRef(0);
  const latestDetailsRequest = useRef(new Map<string, number>());

  useEffect(() => {
    searchSequence.current += 1;
    detailsGeneration.current += 1;
    latestDetailsRequest.current.clear();
    setSearching(false);
    setWorkspace(loadSchemaWorkspace(browserStorage(), storageKey, scope));
    setValidationError(null);
    setStorageError(null);
    setLoadedStorageKey(storageKey);
  }, [scope, storageKey]);

  useEffect(() => {
    if (loadedStorageKey !== storageKey) return;
    const storage = browserStorage();
    if (!storage) {
      setStorageError('Локальное сохранение снимка недоступно.');
      return;
    }
    const timeout = window.setTimeout(() => {
      try {
        saveSchemaWorkspace(storage, storageKey, scope, workspace);
        setStorageError(null);
      } catch {
        setStorageError('Не удалось сохранить снимок локально.');
      }
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [loadedStorageKey, scope, storageKey, workspace]);

  useEffect(
    () => () => {
      searchSequence.current += 1;
      detailsGeneration.current += 1;
      latestDetailsRequest.current.clear();
    },
    [],
  );

  const snapshot = useMemo(
    () => buildSchemaSnapshot(workspace.resolutions, workspace.requirements),
    [workspace.requirements, workspace.resolutions],
  );

  const updateSourceInput = (
    field: 'input' | 'requirements',
    value: string,
  ) => {
    searchSequence.current += 1;
    detailsGeneration.current += 1;
    latestDetailsRequest.current.clear();
    setSearching(false);
    setWorkspace((current) => ({
      ...current,
      [field]: value,
      resolutions: [],
    }));
    setValidationError(null);
  };

  const hydrateEntityDetails = async (
    entityIds: string[],
    generation: number,
    requestId: number,
  ) => {
    try {
      const response = await loadBusinessDocumentSqlSchemaEntities({
        entity_ids: [...new Set(entityIds)],
        locale: 'ru',
      });
      if (generation !== detailsGeneration.current) return;
      const byId = new Map(
        response.entities.map((details) => [details.entity_id, details]),
      );
      setWorkspace((current) => ({
        ...current,
        resolutions: current.resolutions.map((resolution) => {
          const entityId = resolution.selectedEntityId;
          if (!entityId || !entityIds.includes(entityId)) return resolution;
          if (latestDetailsRequest.current.get(entityId) !== requestId) {
            return resolution;
          }
          const details = byId.get(entityId);
          if (
            !details ||
            details.lookup.status === 'ERROR' ||
            !details.entity
          ) {
            return markSchemaCandidateError(
              resolution,
              entityId,
              details?.lookup.message ||
                'Не удалось загрузить схему выбранной таблицы.',
            );
          }
          return applySchemaCandidateDetails(resolution, details.entity, {
            freshness: details.freshness,
            retrieval: details.retrieval,
            sources: details.sources,
            warnings: details.warnings,
          });
        }),
      }));
    } catch (error) {
      if (generation !== detailsGeneration.current) return;
      const message =
        error instanceof Error
          ? error.message
          : 'Не удалось загрузить схему выбранной таблицы.';
      setWorkspace((current) => ({
        ...current,
        resolutions: current.resolutions.map((resolution) =>
          resolution.selectedEntityId &&
          entityIds.includes(resolution.selectedEntityId) &&
          latestDetailsRequest.current.get(resolution.selectedEntityId) ===
            requestId
            ? markSchemaCandidateError(
                resolution,
                resolution.selectedEntityId,
                message,
              )
            : resolution,
        ),
      }));
    }
  };

  const requestEntityDetails = (entityIds: string[]) => {
    const uniqueEntityIds = [...new Set(entityIds)];
    const requestId = ++detailsRequestSequence.current;
    uniqueEntityIds.forEach((entityId) =>
      latestDetailsRequest.current.set(entityId, requestId),
    );
    return hydrateEntityDetails(
      uniqueEntityIds,
      detailsGeneration.current,
      requestId,
    );
  };

  const selectCandidate = (resolutionIndex: number, entityId: string) => {
    setWorkspace((current) =>
      updateResolution(current, resolutionIndex, (resolution) =>
        markSchemaCandidateLoading(
          chooseSchemaCandidate(resolution, entityId),
          entityId,
        ),
      ),
    );
    void requestEntityDetails([entityId]);
  };

  const searchCatalog = async () => {
    const parsed = parseSchemaTerms(workspace.input);
    const requirementsError = workspace.requirements.trim()
      ? null
      : 'Укажите исходные требования к запросу.';
    setValidationError(requirementsError || parsed.error);
    if (requirementsError || parsed.error) return;

    const sequence = ++searchSequence.current;
    detailsGeneration.current += 1;
    latestDetailsRequest.current.clear();
    const previous = new Map(
      workspace.resolutions.map((resolution) => [
        resolution.term.toLocaleLowerCase(),
        resolution,
      ]),
    );
    setSearching(true);
    try {
      const response = await resolveBusinessDocumentSqlSchema({
        terms: parsed.terms,
        requirements: workspace.requirements,
        locale: 'ru',
      });
      if (sequence !== searchSequence.current) return;
      const responseByTerm = new Map(
        response.resolutions.map((resolution) => [
          resolution.term.toLocaleLowerCase(),
          resolution,
        ]),
      );
      const resolutions = parsed.terms.map((term) => {
        const resolved = responseByTerm.get(term.toLocaleLowerCase());
        if (!resolved) {
          return createSchemaResolutionError(
            term,
            new Error('Сервер не вернул решение для сущности.'),
          );
        }
        if (resolved.lookup.status === 'ERROR') {
          return createSchemaResolutionError(
            term,
            new Error(
              resolved.lookup.message ||
                'Не удалось выполнить поиск по каталогу.',
            ),
          );
        }
        return createSchemaResolution(
          term,
          resolved.catalog_answer,
          previous.get(term.toLocaleLowerCase()),
          {
            kind: resolved.interpretation.kind,
            normalizedTerm: resolved.interpretation.normalized_term,
            recommendedEntityId: resolved.interpretation.recommended_entity_id,
            recommendedColumnIds:
              resolved.interpretation.recommended_column_ids,
            confidence: resolved.interpretation.confidence,
            reason: resolved.interpretation.reason,
            clarificationQuestion:
              resolved.interpretation.clarification_question,
            llmStatus: response.llm.status,
            promptName: response.llm.prompt?.name || null,
            promptVersion: response.llm.prompt?.version || null,
            promptHash: response.llm.prompt?.content_hash || null,
            llmWarning: response.llm.warning,
          },
        );
      });
      const automaticEntityIds = resolutions.flatMap((resolution) =>
        resolution.selectedEntityId ? [resolution.selectedEntityId] : [],
      );
      const prepared = resolutions.map((resolution) =>
        resolution.selectedEntityId
          ? markSchemaCandidateLoading(resolution, resolution.selectedEntityId)
          : resolution,
      );
      setWorkspace((current) => ({ ...current, resolutions: prepared }));
      if (automaticEntityIds.length) {
        await requestEntityDetails(automaticEntityIds);
      }
    } catch (error) {
      if (sequence !== searchSequence.current) return;
      setWorkspace((current) => ({
        ...current,
        resolutions: parsed.terms.map((term) =>
          createSchemaResolutionError(term, error),
        ),
      }));
    } finally {
      if (sequence === searchSequence.current) setSearching(false);
    }
  };

  const downloadSnapshot = () => {
    if (!workspace.resolutions.length) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(snapshot, null, 2)], {
        type: 'application/json;charset=utf-8',
      }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = 'sql-schema-snapshot.v1.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" data-testid="open-schema-workspace">
          <Database className="size-4" />
          <span className="hidden sm:inline">Схема данных</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[92vh] max-w-[min(980px,calc(100vw-2rem))] overflow-hidden">
        <DialogHeader>
          <DialogTitle>Сопоставление сущностей со схемой</DialogTitle>
          <DialogDescription className="text-text-secondary">
            Сервер читает OpenMetadata и связанный Dataset, затем один раз
            обращается к LLM арендатора за объяснением. Несколько кандидатов
            никогда не принимаются автоматически.
          </DialogDescription>
        </DialogHeader>

        <div
          className="min-h-0 space-y-5 overflow-y-auto pe-1"
          data-testid="schema-workspace"
        >
          <section className="space-y-2">
            <label
              htmlFor="schema-original-requirements"
              className="text-sm font-medium"
            >
              Исходные требования к запросу
            </label>
            <Textarea
              id="schema-original-requirements"
              aria-label="Исходные требования к запросу"
              value={workspace.requirements}
              onChange={(event) =>
                updateSourceInput('requirements', event.target.value)
              }
              placeholder="Например: вывести оплаченные заказы за период с клиентом, статусом и суммой."
              rows={4}
              maxLength={20000}
              resize="vertical"
            />
            <p className="text-xs text-text-secondary">
              Этот текст передаётся LLM как контекст задачи отдельно от списка
              сущностей и сохраняется в снимке.
            </p>
          </section>

          <section className="space-y-2">
            <label
              htmlFor="schema-business-entities"
              className="text-sm font-medium"
            >
              Нужные бизнес-сущности
            </label>
            <Textarea
              id="schema-business-entities"
              aria-label="Нужные бизнес-сущности"
              value={workspace.input}
              onChange={(event) =>
                updateSourceInput('input', event.target.value)
              }
              placeholder={'Заказ\nКлиент\nСтатус заказа'}
              rows={4}
              resize="vertical"
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-text-secondary">
                Одна сущность на строку, максимум 8; до 5 кандидатов на
                сущность.
              </p>
              <Button onClick={searchCatalog} disabled={searching} size="sm">
                {searching ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Search className="size-4" />
                )}
                {searching ? 'Ищем' : 'Найти в базе знаний'}
              </Button>
            </div>
            {validationError && (
              <p className="text-xs text-state-error" role="alert">
                {validationError}
              </p>
            )}
            {storageError && (
              <p className="text-xs text-state-warning" role="alert">
                {storageError}
              </p>
            )}
          </section>

          {workspace.resolutions.length > 0 && (
            <section className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-button pb-3">
                <div>
                  <h3 className="text-sm font-semibold">Результат поиска</h3>
                  <p className="mt-1 text-xs text-text-secondary">
                    Подтвердите физическую таблицу и отметьте нужные поля.
                  </p>
                </div>
                <Badge variant="outline" data-testid="schema-snapshot-status">
                  {snapshotLabel(snapshot.status)}
                </Badge>
              </div>

              {workspace.resolutions.map((resolution, resolutionIndex) => {
                const selected = resolution.candidates.find(
                  (candidate) => candidate.id === resolution.selectedEntityId,
                );
                return (
                  <article
                    key={`${resolution.term}-${resolutionIndex}`}
                    className="space-y-3 border-s-2 border-border-button ps-4"
                    data-testid="schema-term-resolution"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="font-medium">{resolution.term}</h4>
                      <Badge
                        variant="outline"
                        className={
                          resolution.status === 'confirmed'
                            ? 'border-state-success/40 text-state-success'
                            : resolution.status === 'error' ||
                                resolution.status === 'not_found'
                              ? 'border-state-error/40 text-state-error'
                              : 'border-state-warning/40 text-state-warning'
                        }
                      >
                        {resolutionLabel(resolution)}
                      </Badge>
                    </div>

                    {resolution.error && (
                      <p className="text-xs text-state-error" role="alert">
                        {resolution.error}
                      </p>
                    )}
                    {resolution.status === 'not_found' && (
                      <p className="text-xs text-text-secondary">
                        Уточните термин, домен или техническое имя таблицы.
                      </p>
                    )}

                    {resolution.interpretation && (
                      <div className="rounded border border-border-button bg-bg-card px-3 py-2 text-xs">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">
                            {resolution.interpretation.llmStatus === 'APPLIED'
                              ? 'Интерпретация LLM'
                              : 'Детерминированное объяснение'}
                          </span>
                          <Badge variant="outline">
                            {resolution.interpretation.kind}
                          </Badge>
                        </div>
                        <p className="mt-1 text-text-secondary">
                          {resolution.interpretation.reason}
                        </p>
                        {resolution.interpretation.recommendedEntityId && (
                          <p className="mt-1 text-text-secondary">
                            Рекомендованный кандидат:{' '}
                            <code>
                              {resolution.candidates.find(
                                (candidate) =>
                                  candidate.id ===
                                  resolution.interpretation
                                    ?.recommendedEntityId,
                              )?.fqn ||
                                resolution.interpretation.recommendedEntityId}
                            </code>
                          </p>
                        )}
                        {resolution.interpretation.recommendedColumnIds.length >
                          0 && (
                          <p className="mt-1 text-text-secondary">
                            Рекомендованные поля:{' '}
                            <code>
                              {resolution.interpretation.recommendedColumnIds.join(
                                ', ',
                              )}
                            </code>
                          </p>
                        )}
                        {resolution.interpretation.clarificationQuestion && (
                          <p className="mt-1 text-state-warning">
                            {resolution.interpretation.clarificationQuestion}
                          </p>
                        )}
                        {resolution.interpretation.llmWarning && (
                          <p className="mt-1 text-state-warning">
                            {resolution.interpretation.llmWarning}
                          </p>
                        )}
                      </div>
                    )}

                    {resolution.candidates.length > 0 && (
                      <fieldset className="space-y-2">
                        <legend className="text-xs font-medium text-text-secondary">
                          Таблицы-кандидаты
                        </legend>
                        {resolution.candidates.map((candidate) => (
                          <label
                            key={candidate.id}
                            className={`block cursor-pointer rounded-md border p-3 transition-colors ${
                              candidate.id === resolution.selectedEntityId
                                ? 'border-accent-primary bg-accent-primary/5'
                                : 'border-border-button hover:border-border-default'
                            }`}
                          >
                            <span className="flex items-start gap-3">
                              <input
                                type="radio"
                                name={`schema-candidate-${resolutionIndex}`}
                                aria-label={`Выбрать ${candidate.fqn}`}
                                checked={
                                  candidate.id === resolution.selectedEntityId
                                }
                                onChange={() =>
                                  selectCandidate(resolutionIndex, candidate.id)
                                }
                                className="mt-1 accent-[var(--color-accent-primary)]"
                              />
                              <span className="min-w-0 flex-1">
                                <span className="flex flex-wrap items-center gap-2">
                                  <code className="break-all text-xs font-semibold text-text-primary">
                                    {candidate.fqn}
                                  </code>
                                  {candidate.version !== null && (
                                    <span className="text-[11px] text-text-secondary">
                                      v{candidate.version}
                                    </span>
                                  )}
                                </span>
                                <span className="mt-1 block text-xs text-text-secondary">
                                  {candidate.description ||
                                    'Описание отсутствует'}
                                </span>
                                <span className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-text-secondary">
                                  <span>
                                    {candidate.columnsTruncated
                                      ? `${candidate.columns.length} из ${candidate.columnCount} полей`
                                      : `${candidate.columnCount} полей`}
                                  </span>
                                  {candidate.matchedBy.length > 0 && (
                                    <span>
                                      Найдено: {candidate.matchedBy.join(', ')}
                                    </span>
                                  )}
                                  {candidate.url && (
                                    <a
                                      href={candidate.url}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="inline-flex items-center gap-1 text-accent-primary hover:underline"
                                      onClick={(event) =>
                                        event.stopPropagation()
                                      }
                                    >
                                      Источник
                                      <ExternalLink className="size-3" />
                                    </a>
                                  )}
                                </span>
                              </span>
                            </span>
                          </label>
                        ))}
                      </fieldset>
                    )}

                    {selected?.schemaStatus === 'loading' && (
                      <p className="flex items-center gap-2 text-xs text-text-secondary">
                        <LoaderCircle className="size-4 animate-spin" />
                        Загружаем полную схему выбранной таблицы…
                      </p>
                    )}
                    {selected?.schemaStatus === 'error' && (
                      <div className="flex flex-wrap items-center gap-3">
                        <p className="text-xs text-state-error" role="alert">
                          {selected.schemaError ||
                            'Не удалось загрузить схему выбранной таблицы.'}
                        </p>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            selectCandidate(resolutionIndex, selected.id)
                          }
                        >
                          Повторить загрузку
                        </Button>
                      </div>
                    )}
                    {selected?.schemaStatus === 'summary' && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          selectCandidate(resolutionIndex, selected.id)
                        }
                      >
                        Загрузить поля выбранной таблицы
                      </Button>
                    )}

                    {selected?.schemaStatus === 'loaded' && (
                      <fieldset className="space-y-2">
                        <legend className="text-xs font-medium text-text-secondary">
                          Поля таблицы — выберите нужные
                        </legend>
                        {selected.columns.length ? (
                          <div className="grid gap-2 sm:grid-cols-2">
                            {selected.columns.map((column) => (
                              <label
                                key={column.id}
                                className="flex cursor-pointer items-start gap-2 rounded border border-border-button px-3 py-2"
                              >
                                <input
                                  type="checkbox"
                                  aria-label={`Поле ${column.name}`}
                                  checked={resolution.selectedColumnIds.includes(
                                    column.id,
                                  )}
                                  onChange={() =>
                                    setWorkspace((current) =>
                                      updateResolution(
                                        current,
                                        resolutionIndex,
                                        (item) =>
                                          toggleSchemaColumn(item, column.id),
                                      ),
                                    )
                                  }
                                  className="mt-0.5 accent-[var(--color-accent-primary)]"
                                />
                                <span className="min-w-0">
                                  <span className="block break-all text-xs font-medium">
                                    {column.name}
                                    {column.dataType
                                      ? ` · ${column.dataType}`
                                      : ''}
                                  </span>
                                  {column.description && (
                                    <span className="mt-0.5 block text-[11px] text-text-secondary">
                                      {column.description}
                                    </span>
                                  )}
                                  {column.constraint && (
                                    <span className="mt-0.5 block text-[11px] text-text-secondary">
                                      Ограничение: {column.constraint}
                                    </span>
                                  )}
                                </span>
                              </label>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs text-state-warning">
                            OpenMetadata не вернул подробности полей.
                          </p>
                        )}
                        {selected.columnsTruncated && (
                          <p className="text-xs text-state-warning">
                            Загружено {selected.columns.length} из{' '}
                            {selected.columnCount} полей. Такой снимок нельзя
                            использовать для генерации SQL.
                          </p>
                        )}
                        {selected.tableConstraints.length > 0 && (
                          <div className="rounded border border-border-button px-3 py-2 text-[11px] text-text-secondary">
                            <span className="font-medium text-text-primary">
                              Ключи и связи
                            </span>
                            <ul className="mt-1 space-y-1">
                              {selected.tableConstraints.map(
                                (constraint, constraintIndex) => (
                                  <li
                                    key={`${constraint.constraintType}-${constraintIndex}`}
                                  >
                                    {constraint.constraintType}:{' '}
                                    {constraint.columns.join(', ')}
                                    {constraint.referredColumns.length > 0
                                      ? ` → ${constraint.referredColumns.join(', ')}`
                                      : ''}
                                  </li>
                                ),
                              )}
                            </ul>
                          </div>
                        )}
                      </fieldset>
                    )}

                    {resolution.freshness && (
                      <p className="text-[11px] text-text-secondary">
                        Снимок каталога:{' '}
                        {resolution.freshness.snapshot_at || 'дата неизвестна'}
                        {resolution.freshness.stale ? ' · устарел' : ''}
                      </p>
                    )}
                    {resolution.sources.length > 0 && (
                      <p className="flex flex-wrap gap-x-2 text-[11px] text-text-secondary">
                        <span>Источники:</span>
                        {resolution.sources.map((source, sourceIndex) =>
                          source.url ? (
                            <a
                              key={`${source.label}-${sourceIndex}`}
                              href={source.url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-accent-primary hover:underline"
                            >
                              {source.label}
                            </a>
                          ) : (
                            <span key={`${source.label}-${sourceIndex}`}>
                              {source.label}
                            </span>
                          ),
                        )}
                      </p>
                    )}
                    {resolution.warnings.map((warning) => (
                      <p
                        key={warning}
                        className="text-[11px] text-state-warning"
                      >
                        {warning}
                      </p>
                    ))}
                  </article>
                );
              })}
            </section>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-button pt-4">
          <p className="text-xs text-text-secondary">
            SQL станет доступен только после подтверждения таблиц, загрузки и
            выбора полей, проверки свежести и отпечатка схемы.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={downloadSnapshot}
            disabled={!workspace.resolutions.length}
            data-testid="schema-snapshot-export"
          >
            <Download className="size-4" />
            Скачать снимок
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
