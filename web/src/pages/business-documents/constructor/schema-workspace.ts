import type {
  CatalogAnswer,
  CatalogEntity,
  Freshness,
} from '@/pages/openmetadata/types';

export const MAX_SCHEMA_TERMS = 8;
export const MAX_SCHEMA_CANDIDATES = 5;
export const MAX_SCHEMA_COLUMNS_PER_TABLE = 500;
export const MAX_SCHEMA_TERM_LENGTH = 200;
export const SCHEMA_SNAPSHOT_FORMAT = 'ragflow-sql-schema-snapshot';
export const SCHEMA_SNAPSHOT_VERSION = '1';

export type SchemaResolutionStatus =
  | 'confirmed'
  | 'needs_clarification'
  | 'not_found'
  | 'error';

export type SchemaDecision = 'automatic_exact' | 'automatic_single' | 'user';

export type SchemaInterpreterStatus = 'APPLIED' | 'FALLBACK' | 'SKIPPED';

export type SchemaTermKind = 'entity' | 'field' | 'unknown';

export interface SchemaInterpretation {
  kind: SchemaTermKind;
  normalizedTerm: string;
  recommendedEntityId: string | null;
  recommendedColumnIds: string[];
  confidence: number | null;
  reason: string;
  clarificationQuestion: string | null;
  llmStatus: SchemaInterpreterStatus;
  promptName: string | null;
  promptVersion: string | null;
  promptHash: string | null;
  llmWarning: string | null;
}

export interface SchemaColumn {
  id: string;
  name: string;
  fqn: string;
  dataType: string;
  description: string;
  constraint: string;
  glossaryTerms: string[];
}

export interface SchemaTableConstraint {
  constraintType: string;
  columns: string[];
  referredColumns: string[];
  relationshipType?: string;
}

export interface SchemaTableCandidate {
  id: string;
  name: string;
  displayName: string | null;
  technicalName: string;
  fqn: string;
  description: string;
  version: number | null;
  updatedAt: string | null;
  service: string;
  schema: string;
  database: string;
  owners: string[];
  domains: string[];
  tags: string[];
  glossaryTerms: string[];
  url: string;
  matchedBy: string[];
  columns: SchemaColumn[];
  columnCount: number;
  columnsTruncated: boolean;
  tableConstraints: SchemaTableConstraint[];
  schemaStatus: 'summary' | 'loading' | 'loaded' | 'error';
  schemaFingerprint: string | null;
  schemaError: string | null;
}

export interface SchemaSource {
  label: string;
  url?: string;
  datasetId?: string;
}

export interface SchemaTermResolution {
  term: string;
  status: SchemaResolutionStatus;
  decision: SchemaDecision | null;
  candidates: SchemaTableCandidate[];
  selectedEntityId: string | null;
  selectedColumnIds: string[];
  freshness: Freshness | null;
  retrieval: string;
  sources: SchemaSource[];
  warnings: string[];
  interpretation: SchemaInterpretation | null;
  error?: string;
}

export interface SchemaWorkspaceState {
  input: string;
  requirements: string;
  resolutions: SchemaTermResolution[];
}

export type SchemaSnapshotStatus = 'READY' | 'NEEDS_CLARIFICATION' | 'STALE';

function uniqueStrings(values: Array<string | null | undefined>) {
  const result: string[] = [];
  const seen = new Set<string>();
  values.forEach((value) => {
    const normalized = String(value ?? '').trim();
    const key = normalized.toLocaleLowerCase();
    if (normalized && !seen.has(key)) {
      seen.add(key);
      result.push(normalized);
    }
  });
  return result;
}

function columnId(tableFqn: string, name: string, fqn?: string) {
  return String(fqn || '').trim() || `${tableFqn}.${name}`;
}

function normalizeConstraint(
  value: NonNullable<CatalogEntity['table_constraints']>[number],
): SchemaTableConstraint | null {
  const constraintType = String(value.constraint_type || '').trim();
  const columns = uniqueStrings(value.columns || []);
  if (!constraintType || !columns.length) return null;
  const relationshipType = String(value.relationship_type || '').trim();
  return {
    constraintType,
    columns,
    referredColumns: uniqueStrings(value.referred_columns || []),
    ...(relationshipType ? { relationshipType } : {}),
  };
}

export function toSchemaTableCandidate(
  entity: CatalogEntity,
): SchemaTableCandidate | null {
  const id = String(entity.id || '').trim();
  const fqn = String(entity.fqn || '').trim();
  const technicalName = String(entity.technical_name || '').trim();
  if (!id || !fqn || !technicalName) return null;

  const columns: SchemaColumn[] = [];
  const seenColumns = new Set<string>();
  (entity.column_details || []).forEach((column) => {
    if (columns.length >= MAX_SCHEMA_COLUMNS_PER_TABLE) return;
    const name = String(column.name || '').trim();
    if (!name) return;
    const id = columnId(fqn, name, column.fqn);
    if (seenColumns.has(id)) return;
    seenColumns.add(id);
    columns.push({
      id,
      name,
      fqn: String(column.fqn || '').trim(),
      dataType: String(column.data_type || '').trim(),
      description: String(column.description || '').trim(),
      constraint: String(column.constraint || '').trim(),
      glossaryTerms: uniqueStrings(column.glossary_terms || []),
    });
  });

  const columnCount = Math.max(
    Number.isInteger(entity.column_count) && entity.column_count >= 0
      ? entity.column_count
      : 0,
    (entity.column_details || []).length,
  );

  return {
    id,
    name: String(entity.name || technicalName).trim(),
    displayName: entity.display_name
      ? String(entity.display_name).trim()
      : null,
    technicalName,
    fqn,
    description: String(entity.description || '').trim(),
    version:
      typeof entity.version === 'number' && Number.isFinite(entity.version)
        ? entity.version
        : null,
    updatedAt: entity.updated_at ? String(entity.updated_at).trim() : null,
    service: String(entity.service || '').trim(),
    schema: String(entity.schema || '').trim(),
    database: String(entity.database || '').trim(),
    owners: uniqueStrings(entity.owners || []),
    domains: uniqueStrings(entity.domains || []),
    tags: uniqueStrings(entity.tags || []),
    glossaryTerms: uniqueStrings(entity.glossary_terms || []),
    url: String(entity.url || '').trim(),
    matchedBy: uniqueStrings(entity.matched_by || []),
    columns,
    columnCount,
    columnsTruncated:
      Boolean(entity.schema_loaded) && columnCount > columns.length,
    tableConstraints: (entity.table_constraints || [])
      .map(normalizeConstraint)
      .filter((value): value is SchemaTableConstraint => value !== null),
    schemaStatus: entity.schema_loaded ? 'loaded' : 'summary',
    schemaFingerprint:
      entity.schema_loaded &&
      typeof entity.schema_fingerprint === 'string' &&
      entity.schema_fingerprint.startsWith('sha256:')
        ? entity.schema_fingerprint
        : null,
    schemaError: null,
  };
}

function normalizedExactValue(value: string) {
  return value
    .trim()
    .replace(/^["'`«“]+|["'`»”]+$/g, '')
    .toLocaleLowerCase();
}

function exactCandidates(term: string, candidates: SchemaTableCandidate[]) {
  const expected = normalizedExactValue(term);
  return candidates.filter((candidate) =>
    [
      candidate.fqn,
      candidate.technicalName,
      candidate.name,
      candidate.displayName,
    ].some((value) =>
      value ? normalizedExactValue(value) === expected : false,
    ),
  );
}

function normalizeSources(answer: CatalogAnswer) {
  return (answer.sources || [])
    .map((source) => {
      const label = String(source.label || '').trim();
      if (!label) return null;
      const url = String(source.url || '').trim();
      const datasetId = String(source.dataset_id || '').trim();
      return {
        label,
        ...(url ? { url } : {}),
        ...(datasetId ? { datasetId } : {}),
      };
    })
    .filter((value): value is SchemaSource => value !== null);
}

export function createSchemaResolution(
  term: string,
  answer: CatalogAnswer,
  previous?: SchemaTermResolution,
  interpretation: SchemaInterpretation | null = null,
): SchemaTermResolution {
  const candidates: SchemaTableCandidate[] = [];
  const seen = new Set<string>();
  (answer.entities || []).forEach((entity) => {
    const candidate = toSchemaTableCandidate(entity);
    if (!candidate || seen.has(candidate.id)) return;
    seen.add(candidate.id);
    if (candidates.length < MAX_SCHEMA_CANDIDATES) candidates.push(candidate);
  });

  const previousCandidate = previous?.selectedEntityId
    ? candidates.find((candidate) => candidate.id === previous.selectedEntityId)
    : undefined;
  const exact = exactCandidates(term, candidates);
  let selectedEntityId: string | null = null;
  let decision: SchemaDecision | null = null;
  if (previousCandidate && previous?.decision === 'user') {
    selectedEntityId = previousCandidate.id;
    decision = 'user';
  } else if (!answer.needs_clarification && exact.length === 1) {
    selectedEntityId = exact[0].id;
    decision = 'automatic_exact';
  } else if (!answer.needs_clarification && candidates.length === 1) {
    selectedEntityId = candidates[0].id;
    decision = 'automatic_single';
  }

  const selectedColumnIds =
    selectedEntityId && previousCandidate?.id === selectedEntityId
      ? previous!.selectedColumnIds
      : [];
  const status: SchemaResolutionStatus = selectedEntityId
    ? 'confirmed'
    : candidates.length
      ? 'needs_clarification'
      : 'not_found';
  const safeInterpretation = interpretation
    ? {
        ...interpretation,
        recommendedEntityId:
          interpretation.recommendedEntityId &&
          candidates.some(
            (candidate) => candidate.id === interpretation.recommendedEntityId,
          )
            ? interpretation.recommendedEntityId
            : null,
        recommendedColumnIds: interpretation.recommendedColumnIds.filter(
          (columnId) =>
            candidates.some((candidate) =>
              candidate.columns.some((column) => column.id === columnId),
            ),
        ),
      }
    : null;

  return {
    term,
    status,
    decision,
    candidates,
    selectedEntityId,
    selectedColumnIds,
    freshness: answer.freshness || null,
    retrieval: String(answer.retrieval || '').trim(),
    sources: normalizeSources(answer),
    warnings: uniqueStrings(answer.warnings || []),
    interpretation: safeInterpretation,
  };
}

export function createSchemaResolutionError(
  term: string,
  error: unknown,
): SchemaTermResolution {
  return {
    term,
    status: 'error',
    decision: null,
    candidates: [],
    selectedEntityId: null,
    selectedColumnIds: [],
    freshness: null,
    retrieval: '',
    sources: [],
    warnings: [],
    interpretation: null,
    error:
      error instanceof Error
        ? error.message
        : 'Не удалось получить данные каталога.',
  };
}

export function chooseSchemaCandidate(
  resolution: SchemaTermResolution,
  entityId: string,
): SchemaTermResolution {
  if (!resolution.candidates.some((candidate) => candidate.id === entityId)) {
    return resolution;
  }
  const selectionChanged = resolution.selectedEntityId !== entityId;
  return {
    ...resolution,
    status: 'confirmed',
    decision: 'user',
    selectedEntityId: entityId,
    selectedColumnIds: selectionChanged ? [] : resolution.selectedColumnIds,
  };
}

export function markSchemaCandidateLoading(
  resolution: SchemaTermResolution,
  entityId: string,
): SchemaTermResolution {
  if (resolution.selectedEntityId !== entityId) return resolution;
  return {
    ...resolution,
    candidates: resolution.candidates.map((candidate) =>
      candidate.id === entityId
        ? { ...candidate, schemaStatus: 'loading', schemaError: null }
        : candidate,
    ),
  };
}

export function applySchemaCandidateDetails(
  resolution: SchemaTermResolution,
  entity: CatalogEntity,
  evidence: {
    freshness: Freshness | null;
    retrieval: string;
    sources: Array<{ label: string; url?: string; dataset_id?: string }>;
    warnings: string[];
  },
): SchemaTermResolution {
  if (resolution.selectedEntityId !== entity.id) return resolution;
  const detailed = toSchemaTableCandidate(entity);
  if (!detailed || detailed.id !== resolution.selectedEntityId) {
    return markSchemaCandidateError(
      resolution,
      entity.id,
      'Сервер вернул некорректную схему выбранной таблицы.',
    );
  }
  const selectedColumnIds = resolution.selectedColumnIds.filter((id) =>
    detailed.columns.some((column) => column.id === id),
  );
  return {
    ...resolution,
    status: 'confirmed',
    candidates: resolution.candidates.map((candidate) =>
      candidate.id === detailed.id ? detailed : candidate,
    ),
    selectedColumnIds,
    freshness: evidence.freshness,
    retrieval: String(evidence.retrieval || '').trim(),
    sources: normalizeSources({ sources: evidence.sources } as CatalogAnswer),
    warnings: uniqueStrings(evidence.warnings),
  };
}

export function markSchemaCandidateError(
  resolution: SchemaTermResolution,
  entityId: string,
  error: string,
): SchemaTermResolution {
  if (resolution.selectedEntityId !== entityId) return resolution;
  return {
    ...resolution,
    candidates: resolution.candidates.map((candidate) =>
      candidate.id === entityId
        ? { ...candidate, schemaStatus: 'error', schemaError: error }
        : candidate,
    ),
  };
}

export function toggleSchemaColumn(
  resolution: SchemaTermResolution,
  columnId: string,
): SchemaTermResolution {
  const selectedTable = resolution.candidates.find(
    (candidate) => candidate.id === resolution.selectedEntityId,
  );
  if (!selectedTable?.columns.some((column) => column.id === columnId)) {
    return resolution;
  }
  const selected = new Set(resolution.selectedColumnIds);
  if (selected.has(columnId)) selected.delete(columnId);
  else selected.add(columnId);
  return { ...resolution, selectedColumnIds: [...selected] };
}

export function parseSchemaTerms(input: string) {
  const terms = uniqueStrings(
    input
      .split(/\r?\n/)
      .map((value) => value.replace(/^\s*[-*•]\s+/, '').trim()),
  );
  if (!terms.length) {
    return { terms, error: 'Укажите хотя бы одну бизнес-сущность.' };
  }
  if (terms.length > MAX_SCHEMA_TERMS) {
    return {
      terms,
      error: `За один шаг можно сопоставить не более ${MAX_SCHEMA_TERMS} сущностей.`,
    };
  }
  const tooLong = terms.find((term) => term.length > MAX_SCHEMA_TERM_LENGTH);
  if (tooLong) {
    return {
      terms,
      error: `Название сущности не должно превышать ${MAX_SCHEMA_TERM_LENGTH} символов.`,
    };
  }
  return { terms, error: null };
}

function latest(values: Array<string | null | undefined>) {
  return values
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);
}

function candidateSummary(candidate: SchemaTableCandidate) {
  return {
    id: candidate.id,
    fqn: candidate.fqn,
    name: candidate.name,
    display_name: candidate.displayName,
    description: candidate.description,
    version: candidate.version,
    updated_at: candidate.updatedAt,
    url: candidate.url,
    matched_by: candidate.matchedBy,
    column_count: candidate.columnCount,
    loaded_column_count: candidate.columns.length,
    columns_truncated: candidate.columnsTruncated,
    schema_status: candidate.schemaStatus.toLocaleUpperCase(),
    schema_fingerprint: candidate.schemaFingerprint,
  };
}

function selectedTableSnapshot(
  resolution: SchemaTermResolution,
  candidate: SchemaTableCandidate,
) {
  const selected = new Set(resolution.selectedColumnIds);
  return {
    id: candidate.id,
    fqn: candidate.fqn,
    name: candidate.name,
    display_name: candidate.displayName,
    technical_name: candidate.technicalName,
    description: candidate.description,
    version: candidate.version,
    updated_at: candidate.updatedAt,
    service: candidate.service,
    database: candidate.database,
    schema: candidate.schema,
    owners: candidate.owners,
    domains: candidate.domains,
    tags: candidate.tags,
    glossary_terms: candidate.glossaryTerms,
    url: candidate.url,
    matched_by: candidate.matchedBy,
    column_count: candidate.columnCount,
    loaded_column_count: candidate.columns.length,
    columns_truncated: candidate.columnsTruncated,
    schema_loaded: candidate.schemaStatus === 'loaded',
    schema_fingerprint: candidate.schemaFingerprint,
    columns: candidate.columns.map((column) => ({
      id: column.id,
      name: column.name,
      fqn: column.fqn,
      data_type: column.dataType,
      description: column.description,
      constraint: column.constraint,
      glossary_terms: column.glossaryTerms,
      selected: selected.has(column.id),
    })),
    table_constraints: candidate.tableConstraints.map((constraint) => ({
      constraint_type: constraint.constraintType,
      columns: constraint.columns,
      referred_columns: constraint.referredColumns,
      ...(constraint.relationshipType
        ? { relationship_type: constraint.relationshipType }
        : {}),
    })),
  };
}

export function buildSchemaSnapshot(
  resolutions: SchemaTermResolution[],
  originalRequirements = '',
) {
  const stale = resolutions.some((resolution) => {
    const freshness = resolution.freshness;
    return (
      !freshness ||
      freshness.stale !== false ||
      !freshness.snapshot_at ||
      !freshness.checked_at
    );
  });
  const unresolved =
    !originalRequirements.trim() ||
    !resolutions.length ||
    resolutions.some((resolution) => {
      const selected = resolution.candidates.find(
        (candidate) => candidate.id === resolution.selectedEntityId,
      );
      return (
        resolution.status !== 'confirmed' ||
        !resolution.selectedEntityId ||
        !selected ||
        selected.schemaStatus !== 'loaded' ||
        selected.columnsTruncated ||
        !selected.schemaFingerprint ||
        selected.version === null ||
        resolution.selectedColumnIds.length === 0
      );
    });
  const status: SchemaSnapshotStatus = unresolved
    ? 'NEEDS_CLARIFICATION'
    : stale
      ? 'STALE'
      : 'READY';
  const sources = new Map<string, SchemaSource>();
  resolutions.forEach((resolution) =>
    resolution.sources.forEach((source) => {
      const key = `${source.label}|${source.url || ''}|${source.datasetId || ''}`;
      sources.set(key, source);
    }),
  );

  return {
    format: SCHEMA_SNAPSHOT_FORMAT,
    schema_version: SCHEMA_SNAPSHOT_VERSION,
    status,
    original_requirements: originalRequirements.trim(),
    source: {
      type: 'openmetadata',
      catalog_snapshot_at:
        latest(
          resolutions.map((resolution) => resolution.freshness?.snapshot_at),
        ) || null,
      checked_at:
        latest(
          resolutions.map((resolution) => resolution.freshness?.checked_at),
        ) || null,
      stale,
      retrieval: uniqueStrings(
        resolutions.map((resolution) => resolution.retrieval),
      ),
      references: [...sources.values()].map((source) => ({
        label: source.label,
        ...(source.url ? { url: source.url } : {}),
        ...(source.datasetId ? { dataset_id: source.datasetId } : {}),
      })),
    },
    requirements: resolutions.map((resolution) => {
      const selected = resolution.candidates.find(
        (candidate) => candidate.id === resolution.selectedEntityId,
      );
      const state =
        resolution.status === 'error'
          ? 'ERROR'
          : resolution.status === 'not_found'
            ? 'NOT_FOUND'
            : !selected
              ? 'NEEDS_CLARIFICATION'
              : selected.schemaStatus === 'error'
                ? 'SCHEMA_ERROR'
                : selected.schemaStatus !== 'loaded'
                  ? 'TABLE_SELECTED'
                  : !resolution.selectedColumnIds.length
                    ? 'FIELDS_REQUIRED'
                    : !selected.schemaFingerprint || selected.version === null
                      ? 'SCHEMA_UNVERIFIED'
                      : resolution.freshness?.stale !== false ||
                          !resolution.freshness.snapshot_at ||
                          !resolution.freshness.checked_at
                        ? 'STALE'
                        : selected.columnsTruncated
                          ? 'SCHEMA_TRUNCATED'
                          : 'CONFIRMED';
      return {
        term: resolution.term,
        state,
        decision: resolution.decision
          ? resolution.decision.toLocaleUpperCase()
          : null,
        interpretation: resolution.interpretation
          ? {
              kind: resolution.interpretation.kind,
              normalized_term: resolution.interpretation.normalizedTerm,
              recommended_entity_id:
                resolution.interpretation.recommendedEntityId,
              recommended_column_ids:
                resolution.interpretation.recommendedColumnIds,
              confidence: resolution.interpretation.confidence,
              reason: resolution.interpretation.reason,
              clarification_question:
                resolution.interpretation.clarificationQuestion,
              llm_status: resolution.interpretation.llmStatus,
              prompt: resolution.interpretation.promptName
                ? {
                    name: resolution.interpretation.promptName,
                    version: resolution.interpretation.promptVersion,
                    content_hash: resolution.interpretation.promptHash,
                  }
                : null,
              llm_warning: resolution.interpretation.llmWarning,
            }
          : null,
        candidates: resolution.candidates.map(candidateSummary),
        ...(selected
          ? { selected_table: selectedTableSnapshot(resolution, selected) }
          : {}),
        ...(resolution.error ? { error: resolution.error } : {}),
      };
    }),
    warnings: uniqueStrings(
      resolutions.flatMap((resolution) => resolution.warnings),
    ),
  };
}
