import {
  BLOCK_TYPE_OPTIONS,
  MAX_SECTION_DEPTH,
  createSection,
  numberSections,
  validateTemplate,
  type ConstructorSection,
  type DocumentBlockType,
  type DocumentTemplateDraft,
} from './model';

export const DOCUMENT_CONSTRUCTOR_FILE_FORMAT = 'ragflow-document-constructor';
const DOCUMENT_CONSTRUCTOR_FILE_SCHEMA_VERSION = '1';

interface ConstructorProjectSection {
  id: string;
  parent_id?: string;
  title: string;
  purpose?: string;
  required: boolean;
  allowed_blocks: DocumentBlockType[];
  requirements?: string[];
  word_limits?: {
    min?: number;
    max?: number;
  };
  generator_instructions?: string;
}

interface ConstructorProjectFile {
  format: typeof DOCUMENT_CONSTRUCTOR_FILE_FORMAT;
  schema_version: typeof DOCUMENT_CONSTRUCTOR_FILE_SCHEMA_VERSION;
  status: 'DRAFT';
  document_type: string;
  template_version: string;
  language: 'ru' | 'en';
  name: string;
  description?: string;
  rendering: {
    body_heading_base_level: number;
    preserve_section_numbers: boolean;
    protocol_in_body: boolean;
  };
  sections: ConstructorProjectSection[];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function requiredString(
  source: Record<string, unknown>,
  key: string,
  context: string,
) {
  const value = source[key];
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${context}: поле ${key} должно быть непустой строкой.`);
  }
  return value.trim();
}

function optionalString(
  source: Record<string, unknown>,
  key: string,
  context: string,
) {
  const value = source[key];
  if (value === undefined) return '';
  if (typeof value !== 'string') {
    throw new Error(`${context}: поле ${key} должно быть строкой.`);
  }
  return value;
}

function requiredBoolean(
  source: Record<string, unknown>,
  key: string,
  context: string,
) {
  const value = source[key];
  if (typeof value !== 'boolean') {
    throw new Error(`${context}: поле ${key} должно быть логическим.`);
  }
  return value;
}

function optionalPositiveInteger(
  source: Record<string, unknown>,
  key: string,
  context: string,
) {
  const value = source[key];
  if (value === undefined) return null;
  if (typeof value !== 'number' || !Number.isInteger(value) || value <= 0) {
    throw new Error(
      `${context}: поле ${key} должно быть положительным целым числом.`,
    );
  }
  return value;
}

function parseContentTypes(value: unknown, context: string) {
  if (!Array.isArray(value) || !value.length) {
    throw new Error(
      `${context}: allowed_blocks должен быть непустым массивом.`,
    );
  }
  const knownTypes = new Set<string>(
    BLOCK_TYPE_OPTIONS.map((option) => option.value),
  );
  if (value.some((item) => typeof item !== 'string' || !knownTypes.has(item))) {
    throw new Error(`${context}: allowed_blocks содержит неизвестный тип.`);
  }
  if (new Set(value).size !== value.length) {
    throw new Error(`${context}: allowed_blocks содержит повторения.`);
  }
  return value as DocumentBlockType[];
}

function parseRequirements(value: unknown, context: string) {
  if (value === undefined) return [];
  if (
    !Array.isArray(value) ||
    value.some((item) => typeof item !== 'string' || !item.trim())
  ) {
    throw new Error(
      `${context}: requirements должен содержать только непустые строки.`,
    );
  }
  return value.map((item) => (item as string).trim());
}

export function exportTemplate(
  draft: DocumentTemplateDraft,
): ConstructorProjectFile {
  const issues = validateTemplate(draft);
  if (issues.length) throw new Error(issues[0].message);

  const numbered = numberSections(draft.sections);
  return {
    format: DOCUMENT_CONSTRUCTOR_FILE_FORMAT,
    schema_version: DOCUMENT_CONSTRUCTOR_FILE_SCHEMA_VERSION,
    status: 'DRAFT',
    document_type: draft.documentType.trim(),
    template_version: draft.version.trim(),
    language: draft.language,
    name: draft.name.trim(),
    ...(draft.description.trim()
      ? { description: draft.description.trim() }
      : {}),
    rendering: {
      body_heading_base_level: draft.headingBaseLevel,
      preserve_section_numbers: draft.preserveSectionNumbers,
      protocol_in_body: draft.protocolInBody,
    },
    sections: numbered.map((section) => {
      const requirements = section.requirements
        .map((requirement) => requirement.trim())
        .filter(Boolean);
      return {
        id: section.resolvedId,
        ...(section.parentResolvedId
          ? { parent_id: section.parentResolvedId }
          : {}),
        title: section.title.trim(),
        ...(section.purpose.trim() ? { purpose: section.purpose.trim() } : {}),
        required: section.required,
        allowed_blocks: [...section.allowedBlocks],
        ...(requirements.length ? { requirements } : {}),
        ...(section.minWords !== null || section.maxWords !== null
          ? {
              word_limits: {
                ...(section.minWords !== null ? { min: section.minWords } : {}),
                ...(section.maxWords !== null ? { max: section.maxWords } : {}),
              },
            }
          : {}),
        ...(section.generationInstructions.trim()
          ? {
              generator_instructions: section.generationInstructions.trim(),
            }
          : {}),
      };
    }),
  };
}

export function importTemplate(value: unknown): DocumentTemplateDraft {
  const source = asRecord(value);
  if (!source) throw new Error('Корень JSON должен быть объектом.');
  if (source.format !== DOCUMENT_CONSTRUCTOR_FILE_FORMAT) {
    throw new Error(
      `Неверный format: ожидается «${DOCUMENT_CONSTRUCTOR_FILE_FORMAT}».`,
    );
  }
  if (source.schema_version !== DOCUMENT_CONSTRUCTOR_FILE_SCHEMA_VERSION) {
    throw new Error(
      `Версия schema_version «${String(source.schema_version)}» не поддерживается.`,
    );
  }
  if (source.status !== 'DRAFT') {
    throw new Error(
      'Конструктор импортирует только проекты со статусом DRAFT.',
    );
  }
  if (!Array.isArray(source.sections) || !source.sections.length) {
    throw new Error('JSON не содержит непустой массив sections.');
  }

  const rendering = asRecord(source.rendering);
  if (!rendering) throw new Error('Поле rendering должно быть объектом.');
  const headingBaseLevel = rendering.body_heading_base_level;
  if (typeof headingBaseLevel !== 'number') {
    throw new Error(
      'rendering: поле body_heading_base_level должно быть числом.',
    );
  }

  const seenIds = new Set<string>();
  const activeAncestorIds: string[] = [];
  const sections = source.sections.map((rawSection, index) => {
    const context = `Раздел ${index + 1}`;
    const section = asRecord(rawSection);
    if (!section) throw new Error(`${context} должен быть объектом.`);
    const id = requiredString(section, 'id', context);
    const title = requiredString(section, 'title', context);
    if (seenIds.has(id)) {
      throw new Error(`${context}: идентификатор «${id}» повторяется.`);
    }

    let depth = 0;
    const rawParentId = section.parent_id;
    if (rawParentId !== undefined) {
      if (typeof rawParentId !== 'string' || !rawParentId.trim()) {
        throw new Error(`${context}: parent_id должен быть непустой строкой.`);
      }
      const parentId = rawParentId.trim();
      const parentDepth = activeAncestorIds.lastIndexOf(parentId);
      if (parentDepth < 0) {
        const reason = seenIds.has(parentId)
          ? 'родитель уже закрыт порядком разделов'
          : 'родитель ещё не объявлен';
        throw new Error(
          `${context}: parent_id «${parentId}» недопустим — ${reason}.`,
        );
      }
      depth = parentDepth + 1;
    }
    if (depth > MAX_SECTION_DEPTH) {
      throw new Error(
        `${context}: глубина превышает ${MAX_SECTION_DEPTH + 1} уровней.`,
      );
    }

    const wordLimitsValue = section.word_limits;
    const wordLimits =
      wordLimitsValue === undefined ? null : asRecord(wordLimitsValue);
    if (wordLimitsValue !== undefined && !wordLimits) {
      throw new Error(`${context}: word_limits должен быть объектом.`);
    }

    const importedSection: ConstructorSection = {
      ...createSection(depth, title),
      customId: id,
      purpose: optionalString(section, 'purpose', context),
      required: requiredBoolean(section, 'required', context),
      allowedBlocks: parseContentTypes(section.allowed_blocks, context),
      requirements: parseRequirements(section.requirements, context),
      minWords: wordLimits
        ? optionalPositiveInteger(wordLimits, 'min', `${context}.word_limits`)
        : null,
      maxWords: wordLimits
        ? optionalPositiveInteger(wordLimits, 'max', `${context}.word_limits`)
        : null,
      generationInstructions: optionalString(
        section,
        'generator_instructions',
        context,
      ),
    };
    seenIds.add(id);
    activeAncestorIds[depth] = id;
    activeAncestorIds.length = depth + 1;
    return importedSection;
  });

  if (source.language !== 'ru' && source.language !== 'en') {
    throw new Error('Поле language должно иметь значение ru или en.');
  }
  const imported: DocumentTemplateDraft = {
    schemaVersion: 1,
    name: requiredString(source, 'name', 'Шаблон'),
    documentType: requiredString(source, 'document_type', 'Шаблон'),
    version: requiredString(source, 'template_version', 'Шаблон'),
    language: source.language,
    description: optionalString(source, 'description', 'Шаблон'),
    headingBaseLevel,
    preserveSectionNumbers: requiredBoolean(
      rendering,
      'preserve_section_numbers',
      'rendering',
    ),
    protocolInBody: requiredBoolean(rendering, 'protocol_in_body', 'rendering'),
    sections,
  };
  const issues = validateTemplate(imported);
  if (issues.length) throw new Error(issues[0].message);
  return imported;
}
