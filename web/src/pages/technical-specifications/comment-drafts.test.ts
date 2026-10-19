import {
  clearCommentDraft,
  editCommentDraft,
  openCommentDraft,
  readCommentDrafts,
  writeCommentDrafts,
} from './comment-drafts';
import { wholeContext, type Specification } from './model';

const doc = { id: 'doc', revision: 3 } as Specification;

beforeEach(() => localStorage.clear());

test('switching scope saves each reply in its original thread', () => {
  let state = readCommentDrafts('comments', doc);
  state = openCommentDraft(state, wholeContext(doc, '5'), 'thread-a');
  state = editCommentDraft(state, 'Ответ для раздела 5');
  const original = state.entries[state.active];
  state = openCommentDraft(state, wholeContext(doc, '8'), null);
  state = editCommentDraft(state, 'Комментарий к API');
  expect(state.entries[state.active].reply_to).toBeNull();
  state = openCommentDraft(state, wholeContext(doc, '5'), 'thread-a');
  expect(state.entries[state.active]).toEqual(original);
  expect(writeCommentDrafts('comments', state)).toBe(true);
  expect(readCommentDrafts('comments', doc)).toEqual(state);
});

test('a completed request clears only the exact submitted draft', () => {
  let state = editCommentDraft(
    readCommentDrafts('comments', doc),
    'Первый текст',
  );
  const submitted = state.entries[state.active].message_id;
  const target = state.active;
  state = editCommentDraft(state, 'Новый текст во время отправки');
  expect(clearCommentDraft(state, target, submitted).entries[target].text).toBe(
    'Новый текст во время отправки',
  );
  expect(
    clearCommentDraft(state, target, state.entries[target].message_id).entries[
      target
    ].text,
  ).toBe('');
});

test('a cached draft for another document is not restored', () => {
  const state = editCommentDraft(
    readCommentDrafts('comments', doc),
    'Чужой текст',
  );
  writeCommentDrafts('comments', state);
  const other = readCommentDrafts('comments', { ...doc, id: 'other' });
  expect(other.entries[other.active].text).toBe('');
  expect(other.entries[other.active].context.document_id).toBe('other');
});
