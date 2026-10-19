import {
  contextLabel,
  draftKey,
  readDraft,
  selectionContext,
  staleContext,
  wholeContext,
  writeDraft,
  type Specification,
} from './model';

const doc = {
  id: 'doc',
  revision: 7,
  sections: { '5': 'До цитата после' },
} as unknown as Specification;

beforeEach(() => localStorage.clear());

test('persists the entire draft separately for actor, workspace and document', () => {
  const context = selectionContext(doc, '5', doc.sections['5'], 3, 9);
  const saved = {
    version: 12,
    draft: { message_id: 'message', text: 'а'.repeat(10000), context },
  };
  const key = draftKey('actor', 'workspace', 'doc');
  expect(writeDraft(key, saved)).toBe(true);
  expect(readDraft(key)).toEqual(saved);
  expect(readDraft(draftKey('other', 'workspace', 'doc'))).toBeNull();
  expect(readDraft(draftKey('actor', 'workspace', 'other'))).toBeNull();
  expect(contextLabel(context)).toBe('Фрагмент раздела 5');
});

test('keeps original scope and quote after the source changes', () => {
  const context = selectionContext(doc, '5', doc.sections['5'], 3, 9);
  expect(staleContext(context, doc.sections)).toBe(false);
  expect(staleContext(context, { '5': 'Добавлено. До цитата после' })).toBe(
    false,
  );
  expect(staleContext(context, { '5': 'До изменено после' })).toBe(true);
  expect(
    staleContext(context, { '5': 'До цитата после До цитата после' }),
  ).toBe(true);
  expect(context.scope).toBe('selection');
  expect(context.anchor?.quote).toBe('цитата');
  expect(wholeContext(doc, '5').scope).toBe('5');
});

test('preserves unicode around selected fragments', () => {
  const content = '🙂'.repeat(70) + 'цитата' + '🙂'.repeat(70);
  const context = selectionContext(doc, '5', content, 140, 146);
  expect(Array.from(context.anchor?.prefix ?? '')).toHaveLength(64);
  expect(Array.from(context.anchor?.suffix ?? '')).toHaveLength(64);
  expect(staleContext(context, { '5': content })).toBe(false);
});

test('reports a storage failure instead of pretending the draft was saved', () => {
  const set = jest
    .spyOn(Storage.prototype, 'setItem')
    .mockImplementation(() => {
      throw new Error('quota');
    });
  expect(
    writeDraft('key', {
      version: 0,
      draft: {
        message_id: 'message',
        text: 'text',
        context: wholeContext(doc),
      },
    }),
  ).toBe(false);
  set.mockRestore();
});
