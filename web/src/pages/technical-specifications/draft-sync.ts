import type { Draft, SavedDraft } from './model';

export function sameDraft(left: Draft, right: Draft) {
  return JSON.stringify(left) === JSON.stringify(right);
}

// A different server version is safe only when it contains this exact draft.
export function draftConflicts(local: SavedDraft, remote: SavedDraft) {
  return (
    local.version !== remote.version && !sameDraft(local.draft, remote.draft)
  );
}
