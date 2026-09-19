import {
  assignBusinessDocumentOwner,
  checkBusinessDocumentEvaUpdate,
  compileBusinessDocumentSqlQuery,
  createBusinessDocumentSqlAgentProject,
  createBusinessDocumentSqlCatalogBinding,
  createBusinessDocumentSqlExecutionProfile,
  createEvaDocumentChange,
  decideBusinessDocumentSqlAgentProposal,
  fetchBusinessDocumentSqlAgentProject,
  generateEvaDocumentChangeDraft,
  getBusinessDocumentCapabilities,
  listBusinessDocumentAccessUsers,
  listBusinessDocumentCatalog,
  listBusinessDocuments,
  listBusinessDocumentSqlAgentProjects,
  listBusinessDocumentSqlCatalogBindings,
  listBusinessDocumentSqlExecutionConnectors,
  listBusinessDocumentSqlExecutionProfiles,
  loadBusinessDocumentSqlSchemaEntities,
  planBusinessDocumentSqlQuery,
  prepareEvaDocumentChange,
  publishEvaDocumentChange,
  requestBusinessDocumentSqlAgent,
  resolveBusinessDocumentSqlExecutionBinding,
  resolveBusinessDocumentSqlSchema,
  searchEvaDocumentSources,
  submitBusinessDocumentCommand,
  updateBusinessDocumentSqlCatalogBinding,
  updateBusinessDocumentSqlExecutionProfile,
  updateBusinessDocumentUserRole,
} from '@/services/business-document-service';
import api from '@/utils/api';
import request from '@/utils/next-request';

jest.mock('@/utils/next-request', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    put: jest.fn(),
  },
}));

const mockedGet = jest.mocked(request.get);
const mockedPost = jest.mocked(request.post);
const mockedPatch = jest.mocked(request.patch);
const mockedPut = jest.mocked(request.put);

beforeEach(() => jest.clearAllMocks());

test('loads business-document capabilities without querying the document list', async () => {
  const access = {
    access_role: 'AUTHOR_CREATOR' as const,
    capabilities: {
      read: true,
      create: true,
      edit_own: true,
      edit_all: false,
      delete: false,
      assign: false,
    },
  };
  mockedGet.mockResolvedValueOnce({ data: { code: 0, data: access } });

  await expect(getBusinessDocumentCapabilities()).resolves.toEqual(access);
  expect(mockedGet).toHaveBeenCalledWith(api.businessDocumentCapabilities, {
    skipErrorNotification: true,
  });
});

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

  await expect(listBusinessDocuments(2, 10, 'mine')).resolves.toEqual(list);
  expect(mockedGet).toHaveBeenCalledWith(api.businessDocuments, {
    params: { page: 2, page_size: 10, scope: 'mine' },
    skipErrorNotification: true,
  });
});

test('checks the linked EVA page without mutating the business document', async () => {
  const status = {
    document_id: 'doc-1',
    changed: true,
    direction: 'FROM_EVA' as const,
    remote_version: '2',
    baseline_version: '1',
    can_pull: true,
  };
  mockedGet.mockResolvedValueOnce({ data: { code: 0, data: status } });

  await expect(checkBusinessDocumentEvaUpdate('doc-1')).resolves.toEqual(
    status,
  );
  expect(mockedGet).toHaveBeenCalledWith(
    api.businessDocumentEvaStatus('doc-1'),
    { skipErrorNotification: true },
  );
});

test('loads the L5 document catalog from its dedicated endpoint', async () => {
  const catalog = {
    items: [
      {
        id: 'L2-01.01.04.01.01',
        title: 'Разрешённый документ',
        capability_level: 'L5' as const,
        hierarchy: {},
      },
    ],
    total: 1,
  };
  mockedGet.mockResolvedValueOnce({ data: { code: 0, data: catalog } });

  await expect(listBusinessDocumentCatalog()).resolves.toEqual(catalog);
  expect(mockedGet).toHaveBeenCalledWith(api.businessDocumentCatalog, {
    skipErrorNotification: true,
  });
});

test('resolves all SQL schema terms through one business-document request', async () => {
  const input = {
    terms: ['Заказ', 'Клиент'],
    requirements: 'Нужны заказы корпоративных клиентов',
    locale: 'ru' as const,
  };
  const result = {
    schema_version: '1',
    status: 'NEEDS_CLARIFICATION',
    resolutions: [],
    llm: {
      status: 'APPLIED',
      prompt: {
        name: 'sql_schema_interpreter',
        version: '1',
        content_hash: 'sha256:test',
      },
      warning: null,
    },
  };
  mockedPost.mockResolvedValueOnce({ data: { code: 0, data: result } });

  await expect(resolveBusinessDocumentSqlSchema(input)).resolves.toEqual(
    result,
  );
  expect(mockedPost).toHaveBeenCalledWith(
    api.businessDocumentSqlSchemaResolve,
    input,
    { skipErrorNotification: true },
  );
});

test('loads full schemas only for selected table IDs', async () => {
  const input = { entity_ids: ['orders'], locale: 'ru' as const };
  const result = {
    schema_version: '1',
    status: 'READY',
    entities: [],
  };
  mockedPost.mockResolvedValueOnce({ data: { code: 0, data: result } });

  await expect(loadBusinessDocumentSqlSchemaEntities(input)).resolves.toEqual(
    result,
  );
  expect(mockedPost).toHaveBeenCalledWith(
    api.businessDocumentSqlSchemaEntities,
    input,
    { skipErrorNotification: true },
  );
});

test('compiles a structured SQL specification through the guarded endpoint', async () => {
  const input = {
    schema_version: '1' as const,
    schema_snapshot: {},
    accepted_requirements: 'Нужны заказы',
    accepted_schema: [],
    specification: {
      dialect: 'postgres' as const,
      from: { entity_id: 'orders', alias: 'o' },
      select: [],
      joins: [],
      filters: [],
      order_by: [],
      parameters: [],
      limit_parameter: 'row_limit',
    },
  };
  const result = {
    schema_version: '1',
    status: 'NEEDS_CLARIFICATION',
    snapshot_fingerprint: 'sha256:test',
    blocking_issues: [],
    sql: null,
    parameters: {},
    guard: { status: 'NOT_RUN' },
  };
  mockedPost.mockResolvedValueOnce({ data: { code: 0, data: result } });

  await expect(compileBusinessDocumentSqlQuery(input)).resolves.toEqual(result);
  expect(mockedPost).toHaveBeenCalledWith(
    api.businessDocumentSqlQueryCompile,
    input,
    { skipErrorNotification: true },
  );
});

test('requests a catalog-bound SQL plan through the tenant LLM endpoint', async () => {
  const input = {
    schema_version: '1' as const,
    schema_snapshot: {},
    accepted_requirements: 'Нужны заказы',
    accepted_schema: [],
    locale: 'ru' as const,
  };
  const result = {
    schema_version: '1',
    status: 'FALLBACK',
    proposal: null,
    clarification_questions: [],
    warning: 'Продолжите вручную',
    diagnostic: 'QueryPlanUnavailable',
    llm: { status: 'FALLBACK', prompt: null, warning: 'Продолжите вручную' },
  };
  mockedPost.mockResolvedValueOnce({ data: { code: 0, data: result } });

  await expect(planBusinessDocumentSqlQuery(input)).resolves.toEqual(result);
  expect(mockedPost).toHaveBeenCalledWith(
    api.businessDocumentSqlQueryPlan,
    input,
    { skipErrorNotification: true },
  );
});

test('uses the durable SQL agent project endpoints as one versioned cycle', async () => {
  const createInput = {
    schema_version: '1' as const,
    title: 'Заказы',
    source_request: 'Покажи заказы',
    locale: 'ru' as const,
  };
  const project = {
    id: 'project-1',
    state_version: 1,
    next_agent: 'REQUIREMENTS' as const,
  };
  const runInput = {
    schema_version: '1' as const,
    expected_state_version: 1,
    idempotency_key: 'run-1',
    kind: 'REQUIREMENTS' as const,
    payload: {},
  };
  const decisionInput = {
    schema_version: '1' as const,
    expected_state_version: 2,
    idempotency_key: 'accept-1',
    decision: 'ACCEPT' as const,
    artifact_payload: null,
  };
  mockedPost
    .mockResolvedValueOnce({ data: { code: 0, data: project } })
    .mockResolvedValueOnce({
      data: { code: 0, data: { ...project, state_version: 2 } },
    })
    .mockResolvedValueOnce({
      data: { code: 0, data: { ...project, state_version: 3 } },
    });
  mockedGet
    .mockResolvedValueOnce({ data: { code: 0, data: [project] } })
    .mockResolvedValueOnce({ data: { code: 0, data: project } });

  await createBusinessDocumentSqlAgentProject(createInput);
  await listBusinessDocumentSqlAgentProjects();
  await fetchBusinessDocumentSqlAgentProject('project-1');
  await requestBusinessDocumentSqlAgent('project-1', runInput);
  await decideBusinessDocumentSqlAgentProposal(
    'project-1',
    'proposal-1',
    decisionInput,
  );

  expect(mockedPost).toHaveBeenNthCalledWith(
    1,
    api.businessDocumentSqlQueryProjects,
    createInput,
    { skipErrorNotification: true },
  );
  expect(mockedGet).toHaveBeenNthCalledWith(
    1,
    api.businessDocumentSqlQueryProjects,
    { skipErrorNotification: true },
  );
  expect(mockedGet).toHaveBeenNthCalledWith(
    2,
    api.businessDocumentSqlQueryProject('project-1'),
    { skipErrorNotification: true },
  );
  expect(mockedPost).toHaveBeenNthCalledWith(
    2,
    api.businessDocumentSqlAgentJobs('project-1'),
    runInput,
    { skipErrorNotification: true },
  );
  expect(mockedPost).toHaveBeenNthCalledWith(
    3,
    api.businessDocumentSqlAgentProposalDecision('project-1', 'proposal-1'),
    decisionInput,
    { skipErrorNotification: true },
  );
});

test('uses the central SQL execution registry and a separate resolution endpoint', async () => {
  const connectors = {
    schema_version: '1' as const,
    items: [
      {
        id: 'connector-1',
        name: 'Warehouse',
        source: 'postgresql' as const,
        database: 'analytics',
        available: true,
      },
    ],
  };
  const profileInput = {
    schema_version: '1' as const,
    name: 'Warehouse RO',
    connector_id: 'connector-1',
    dialect: 'postgres' as const,
    allowed_schemas: ['dwh'],
    statement_timeout_ms: 30000,
    max_rows: 1000,
    max_result_bytes: 5000000,
    enabled: true,
  };
  const profile = {
    id: 'profile-1',
    ...profileInput,
    policy_fingerprint: 'sha256:policy',
    target_database: 'analytics',
    version: 1,
    available: true,
    policy_valid: true,
    connector_available: true,
    connector_identity_matches: true,
    connector: connectors.items[0],
    created_by: 'admin-1',
    updated_by: 'admin-1',
  };
  const bindingInput = {
    schema_version: '1' as const,
    catalog_service: 'warehouse',
    catalog_database: 'analytics',
    catalog_schema: 'dwh',
    execution_profile_id: 'profile-1',
    enabled: true,
  };
  const binding = {
    id: 'binding-1',
    ...bindingInput,
    version: 1,
    created_by: 'admin-1',
    updated_by: 'admin-1',
  };
  const resolveInput = {
    schema_version: '1' as const,
    schema_snapshot: {},
    accepted_requirements: 'Нужны заказы',
    accepted_schema: [],
    selected_profile_id: null,
  };
  const resolved = {
    schema_version: '1' as const,
    status: 'BOUND' as const,
    reason: null,
    snapshot_fingerprint: 'sha256:snapshot',
    catalog_scopes: [],
    unresolved_catalog_scopes: [],
    candidates: [],
    selection: null,
  };

  mockedGet
    .mockResolvedValueOnce({ data: { code: 0, data: connectors } })
    .mockResolvedValueOnce({
      data: { code: 0, data: { schema_version: '1', items: [profile] } },
    })
    .mockResolvedValueOnce({
      data: { code: 0, data: { schema_version: '1', items: [binding] } },
    });
  mockedPost
    .mockResolvedValueOnce({ data: { code: 0, data: profile } })
    .mockResolvedValueOnce({ data: { code: 0, data: binding } })
    .mockResolvedValueOnce({ data: { code: 0, data: resolved } });
  mockedPut
    .mockResolvedValueOnce({
      data: { code: 0, data: { ...profile, version: 2 } },
    })
    .mockResolvedValueOnce({
      data: { code: 0, data: { ...binding, version: 2 } },
    });

  await expect(listBusinessDocumentSqlExecutionConnectors()).resolves.toEqual(
    connectors,
  );
  await expect(listBusinessDocumentSqlExecutionProfiles()).resolves.toEqual({
    schema_version: '1',
    items: [profile],
  });
  await expect(
    createBusinessDocumentSqlExecutionProfile(profileInput),
  ).resolves.toEqual(profile);
  await expect(
    updateBusinessDocumentSqlExecutionProfile('profile-1', {
      ...profileInput,
      expected_version: 1,
    }),
  ).resolves.toEqual({ ...profile, version: 2 });
  await expect(listBusinessDocumentSqlCatalogBindings()).resolves.toEqual({
    schema_version: '1',
    items: [binding],
  });
  await expect(
    createBusinessDocumentSqlCatalogBinding(bindingInput),
  ).resolves.toEqual(binding);
  await expect(
    updateBusinessDocumentSqlCatalogBinding('binding-1', {
      ...bindingInput,
      expected_version: 1,
    }),
  ).resolves.toEqual({ ...binding, version: 2 });
  await expect(
    resolveBusinessDocumentSqlExecutionBinding(resolveInput),
  ).resolves.toEqual(resolved);

  expect(mockedGet).toHaveBeenNthCalledWith(
    1,
    api.businessDocumentSqlExecutionConnectors,
    { skipErrorNotification: true },
  );
  expect(mockedGet).toHaveBeenNthCalledWith(
    2,
    api.businessDocumentSqlExecutionProfiles,
    { skipErrorNotification: true },
  );
  expect(mockedPost).toHaveBeenNthCalledWith(
    1,
    api.businessDocumentSqlExecutionProfiles,
    profileInput,
    { skipErrorNotification: true },
  );
  expect(mockedPut).toHaveBeenNthCalledWith(
    1,
    api.businessDocumentSqlExecutionProfile('profile-1'),
    { ...profileInput, expected_version: 1 },
    { skipErrorNotification: true },
  );
  expect(mockedGet).toHaveBeenNthCalledWith(
    3,
    api.businessDocumentSqlCatalogBindings,
    { skipErrorNotification: true },
  );
  expect(mockedPost).toHaveBeenNthCalledWith(
    2,
    api.businessDocumentSqlCatalogBindings,
    bindingInput,
    { skipErrorNotification: true },
  );
  expect(mockedPut).toHaveBeenNthCalledWith(
    2,
    api.businessDocumentSqlCatalogBinding('binding-1'),
    { ...bindingInput, expected_version: 1 },
    { skipErrorNotification: true },
  );
  expect(mockedPost).toHaveBeenNthCalledWith(
    3,
    api.businessDocumentSqlExecutionBindingResolve,
    resolveInput,
    { skipErrorNotification: true },
  );
});

test('uses explicit access and ownership endpoints', async () => {
  const users = {
    items: [
      {
        user_id: 'author-2',
        nickname: 'Второй автор',
        role: 'AUTHOR_CREATOR' as const,
      },
    ],
  };
  const assigned = { document_id: 'doc-1', owner_id: 'author-2' };
  mockedGet.mockResolvedValueOnce({ data: { code: 0, data: users } });
  mockedPut.mockResolvedValueOnce({ data: { code: 0, data: assigned } });
  mockedPatch.mockResolvedValueOnce({
    data: { code: 0, data: users.items[0] },
  });

  await expect(listBusinessDocumentAccessUsers()).resolves.toEqual(users);
  await expect(
    assignBusinessDocumentOwner('doc-1', 'author-2', 7),
  ).resolves.toEqual(assigned);
  await expect(
    updateBusinessDocumentUserRole('author-2', 'AUTHOR_EDITOR'),
  ).resolves.toEqual(users.items[0]);

  expect(mockedGet).toHaveBeenCalledWith(api.businessDocumentAccessUsers, {
    skipErrorNotification: true,
  });
  expect(mockedPut).toHaveBeenCalledWith(
    api.businessDocumentOwner('doc-1'),
    { owner_id: 'author-2', expected_state_version: 7 },
    { skipErrorNotification: true },
  );
  expect(mockedPatch).toHaveBeenCalledWith(
    api.businessDocumentAccessUser('author-2'),
    { role: 'AUTHOR_EDITOR' },
    { skipErrorNotification: true },
  );
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
    details: { question_ids: ['question-1'] },
  });
  expect(mockedPost).toHaveBeenCalledWith(
    api.businessDocumentCommands('doc-1'),
    expect.any(Object),
    { skipErrorNotification: true },
  );
});

test('uses a nested backend message without leaking an undefined transport error', async () => {
  mockedGet.mockRejectedValueOnce({
    isAxiosError: true,
    message: 'Request error 404: undefined',
    response: {
      status: 404,
      data: {
        code: 404,
        data: {
          error_code: 'DOCUMENT_NOT_FOUND',
          message: 'Бизнес-документ не найден',
        },
      },
    },
  });

  await expect(listBusinessDocuments()).rejects.toThrow(
    'Бизнес-документ не найден',
  );
});

test('localizes transport failures instead of exposing an English Axios message', async () => {
  mockedGet.mockRejectedValueOnce({
    isAxiosError: true,
    message: 'Network Error',
  });

  await expect(listBusinessDocuments()).rejects.toThrow(
    'Не удалось связаться с сервером. Проверьте подключение и повторите попытку.',
  );
});

test('explains a server failure when the backend returned no message', async () => {
  mockedGet.mockRejectedValueOnce({
    isAxiosError: true,
    message: 'Request failed with status code 503',
    response: { status: 503, data: {} },
  });

  await expect(listBusinessDocuments()).rejects.toThrow(
    'Сервер не смог выполнить запрос. Повторите попытку позже; если ошибка сохранится, обратитесь к администратору.',
  );
});

test('uses explicit EVA source, agent-generation and publish endpoints', async () => {
  const sourceResult = { items: [], connectors: [] };
  const change = { change_id: 'change-1' };
  mockedGet.mockResolvedValueOnce({ data: { code: 0, data: sourceResult } });
  mockedPost
    .mockResolvedValueOnce({ data: { code: 0, data: change } })
    .mockResolvedValueOnce({ data: { code: 0, data: change } })
    .mockResolvedValueOnce({ data: { code: 0, data: change } })
    .mockResolvedValueOnce({ data: { code: 0, data: change } });

  await expect(searchEvaDocumentSources('BR-42')).resolves.toEqual(
    sourceResult,
  );
  await createEvaDocumentChange({
    connector_id: 'connector-1',
    document_id: 'CmfDocument:doc-1',
    change_summary: 'Уточнить цель',
  });
  await generateEvaDocumentChangeDraft('change-1', {
    expected_state_version: 2,
    refinement: 'Сделать формулировку измеримой',
  });
  await prepareEvaDocumentChange('change-1', 4, true);
  await publishEvaDocumentChange('change-1', 5);

  expect(mockedGet).toHaveBeenCalledWith(api.evaBusinessDocumentSources, {
    params: { query: 'BR-42' },
    skipErrorNotification: true,
  });
  expect(mockedPost).toHaveBeenNthCalledWith(
    1,
    api.evaBusinessDocumentChanges,
    {
      connector_id: 'connector-1',
      document_id: 'CmfDocument:doc-1',
      change_summary: 'Уточнить цель',
    },
    { skipErrorNotification: true },
  );
  expect(mockedPost).toHaveBeenNthCalledWith(
    2,
    api.evaBusinessDocumentChangeGenerate('change-1'),
    {
      expected_state_version: 2,
      refinement: 'Сделать формулировку измеримой',
    },
    { skipErrorNotification: true },
  );
  expect(mockedPost).toHaveBeenNthCalledWith(
    3,
    api.evaBusinessDocumentChangePrepare('change-1'),
    { expected_state_version: 4, force_overwrite: true },
    { skipErrorNotification: true },
  );
  expect(mockedPost).toHaveBeenNthCalledWith(
    4,
    api.evaBusinessDocumentChangePublish('change-1'),
    { expected_state_version: 5 },
    { skipErrorNotification: true },
  );
});
