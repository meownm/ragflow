import {
  messageId,
  wholeContext,
  type Context,
  type Draft,
  type Specification,
} from './model';

export interface CommentDraft extends Draft {
  reply_to: string | null;
}
export interface CommentDrafts {
  active: string;
  entries: Record<string, CommentDraft>;
}

function target(context: Context, replyTo: string | null) {
  return replyTo
    ? `thread:${replyTo}`
    : JSON.stringify([context.scope, context.anchor]);
}

export function openCommentDraft(
  value: CommentDrafts,
  context: Context,
  replyTo: string | null,
): CommentDrafts {
  const key = target(context, replyTo);
  return {
    active: key,
    entries: {
      ...value.entries,
      [key]: value.entries[key] ?? {
        message_id: messageId(),
        text: '',
        context,
        reply_to: replyTo,
      },
    },
  };
}

export function readCommentDrafts(
  key: string,
  document: Specification,
): CommentDrafts {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? 'null');
    if (
      value?.entries?.[value.active] &&
      Object.values(value.entries).every((item) => {
        const draft = item as CommentDraft;
        return (
          typeof draft.message_id === 'string' &&
          typeof draft.text === 'string' &&
          draft.context?.document_id === document.id &&
          (draft.reply_to === null || typeof draft.reply_to === 'string')
        );
      })
    )
      return value;
  } catch {
    /* An invalid cache does not become a context for another document. */
  }
  return openCommentDraft(
    { active: '', entries: {} },
    wholeContext(document),
    null,
  );
}

export function writeCommentDrafts(key: string, value: CommentDrafts): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export function editCommentDraft(
  value: CommentDrafts,
  text: string,
): CommentDrafts {
  return {
    ...value,
    entries: {
      ...value.entries,
      [value.active]: {
        ...value.entries[value.active],
        message_id: messageId(),
        text,
      },
    },
  };
}

export function clearCommentDraft(
  value: CommentDrafts,
  key: string,
  submittedId: string,
): CommentDrafts {
  const current = value.entries[key];
  if (!current || current.message_id !== submittedId) return value;
  return {
    ...value,
    entries: {
      ...value.entries,
      [key]: { ...current, message_id: messageId(), text: '' },
    },
  };
}
