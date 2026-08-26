import {
  listBusinessDocuments,
  submitBusinessDocumentCommand,
} from '@/services/business-document-service';
import api from '@/utils/api';
import request from '@/utils/next-request';

jest.mock('@/utils/next-request', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

const mockedGet = jest.mocked(request.get);
const mockedPost = jest.mocked(request.post);

beforeEach(() => jest.clearAllMocks());

test('loads the canonical paginated document list envelope', async () => {
  const list = {
    items: [
      {
        document_id: 'doc-1',
        title: 'Требования',
        lifecycle_state: 'INTAKE',
        operation_state: 'IDLE',
        state_version: 1,
        current_revision_number: null,
        update_time: 1_787_695_200,
      },
    ],
    total: 1,
    page: 2,
    page_size: 10,
  };
  mockedGet.mockResolvedValueOnce({ data: { code: 0, data: list } });

  await expect(listBusinessDocuments(2, 10)).resolves.toEqual(list);
  expect(mockedGet).toHaveBeenCalledWith(api.businessDocuments, {
    params: { page: 2, page_size: 10 },
  });
});

test('reads the domain error code from the backend error envelope', async () => {
  mockedPost.mockRejectedValueOnce({
    isAxiosError: true,
    message: 'Request failed with status code 409',
    response: {
      status: 409,
      data: {
        code: 409,
        message: 'Есть открытые вопросы',
        data: {
          error_code: 'OPEN_REVIEW_QUESTIONS',
          details: { question_ids: ['question-1'] },
        },
      },
    },
  });

  const promise = submitBusinessDocumentCommand('doc-1', {
    schema_version: '1',
    command_id: 'cmd-1',
    idempotency_key: 'idem-1',
    expected_state_version: 4,
    type: 'APPLY_CHANGES',
    payload: { base_revision_id: 'revision-1' },
  });

  await expect(promise).rejects.toMatchObject({
    name: 'BusinessDocumentConflictError',
    code: 'OPEN_REVIEW_QUESTIONS',
    message: 'Есть открытые вопросы',
  });
});
