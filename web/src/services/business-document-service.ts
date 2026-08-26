import type {
  BusinessDocumentCommand,
  BusinessDocumentCommandResult,
  BusinessDocumentList,
  BusinessDocumentProjection,
  CreateBusinessDocumentRequest,
  EvaDocumentChange,
  EvaDocumentChangeList,
  EvaDocumentSourceSearchResult,
} from '@/pages/business-documents/types';
import api from '@/utils/api';
import request from '@/utils/next-request';
import axios, { AxiosRequestConfig } from 'axios';

type ApiEnvelope<T> = {
  code: number;
  data: T;
  message?: string;
};

type BusinessDocumentErrorPayload = {
  code?: number;
  message?: string;
  data?: {
    error_code?: string;
    details?: unknown;
    message?: string;
  };
};

function requestConfig(config: AxiosRequestConfig = {}): AxiosRequestConfig {
  return {
    ...config,
    // This page renders domain and retry errors in context. Avoid a duplicate
    // global toast from the shared Axios interceptor.
    skipErrorNotification: true,
  } as AxiosRequestConfig;
}

function unwrap<T>(payload: T | ApiEnvelope<T>): T {
  if (
    payload &&
    typeof payload === 'object' &&
    'code' in payload &&
    'data' in payload
  ) {
    const envelope = payload as ApiEnvelope<T>;
    if (envelope.code !== 0) {
      throw new Error(envelope.message || 'Business document request failed');
    }
    return envelope.data;
  }
  return payload as T;
}

export class BusinessDocumentConflictError extends Error {
  readonly code: string;

  constructor(message: string, code = 'CONFLICT') {
    super(message);
    this.name = 'BusinessDocumentConflictError';
    this.code = code;
  }
}

function rethrowBusinessDocumentError(error: unknown): never {
  if (axios.isAxiosError(error)) {
    const payload = error.response?.data as
      | BusinessDocumentErrorPayload
      | undefined;
    const message = payload?.message || payload?.data?.message;
    if (error.response?.status === 409) {
      throw new BusinessDocumentConflictError(
        message || 'Команда конфликтует с состоянием документа.',
        payload?.data?.error_code || 'CONFLICT',
      );
    }
    throw new Error(
      message || error.message || 'Не удалось выполнить запрос документа.',
    );
  }
  throw error;
}

export async function createBusinessDocument(
  input: CreateBusinessDocumentRequest,
) {
  try {
    const response = await request.post(
      api.businessDocuments,
      input,
      requestConfig(),
    );
    return unwrap<BusinessDocumentProjection>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function fetchBusinessDocument(documentId: string) {
  try {
    const response = await request.get(
      api.businessDocument(documentId),
      requestConfig(),
    );
    return unwrap<BusinessDocumentProjection>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function listBusinessDocuments(page = 1, pageSize = 20) {
  try {
    const response = await request.get(
      api.businessDocuments,
      requestConfig({ params: { page, page_size: pageSize } }),
    );
    return unwrap<BusinessDocumentList>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function submitBusinessDocumentCommand(
  documentId: string,
  command: BusinessDocumentCommand,
) {
  try {
    const response = await request.post(
      api.businessDocumentCommands(documentId),
      command,
      requestConfig(),
    );
    return unwrap<BusinessDocumentCommandResult>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function searchEvaDocumentSources(
  query: string,
  connectorId?: string,
) {
  try {
    const response = await request.get(
      api.evaBusinessDocumentSources,
      requestConfig({
        params: {
          query,
          ...(connectorId ? { connector_id: connectorId } : {}),
        },
      }),
    );
    return unwrap<EvaDocumentSourceSearchResult>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function createEvaDocumentChange(input: {
  connector_id: string;
  document_id: string;
  change_summary: string;
}) {
  try {
    const response = await request.post(
      api.evaBusinessDocumentChanges,
      input,
      requestConfig(),
    );
    return unwrap<EvaDocumentChange>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function listEvaDocumentChanges(page = 1, pageSize = 20) {
  try {
    const response = await request.get(
      api.evaBusinessDocumentChanges,
      requestConfig({ params: { page, page_size: pageSize } }),
    );
    return unwrap<EvaDocumentChangeList>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function fetchEvaDocumentChange(changeId: string) {
  try {
    const response = await request.get(
      api.evaBusinessDocumentChange(changeId),
      requestConfig(),
    );
    return unwrap<EvaDocumentChange>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function saveEvaDocumentChangeDraft(
  changeId: string,
  input: { expected_state_version: number; draft_markdown: string },
) {
  try {
    const response = await request.put(
      api.evaBusinessDocumentChangeDraft(changeId),
      input,
      requestConfig(),
    );
    return unwrap<EvaDocumentChange>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

async function submitEvaDocumentChangeAction(
  url: string,
  expectedStateVersion: number,
) {
  try {
    const response = await request.post(
      url,
      { expected_state_version: expectedStateVersion },
      requestConfig(),
    );
    return unwrap<EvaDocumentChange>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export const approveEvaDocumentChange = (
  changeId: string,
  expectedStateVersion: number,
) =>
  submitEvaDocumentChangeAction(
    api.evaBusinessDocumentChangeApprove(changeId),
    expectedStateVersion,
  );

export const prepareEvaDocumentChange = (
  changeId: string,
  expectedStateVersion: number,
) =>
  submitEvaDocumentChangeAction(
    api.evaBusinessDocumentChangePrepare(changeId),
    expectedStateVersion,
  );

export const publishEvaDocumentChange = (
  changeId: string,
  expectedStateVersion: number,
) =>
  submitEvaDocumentChangeAction(
    api.evaBusinessDocumentChangePublish(changeId),
    expectedStateVersion,
  );
