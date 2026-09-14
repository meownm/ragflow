import {
  BLOCK_TYPE_OPTIONS,
  MAX_HEADING_BASE_LEVEL,
  MIN_HEADING_BASE_LEVEL,
  createSection,
  normalizeOutlineDepths,
  type ConstructorSection,
  type DocumentBlockType,
  type DocumentTemplateDraft,
} from './model';
import { createSqlQueryTemplate } from './sql-query-template';

const STORAGE_KEY_PREFIX = 'ragflow.experimental-document-constructor.v2';
const STORAGE_FORMAT = 'ragflow-document-constructor-storage';
const STORAGE_SCHEMA_VERSION = 1;

export interface DocumentConstructorStorageScope {
  userId: string;
  tenantId: string;
}

type DraftStorage = Pick<Storage, 'getItem' | 'setItem'>;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback;
}

function positiveIntegerOrNull(value: unknown) {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
    ? value
    : null;
}

function normalizeStoredSection(
  value: unknown,
  index: number,
): ConstructorSection | null {
  const source = asRecord(value);
  if (!source) return null;
  const knownTypes = new Set<string>(
    BLOCK_TYPE_OPTIONS.map((option) => option.value),
  );
  const allowedBlocks = Array.isArray(source.allowedBlocks)
    ? source.allowedBlocks.filter(
        (item): item is DocumentBlockType =>
          typeof item === 'string' && knownTypes.has(item),
      )
    : [];
  const depthValue =
    typeof source.depth === 'number' ? source.depth : Number(source.depth);

  return {
    ...createSection(depthValue, `Раздел ${index + 1}`),
    customId: stringValue(source.customId),
    title: stringValue(source.title).trim() || `Раздел ${index + 1}`,
    purpose: stringValue(source.purpose),
    required: source.required !== false,
    allowedBlocks: allowedBlocks.length ? allowedBlocks : ['paragraph'],
    requirements: Array.isArray(source.requirements)
      ? source.requirements.filter(
          (item): item is string => typeof item === 'string',
        )
      : [],
    minWords: positiveIntegerOrNull(source.minWords),
    maxWords: positiveIntegerOrNull(source.maxWords),
    generationInstructions: stringValue(source.generationInstructions),
  };
}

function normalizeStoredDraft(value: unknown) {
  const source = asRecord(value);
  if (
    source?.schemaVersion !== 1 ||
    !Array.isArray(source.sections) ||
    !source.sections.length
  ) {
    return null;
  }

  const fallback = createSqlQueryTemplate();
  const sections = source.sections
    .map(normalizeStoredSection)
    .filter((section): section is ConstructorSection => section !== null);
  if (!sections.length) return null;
  const requestedHeading =
    typeof source.headingBaseLevel === 'number' &&
    Number.isFinite(source.headingBaseLevel)
      ? Math.floor(source.headingBaseLevel)
      : fallback.headingBaseLevel;

  return {
    schemaVersion: 1,
    name: stringValue(source.name).trim() || fallback.name,
    documentType:
      stringValue(source.documentType).trim() || fallback.documentType,
    version: stringValue(source.version).trim() || fallback.version,
    language: source.language === 'en' ? 'en' : 'ru',
    description: stringValue(source.description),
    headingBaseLevel: Math.max(
      MIN_HEADING_BASE_LEVEL,
      Math.min(MAX_HEADING_BASE_LEVEL, requestedHeading),
    ),
    preserveSectionNumbers: source.preserveSectionNumbers !== false,
    protocolInBody: source.protocolInBody === true,
    sections: normalizeOutlineDepths(sections),
  } satisfies DocumentTemplateDraft;
}

function ownerMatches(value: unknown, scope: DocumentConstructorStorageScope) {
  const owner = asRecord(value);
  return owner?.userId === scope.userId && owner?.tenantId === scope.tenantId;
}

export function createDocumentConstructorStorageKey(
  scope: DocumentConstructorStorageScope,
) {
  const userId = scope.userId.trim();
  const tenantId = scope.tenantId.trim();
  if (!userId || !tenantId) {
    throw new Error('Для локального черновика нужны userId и tenantId.');
  }
  return `${STORAGE_KEY_PREFIX}:${encodeURIComponent(tenantId)}:${encodeURIComponent(userId)}`;
}

export function loadStoredTemplate(
  storage: DraftStorage | undefined,
  key: string,
  scope: DocumentConstructorStorageScope,
) {
  if (!storage) return createSqlQueryTemplate();
  try {
    const raw = storage.getItem(key);
    if (!raw) return createSqlQueryTemplate();
    const envelope = asRecord(JSON.parse(raw));
    if (
      envelope?.format !== STORAGE_FORMAT ||
      envelope.schemaVersion !== STORAGE_SCHEMA_VERSION ||
      !ownerMatches(envelope.owner, scope)
    ) {
      return createSqlQueryTemplate();
    }
    return normalizeStoredDraft(envelope.draft) ?? createSqlQueryTemplate();
  } catch {
    return createSqlQueryTemplate();
  }
}

export function saveStoredTemplate(
  storage: DraftStorage,
  key: string,
  scope: DocumentConstructorStorageScope,
  draft: DocumentTemplateDraft,
) {
  storage.setItem(
    key,
    JSON.stringify({
      format: STORAGE_FORMAT,
      schemaVersion: STORAGE_SCHEMA_VERSION,
      owner: scope,
      draft,
    }),
  );
}
