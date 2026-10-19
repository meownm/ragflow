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
