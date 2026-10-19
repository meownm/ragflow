import type { EvaPage } from '@/interfaces/database/eva-page';
import type {
  Draft,
  Revision,
  SavedDraft,
  Specification,
} from '@/pages/technical-specifications/model';
import { restAPIv1 } from '@/utils/api';
import request from '@/utils/next-request';

interface Envelope<T> {
  code: number;
  message?: string;
  data: T;
}

function unwrap<T>(value: Envelope<T>): T {
  if (value.code !== 0)
    throw new Error(value.message ?? 'Не удалось выполнить запрос');
  return value.data;
}

function url(workspace: string, id?: string) {
  return `${restAPIv1}/source-workspaces/${encodeURIComponent(workspace)}/technical-specifications${id ? `/${encodeURIComponent(id)}` : ''}`;
}

export async function listSpecifications(workspace: string) {
  const response = await request.get<Envelope<Specification[]>>(url(workspace));
  return unwrap(response.data);
}

export async function createSpecification(workspace: string, title: string) {
  const response = await request.post<Envelope<Specification>>(url(workspace), {
    title,
  });
  return unwrap(response.data);
}

export async function getSpecification(workspace: string, id: string) {
  const response = await request.get<Envelope<Specification>>(
    url(workspace, id),
  );
  return unwrap(response.data);
}

export async function importEvaSpecification(
  workspace: string,
  pageUrl: string,
  copy: boolean,
  selected?: EvaPage,
) {
  const response = await request.post<Envelope<Specification>>(
    `${url(workspace)}/import-eva`,
    {
      url: pageUrl,
      copy,
      ...(selected
        ? {
            selection: {
              id: selected.id,
              project_id: selected.project_id,
              version: selected.version,
            },
          }
        : {}),
    },
  );
  return unwrap(response.data);
}

export async function searchEvaPages(
  workspace: string,
  query: string,
  signal?: AbortSignal,
): Promise<EvaPage[]> {
  const response = await request.get<Envelope<{ items: EvaPage[] }>>(
    `${url(workspace)}/eva-pages`,
    { params: { query }, signal },
  );
  return unwrap(response.data).items;
}

export async function saveSpecification(
  workspace: string,
  value: Specification,
) {
  const response = await request.put<Envelope<Specification>>(
    url(workspace, value.id),
    {
      title: value.title,
      version: value.version,
      sections: value.sections,
    },
  );
  return unwrap(response.data);
}

export async function specificationAction(
  workspace: string,
  id: string,
  action: string,
  data: object,
) {
  const response = await request.post<Envelope<Specification>>(
    `${url(workspace, id)}/${action}`,
    data,
  );
  return unwrap(response.data);
}

export async function getSpecificationDraft(
  workspace: string,
  id: string,
): Promise<SavedDraft | null> {
  const response = await request.get<
    Envelope<({ version: number } & Draft) | null>
  >(`${url(workspace, id)}/draft`);
  const value = unwrap(response.data);
  return value
    ? {
        version: value.version,
        draft: {
          message_id: value.message_id,
          text: value.text,
          context: value.context,
        },
      }
    : null;
}

export async function saveSpecificationDraft(
  workspace: string,
  id: string,
  value: SavedDraft,
): Promise<SavedDraft> {
  const response = await request.put<Envelope<{ version: number } & Draft>>(
    `${url(workspace, id)}/draft`,
    value,
  );
  const result = unwrap(response.data);
  return {
    version: result.version,
    draft: {
      message_id: result.message_id,
      text: result.text,
      context: result.context,
    },
  };
}

export async function specificationHistory(
  workspace: string,
  id: string,
): Promise<Revision[]> {
  const response = await request.get<Envelope<Revision[]>>(
    `${url(workspace, id)}/revisions`,
  );
  return unwrap(response.data);
}

export async function specificationRevision(
  workspace: string,
  id: string,
  number: number,
): Promise<Revision> {
  const response = await request.get<Envelope<Revision>>(
    `${url(workspace, id)}/revisions/${number}`,
  );
  return unwrap(response.data);
}
