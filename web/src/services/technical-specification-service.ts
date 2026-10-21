import type { EvaPage } from '@/interfaces/database/eva-page';
import type {
  Draft,
  Revision,
  SavedDraft,
  Specification,
  SpecificationSummary,
} from '@/pages/technical-specifications/model';
import { restAPIv1 } from '@/utils/api';
import request from '@/utils/next-request';
import type { AxiosRequestConfig } from 'axios';

const handledErrors = { skipErrorNotification: true } as AxiosRequestConfig;

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

export interface AlignmentCheck {
  id: string;
  current: boolean;
  qualifies: boolean;
  status?: string;
  reason_code?: string;
  result?: {
    quality_status: string;
    evidence_status?: string;
    reason_code?: string;
    report?: {
      requirements: {
        id: string;
        coverage: string;
        consistency: string;
        reason: string;
        references: { section: string; quote: string }[];
      }[];
      additional_rules: { kind: string; reason: string }[];
      joint: { consistency: string; reason: string };
    };
  };
}

export interface AlignmentView {
  document: Specification;
  checks: AlignmentCheck[];
}

export async function listAlignmentSources(
  workspace: string,
  query = '',
  page = 1,
) {
  const response = await request.get<
    Envelope<{
      items: { document_id: string; title: string; revision_number: number }[];
      has_more?: boolean;
      page?: number;
    }>
  >(`${url(workspace)}/business-sources`, {
    ...handledErrors,
    params: { query, page },
  });
  return unwrap(response.data);
}

export async function getAlignment(workspace: string, id: string) {
  const response = await request.get<Envelope<AlignmentView>>(
    `${url(workspace, id)}/alignment`,
    handledErrors,
  );
  return unwrap(response.data);
}

export async function checkAlignment(
  workspace: string,
  id: string,
  data: {
    version: number;
    request_id: string;
    business_document_ids: string[];
  },
) {
  const response = await request.post<Envelope<AlignmentView>>(
    `${url(workspace, id)}/alignment`,
    data,
    handledErrors,
  );
  return unwrap(response.data);
}

export async function listSpecifications(workspace: string) {
  const response = await request.get<Envelope<SpecificationSummary[]>>(
    url(workspace),
  );
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

export async function exportSpecification(
  workspace: string,
  id: string,
  revision: number,
) {
  const response = await request.post<
    Envelope<{
      filename: string;
      mime_type: string;
      content: string;
      revision: number;
    }>
  >(`${url(workspace, id)}/export`, { revision });
  return unwrap(response.data);
}

export async function importEvaSpecification(
  workspace: string,
  pageUrl: string,
  copy: boolean,
  selected: Pick<EvaPage, 'id' | 'project_id' | 'version'>,
  operationId: string,
) {
  const response = await request.post<Envelope<Specification>>(
    `${url(workspace)}/import-eva`,
    {
      url: pageUrl,
      copy,
      operation_id: operationId,
      selection: {
        id: selected.id,
        project_id: selected.project_id,
        version: selected.version,
      },
    },
    handledErrors,
  );
  return unwrap(response.data);
}

export interface EvaImportOperation {
  id: string;
  state: 'processing' | 'completed' | 'failed' | 'canceled';
  document?: Specification;
  document_unavailable?: boolean;
}

export async function getEvaImport(workspace: string, operationId: string) {
  const response = await request.get<Envelope<EvaImportOperation | null>>(
    `${url(workspace)}/import-eva/${encodeURIComponent(operationId)}`,
    handledErrors,
  );
  return unwrap(response.data);
}

export async function cancelEvaImport(
  workspace: string,
  page: Pick<EvaPage, 'id' | 'project_id' | 'version' | 'web_url'>,
  copy: boolean,
  operationId: string,
) {
  const response = await request.post<Envelope<EvaImportOperation>>(
    `${url(workspace)}/import-eva/cancel`,
    {
      operation_id: operationId,
      url: page.web_url,
      copy,
      selection: {
        id: page.id,
        project_id: page.project_id,
        version: page.version,
      },
    },
    handledErrors,
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
    { ...handledErrors, params: { query }, signal },
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
    action === 'publish-eva' || action === 'connect-eva'
      ? handledErrors
      : undefined,
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
