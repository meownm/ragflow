import { createSection, type DocumentTemplateDraft } from './model';
import {
  DOCUMENT_CONSTRUCTOR_FILE_FORMAT,
  exportTemplate,
  importTemplate,
} from './template-codec';

function createCodecDraft(): DocumentTemplateDraft {
  const requirements = createSection(0, 'Требования');
  requirements.allowedBlocks = ['paragraph', 'list', 'table'];
  const functional = createSection(1, 'Функциональные требования');
  functional.allowedBlocks = ['paragraph', 'list', 'table', 'plantuml'];

  return {
    schemaVersion: 1,
    name: 'Тестовый шаблон',
    documentType: 'test_document',
    version: '1.0.0',
    language: 'ru',
    description: '',
    headingBaseLevel: 2,
    preserveSectionNumbers: true,
    protocolInBody: false,
    sections: [
      createSection(0, 'Цель и ожидаемый результат'),
      createSection(0, 'Контекст и границы'),
      requirements,
      functional,
      createSection(0, 'Критерии приёмки'),
    ],
  };
}

describe('document constructor project codec', () => {
  it('exports the owned versioned format without backend semantic aliases', () => {
    const draft = createCodecDraft();
    draft.sections[2] = { ...draft.sections[2], customId: '10' };
    draft.sections[3] = {
      ...draft.sections[3],
      minWords: 120,
      maxWords: 300,
      generationInstructions: 'Использовать только проверяемые факты.',
      allowedBlocks: ['paragraph', 'table'],
      requirements: ['  Указать метрику успеха  ', ''],
    };

    const exported = exportTemplate(draft);

    expect(exported).toMatchObject({
      format: DOCUMENT_CONSTRUCTOR_FILE_FORMAT,
      schema_version: '1',
      status: 'DRAFT',
    });
    expect(exported.sections[3]).toEqual({
      id: '10.1',
      parent_id: '10',
      title: 'Функциональные требования',
      required: true,
      allowed_blocks: ['paragraph', 'table'],
      requirements: ['Указать метрику успеха'],
      word_limits: { min: 120, max: 300 },
      generator_instructions: 'Использовать только проверяемые факты.',
    });
    expect(exported.sections[3]).not.toHaveProperty('semantic_requirements');
    expect(exported.sections[3]).not.toHaveProperty('content_constraints');
  });

  it('round-trips its own format without changing IDs or parents', () => {
    const draft = createCodecDraft();
    draft.sections[2] = { ...draft.sections[2], customId: '10' };
    const exported = exportTemplate(draft);

    expect(exportTemplate(importTemplate(exported))).toEqual(exported);
  });

  it('rejects unsupported schema versions and foreign JSON formats', () => {
    const exported = exportTemplate(createCodecDraft());

    expect(() => importTemplate({ ...exported, schema_version: '2' })).toThrow(
      'не поддерживается',
    );
    expect(() => importTemplate({ ...exported, format: undefined })).toThrow(
      'Неверный format',
    );
  });

  it('rejects forward, dangling and already closed parents', () => {
    const exported = exportTemplate(createCodecDraft());
    const child = exported.sections[3];
    const beforeParent = {
      ...exported,
      sections: [
        exported.sections[0],
        exported.sections[1],
        child,
        exported.sections[2],
        exported.sections[4],
      ],
    };
    expect(() => importTemplate(beforeParent)).toThrow('ещё не объявлен');

    const danglingParent = {
      ...exported,
      sections: exported.sections.map((section, index) =>
        index === 3 ? { ...section, parent_id: '99' } : section,
      ),
    };
    expect(() => importTemplate(danglingParent)).toThrow('ещё не объявлен');

    const closedParent = {
      ...exported,
      sections: [
        exported.sections[0],
        exported.sections[1],
        exported.sections[2],
        exported.sections[4],
        child,
      ],
    };
    expect(() => importTemplate(closedParent)).toThrow('родитель уже закрыт');
  });

  it('rejects an ID that contradicts its declared parent', () => {
    const exported = exportTemplate(createCodecDraft());
    const malformed = {
      ...exported,
      sections: exported.sections.map((section, index) =>
        index === 3 ? { ...section, id: '9.1' } : section,
      ),
    };

    expect(() => importTemplate(malformed)).toThrow(
      'не является непосредственным дочерним номером',
    );
  });
});
