import {
  cleanDocument,
  readDocumentDraft,
  reconcileDocument,
  writeDocumentDraft,
} from './document-draft';

const original = {
  id: 'doc',
  revision: 1,
  title: 'ТЗ',
  sections: Object.fromEntries(
    Array.from({ length: 9 }, (_, i) => [String(i + 1), 'До']),
  ),
};

test('manual text and its base survive reopening', () => {
  const local = {
    ...cleanDocument(original),
    sections: { ...original.sections, '1': 'Моя правка' },
  };
  expect(writeDocumentDraft('manual', local)).toBe(true);
  expect(readDocumentDraft('manual', original)).toEqual(local);
});

test('acknowledging an in-flight save preserves later typing without a false conflict', () => {
  const submitted = {
    ...cleanDocument(original),
    sections: { ...original.sections, '1': 'Отправленное' },
  };
  const later = {
    ...submitted,
    sections: {
      ...submitted.sections,
      '1': 'Более новый ввод',
      '9': 'Новый журнал',
    },
  };
  const saved = { ...original, revision: 2, sections: submitted.sections };
  const next = reconcileDocument(later, saved, submitted);
  expect(next.sections['1']).toBe('Более новый ввод');
  expect(next.sections['9']).toBe('Новый журнал');
  expect(next.base).toMatchObject(saved);
  expect(next.conflicts).toEqual([]);
});

test('server changes in other sections are retained and overlap requires a choice', () => {
  const local = {
    ...cleanDocument(original),
    sections: { ...original.sections, '1': 'Моя правка', '2': 'Моя настройка' },
  };
  const remote = {
    ...original,
    revision: 2,
    sections: {
      ...original.sections,
      '2': 'Настройка другого автора',
      '9': 'Внешний журнал',
    },
  };
  const merged = reconcileDocument(local, remote);
  expect(merged.sections['1']).toBe('Моя правка');
  expect(merged.sections['9']).toBe('Внешний журнал');
  expect(merged.conflicts).toEqual(['2']);
  expect(merged.sections['2']).toBe('Моя настройка');
});

test('an unresolved choice survives another server revision', () => {
  const local = {
    ...cleanDocument(original),
    sections: { ...original.sections, '1': 'Моя правка' },
  };
  const remote = {
    ...original,
    revision: 2,
    sections: { ...original.sections, '1': 'Внешняя правка' },
  };
  const pending = reconcileDocument(local, remote);
  expect(
    reconcileDocument(pending, { ...remote, revision: 3 }).conflicts,
  ).toEqual(['1']);
});

test('polling an own save and its later response use the same acknowledgement', () => {
  const submitted = {
    ...cleanDocument(original),
    sections: { ...original.sections, '1': 'A' },
  };
  const local = { ...submitted, sections: { ...submitted.sections, '1': 'B' } };
  const remote = { ...original, revision: 2, sections: submitted.sections };
  const polled = reconcileDocument(local, remote, submitted);
  expect(polled.conflicts).toEqual([]);
  expect(polled.sections['1']).toBe('B');
  expect(reconcileDocument(polled, remote, submitted)).toBe(polled);
  const external = {
    ...remote,
    revision: 3,
    sections: { ...remote.sections, '1': 'Other author' },
  };
  expect(reconcileDocument(polled, external, submitted).conflicts).toEqual([
    '1',
  ]);
});
