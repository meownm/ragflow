import { draftConflicts } from './draft-sync';
import type { SavedDraft } from './model';

const local: SavedDraft = {
  version: 2,
  draft: {
    message_id: 'draft',
    text: 'Текст',
    context: { document_id: 'doc', scope: 'all', revision: 1, anchor: null },
  },
};

test('offline edits can save when the server still has the known version', () => {
  expect(
    draftConflicts(local, {
      ...local,
      draft: { ...local.draft, text: 'До правки' },
    }),
  ).toBe(false);
});

test('a completed write with a lost response can be reconciled', () => {
  expect(draftConflicts(local, { ...local, version: 3 })).toBe(false);
});

test('another tab changing only the context is still a conflict', () => {
  expect(
    draftConflicts(local, {
      version: 3,
      draft: {
        ...local.draft,
        context: { ...local.draft.context, scope: '2' },
      },
    }),
  ).toBe(true);
});
