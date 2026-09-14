import {
  addChildSection,
  createSection,
  deleteSectionTree,
  indentSection,
  moveSection,
  numberSections,
  outdentSection,
  validateTemplate,
  type DocumentTemplateDraft,
} from './model';

function createModelDraft(): DocumentTemplateDraft {
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
      createSection(0, 'Требования'),
      createSection(1, 'Функциональные требования'),
      createSection(0, 'Критерии приёмки'),
    ],
  };
}

describe('document constructor model', () => {
  it('numbers the outline and resolves parent IDs', () => {
    const draft = createModelDraft();

    expect(
      numberSections(draft.sections).map((section) => ({
        id: section.resolvedId,
        parent: section.parentResolvedId,
      })),
    ).toEqual([
      { id: '1', parent: undefined },
      { id: '2', parent: undefined },
      { id: '3', parent: undefined },
      { id: '3.1', parent: '3' },
      { id: '4', parent: undefined },
    ]);
  });

  it('derives automatic child IDs from a custom parent ID', () => {
    const draft = createModelDraft();
    draft.sections[2] = { ...draft.sections[2], customId: '10' };

    expect(numberSections(draft.sections)[3]).toMatchObject({
      resolvedId: '10.1',
      parentResolvedId: '10',
    });
  });

  it('keeps child subtrees together while changing structure', () => {
    const draft = createModelDraft();
    const requirementsUid = draft.sections[2].uid;
    const acceptanceUid = draft.sections[4].uid;

    const indented = indentSection(draft.sections, acceptanceUid);
    expect(indented[4].depth).toBe(1);
    expect(numberSections(indented)[4].resolvedId).toBe('3.2');

    const outdented = outdentSection(indented, acceptanceUid);
    expect(outdented[4].depth).toBe(0);

    const moved = moveSection(outdented, requirementsUid, 'up');
    expect(moved.map((section) => section.title)).toEqual([
      'Цель и ожидаемый результат',
      'Требования',
      'Функциональные требования',
      'Контекст и границы',
      'Критерии приёмки',
    ]);
  });

  it('adds and removes a complete nested section tree', () => {
    const draft = createModelDraft();
    const parentUid = draft.sections[2].uid;
    const withChild = addChildSection(draft.sections, parentUid);
    const added = withChild.find(
      (section) => !draft.sections.some((item) => item.uid === section.uid),
    );

    expect(added?.depth).toBe(1);
    expect(withChild.indexOf(added!)).toBe(4);
    expect(deleteSectionTree(withChild, parentUid)).toHaveLength(3);
  });

  it('does not add a child below the configured depth limit', () => {
    const root = createSection(0, 'Root');
    const child = createSection(1, 'Child');
    const deepest = createSection(2, 'Deepest');
    const sections = [root, child, deepest];

    expect(addChildSection(sections, deepest.uid, 2)).toBe(sections);
  });

  it('does not indent a subtree when a descendant would exceed the limit', () => {
    const root = createSection(0, 'Root');
    const previous = createSection(1, 'Previous');
    const target = createSection(1, 'Target');
    const descendant = createSection(2, 'Descendant');
    const sections = [root, previous, target, descendant];

    expect(indentSection(sections, target.uid, 2)).toBe(sections);
  });

  it('reports heading overflow and malformed outline depth', () => {
    const draft = createModelDraft();
    draft.headingBaseLevel = 4;
    draft.sections = [
      createSection(0, 'Root'),
      createSection(1, 'Child'),
      createSection(2, 'Grandchild'),
      createSection(3, 'Too deep'),
      createSection(6, 'Invalid'),
    ];

    expect(validateTemplate(draft).map((issue) => issue.code)).toEqual(
      expect.arrayContaining([
        'HEADING_DEPTH_INVALID',
        'SECTION_DEPTH_INVALID',
      ]),
    );
    expect(() => numberSections(draft.sections)).toThrow(
      'нельзя пронумеровать',
    );
  });

  it('reports duplicate IDs, parent mismatches and inverted word limits', () => {
    const draft = createModelDraft();
    draft.sections[0] = {
      ...draft.sections[0],
      customId: '2',
      minWords: 500,
      maxWords: 100,
    };
    draft.sections[2] = { ...draft.sections[2], customId: '10' };
    draft.sections[3] = { ...draft.sections[3], customId: '3.1' };

    expect(validateTemplate(draft).map((issue) => issue.code)).toEqual(
      expect.arrayContaining([
        'SECTION_ID_DUPLICATE',
        'SECTION_ID_PARENT_MISMATCH',
        'WORD_LIMIT_INVALID',
      ]),
    );
  });
});
