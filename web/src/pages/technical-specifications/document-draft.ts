import { headings, type Sections, type Specification } from './model';

type Snapshot = Pick<Specification, 'id' | 'revision' | 'title' | 'sections'>;
export interface DocumentDraft {
  base: Snapshot;
  title: string;
  sections: Sections;
  conflicts: string[];
}

export function cleanDocument(value: Snapshot): DocumentDraft {
  const base = {
    id: value.id,
    revision: value.revision,
    title: value.title,
    sections: value.sections,
  };
  return { base, title: value.title, sections: value.sections, conflicts: [] };
}

export function documentDirty(value: DocumentDraft) {
  return (
    value.title !== value.base.title ||
    headings.some(
      (_, index) =>
        value.sections[String(index + 1)] !==
        value.base.sections[String(index + 1)],
    )
  );
}

export function matchesDocumentSave(
  submitted: DocumentDraft,
  remote: Snapshot,
): boolean {
  return (
    remote.revision > submitted.base.revision &&
    remote.id === submitted.base.id &&
    remote.title === submitted.title &&
    headings.every((_, index) => {
      const key = String(index + 1);
      return remote.sections[key] === submitted.sections[key];
    })
  );
}

export function reconcileDocument(
  local: DocumentDraft,
  remote: Snapshot,
  submitted?: DocumentDraft | null,
): DocumentDraft {
  if (local.base.revision >= remote.revision) return local;
  const next = cleanDocument(remote);
  // Polling and write responses use the same acknowledgement path. A server
  // echo of our submitted content must preserve typing made during the request.
  const ownSave =
    submitted &&
    local.base.revision === submitted.base.revision &&
    matchesDocumentSave(submitted, remote);
  for (const key of [
    'title',
    ...headings.map((_, index) => String(index + 1)),
  ]) {
    const before =
      key === 'title'
        ? ownSave
          ? submitted.title
          : local.base.title
        : ownSave
          ? submitted.sections[key]
          : local.base.sections[key];
    const mine = key === 'title' ? local.title : local.sections[key];
    const theirs = key === 'title' ? remote.title : remote.sections[key];
    const conflict =
      mine !== theirs &&
      ((mine !== before && theirs !== before) || local.conflicts.includes(key));
    if (conflict) next.conflicts.push(key);
    const text =
      mine === before && !local.conflicts.includes(key) ? theirs : mine;
    if (key === 'title') next.title = text;
    else next.sections = { ...next.sections, [key]: text };
  }
  return next;
}

function validSections(value: unknown): value is Sections {
  return (
    !!value &&
    typeof value === 'object' &&
    Object.keys(value).length === 9 &&
    headings.every(
      (_, index) => typeof (value as Sections)[String(index + 1)] === 'string',
    )
  );
}

export function readDocumentDraft(
  key: string,
  document: Snapshot,
): DocumentDraft {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? 'null');
    if (
      value?.base?.id === document.id &&
      Number.isInteger(value.base.revision) &&
      typeof value.title === 'string' &&
      typeof value.base.title === 'string' &&
      validSections(value.sections) &&
      validSections(value.base.sections) &&
      Array.isArray(value.conflicts) &&
      value.conflicts.every((item: unknown) => typeof item === 'string')
    ) {
      return reconcileDocument(value, document);
    }
  } catch {
    /* Keep the server document available when the cache is unreadable. */
  }
  return cleanDocument(document);
}

export function writeDocumentDraft(key: string, value: DocumentDraft) {
  try {
    if (documentDirty(value) || value.conflicts.length)
      localStorage.setItem(key, JSON.stringify(value));
    else localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}
