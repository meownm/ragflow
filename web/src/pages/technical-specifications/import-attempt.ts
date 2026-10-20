import type { EvaPage } from '@/interfaces/database/eva-page';

export interface ImportAttempt {
  id: string;
  copy: boolean;
  page: Pick<EvaPage, 'id' | 'project_id' | 'version' | 'web_url' | 'name'>;
}

export function importPrefix(actor: string, workspace: string) {
  return `spec-eva-import:${JSON.stringify([actor, workspace])}:`;
}

export function readImport(prefix: string): ImportAttempt | null {
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index);
    if (!key?.startsWith(prefix)) continue;
    const value = JSON.parse(localStorage.getItem(key) ?? 'null');
    if (
      !value ||
      typeof value.id !== 'string' ||
      key !== prefix + value.id ||
      !/^[a-f0-9]{32}$/.test(value.id) ||
      typeof value.copy !== 'boolean' ||
      !value.page ||
      ['id', 'project_id', 'version', 'web_url', 'name'].some(
        (field) => typeof value.page[field] !== 'string' || !value.page[field],
      )
    )
      throw new Error('Некорректная запись незавершённого импорта');
    return value;
  }
  return null;
}

export function rememberImport(prefix: string, value: ImportAttempt) {
  const previous = readImport(prefix);
  if (previous) return previous;
  const json = JSON.stringify(value);
  localStorage.setItem(prefix + value.id, json);
  if (localStorage.getItem(prefix + value.id) !== json)
    throw new Error('Не удалось сохранить запрос импорта');
  return value;
}

export function forgetImport(prefix: string, id: string) {
  localStorage.removeItem(prefix + id);
}
