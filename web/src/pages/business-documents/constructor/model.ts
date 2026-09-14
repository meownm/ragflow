export const BLOCK_TYPE_OPTIONS = [
  { value: 'paragraph', label: 'Текст' },
  { value: 'list', label: 'Список' },
  { value: 'table', label: 'Таблица' },
  { value: 'code', label: 'Код' },
  { value: 'plantuml', label: 'Диаграмма PlantUML' },
  { value: 'image', label: 'Изображение' },
  { value: 'reference', label: 'Ссылка на источник' },
] as const;

export const MAX_SECTION_DEPTH = 5;
export const MIN_HEADING_BASE_LEVEL = 1;
export const MAX_HEADING_BASE_LEVEL = 4;
const MAX_RENDERED_HEADING_LEVEL = 6;

export type DocumentBlockType = (typeof BLOCK_TYPE_OPTIONS)[number]['value'];

export interface ConstructorSection {
  uid: string;
  depth: number;
  customId: string;
  title: string;
  purpose: string;
  required: boolean;
  allowedBlocks: DocumentBlockType[];
  requirements: string[];
  minWords: number | null;
  maxWords: number | null;
  generationInstructions: string;
}

export interface DocumentTemplateDraft {
  schemaVersion: 1;
  name: string;
  documentType: string;
  version: string;
  language: 'ru' | 'en';
  description: string;
  headingBaseLevel: number;
  preserveSectionNumbers: boolean;
  protocolInBody: boolean;
  sections: ConstructorSection[];
}

interface NumberedConstructorSection extends ConstructorSection {
  resolvedId: string;
  parentResolvedId?: string;
}

export interface TemplateValidationIssue {
  code: string;
  message: string;
  sectionUid?: string;
}

let uidSequence = 0;

function makeUid(prefix = 'section') {
  uidSequence += 1;
  return `${prefix}-${Date.now().toString(36)}-${uidSequence.toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 7)}`;
}

export function createSection(
  depth: number,
  title = 'Новый раздел',
): ConstructorSection {
  return {
    uid: makeUid(),
    depth,
    customId: '',
    title,
    purpose: '',
    required: true,
    allowedBlocks: ['paragraph', 'list'],
    requirements: [],
    minWords: null,
    maxWords: null,
    generationInstructions: '',
  };
}

function clampDepth(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(MAX_SECTION_DEPTH, Math.floor(value)));
}

export function normalizeOutlineDepths(sections: ConstructorSection[]) {
  let previousDepth = 0;
  return sections.map((section, index) => {
    const requestedDepth = clampDepth(section.depth);
    const depth = index === 0 ? 0 : Math.min(requestedDepth, previousDepth + 1);
    previousDepth = depth;
    return { ...section, depth };
  });
}

export function maxSectionDepthForHeading(headingBaseLevel: number) {
  if (
    !Number.isInteger(headingBaseLevel) ||
    headingBaseLevel < MIN_HEADING_BASE_LEVEL ||
    headingBaseLevel > MAX_HEADING_BASE_LEVEL
  ) {
    return 0;
  }
  return Math.min(
    MAX_SECTION_DEPTH,
    MAX_RENDERED_HEADING_LEVEL - headingBaseLevel,
  );
}

function outlineCanBeNumbered(sections: ConstructorSection[]) {
  let previousDepth = 0;
  return sections.every((section, index) => {
    const valid =
      Number.isInteger(section.depth) &&
      section.depth >= 0 &&
      section.depth <= MAX_SECTION_DEPTH &&
      (index === 0 ? section.depth === 0 : section.depth <= previousDepth + 1);
    previousDepth = section.depth;
    return valid;
  });
}

export function numberSections(
  sections: ConstructorSection[],
): NumberedConstructorSection[] {
  if (!outlineCanBeNumbered(sections)) {
    throw new Error('Структуру с недопустимой глубиной нельзя пронумеровать.');
  }
  const counters = Array.from({ length: MAX_SECTION_DEPTH + 1 }, () => 0);
  const ancestorIds: string[] = [];

  return sections.map((section) => {
    counters[section.depth] += 1;
    counters.fill(0, section.depth + 1);
    const parentResolvedId =
      section.depth > 0 ? ancestorIds[section.depth - 1] : undefined;
    const automaticId = parentResolvedId
      ? `${parentResolvedId}.${counters[section.depth]}`
      : `${counters[0]}`;
    const resolvedId = section.customId.trim() || automaticId;
    ancestorIds[section.depth] = resolvedId;
    ancestorIds.length = section.depth + 1;
    return { ...section, resolvedId, parentResolvedId };
  });
}

function subtreeEndIndex(sections: ConstructorSection[], index: number) {
  const depth = sections[index]?.depth;
  if (depth === undefined) return index;
  let cursor = index + 1;
  while (cursor < sections.length && sections[cursor].depth > depth) {
    cursor += 1;
  }
  return cursor;
}

export function addRootSection(sections: ConstructorSection[]) {
  return [...sections, createSection(0)];
}

export function addChildSection(
  sections: ConstructorSection[],
  parentUid: string,
  maxDepth = MAX_SECTION_DEPTH,
) {
  const parentIndex = sections.findIndex(
    (section) => section.uid === parentUid,
  );
  if (
    parentIndex < 0 ||
    sections[parentIndex].depth >= Math.min(MAX_SECTION_DEPTH, maxDepth)
  ) {
    return sections;
  }
  const insertionIndex = subtreeEndIndex(sections, parentIndex);
  const next = [...sections];
  next.splice(
    insertionIndex,
    0,
    createSection(sections[parentIndex].depth + 1),
  );
  return next;
}

export function deleteSectionTree(
  sections: ConstructorSection[],
  sectionUid: string,
) {
  const index = sections.findIndex((section) => section.uid === sectionUid);
  if (index < 0) return sections;
  return [
    ...sections.slice(0, index),
    ...sections.slice(subtreeEndIndex(sections, index)),
  ];
}

export function indentSection(
  sections: ConstructorSection[],
  sectionUid: string,
  maxDepth = MAX_SECTION_DEPTH,
) {
  const index = sections.findIndex((section) => section.uid === sectionUid);
  if (index <= 0) return sections;
  const previousIndex = previousSiblingIndex(sections, index);
  if (previousIndex < 0) return sections;
  const end = subtreeEndIndex(sections, index);
  const deepestSubtreeDepth = sections
    .slice(index, end)
    .reduce((deepest, section) => Math.max(deepest, section.depth), 0);
  if (deepestSubtreeDepth >= Math.min(MAX_SECTION_DEPTH, maxDepth)) {
    return sections;
  }
  return sections.map((section, sectionIndex) =>
    sectionIndex >= index && sectionIndex < end
      ? { ...section, depth: section.depth + 1 }
      : section,
  );
}

export function outdentSection(
  sections: ConstructorSection[],
  sectionUid: string,
) {
  const index = sections.findIndex((section) => section.uid === sectionUid);
  if (index < 0 || sections[index].depth === 0) return sections;
  const end = subtreeEndIndex(sections, index);
  return sections.map((section, sectionIndex) =>
    sectionIndex >= index && sectionIndex < end
      ? { ...section, depth: section.depth - 1 }
      : section,
  );
}

function previousSiblingIndex(sections: ConstructorSection[], index: number) {
  const depth = sections[index].depth;
  let cursor = index - 1;
  while (cursor >= 0 && sections[cursor].depth > depth) cursor -= 1;
  return cursor >= 0 && sections[cursor].depth === depth ? cursor : -1;
}

export function moveSection(
  sections: ConstructorSection[],
  sectionUid: string,
  direction: 'up' | 'down',
) {
  const index = sections.findIndex((section) => section.uid === sectionUid);
  if (index < 0) return sections;
  const currentEnd = subtreeEndIndex(sections, index);
  const sectionTree = sections.slice(index, currentEnd);

  if (direction === 'up') {
    const siblingIndex = previousSiblingIndex(sections, index);
    if (siblingIndex < 0) return sections;
    return [
      ...sections.slice(0, siblingIndex),
      ...sectionTree,
      ...sections.slice(siblingIndex, index),
      ...sections.slice(currentEnd),
    ];
  }

  if (
    currentEnd >= sections.length ||
    sections[currentEnd].depth !== sections[index].depth
  ) {
    return sections;
  }
  const nextSiblingEnd = subtreeEndIndex(sections, currentEnd);
  return [
    ...sections.slice(0, index),
    ...sections.slice(currentEnd, nextSiblingEnd),
    ...sectionTree,
    ...sections.slice(nextSiblingEnd),
  ];
}

function isPositiveIntegerOrNull(value: number | null) {
  return value === null || (Number.isInteger(value) && value > 0);
}

export function validateTemplate(draft: DocumentTemplateDraft) {
  const issues: TemplateValidationIssue[] = [];
  if (!draft.name.trim()) {
    issues.push({
      code: 'NAME_REQUIRED',
      message: 'Укажите название шаблона.',
    });
  }
  if (!/^[a-z][a-z0-9_-]{2,63}$/.test(draft.documentType.trim())) {
    issues.push({
      code: 'DOCUMENT_TYPE_INVALID',
      message:
        'Технический код: 3–64 строчных латинских символа, цифры, _ или -.',
    });
  }
  if (!draft.version.trim()) {
    issues.push({
      code: 'VERSION_REQUIRED',
      message: 'Укажите версию шаблона.',
    });
  }
  const headingLevelIsValid =
    Number.isInteger(draft.headingBaseLevel) &&
    draft.headingBaseLevel >= MIN_HEADING_BASE_LEVEL &&
    draft.headingBaseLevel <= MAX_HEADING_BASE_LEVEL;
  if (!headingLevelIsValid) {
    issues.push({
      code: 'HEADING_BASE_LEVEL_INVALID',
      message: `Базовый уровень заголовка должен быть от H${MIN_HEADING_BASE_LEVEL} до H${MAX_HEADING_BASE_LEVEL}.`,
    });
  }
  if (!draft.sections.length) {
    issues.push({
      code: 'SECTIONS_REQUIRED',
      message: 'Добавьте хотя бы один раздел.',
    });
    return issues;
  }

  let outlineIsValid = true;
  let previousDepth = 0;
  let deepestSection = draft.sections[0];
  draft.sections.forEach((section, index) => {
    const depthIsValid =
      Number.isInteger(section.depth) &&
      section.depth >= 0 &&
      section.depth <= MAX_SECTION_DEPTH;
    if (!depthIsValid) {
      outlineIsValid = false;
      issues.push({
        code: 'SECTION_DEPTH_INVALID',
        message: `У раздела ${index + 1} недопустимая глубина.`,
        sectionUid: section.uid,
      });
      return;
    }
    if (index === 0 && section.depth !== 0) {
      outlineIsValid = false;
      issues.push({
        code: 'SECTION_ROOT_REQUIRED',
        message: 'Первый раздел должен быть корневым.',
        sectionUid: section.uid,
      });
    } else if (index > 0 && section.depth > previousDepth + 1) {
      outlineIsValid = false;
      issues.push({
        code: 'SECTION_DEPTH_GAP',
        message: `Перед разделом ${index + 1} пропущен уровень вложенности.`,
        sectionUid: section.uid,
      });
    }
    previousDepth = section.depth;
    if (section.depth > deepestSection.depth) deepestSection = section;
  });

  if (
    headingLevelIsValid &&
    draft.headingBaseLevel + deepestSection.depth > MAX_RENDERED_HEADING_LEVEL
  ) {
    issues.push({
      code: 'HEADING_DEPTH_INVALID',
      message: `При базовом уровне H${draft.headingBaseLevel} доступно не более ${maxSectionDepthForHeading(draft.headingBaseLevel) + 1} уровней разделов.`,
      sectionUid: deepestSection.uid,
    });
  }

  if (!outlineIsValid) return issues;

  const numbered = numberSections(draft.sections);
  const seenIds = new Set<string>();
  numbered.forEach((section) => {
    if (!section.title.trim()) {
      issues.push({
        code: 'SECTION_TITLE_REQUIRED',
        message: `У раздела ${section.resolvedId} нет названия.`,
        sectionUid: section.uid,
      });
    }
    if (!/^\d+(?:\.\d+)*$/.test(section.resolvedId)) {
      issues.push({
        code: 'SECTION_ID_INVALID',
        message: `Идентификатор «${section.resolvedId}» должен состоять из чисел и точек.`,
        sectionUid: section.uid,
      });
    }
    if (section.resolvedId.length > 16) {
      issues.push({
        code: 'SECTION_ID_TOO_LONG',
        message: `Идентификатор «${section.resolvedId}» длиннее 16 символов.`,
        sectionUid: section.uid,
      });
    }
    const childIdPrefix = section.parentResolvedId
      ? `${section.parentResolvedId}.`
      : '';
    const childIdSuffix = section.resolvedId.slice(childIdPrefix.length);
    if (
      section.parentResolvedId &&
      (!section.resolvedId.startsWith(childIdPrefix) ||
        !/^\d+$/.test(childIdSuffix))
    ) {
      issues.push({
        code: 'SECTION_ID_PARENT_MISMATCH',
        message: `Идентификатор «${section.resolvedId}» не является непосредственным дочерним номером раздела ${section.parentResolvedId}.`,
        sectionUid: section.uid,
      });
    }
    if (seenIds.has(section.resolvedId)) {
      issues.push({
        code: 'SECTION_ID_DUPLICATE',
        message: `Идентификатор «${section.resolvedId}» повторяется.`,
        sectionUid: section.uid,
      });
    }
    seenIds.add(section.resolvedId);
    if (!section.allowedBlocks.length) {
      issues.push({
        code: 'BLOCK_TYPE_REQUIRED',
        message: `Для раздела ${section.resolvedId} выберите хотя бы один тип содержимого.`,
        sectionUid: section.uid,
      });
    }
    if (
      !isPositiveIntegerOrNull(section.minWords) ||
      !isPositiveIntegerOrNull(section.maxWords)
    ) {
      issues.push({
        code: 'WORD_LIMIT_VALUE_INVALID',
        message: `В разделе ${section.resolvedId} лимиты слов должны быть положительными целыми числами.`,
        sectionUid: section.uid,
      });
    } else if (
      section.minWords !== null &&
      section.maxWords !== null &&
      section.minWords > section.maxWords
    ) {
      issues.push({
        code: 'WORD_LIMIT_INVALID',
        message: `В разделе ${section.resolvedId} минимум слов больше максимума.`,
        sectionUid: section.uid,
      });
    }
  });
  return issues;
}
