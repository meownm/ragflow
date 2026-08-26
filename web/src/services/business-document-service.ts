import type {
  BusinessDocumentCommand,
  BusinessDocumentCommandResult,
  BusinessDocumentList,
  BusinessDocumentProjection,
  CreateBusinessDocumentRequest,
} from '@/pages/business-documents/types';
import api from '@/utils/api';
import request from '@/utils/next-request';
import axios from 'axios';

type ApiEnvelope<T> = {
  code: number;
  data: T;
  message?: string;
};

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
      | {
          code?: number;
          message?: string;
          data?: { error_code?: string; details?: unknown };
        }
      | undefined;
    if (error.response?.status === 409) {
      throw new BusinessDocumentConflictError(
        payload?.message || 'Команда конфликтует с состоянием документа.',
        payload?.data?.error_code || 'CONFLICT',
      );
    }
    throw new Error(payload?.message || error.message);
  }
  throw error;
}

export async function createBusinessDocument(
  input: CreateBusinessDocumentRequest,
) {
  try {
    const response = await request.post(api.businessDocuments, input);
    return unwrap<BusinessDocumentProjection>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function fetchBusinessDocument(documentId: string) {
  try {
    const response = await request.get(api.businessDocument(documentId));
    return unwrap<BusinessDocumentProjection>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}

export async function listBusinessDocuments(page = 1, pageSize = 20) {
  try {
    const response = await request.get(api.businessDocuments, {
      params: { page, page_size: pageSize },
    });
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
    );
    return unwrap<BusinessDocumentCommandResult>(response.data);
  } catch (error) {
    return rethrowBusinessDocumentError(error);
  }
}
