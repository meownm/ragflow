import type { Freshness } from '@/pages/openmetadata/types';
import type { DocumentConstructorStorageScope } from './draft-storage';
import {
  MAX_SCHEMA_CANDIDATES,
  MAX_SCHEMA_COLUMNS_PER_TABLE,
  MAX_SCHEMA_TERM_LENGTH,
  MAX_SCHEMA_TERMS,
  type SchemaColumn,
  type SchemaDecision,
  type SchemaInterpretation,
  type SchemaSource,
  type SchemaTableCandidate,
  type SchemaTableConstraint,
  type SchemaTermResolution,
  type SchemaWorkspaceState,
} from './schema-workspace';

const STORAGE_FORMAT = 'ragflow-sql-schema-workspace';
const STORAGE_KEY_VERSION = 1;
const STORAGE_SCHEMA_VERSION = 2;

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown, maxLength = 10_000) {
  return typeof value === 'string' && value.length <= maxLength ? value : null;
}

function stringArray(value: unknown, maxItems = 100) {
  if (
    !Array.isArray(value) ||
    value.length > maxItems ||
    value.some((item) => typeof item !== 'string' || item.length > 2_000)
  ) {
    return null;
  }
  return [...value];
}

function parseColumn(value: unknown): SchemaColumn | null {
  const source = asRecord(value);
  if (!source) return null;
  const id = stringValue(source.id, 1_000);
  const name = stringValue(source.name, 500);
  const fqn = stringValue(source.fqn, 1_000);
  const dataType = stringValue(source.dataType, 500);
  const description = stringValue(source.description);
  const constraint = stringValue(source.constraint, 500);
  const glossaryTerms = stringArray(source.glossaryTerms);
  if (
    !id ||
    !name ||
    fqn === null ||
    dataType === null ||
    description === null ||
    constraint === null ||
    glossaryTerms === null
  ) {
    return null;
  }
  return { id, name, fqn, dataType, description, constraint, glossaryTerms };
}

function parseConstraint(value: unknown): SchemaTableConstraint | null {
  const source = asRecord(value);
  if (!source) return null;
  const constraintType = stringValue(source.constraintType, 500);
  const columns = stringArray(source.columns);
  const referredColumns = stringArray(source.referredColumns);
  const relationshipType =
    source.relationshipType === undefined
      ? undefined
      : stringValue(source.relationshipType, 500);
  if (
    !constraintType ||
    columns === null ||
    referredColumns === null ||
    relationshipType === null
  ) {
    return null;
  }
  return {
    constraintType,
    columns,
    referredColumns,
    ...(relationshipType ? { relationshipType } : {}),
  };
}

function parseCandidate(value: unknown): SchemaTableCandidate | null {
  const source = asRecord(value);
  if (!source) return null;
  const id = stringValue(source.id, 500);
  const name = stringValue(source.name, 500);
  const displayName =
    source.displayName === null ? null : stringValue(source.displayName, 500);
  const technicalName = stringValue(source.technicalName, 500);
  const fqn = stringValue(source.fqn, 1_000);
  const description = stringValue(source.description);
  const updatedAt =
    source.updatedAt === null ? null : stringValue(source.updatedAt, 200);
  const service = stringValue(source.service, 500);
  const schema = stringValue(source.schema, 500);
  const database = stringValue(source.database, 500);
  const owners = stringArray(source.owners);
  const domains = stringArray(source.domains);
  const tags = stringArray(source.tags);
  const glossaryTerms = stringArray(source.glossaryTerms);
  const url = stringValue(source.url, 2_000);
  const matchedBy = stringArray(source.matchedBy);
  if (
    !id ||
    !name ||
    (displayName === null && source.displayName !== null) ||
    !technicalName ||
    !fqn ||
    description === null ||
    (updatedAt === null && source.updatedAt !== null) ||
    service === null ||
    schema === null ||
    database === null ||
    owners === null ||
    domains === null ||
    tags === null ||
    glossaryTerms === null ||
    url === null ||
    matchedBy === null ||
    !Array.isArray(source.columns) ||
    source.columns.length > MAX_SCHEMA_COLUMNS_PER_TABLE ||
    !Array.isArray(source.tableConstraints) ||
    source.tableConstraints.length > 100
  ) {
    return null;
  }
  const columns = source.columns.map(parseColumn);
  const tableConstraints = source.tableConstraints.map(parseConstraint);
  if (
    columns.some((column) => column === null) ||
    tableConstraints.some((constraint) => constraint === null)
  ) {
    return null;
  }
  const version =
    source.version === null
      ? null
      : typeof source.version === 'number' && Number.isFinite(source.version)
        ? source.version
        : undefined;
  if (version === undefined) return null;
  const columnCount = source.columnCount;
  const columnsTruncated = source.columnsTruncated;
  const rawSchemaStatus = source.schemaStatus;
  const schemaStatus =
    rawSchemaStatus === undefined
      ? 'summary'
      : rawSchemaStatus === 'loading'
        ? 'summary'
        : ['summary', 'loaded', 'error'].includes(String(rawSchemaStatus))
          ? String(rawSchemaStatus)
          : null;
  const schemaFingerprint =
    source.schemaFingerprint === undefined || source.schemaFingerprint === null
      ? null
      : stringValue(source.schemaFingerprint, 200);
  const schemaError =
    source.schemaError === undefined || source.schemaError === null
      ? null
      : stringValue(source.schemaError);
  if (
    typeof columnCount !== 'number' ||
    !Number.isInteger(columnCount) ||
    columnCount < columns.length ||
    typeof columnsTruncated !== 'boolean' ||
    !schemaStatus ||
    (schemaFingerprint === null && source.schemaFingerprint != null) ||
    (schemaError === null && source.schemaError != null) ||
    columnsTruncated !==
      (schemaStatus === 'loaded' && columnCount > columns.length)
  ) {
    return null;
  }
  return {
    id,
    name,
    displayName,
    technicalName,
    fqn,
    description,
    version,
    updatedAt,
    service,
    schema,
    database,
    owners,
    domains,
    tags,
    glossaryTerms,
    url,
    matchedBy,
    columns: columns as SchemaColumn[],
    columnCount,
    columnsTruncated,
    tableConstraints: tableConstraints as SchemaTableConstraint[],
    schemaStatus: schemaStatus as SchemaTableCandidate['schemaStatus'],
    schemaFingerprint,
    schemaError,
  };
}

function parseFreshness(value: unknown): Freshness | null {
  if (value === null) return null;
  const source = asRecord(value);
  if (
    !source ||
    typeof source.stale !== 'boolean' ||
    typeof source.threshold_hours !== 'number' ||
    !Number.isFinite(source.threshold_hours)
  ) {
    return null;
  }
  const snapshotAt = source.snapshot_at;
  const latestEntityUpdatedAt = source.latest_entity_updated_at;
  const checkedAt = source.checked_at;
  const catalogCheckedAt = source.catalog_checked_at;
  const ageHours = source.age_hours;
  if (
    (snapshotAt !== undefined &&
      snapshotAt !== null &&
      stringValue(snapshotAt, 200) === null) ||
    (latestEntityUpdatedAt !== undefined &&
      latestEntityUpdatedAt !== null &&
      stringValue(latestEntityUpdatedAt, 200) === null) ||
    (checkedAt !== undefined && stringValue(checkedAt, 200) === null) ||
    (catalogCheckedAt !== undefined &&
      stringValue(catalogCheckedAt, 200) === null) ||
    (ageHours !== undefined &&
      ageHours !== null &&
      (typeof ageHours !== 'number' || !Number.isFinite(ageHours)))
  ) {
    return null;
  }
  return {
    ...(snapshotAt !== undefined
      ? { snapshot_at: snapshotAt as string | null }
      : {}),
    ...(latestEntityUpdatedAt !== undefined
      ? { latest_entity_updated_at: latestEntityUpdatedAt as string | null }
      : {}),
    ...(checkedAt !== undefined ? { checked_at: checkedAt as string } : {}),
    ...(catalogCheckedAt !== undefined
      ? { catalog_checked_at: catalogCheckedAt as string }
      : {}),
    ...(ageHours !== undefined ? { age_hours: ageHours as number | null } : {}),
    stale: source.stale,
    threshold_hours: source.threshold_hours,
  };
}

function parseSource(value: unknown): SchemaSource | null {
  const source = asRecord(value);
  if (!source) return null;
  const label = stringValue(source.label, 500);
  const url =
    source.url === undefined ? undefined : stringValue(source.url, 2_000);
  const datasetId =
    source.datasetId === undefined
      ? undefined
      : stringValue(source.datasetId, 500);
  if (!label || url === null || datasetId === null) return null;
  return {
    label,
    ...(url ? { url } : {}),
    ...(datasetId ? { datasetId } : {}),
  };
}

function parseInterpretation(
  value: unknown,
  candidates: SchemaTableCandidate[],
): SchemaInterpretation | null {
  if (value === undefined || value === null) return null;
  const source = asRecord(value);
  if (!source) return null;
  const normalizedTerm = stringValue(
    source.normalizedTerm,
    MAX_SCHEMA_TERM_LENGTH,
  );
  const reason = stringValue(source.reason, 2_000);
  const promptVersion = stringValue(source.promptVersion, 500);
  const promptName =
    source.promptName === undefined || source.promptName === null
      ? null
      : stringValue(source.promptName, 500);
  const promptHash =
    source.promptHash === undefined || source.promptHash === null
      ? null
      : stringValue(source.promptHash, 200);
  const clarificationQuestion =
    source.clarificationQuestion === null
      ? null
      : stringValue(source.clarificationQuestion, 1_000);
  const llmWarning =
    source.llmWarning === null ? null : stringValue(source.llmWarning, 2_000);
  const recommendedEntityId =
    source.recommendedEntityId === null
      ? null
      : stringValue(source.recommendedEntityId, 500);
  const recommendedColumnIds =
    source.recommendedColumnIds === undefined
      ? []
      : stringArray(source.recommendedColumnIds, 60);
  const confidence = source.confidence;
  if (
    !normalizedTerm ||
    !reason ||
    (promptVersion === null && source.promptVersion != null) ||
    (promptName === null && source.promptName != null) ||
    (promptHash === null && source.promptHash != null) ||
    recommendedColumnIds === null ||
    (clarificationQuestion === null && source.clarificationQuestion !== null) ||
    (llmWarning === null && source.llmWarning !== null) ||
    (recommendedEntityId === null && source.recommendedEntityId !== null) ||
    !['entity', 'field', 'unknown'].includes(String(source.kind)) ||
    !['APPLIED', 'FALLBACK', 'SKIPPED'].includes(String(source.llmStatus)) ||
    (confidence !== null &&
      (typeof confidence !== 'number' ||
        !Number.isFinite(confidence) ||
        confidence < 0 ||
        confidence > 1)) ||
    (recommendedEntityId !== null &&
      !candidates.some((candidate) => candidate.id === recommendedEntityId)) ||
    recommendedColumnIds.some(
      (columnId) =>
        !candidates.some((candidate) =>
          candidate.columns.some((column) => column.id === columnId),
        ),
    )
  ) {
    return null;
  }
  return {
    kind: source.kind as SchemaInterpretation['kind'],
    normalizedTerm,
    recommendedEntityId,
    recommendedColumnIds,
    confidence: confidence as number | null,
    reason,
    clarificationQuestion,
    llmStatus: source.llmStatus as SchemaInterpretation['llmStatus'],
    promptName,
    promptVersion,
    promptHash,
    llmWarning,
  };
}

function parseResolution(value: unknown): SchemaTermResolution | null {
  const source = asRecord(value);
  if (!source) return null;
  const term = stringValue(source.term, MAX_SCHEMA_TERM_LENGTH);
  if (
    !term ||
    !Array.isArray(source.candidates) ||
    source.candidates.length > MAX_SCHEMA_CANDIDATES ||
    !Array.isArray(source.sources) ||
    source.sources.length > 100 ||
    !Array.isArray(source.selectedColumnIds)
  ) {
    return null;
  }
  const candidates = source.candidates.map(parseCandidate);
  const sources = source.sources.map(parseSource);
  const selectedColumnIds = stringArray(source.selectedColumnIds, 500);
  const warnings = stringArray(source.warnings);
  const freshness = parseFreshness(source.freshness);
  const retrieval = stringValue(source.retrieval, 500);
  if (
    candidates.some((candidate) => candidate === null) ||
    sources.some((item) => item === null) ||
    selectedColumnIds === null ||
    warnings === null ||
    retrieval === null ||
    (freshness === null && source.freshness !== null)
  ) {
    return null;
  }
  const parsedCandidates = candidates as SchemaTableCandidate[];
  const interpretation = parseInterpretation(
    source.interpretation,
    parsedCandidates,
  );
  if (
    source.interpretation !== undefined &&
    source.interpretation !== null &&
    !interpretation
  ) {
    return null;
  }
  const selectedEntityId =
    source.selectedEntityId === null
      ? null
      : stringValue(source.selectedEntityId, 500);
  const selected = parsedCandidates.find(
    (candidate) => candidate.id === selectedEntityId,
  );
  if (selectedEntityId !== null && (!selectedEntityId || !selected)) {
    return null;
  }
  const selectedIds = new Set(selected?.columns.map((column) => column.id));
  if (selectedColumnIds.some((id) => !selectedIds.has(id))) return null;
  const decision = source.decision as SchemaDecision | null;
  if (
    decision !== null &&
    !['automatic_exact', 'automatic_single', 'user'].includes(decision)
  ) {
    return null;
  }
  const status = selected
    ? 'confirmed'
    : parsedCandidates.length
      ? 'needs_clarification'
      : source.status === 'error'
        ? 'error'
        : 'not_found';
  const error =
    source.error === undefined ? undefined : stringValue(source.error);
  if (error === null) return null;
  return {
    term,
    status,
    decision: selected ? decision : null,
    candidates: parsedCandidates,
    selectedEntityId,
    selectedColumnIds,
    freshness,
    retrieval,
    sources: sources as SchemaSource[],
    warnings,
    interpretation,
    ...(error ? { error } : {}),
  };
}

export function emptySchemaWorkspace(): SchemaWorkspaceState {
  return { input: '', requirements: '', resolutions: [] };
}

export function createSchemaWorkspaceStorageKey(
  scope: DocumentConstructorStorageScope,
) {
  const tenantId = scope.tenantId.trim();
  const userId = scope.userId.trim();
  if (!tenantId || !userId) {
    throw new Error('Для снимка схемы нужны userId и tenantId.');
  }
  return `${STORAGE_FORMAT}.v${STORAGE_KEY_VERSION}:${encodeURIComponent(tenantId)}:${encodeURIComponent(userId)}`;
}

export function loadSchemaWorkspace(
  storage: StorageLike | undefined,
  key: string,
  scope: DocumentConstructorStorageScope,
): SchemaWorkspaceState {
  if (!storage) return emptySchemaWorkspace();
  try {
    const envelope = asRecord(JSON.parse(storage.getItem(key) || 'null'));
    const owner = asRecord(envelope?.owner);
    const workspace = asRecord(envelope?.workspace);
    if (
      envelope?.format !== STORAGE_FORMAT ||
      ![1, STORAGE_SCHEMA_VERSION].includes(Number(envelope.schemaVersion)) ||
      owner?.userId !== scope.userId ||
      owner.tenantId !== scope.tenantId ||
      !workspace ||
      typeof workspace.input !== 'string' ||
      workspace.input.length > 5_000 ||
      (workspace.requirements !== undefined &&
        (typeof workspace.requirements !== 'string' ||
          workspace.requirements.length > 20_000)) ||
      !Array.isArray(workspace.resolutions) ||
      workspace.resolutions.length > MAX_SCHEMA_TERMS
    ) {
      return emptySchemaWorkspace();
    }
    const resolutions = workspace.resolutions.map(parseResolution);
    if (resolutions.some((resolution) => resolution === null)) {
      return emptySchemaWorkspace();
    }
    return {
      input: workspace.input,
      requirements:
        typeof workspace.requirements === 'string'
          ? workspace.requirements
          : '',
      resolutions: resolutions as SchemaTermResolution[],
    };
  } catch {
    return emptySchemaWorkspace();
  }
}

export function saveSchemaWorkspace(
  storage: StorageLike,
  key: string,
  scope: DocumentConstructorStorageScope,
  workspace: SchemaWorkspaceState,
) {
  storage.setItem(
    key,
    JSON.stringify({
      format: STORAGE_FORMAT,
      schemaVersion: STORAGE_SCHEMA_VERSION,
      owner: scope,
      workspace,
    }),
  );
}
