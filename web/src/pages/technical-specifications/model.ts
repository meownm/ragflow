export const headings = [
  'Назначение и цель',
  'Общие сведения, термины, сокращения и ссылки',
  'Компоненты подлежащие доработки с описанием изменений',
  'Конфигурирование (настройки)',
  'Прототипы интерфейсов и сценарии использования',
  'Логика работы',
  'Модель данных',
  'Сигнатура взаимодействия',
  'Журналирование',
] as const;

export type Sections = Record<string, string>;
export interface Context {
  document_id: string;
  scope: string;
  revision: number;
  anchor: null | {
    section: string;
    quote: string;
    prefix: string;
    suffix: string;
  };
}
export interface Draft {
  message_id: string;
  text: string;
  context: Context;
}
export interface Comment {
  id: string;
  author_id: string;
  context: Context;
  text: string;
  resolved: boolean;
  replies: { id: string; author_id: string; text: string }[];
}
export interface Proposal {
  id: string;
  message_id?: string;
  base_revision: number;
  sections: Sections;
  status: 'ready' | 'applied' | 'needs_resolution';
  source: 'chat' | 'eva';
  issues?: string[];
  conflicts?: {
    section: string;
    base: string;
    local: string;
    remote: string;
  }[];
}
export interface Specification {
  id: string;
  workspace_id: string;
  title: string;
  version: number;
  revision: number;
  sections: Sections;
  messages: {
    id: string;
    reply_to?: string;
    role: string;
    text: string;
    context?: Context;
    author_id?: string;
    status?: 'processing' | 'completed' | 'failed';
    source_revision?: number;
    error?: { code: string; message: string } | null;
  }[];
  comments: Comment[];
  people: Record<string, string>;
  eva_export?: null | {
    status: 'publishing' | 'published' | 'failed';
    revision: number;
  };
  proposals: Proposal[];
  eva: null | {
    page_url: string;
    remote_version: string;
    document_name?: string;
  };
}
export type SpecificationSummary = Pick<
  Specification,
  'id' | 'title' | 'version' | 'revision'
>;
export interface Revision {
  number: number;
  title: string;
  author_id: string;
  reason: string;
  created_at: string;
  sections?: Sections;
}

export function wholeContext(document: Specification, scope = 'all'): Context {
  return {
    document_id: document.id,
    scope,
    revision: document.revision,
    anchor: null,
  };
}

export function contextLabel(value: Context): string {
  return value.scope === 'all'
    ? 'Всё ТЗ'
    : value.scope === 'selection'
      ? `Фрагмент раздела ${value.anchor?.section}`
      : `Раздел ${value.scope}`;
}

export function staleContext(value: Context, sections: Sections): boolean {
  if (value.scope !== 'selection') return false;
  if (!value.anchor) return true;
  const { section, quote, prefix, suffix } = value.anchor;
  if (!quote) return true;
  const full = prefix + quote + suffix;
  const content = sections[section] ?? '';
  const start = content.indexOf(full);
  return start < 0 || content.indexOf(full, start + 1) >= 0;
}

export function selectionContext(
  document: Specification,
  section: string,
  content: string,
  start: number,
  end: number,
): Context {
  if (start === end) return wholeContext(document, section);
  return {
    ...wholeContext(document, 'selection'),
    anchor: {
      section,
      quote: content.slice(start, end),
      prefix: Array.from(content.slice(0, start)).slice(-64).join(''),
      suffix: Array.from(content.slice(end)).slice(0, 64).join(''),
    },
  };
}

export function draftKey(actor: string, workspace: string, document: string) {
  return `technical-specification-draft-v1:${actor}:${workspace}:${document}`;
}

export function messageId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
    value.toString(16).padStart(2, '0'),
  ).join('');
}

export interface SavedDraft {
  version: number;
  draft: Draft;
}

export function readDraft(key: string): SavedDraft | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? 'null');
    if (
      !value ||
      !Number.isInteger(value.version) ||
      typeof value.draft?.message_id !== 'string' ||
      typeof value.draft?.text !== 'string' ||
      typeof value.draft?.context?.document_id !== 'string' ||
      typeof value.draft?.context?.scope !== 'string'
    )
      return null;
    return value;
  } catch {
    return null;
  }
}

export function writeDraft(key: string, value: SavedDraft): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
