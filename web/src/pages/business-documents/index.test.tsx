import {
  approveEvaDocumentChange,
  assignBusinessDocumentOwner,
  BusinessDocumentConflictError,
  checkBusinessDocumentEvaAccess,
  checkBusinessDocumentEvaUpdate,
  createBusinessDocument,
  createEvaChangeFromBusinessDocument,
  deleteBusinessDocument,
  downloadBusinessDocumentExport,
  fetchBusinessDocument,
  fetchBusinessDocumentChangePreview,
  fetchEvaDocumentChange,
  generateEvaDocumentChangeDraft,
  listBusinessDocumentAccessUsers,
  listBusinessDocumentCatalog,
  listBusinessDocumentRevisions,
  listBusinessDocuments,
  prepareEvaDocumentChange,
  publishEvaDocumentChange,
  pullBusinessDocumentFromEva,
  rebindBusinessDocumentToEva,
  submitBusinessDocumentCommand,
  watchBusinessDocumentJobEvents,
} from '@/services/business-document-service';
import { downloadFileFromBlob } from '@/utils/file-util';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import {
  MemoryRouter,
  Route,
  Routes as RouterRoutes,
  useNavigate,
} from 'react-router';
import BusinessDocumentsPage from '.';

const mockUseSystemConfig = jest.fn();

jest.mock('@/hooks/use-system-request', () => ({
  useSystemConfig: () => mockUseSystemConfig(),
}));

type BusinessDocumentProjection = import('./types').BusinessDocumentProjection;
type BusinessDocumentCommandResult =
  import('./types').BusinessDocumentCommandResult;
type BusinessDocumentJobStreamEvent =
  import('./types').BusinessDocumentJobStreamEvent;
type EvaDocumentChange = import('./types').EvaDocumentChange;

jest.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ children }: { children?: string }) => (
    <span data-source-start={0} data-source-end={(children ?? '').length}>
      {(children ?? '')
        .replace(/\*\*(.*?)\*\*/g, '$1')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')}
    </span>
  ),
}));

jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: () => ({ data: { id: 'user-1' }, loading: false }),
  useFetchTenantInfo: () => ({
    data: { tenant_id: 'tenant-1' },
    loading: false,
  }),
}));

jest.mock('remark-gfm', () => ({
  __esModule: true,
  default: () => undefined,
}));

jest.mock('remark-breaks', () => ({
  __esModule: true,
  default: () => undefined,
}));

jest.mock('@/components/diagram-code-block', () => ({
  DiagramCodeBlock: ({ source }: { source: string }) => <pre>{source}</pre>,
}));

jest.mock('@/components/ui/audio-button', () => ({
  AudioButton: ({
    ariaLabel,
    disabled,
    onOk,
    testId,
  }: {
    ariaLabel?: string;
    disabled?: boolean;
    onOk?: (transcript: string) => void;
    testId?: string;
  }) => (
    <button
      type="button"
      aria-label={ariaLabel}
      data-testid={testId}
      disabled={disabled}
      onClick={() => onOk?.('текст из диктовки')}
    >
      Голосовой ввод
    </button>
  ),
}));

jest.mock('@/utils/file-util', () => ({
  downloadFileFromBlob: jest.fn(),
}));

jest.mock('@/services/business-document-service', () => {
  const actual = jest.requireActual('@/services/business-document-service');
  return {
    ...actual,
    createBusinessDocument: jest.fn(),
    checkBusinessDocumentEvaUpdate: jest.fn(),
    checkBusinessDocumentEvaAccess: jest.fn(),
    createEvaChangeFromBusinessDocument: jest.fn(),
    deleteBusinessDocument: jest.fn(),
    downloadBusinessDocumentExport: jest.fn(),
    fetchBusinessDocument: jest.fn(),
    fetchBusinessDocumentChangePreview: jest.fn(),
    listBusinessDocuments: jest.fn(),
    listBusinessDocumentRevisions: jest.fn(),
    listBusinessDocumentAccessUsers: jest.fn(),
    listBusinessDocumentCatalog: jest.fn(),
    assignBusinessDocumentOwner: jest.fn(),
    submitBusinessDocumentCommand: jest.fn(),
    watchBusinessDocumentJobEvents: jest.fn(),
    fetchEvaDocumentChange: jest.fn(),
    generateEvaDocumentChangeDraft: jest.fn(),
    approveEvaDocumentChange: jest.fn(),
    prepareEvaDocumentChange: jest.fn(),
    publishEvaDocumentChange: jest.fn(),
    pullBusinessDocumentFromEva: jest.fn(),
    rebindBusinessDocumentToEva: jest.fn(),
  };
});

const mockedCreate = jest.mocked(createBusinessDocument);
const mockedCheckEvaUpdate = jest.mocked(checkBusinessDocumentEvaUpdate);
const mockedCreateEvaFromDocument = jest.mocked(
  createEvaChangeFromBusinessDocument,
);
const mockedDelete = jest.mocked(deleteBusinessDocument);
const mockedDownloadExport = jest.mocked(downloadBusinessDocumentExport);
const mockedDownloadFileFromBlob = jest.mocked(downloadFileFromBlob);
const mockedFetch = jest.mocked(fetchBusinessDocument);
const mockedFetchPreview = jest.mocked(fetchBusinessDocumentChangePreview);
const mockedList = jest.mocked(listBusinessDocuments);
const mockedListRevisions = jest.mocked(listBusinessDocumentRevisions);
const mockedListAccessUsers = jest.mocked(listBusinessDocumentAccessUsers);
const mockedListCatalog = jest.mocked(listBusinessDocumentCatalog);
const mockedAssignOwner = jest.mocked(assignBusinessDocumentOwner);
const mockedSubmit = jest.mocked(submitBusinessDocumentCommand);
const mockedWatch = jest.mocked(watchBusinessDocumentJobEvents);
const mockedFetchEva = jest.mocked(fetchEvaDocumentChange);
const mockedGenerateEva = jest.mocked(generateEvaDocumentChangeDraft);
const mockedApproveEva = jest.mocked(approveEvaDocumentChange);
const mockedPrepareEva = jest.mocked(prepareEvaDocumentChange);
const mockedPublishEva = jest.mocked(publishEvaDocumentChange);
const mockedPullEvaDocument = jest.mocked(pullBusinessDocumentFromEva);
const mockedRebindEvaDocument = jest.mocked(rebindBusinessDocumentToEva);
const mockedCheckEvaAccess = jest.mocked(checkBusinessDocumentEvaAccess);

const catalogEntry = {
  id: 'L2-99.99.99.99.99',
  title: 'Новый продукт',
  title_en: 'New Product',
  description: 'Разрешённый L5-документ',
  capability_level: 'L5' as const,
  capability_type: 'Core',
  hierarchy: {},
};

const firstSectionText =
  'Повторяемая фраза. Сократить время перевода. Результат измеряется в минутах.';
const secondSectionText =
  'Повторяемая фраза. Текущий процесс состоит из нескольких шагов.';

const projection: BusinessDocumentProjection = {
  document_id: 'doc-1',
  title: 'Переводы одной кнопкой',
  document_type: 'business_requirements',
  state_version: 18,
  lifecycle_state: 'REVIEW',
  operation_state: 'IDLE',
  current_revision: {
    revision_id: 'revision-3',
    revision_number: 3,
    document_ast: {
      schema_version: '1',
      document_type: 'business_requirements',
      template_version: '1.0.0',
      sections: [
        {
          id: '1',
          title: 'Цель',
          blocks: [{ type: 'paragraph', text: firstSectionText }],
        },
        {
          id: '2',
          title: 'Текущий процесс',
          blocks: [{ type: 'paragraph', text: secondSectionText }],
        },
        {
          id: '3',
          title: 'Таблица значений',
          blocks: [
            {
              type: 'table',
              headers: ['Флаг', 'Значение'],
              rows: [
                [true, null],
                [false, 7],
              ],
            },
          ],
        },
      ],
    },
    section_texts: {
      '1': firstSectionText,
      '2': secondSectionText,
      '3': '| Флаг | Значение |\n| --- | --- |\n| True | None |\n| False | 7 |',
    },
    body_markdown: `## 1. Цель\n${firstSectionText}\n\n## 2. Текущий процесс\n${secondSectionText}`,
    content_hash: 'sha256:body',
  },
  active_review_cycle: 2,
  protocol: {
    questions: [
      {
        question_id: 'question-9',
        sequence_number: 9,
        target_section_id: '5.5',
        text: 'Какие события необходимо отслеживать?',
        options: [
          { option_id: 'success', label: 'Только успешные операции' },
          {
            option_id: 'all',
            label: 'Успешные и неуспешные операции',
          },
        ],
        allow_custom_answer: true,
        status: 'OPEN',
      },
    ],
    proposals: [
      {
        proposal_id: 'proposal-12',
        target_section_id: '3.1',
        text: 'Добавить количественную оценку нагрузки',
        rationale: 'Она нужна для нефункциональных требований',
        decision: 'PENDING',
      },
    ],
    comments: [
      {
        comment_id: 'comment-4',
        section_id: '1',
        text: 'Уточнить терминологию',
        anchor_status: 'ANCHORED',
        anchor: {
          revision_id: 'revision-3',
          section_id: '1',
          selected_text: 'Сократить время перевода',
          prefix: 'Повторяемая фраза. ',
          suffix: '. Результат измеряется в минутах.',
          start_offset: firstSectionText.indexOf('Сократить время перевода'),
          end_offset:
            firstSectionText.indexOf('Сократить время перевода') +
            'Сократить время перевода'.length,
        },
        disposition: {
          comment_event_id: 'event-comment-4',
          disposition: 'CONFIRMED_CHANGE',
        },
      },
    ],
  },
  allowed_commands: [
    'ANSWER_QUESTION',
    'DECIDE_PROPOSAL',
    'ACCEPT_PROPOSAL',
    'ADD_COMMENT',
    'APPLY_CHANGES',
  ],
};

const commandResult: BusinessDocumentCommandResult = {
  accepted: true,
  document_id: 'doc-1',
  state_version: 19,
  lifecycle_state: 'REVIEW',
  operation_state: 'ANALYZING',
  job_id: 'job-1',
};

const evaChange: EvaDocumentChange = {
  change_id: 'change-1',
  state_version: 2,
  workflow_state: 'EDITING',
  change_summary: 'Уточнить ожидаемый результат.',
  source: {
    connector_id: 'connector-1',
    project_id: 'project-1',
    document_id: 'CmfDocument:doc-1',
    document_code: 'BR-42',
    document_name: 'Переводы одной кнопкой',
    web_url: 'https://eva.example.com/project/Document/BR-42',
    base_version: '1|version-1|2026-08-26',
    base_content_hash: 'sha256:base',
  },
  base_markdown: '# Требования\n\n## Цель\n\nСтарый текст.',
  draft_markdown: '# Требования\n\n## Цель\n\nНовый текст.',
  draft_content_hash: 'sha256:draft',
  diff: {
    changed: true,
    added_lines: 1,
    removed_lines: 1,
    changed_sections: 1,
    sections: [
      {
        key: 'цель:1',
        title: 'Цель',
        lines: [
          { type: 'context', content: '## Цель' },
          { type: 'removed', content: 'Старый текст.' },
          { type: 'added', content: 'Новый текст.' },
        ],
      },
    ],
  },
  allowed_actions: ['GENERATE_DRAFT', 'APPROVE'],
  events: [
    {
      event_id: 'event-1',
      sequence: 1,
      event_type: 'CHANGE_REQUEST_CREATED',
      actor_id: 'author-1',
      payload: {},
      create_time: 1,
    },
  ],
};

function renderPage(path = '/business-documents/doc-1') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <RouterRoutes>
          <Route
            path="/business-documents"
            element={<BusinessDocumentsPage />}
          />
          <Route
            path="/business-documents/:id"
            element={<BusinessDocumentsPage />}
          />
          <Route
            path="/business-documents/eva/:changeId"
            element={<BusinessDocumentsPage />}
          />
        </RouterRoutes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, client };
}

async function selectCatalogEntry() {
  await screen.findByRole('option', { name: catalogEntry.title });
  const select = await screen.findByRole('combobox', {
    name: 'Название документа',
  });
  fireEvent.change(select, { target: { value: catalogEntry.id } });
  await waitFor(() => expect(select).toHaveValue(catalogEntry.id));
}

function mockTextSelection(
  selectedText: string,
  startSection: HTMLElement,
  endSection: HTMLElement = startSection,
) {
  const startNode = startSection.firstChild ?? startSection;
  const endNode = endSection.firstChild ?? endSection;
  const commonAncestorContainer =
    startSection === endSection
      ? startSection
      : screen.getByTestId('business-document-markdown');
  return jest.spyOn(window, 'getSelection').mockReturnValue({
    rangeCount: 1,
    getRangeAt: () =>
      ({
        commonAncestorContainer,
        startContainer: startNode,
        endContainer: endNode,
      }) as unknown as Range,
    toString: () => selectedText,
  } as unknown as Selection);
}

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

beforeEach(() => {
  mockUseSystemConfig.mockReturnValue({
    config: { visibleSections: ['business_documents', 'document_constructor'] },
    loading: false,
  });
  jest.clearAllMocks();
  mockedWatch.mockResolvedValue(undefined);
  window.localStorage.clear();
  mockedFetch.mockResolvedValue(projection);
  mockedFetchPreview.mockResolvedValue({
    job_id: 'job-preview',
    base_revision_id: 'revision-3',
    state_version: 18,
    sections: [
      {
        section_id: '1',
        title: 'Цель',
        before: firstSectionText,
        after: 'Новая измеримая цель.',
        source_event_ids: ['event-comment-4'],
        sources: [
          {
            event_id: 'event-comment-4',
            kind: 'comment',
            entity_id: 'comment-4',
            label: 'Комментарий автора',
            text: 'Уточнить цель',
          },
        ],
      },
    ],
    acknowledged_no_change_event_ids: [],
  });
  mockedList.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 20,
    access_role: 'AUTHOR_CREATOR',
    capabilities: {
      read: true,
      create: true,
      edit_own: true,
      edit_all: false,
      delete: false,
      assign: false,
    },
  });
  mockedListRevisions.mockResolvedValue([projection.current_revision!]);
  mockedListAccessUsers.mockResolvedValue({ items: [] });
  mockedListCatalog.mockResolvedValue({ items: [catalogEntry], total: 1 });
  mockedAssignOwner.mockResolvedValue(projection);
  mockedFetchEva.mockResolvedValue(evaChange);
  mockedGenerateEva.mockResolvedValue(evaChange);
  mockedApproveEva.mockResolvedValue(evaChange);
  mockedPrepareEva.mockResolvedValue(evaChange);
  mockedPublishEva.mockResolvedValue(evaChange);
  mockedSubmit.mockResolvedValue(commandResult);
  mockedCreate.mockResolvedValue(projection);
  mockedDelete.mockResolvedValue({
    document_id: 'saved-1',
    deleted: true,
    deleted_artifacts: 0,
    storage_cleanup_failures: 0,
  });
  mockedCreateEvaFromDocument.mockResolvedValue(evaChange);
  mockedPullEvaDocument.mockResolvedValue({
    document: projection,
    sync: { changed: false, direction: 'FROM_EVA' },
  });
  mockedCheckEvaUpdate.mockResolvedValue({
    document_id: 'doc-1',
    changed: false,
    direction: 'FROM_EVA',
    can_pull: true,
  });
  mockedCheckEvaAccess.mockResolvedValue({
    state: 'AVAILABLE',
    connector_id: 'connector-1',
  });
});

test('renders a dense read-only workbench from the server projection', async () => {
  renderPage();

  expect(
    await screen.findByTestId('business-document-workbench'),
  ).toBeInTheDocument();
  expect(screen.getByTestId('business-document-pane')).toHaveTextContent(
    'Сократить время перевода',
  );
  expect(screen.getByTestId('business-document-protocol')).toHaveTextContent(
    'Какие события необходимо отслеживать?',
  );
  expect(screen.getByTestId('business-document-question')).toBeInTheDocument();
  expect(screen.getByTestId('business-document-proposal')).toBeInTheDocument();
  expect(screen.getByTestId('business-document-comment')).toBeInTheDocument();
  expect(
    screen.getByTestId('business-document-comment-disposition'),
  ).toHaveTextContent('Подтверждено к правке');
  expect(screen.getByTestId('apply-changes-button')).toHaveTextContent(
    'Применить исправления',
  );
  expect(screen.queryByRole('textbox', { name: 'Документ' })).toBeNull();
  expect(screen.getAllByTestId('business-document-section')[2]).toHaveAttribute(
    'data-section-text',
    '| Флаг | Значение |\n| --- | --- |\n| True | None |\n| False | 7 |',
  );
});

test('allows preparing ready changes while an unanswered question stays visible', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    allowed_commands: ['ANSWER_QUESTION', 'PREPARE_CHANGES'],
  });
  renderPage();

  const prepare = await screen.findByTestId('prepare-changes-button');
  expect(prepare).toBeEnabled();
  expect(screen.getByTestId('business-document-question')).toBeVisible();
  expect(
    screen.getByRole('radio', { name: 'Только успешные операции' }),
  ).toBeEnabled();
  fireEvent.click(prepare);
  await waitFor(() =>
    expect(mockedSubmit).toHaveBeenCalledWith(
      'doc-1',
      expect.objectContaining({
        type: 'PREPARE_CHANGES',
        payload: { base_revision_id: projection.current_revision!.revision_id },
      }),
    ),
  );
});

test('shows an explicit message for an empty document section', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    current_revision: {
      ...projection.current_revision!,
      document_ast: {
        ...projection.current_revision!.document_ast,
        sections: [
          ...projection.current_revision!.document_ast.sections,
          { id: '4', title: 'Ограничения', blocks: [] },
        ],
      },
      section_texts: {
        ...projection.current_revision!.section_texts,
        '4': '',
      },
    },
  });
  renderPage();

  const sections = await screen.findAllByTestId('business-document-section');
  expect(sections[3]).toHaveTextContent('4. Ограничения');
  expect(sections[3]).toHaveTextContent('Требования отсутствуют');
  expect(sections[3]).toHaveAttribute('data-section-text', '');
});

test('uses semantic status colors for questions and proposals', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    protocol: {
      ...projection.protocol,
      questions: [
        ...projection.protocol.questions,
        {
          ...projection.protocol.questions[0],
          question_id: 'question-answered',
          sequence_number: 10,
          status: 'ANSWERED',
          answer: { selected_option_id: 'all' },
        },
        {
          ...projection.protocol.questions[0],
          question_id: 'question-cancelled',
          sequence_number: 11,
          status: 'CANCELLED',
        },
      ],
      proposals: [
        ...projection.protocol.proposals,
        {
          ...projection.protocol.proposals[0],
          proposal_id: 'proposal-accepted',
          decision: 'ACCEPTED',
        },
        {
          ...projection.protocol.proposals[0],
          proposal_id: 'proposal-rejected',
          decision: 'REJECTED',
        },
      ],
    },
  });
  renderPage();

  const questions = await screen.findAllByTestId('business-document-question');
  const progress = screen.getByTestId('business-document-review-progress');
  expect(progress).toHaveTextContent('Ответы1/2');
  expect(progress).toHaveTextContent('Предложения2/3');
  expect(progress).toHaveTextContent('Закрыто: 1');
  expect(
    screen.getByRole('progressbar', { name: 'Ответы на вопросы' }),
  ).toHaveAttribute('aria-valuemax', '2');
  expect(
    screen.getByRole('progressbar', { name: 'Решения по предложениям' }),
  ).toHaveAttribute('aria-valuenow', '2');
  expect(questions.find((item) => item.dataset.status === 'OPEN')).toHaveClass(
    'bg-accent-primary/5',
  );
  expect(
    questions.find((item) => item.dataset.status === 'ANSWERED'),
  ).toHaveClass('bg-state-success/5');
  expect(
    questions.find((item) => item.dataset.status === 'CANCELLED'),
  ).toHaveClass('bg-bg-card/40');
  expect(screen.getByTestId('question-status-question-9')).toHaveTextContent(
    'Требует ответа',
  );
  expect(screen.getByTestId('question-status-question-answered')).toHaveClass(
    'text-state-success',
  );
  expect(
    screen.getByTestId('question-status-question-cancelled'),
  ).toHaveTextContent('Закрыт');

  const proposals = screen.getAllByTestId('business-document-proposal');
  expect(
    proposals.find((item) => item.dataset.status === 'PENDING'),
  ).toHaveClass('bg-state-warning/5');
  expect(
    proposals.find((item) => item.dataset.status === 'ACCEPTED'),
  ).toHaveClass('bg-state-success/5');
  expect(
    proposals.find((item) => item.dataset.status === 'REJECTED'),
  ).toHaveClass('bg-state-error/5');
  expect(
    screen.getByTestId('proposal-status-proposal-accepted'),
  ).toHaveTextContent('Принято');
  expect(screen.getByTestId('proposal-status-proposal-rejected')).toHaveClass(
    'text-state-error',
  );
});

test('renders an undecided proposal as closed history after review completion', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'AGREED',
    allowed_commands: ['START_REVIEW', 'REQUEST_EXPORT', 'ARCHIVE'],
  });
  renderPage();

  const status = await screen.findByTestId('proposal-status-proposal-12');
  expect(
    screen.getByTestId('business-document-review-progress'),
  ).toHaveTextContent('Без решения: 1');
  expect(
    screen.getByRole('progressbar', { name: 'Решения по предложениям' }),
  ).toHaveAttribute('aria-valuenow', '0');
  expect(status).toHaveTextContent('Не принято');
  expect(status).toHaveClass('text-text-disabled');
  expect(screen.getByTestId('closed-proposal-proposal-12')).toHaveTextContent(
    'Решение не было принято до завершения этапа внесения изменений.',
  );
  expect(
    screen.queryByTestId('accept-proposal-proposal-12'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByTestId('reject-proposal-proposal-12'),
  ).not.toBeInTheDocument();
});

test('shows which comments, questions and AI proposals produced each revision', async () => {
  mockedListRevisions.mockResolvedValueOnce([
    {
      ...projection.current_revision!,
      author_id: 'author-4',
      author_name: 'Мария Авторова',
      author_login: 'maria@example.test',
      created_at: 1_788_200_000,
      change_basis: [
        {
          event_id: 'event-draft',
          actor_id: 'ai-worker-1',
          actor_type: 'AI',
          initiated_by_actor_id: 'author-4',
          initiated_by_actor_name: 'Мария Авторова',
          initiated_by_actor_login: 'maria@example.test',
          type: 'INITIAL_DRAFT',
          title: 'Первичный черновик',
          summary: 'Исходная идея документа.',
        },
        {
          event_id: 'event-question',
          actor_id: 'author-2',
          actor_type: 'USER',
          actor_name: 'Иван Редактор',
          actor_login: 'ivan@example.test',
          type: 'QUESTION',
          title: 'Ответ на вопрос',
          summary: 'Как измеряется скорость перевода?',
          details: 'Не более двух минут',
          section_id: '3.1',
        },
        {
          event_id: 'event-comment',
          actor_id: 'author-3',
          actor_type: 'USER',
          actor_name: 'Анна Эксперт',
          actor_login: 'anna@example.test',
          type: 'COMMENT',
          title: 'Комментарий автора',
          summary: 'Добавить негативный сценарий.',
          section_id: '4.3',
        },
        {
          event_id: 'event-proposal',
          type: 'PROPOSAL',
          title: 'Принятое предложение ИИ',
          summary: 'Уточнить критерий успешности.',
          section_id: '5.5',
        },
      ],
    },
  ]);
  renderPage();

  fireEvent.click(
    await screen.findByTestId('business-document-history-toggle'),
  );

  expect(
    await screen.findByText('Как измеряется скорость перевода?'),
  ).toBeVisible();
  expect(screen.getByTestId('business-document-history')).toHaveTextContent(
    'Добавить негативный сценарий.',
  );
  expect(screen.getByTestId('business-document-history')).toHaveTextContent(
    'Принятое предложение ИИ',
  );
  expect(screen.getByTestId('business-document-history')).toHaveTextContent(
    'Изменил: Иван Редактор (ivan@example.test)',
  );
  expect(screen.getByTestId('business-document-history')).toHaveTextContent(
    'Изменил: Анна Эксперт (anna@example.test)',
  );
  expect(screen.getByTestId('business-document-history')).toHaveTextContent(
    'Инициировал: Мария Авторова (maria@example.test)',
  );
  expect(screen.getByTestId('business-document-history')).toHaveTextContent(
    'Исполнитель: ai-worker-1',
  );
  expect(screen.getByTestId('business-document-history')).toHaveTextContent(
    'Автор изменений: Мария Авторова (maria@example.test)',
  );
  expect(mockedListRevisions).toHaveBeenCalledWith('doc-1');
});

test('keeps the section diff available in revision history', async () => {
  mockedListRevisions.mockResolvedValueOnce([
    projection.current_revision!,
    {
      ...projection.current_revision!,
      revision_id: 'revision-2',
      revision_number: 2,
      section_texts: {
        ...projection.current_revision!.section_texts,
        '1': 'Старая цель.',
      },
    },
  ]);
  renderPage();
  fireEvent.click(
    await screen.findByTestId('business-document-history-toggle'),
  );
  const diff = await screen.findByTestId('business-document-revision-diff');
  expect(diff).toHaveTextContent('Изменения ревизии 3');
  expect(diff).toHaveTextContent('Старая цель.');
  expect(diff).toHaveTextContent(firstSectionText);
  expect(screen.getAllByTestId('business-document-section')[0]).toHaveClass(
    'ring-1',
  );
});

test('shows evidence changes in history even when section text is unchanged', async () => {
  const current = {
    ...projection.current_revision!,
    document_ast: {
      ...projection.current_revision!.document_ast,
      sections: projection.current_revision!.document_ast.sections.map(
        (section) =>
          section.id === '1'
            ? {
                ...section,
                evidence_refs: ['ragflow://dataset/d/document/new/chunk/1'],
              }
            : section,
      ),
    },
  };
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    current_revision: current,
  });
  mockedListRevisions.mockResolvedValueOnce([
    current,
    {
      ...projection.current_revision!,
      revision_id: 'revision-2',
      revision_number: 2,
    },
  ]);
  renderPage();
  fireEvent.click(
    await screen.findByTestId('business-document-history-toggle'),
  );
  const diff = await screen.findByTestId('business-document-revision-diff');
  expect(diff).toHaveTextContent('Источники до');
  expect(diff).toHaveTextContent('ragflow://dataset/d/document/new/chunk/1');
  expect(screen.getAllByTestId('business-document-section')[0]).toHaveClass(
    'ring-1',
  );
});

test('offers personal-token EVA actions for a verified page binding', async () => {
  const linked = {
    ...projection,
    lifecycle_state: 'AGREED' as const,
    operation_state: 'IDLE' as const,
    eva_binding: {
      page_url: 'https://eva.example.com/project/Document/BR-42',
      status: 'CONNECTED' as const,
      capabilities: [
        'OPEN' as const,
        'PULL_FROM_EVA' as const,
        'CREATE_EVA_CHANGE' as const,
      ],
      connector_id: 'connector-1',
      document_id: 'eva-document-1',
      document_code: 'BR-42',
      document_name: 'Переводы одной кнопкой',
    },
  };
  mockedFetch.mockResolvedValueOnce(linked);
  mockedCheckEvaUpdate.mockResolvedValueOnce({
    document_id: 'doc-1',
    changed: true,
    direction: 'FROM_EVA',
    remote_version: '2',
    baseline_version: '1',
    can_pull: true,
  });
  mockedPullEvaDocument.mockResolvedValueOnce({
    document: { ...linked, lifecycle_state: 'REVIEW' },
    sync: { changed: true, direction: 'FROM_EVA', event_id: 'eva-pull-1' },
  });
  renderPage();

  fireEvent.click(
    await screen.findByTestId('business-document-details-toggle'),
  );
  expect(
    await screen.findByTestId('business-document-eva-binding'),
  ).toHaveTextContent('Переводы одной кнопкой');
  expect(
    await screen.findByRole('button', {
      name: 'Сохранить текущий вариант в EVA',
    }),
  ).toBeVisible();
  expect(
    screen.getByRole('button', {
      name: 'Загрузить обновление из EVA',
    }),
  ).toBeVisible();
  expect(screen.getByTestId('eva-update-available')).toHaveTextContent(
    'В EVA есть более новая версия документа',
  );
  fireEvent.click(screen.getByTestId('pull-business-document-from-eva'));
  await waitFor(() =>
    expect(mockedPullEvaDocument).toHaveBeenCalledWith('doc-1', 18),
  );
  expect(
    await screen.findByText(
      'Новая версия EVA добавлена в цикл внесения изменений. Запустите анализ замечаний.',
    ),
  ).toBeVisible();
});

test('shows one token warning and disables EVA write without a personal token', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'AGREED',
    eva_binding: {
      page_url: 'https://eva.example.com/project/Document/BR-42',
      status: 'CONNECTED',
      capabilities: ['OPEN', 'PULL_FROM_EVA', 'CREATE_EVA_CHANGE'],
      connector_id: 'connector-1',
      document_id: 'eva-document-1',
    },
  });
  mockedCheckEvaAccess.mockResolvedValueOnce({
    state: 'MISSING',
    connector_id: 'connector-1',
  });

  renderPage();

  fireEvent.click(
    await screen.findByTestId('business-document-details-toggle'),
  );
  expect(
    await screen.findByTestId('business-document-eva-binding'),
  ).toBeVisible();
  await waitFor(() => expect(mockedCheckEvaAccess).toHaveBeenCalled());
  expect(
    screen.getByRole('button', {
      name: 'Сохранить текущий вариант в EVA',
    }),
  ).toBeDisabled();
  expect(screen.getAllByTestId('eva-access-notice')).toHaveLength(1);
  expect(screen.queryByTestId('eva-access-indicator')).not.toBeInTheDocument();
  expect(screen.getByTestId('eva-token-settings-link')).toHaveAttribute(
    'target',
    '_blank',
  );
  expect(
    screen.queryByRole('button', {
      name: 'Загрузить обновление из EVA',
    }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Закрыть предупреждение EVA' }),
  );
  expect(screen.queryByTestId('eva-access-notice')).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', {
      name: 'Сохранить текущий вариант в EVA',
    }),
  ).toBeDisabled();
});

test('reconnects a linked EVA page before offering its newer version', async () => {
  const linkOnly = {
    ...projection,
    lifecycle_state: 'AGREED' as const,
    eva_binding: {
      page_url: 'https://eva.example.com/project/Document/BR-42',
      status: 'LINK_ONLY' as const,
      capabilities: ['OPEN' as const],
      document_code: 'BR-42',
    },
  };
  const connected = {
    ...linkOnly,
    state_version: 19,
    eva_binding: {
      ...linkOnly.eva_binding,
      status: 'CONNECTED' as const,
      capabilities: [
        'OPEN' as const,
        'PULL_FROM_EVA' as const,
        'CREATE_EVA_CHANGE' as const,
      ],
      connector_id: 'connector-1',
      document_id: 'eva-document-1',
    },
  };
  mockedFetch.mockResolvedValueOnce(linkOnly);
  mockedRebindEvaDocument.mockResolvedValueOnce(connected);
  mockedCheckEvaUpdate.mockResolvedValueOnce({
    document_id: 'doc-1',
    changed: true,
    direction: 'FROM_EVA',
    can_pull: true,
  });
  mockedPullEvaDocument.mockResolvedValueOnce({
    document: connected,
    sync: { changed: false, direction: 'FROM_EVA' },
  });

  renderPage();

  fireEvent.click(
    await screen.findByTestId('business-document-details-toggle'),
  );
  fireEvent.click(await screen.findByTestId('rebind-business-document-to-eva'));
  await waitFor(() =>
    expect(mockedRebindEvaDocument).toHaveBeenCalledWith('doc-1', 18),
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Загрузить обновление из EVA',
    }),
  );
  await waitFor(() =>
    expect(mockedPullEvaDocument).toHaveBeenCalledWith('doc-1', 19),
  );
});

test('attaches an existing assigned document without creating a new revision', async () => {
  const connected = {
    ...projection,
    state_version: 19,
    eva_binding: {
      page_url: 'https://eva.example.com/project/Document/BR-42',
      status: 'CONNECTED' as const,
      capabilities: [
        'OPEN' as const,
        'PULL_FROM_EVA' as const,
        'CREATE_EVA_CHANGE' as const,
      ],
      document_name: 'Корпоративные новости',
    },
  };
  mockedRebindEvaDocument.mockResolvedValueOnce(connected);
  renderPage();
  const input = await screen.findByRole('textbox', {
    name: 'Ссылка на страницу EVA',
  });
  fireEvent.change(input, {
    target: { value: connected.eva_binding.page_url },
  });
  const bind = screen.getByRole('button', { name: 'Связать с EVA' });
  await waitFor(() => expect(bind).toBeEnabled());
  fireEvent.click(bind);
  await waitFor(() =>
    expect(mockedRebindEvaDocument).toHaveBeenCalledWith(
      'doc-1',
      18,
      connected.eva_binding.page_url,
    ),
  );
  expect(
    await screen.findByTestId('business-document-eva-binding'),
  ).toHaveTextContent('Корпоративные новости');
  expect(mockedCreate).not.toHaveBeenCalled();
  expect(mockedSubmit).not.toHaveBeenCalled();
});

test('does not offer initial EVA binding to a read-only user', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    permissions: { read: true, edit: false, delete: false },
  });
  renderPage();
  await screen.findByTestId('business-document-workbench');
  expect(
    screen.queryByRole('button', { name: 'Связать с EVA' }),
  ).not.toBeInTheDocument();
});

test('reconnects a link-only EVA page after a connector becomes available', async () => {
  const linkOnly = {
    ...projection,
    eva_binding: {
      page_url: 'http://host.docker.internal:8084//project/Document/DOC-001883',
      status: 'LINK_ONLY' as const,
      capabilities: ['OPEN' as const],
      document_code: 'DOC-001883',
    },
  };
  const connected = {
    ...linkOnly,
    state_version: 19,
    eva_binding: {
      page_url: 'https://eva.example.com/project/Document/DOC-001883',
      status: 'CONNECTED' as const,
      capabilities: [
        'OPEN' as const,
        'PULL_FROM_EVA' as const,
        'CREATE_EVA_CHANGE' as const,
      ],
      connector_id: 'connector-business-documents',
      document_id: 'CmfDocument:doc-1',
      document_code: 'DOC-001883',
      document_name: 'Документ1',
    },
  };
  mockedFetch.mockResolvedValueOnce(linkOnly);
  mockedRebindEvaDocument.mockResolvedValueOnce(connected);
  renderPage();

  fireEvent.click(
    await screen.findByTestId('business-document-details-toggle'),
  );
  expect(
    await screen.findByText('Только ссылка — доступный коннектор не найден'),
  ).toBeVisible();
  fireEvent.click(screen.getByTestId('rebind-business-document-to-eva'));

  await waitFor(() =>
    expect(mockedRebindEvaDocument).toHaveBeenCalledWith('doc-1', 18),
  );
  expect(
    await screen.findByText('Страница EVA подключена. Синхронизация доступна.'),
  ).toBeVisible();
  expect(screen.getByTestId('business-document-eva-binding')).toHaveTextContent(
    'Документ1',
  );
});

test('does not check or offer EVA pull until the first local revision exists', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'INTAKE',
    current_revision: null,
    eva_binding: {
      page_url: 'https://eva.example.com/project/Document/BR-42',
      status: 'CONNECTED',
      capabilities: ['OPEN', 'PULL_FROM_EVA'],
      connector_id: 'connector-1',
      document_id: 'eva-document-1',
    },
  });
  renderPage();

  fireEvent.click(
    await screen.findByTestId('business-document-details-toggle'),
  );
  expect(
    await screen.findByTestId('business-document-eva-binding'),
  ).toBeVisible();
  expect(
    screen.queryByTestId('pull-business-document-from-eva'),
  ).not.toBeInTheDocument();
  expect(mockedCheckEvaUpdate).not.toHaveBeenCalled();
  expect(mockedPullEvaDocument).not.toHaveBeenCalled();
});

test.each(['REVIEW', 'AGREED'] as const)(
  'opens the guarded EVA publication workflow from a %s revision',
  async (lifecycleState) => {
    mockedFetch.mockResolvedValueOnce({
      ...projection,
      lifecycle_state: lifecycleState,
      operation_state: 'IDLE',
      eva_binding: {
        page_url: 'https://eva.example.com/project/Document/BR-42',
        status: 'CONNECTED',
        capabilities: ['OPEN', 'CREATE_EVA_CHANGE'],
        connector_id: 'connector-1',
        document_id: 'eva-document-1',
      },
    });
    renderPage();

    fireEvent.click(
      await screen.findByTestId('business-document-details-toggle'),
    );
    fireEvent.click(await screen.findByTestId('push-business-document-to-eva'));
    await waitFor(() =>
      expect(mockedCreateEvaFromDocument).toHaveBeenCalledWith('doc-1', 18),
    );
    expect(await screen.findByTestId('eva-change-workbench')).toBeVisible();
  },
);

test('allows saving a review revision into an empty EVA page without an error or pull action', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'REVIEW',
    operation_state: 'IDLE',
    eva_binding: {
      page_url: 'https://eva.example.com/project/Document/BR-42',
      status: 'CONNECTED',
      capabilities: ['OPEN', 'PULL_FROM_EVA', 'CREATE_EVA_CHANGE'],
      connector_id: 'connector-1',
      document_id: 'eva-document-1',
    },
  });
  mockedCheckEvaUpdate.mockResolvedValueOnce({
    document_id: 'doc-1',
    changed: false,
    direction: 'FROM_EVA',
    can_pull: false,
  });
  renderPage();
  fireEvent.click(
    await screen.findByTestId('business-document-details-toggle'),
  );
  await waitFor(() =>
    expect(mockedCheckEvaUpdate).toHaveBeenCalledWith('doc-1'),
  );
  const save = await screen.findByTestId('push-business-document-to-eva');
  expect(save).toBeEnabled();
  expect(
    screen.queryByTestId('pull-business-document-from-eva'),
  ).not.toBeInTheDocument();
  expect(screen.queryByText('Ошибка EVA')).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Не удалось проверить обновления EVA/),
  ).not.toBeInTheDocument();
  fireEvent.click(save);
  await waitFor(() =>
    expect(mockedCreateEvaFromDocument).toHaveBeenCalledWith('doc-1', 18),
  );
  expect(await screen.findByTestId('eva-change-workbench')).toBeVisible();
});

test('marks a preserved comment when its anchor belongs to an older revision', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    protocol: {
      ...projection.protocol,
      comments: projection.protocol.comments.map((comment) => ({
        ...comment,
        anchor_status: 'ORPHANED' as const,
        revision_id: 'revision-2',
      })),
    },
  });
  renderPage();

  expect(
    await screen.findByTestId('business-document-orphaned-anchor'),
  ).toHaveTextContent('Фрагмент относится к предыдущей ревизии');
  expect(screen.getByTestId('business-document-comment')).toHaveTextContent(
    'Уточнить терминологию',
  );
});

test('shows all agent comment dispositions as read-only protocol status', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    protocol: {
      ...projection.protocol,
      comments: [
        {
          ...projection.protocol.comments[0],
          comment_id: 'comment-needs-question',
          disposition: {
            comment_event_id: 'event-needs-question',
            disposition: 'NEEDS_QUESTION',
            question_id: 'question-clarification',
            question_semantic_tag: 'scope.clarification',
          },
        },
        {
          ...projection.protocol.comments[0],
          comment_id: 'comment-no-change',
          disposition: {
            comment_event_id: 'event-no-change',
            disposition: 'NO_CHANGE',
          },
        },
      ],
    },
  });
  renderPage();

  const statuses = await screen.findAllByTestId(
    'business-document-comment-disposition',
  );
  expect(statuses.map((status) => status.textContent)).toEqual([
    'Требует уточнения',
    'Без изменения',
  ]);
  statuses.forEach((status) =>
    expect(status.querySelector('button')).toBeNull(),
  );
});

test('downloads completed exports through the authenticated request client', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    latest_exports: [
      {
        artifact_id: 'artifact-md-r2',
        revision_id: 'revision-2',
        revision_number: 2,
        format: 'MARKDOWN',
        filename: 'requirements_r2.md',
        mime_type: 'text/markdown; charset=utf-8',
        size: 512,
        content_hash: 'sha256:artifact',
        create_time: 1_787_695_200,
      },
    ],
  });
  renderPage();

  const exports = await screen.findByTestId('business-document-exports');
  const download = screen.getByRole('link', { name: /Markdown\s+r2/ });
  expect(exports).toContainElement(download);
  expect(download).toHaveAttribute(
    'href',
    '/api/v1/business-documents/doc-1/exports/artifact-md-r2/download',
  );
  expect(download).toHaveAttribute('download', 'requirements_r2.md');
  expect(download).not.toHaveTextContent('r3');

  const blob = new Blob(['# requirements'], { type: 'text/markdown' });
  mockedDownloadExport.mockResolvedValueOnce(blob);
  fireEvent.click(download);
  await waitFor(() =>
    expect(mockedDownloadExport).toHaveBeenCalledWith(
      'doc-1',
      'artifact-md-r2',
    ),
  );
  expect(mockedDownloadFileFromBlob).toHaveBeenCalledWith(
    blob,
    'requirements_r2.md',
  );
});

test('does not expose DOCX export actions or completed DOCX artifacts', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'AGREED',
    allowed_commands: ['REQUEST_EXPORT'],
    latest_exports: [
      {
        artifact_id: 'artifact-docx-r3',
        revision_id: 'revision-3',
        revision_number: 3,
        format: 'DOCX',
        filename: 'requirements_r3.docx',
        mime_type:
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        size: 1024,
        content_hash: 'sha256:docx-artifact',
        create_time: 1_787_695_200,
      },
    ],
  });
  renderPage();

  expect(
    await screen.findByRole('button', { name: 'Создать Markdown' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Создать HTML для EvaWiki' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'DOCX' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText('Готовые файлы:')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /Word/ })).not.toBeInTheDocument();
});

test('keeps all agreed-state actions in a wrapping mobile header', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'AGREED',
    allowed_commands: ['START_REVIEW', 'REQUEST_EXPORT', 'ARCHIVE'],
  });
  renderPage();

  const workbench = await screen.findByTestId('business-document-workbench');
  const actions = screen.getByTestId('business-document-actions');
  const title = screen.getByRole('heading', {
    name: 'Переводы одной кнопкой',
  });

  expect(workbench).toHaveClass('min-w-0');
  expect(actions).toHaveClass('w-full', 'min-w-0', 'flex-wrap');
  expect(actions).toHaveClass('sm:w-auto');
  expect(title).toHaveClass('break-words', 'sm:truncate');
  expect(
    screen.getByRole('button', { name: 'Начать вносить изменения' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Создать Markdown' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Создать HTML для EvaWiki' }),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'В архив' })).toBeInTheDocument();
});

test('gates every interaction using allowed_commands', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    allowed_commands: [],
  });
  renderPage();

  expect(
    await screen.findByTestId('answer-question-question-9'),
  ).toBeDisabled();
  expect(screen.getByTestId('accept-proposal-proposal-12')).toBeDisabled();
  expect(screen.getByTestId('reject-proposal-proposal-12')).toBeDisabled();
  expect(screen.getByRole('textbox', { name: 'Комментарий' })).toBeDisabled();
  expect(screen.queryByTestId('apply-changes-button')).toBeNull();
});

test('submits a structured answer and current state version', async () => {
  renderPage();

  fireEvent.click(
    await screen.findByRole('radio', {
      name: 'Успешные и неуспешные операции',
    }),
  );
  fireEvent.click(screen.getByTestId('answer-question-question-9'));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenCalledWith(
    'doc-1',
    expect.objectContaining({
      schema_version: '1',
      expected_state_version: 18,
      type: 'ANSWER_QUESTION',
      payload: {
        question_id: 'question-9',
        selected_option_id: 'all',
        custom_answer: null,
      },
    }),
  );
});

test('lets the author collapse and reopen an individual question', async () => {
  renderPage();

  const toggle = await screen.findByTestId('toggle-question-question-9');
  expect(toggle).toHaveAttribute('aria-expanded', 'true');

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'false');

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
});

test('collapses an answered question and focuses the next unanswered one', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    protocol: {
      ...projection.protocol,
      questions: [
        ...projection.protocol.questions,
        {
          question_id: 'question-10',
          sequence_number: 10,
          target_section_id: '5.6',
          text: 'Как долго хранить историю операций?',
          options: [
            { option_id: 'month', label: 'Один месяц' },
            { option_id: 'year', label: 'Один год' },
          ],
          allow_custom_answer: false,
          status: 'OPEN',
        },
      ],
    },
  });
  renderPage();

  const currentToggle = await screen.findByTestId('toggle-question-question-9');
  const nextToggle = screen.getByTestId('toggle-question-question-10');
  const scrollContainer = screen.getByTestId(
    'business-document-protocol-scroll',
  );
  const scrollIntoView = jest.fn();
  nextToggle.scrollIntoView = scrollIntoView;
  jest.spyOn(nextToggle, 'getBoundingClientRect').mockReturnValue({
    bottom: 540,
    height: 40,
    left: 0,
    right: 100,
    top: 500,
    width: 100,
    x: 0,
    y: 500,
    toJSON: () => ({}),
  });
  jest.spyOn(scrollContainer, 'getBoundingClientRect').mockReturnValue({
    bottom: 300,
    height: 300,
    left: 0,
    right: 430,
    top: 0,
    width: 430,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });

  expect(currentToggle).toHaveAttribute('aria-expanded', 'true');
  expect(nextToggle).toHaveAttribute('aria-expanded', 'false');

  fireEvent.click(
    screen.getByRole('radio', {
      name: 'Успешные и неуспешные операции',
    }),
  );
  fireEvent.click(screen.getByTestId('answer-question-question-9'));

  await waitFor(() => {
    expect(currentToggle).toHaveAttribute('aria-expanded', 'false');
    expect(nextToggle).toHaveAttribute('aria-expanded', 'true');
    expect(nextToggle).toHaveFocus();
  });
  expect(scrollIntoView).toHaveBeenCalledWith({
    behavior: 'smooth',
    block: 'nearest',
  });
});

test('records proposal decisions and author comments as commands', async () => {
  renderPage();

  fireEvent.click(await screen.findByTestId('accept-proposal-proposal-12'));
  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenLastCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'ACCEPT_PROPOSAL',
      payload: { proposal_id: 'proposal-12' },
    }),
  );

  await waitFor(() =>
    expect(screen.getByRole('textbox', { name: 'Комментарий' })).toBeEnabled(),
  );
  expect(
    screen.getByTestId('business-document-comment-scope-selector'),
  ).toHaveTextContent('весь документ');
  expect(screen.getByRole('textbox', { name: 'Комментарий' })).toHaveAttribute(
    'placeholder',
    'Комментарий ко всему документу',
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий' }), {
    target: { value: 'Добавить метрику времени ответа' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(2));
  expect(mockedSubmit).toHaveBeenLastCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'ADD_COMMENT',
      payload: {
        text: 'Добавить метрику времени ответа',
        revision_id: 'revision-3',
        section_id: null,
        anchor: null,
      },
    }),
  );
});

test('binds a comment to text selected in the current revision', async () => {
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const [firstSection] = await screen.findAllByTestId(
    'business-document-section',
  );
  const selectionSpy = mockTextSelection(
    'Сократить время перевода',
    firstSection,
  );
  const startOffset = firstSectionText.indexOf('Сократить время перевода');
  const endOffset = startOffset + 'Сократить время перевода'.length;

  fireEvent.mouseUp(markdown);
  expect(
    screen.getByTestId('business-document-comment-scope-selector'),
  ).toHaveTextContent('выделенный фрагмент · § 1');
  expect(
    screen.getByTestId('business-document-comment-composer'),
  ).toHaveTextContent('Сократить время перевода');
  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий' }), {
    target: { value: 'Уточнить измеримый результат' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'ADD_COMMENT',
      payload: {
        text: 'Уточнить измеримый результат',
        revision_id: 'revision-3',
        section_id: '1',
        anchor: {
          revision_id: 'revision-3',
          section_id: '1',
          selected_text: 'Сократить время перевода',
          prefix: firstSectionText.slice(0, startOffset),
          suffix: firstSectionText.slice(endOffset, endOffset + 64),
          start_offset: startOffset,
          end_offset: endOffset,
        },
      },
    }),
  );
  selectionSpy.mockRestore();
});

test('maps browser-normalized whitespace back to the canonical section text', async () => {
  const canonicalText = 'Правило адреса:\n  Группа  А';
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    current_revision: {
      ...projection.current_revision!,
      document_ast: {
        ...projection.current_revision!.document_ast,
        sections: [
          {
            id: '4',
            title: 'Логика',
            blocks: [{ type: 'paragraph', text: canonicalText }],
          },
        ],
      },
      section_texts: { '4': canonicalText },
      body_markdown: `## 4. Логика\n${canonicalText}`,
    },
  });
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const [section] = await screen.findAllByTestId('business-document-section');
  const selectionSpy = mockTextSelection('Правило адреса: Группа А', section);

  fireEvent.mouseUp(markdown);
  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий' }), {
    target: { value: 'Уточнить правило' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit.mock.calls[0][1]).toEqual(
    expect.objectContaining({
      payload: expect.objectContaining({
        anchor: expect.objectContaining({
          selected_text: canonicalText,
          start_offset: 0,
          end_offset: canonicalText.length,
        }),
      }),
    }),
  );
  selectionSpy.mockRestore();
});

test('maps rendered Markdown text back to canonical source offsets', async () => {
  const canonicalText = 'Правило **адреса** и [имя](https://example.test)';
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    current_revision: {
      ...projection.current_revision!,
      document_ast: {
        ...projection.current_revision!.document_ast,
        sections: [
          {
            id: '4',
            title: 'Логика',
            blocks: [{ type: 'paragraph', text: canonicalText }],
          },
        ],
      },
      section_texts: { '4': canonicalText },
      body_markdown: `## 4. Логика\n${canonicalText}`,
    },
  });
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const [section] = await screen.findAllByTestId('business-document-section');
  const selectionSpy = mockTextSelection('имя', section);

  fireEvent.mouseUp(markdown);
  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий' }), {
    target: { value: 'Уточнить имя' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  const startOffset = canonicalText.indexOf('имя');
  expect(mockedSubmit.mock.calls[0][1]).toEqual(
    expect.objectContaining({
      payload: expect.objectContaining({
        anchor: expect.objectContaining({
          selected_text: 'имя',
          start_offset: startOffset,
          end_offset: startOffset + 'имя'.length,
        }),
      }),
    }),
  );
  selectionSpy.mockRestore();
});

test('keeps the prompt editable while work is running and saves it for later', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    operation_state: 'ANALYZING_REVIEW',
    latest_job: {
      job_id: 'job-running',
      job_type: 'ASSESS_REVIEW',
      status: 'RUNNING',
      attempt: 1,
      max_attempts: 3,
      progress_stage: 'MODEL_REQUEST',
      progress_message: 'Анализируем замечания',
    },
  });
  renderPage();

  const comment = await screen.findByRole('textbox', { name: 'Комментарий' });
  expect(comment).toBeEnabled();
  fireEvent.change(comment, {
    target: { value: 'Добавить это в следующей версии' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Сохранить промпт на будущее' }),
  );

  expect(
    screen.getByTestId('business-document-saved-prompts'),
  ).toHaveTextContent('Добавить это в следующей версии');
  expect(
    screen.getByRole('button', { name: 'Добавить комментарий' }),
  ).toBeDisabled();
  expect(
    window.localStorage.getItem(
      'ragflow.business-documents.saved-prompts.tenant-1.user-1.doc-1',
    ),
  ).toContain('Добавить это в следующей версии');
  expect(
    window.localStorage.getItem(
      'ragflow.business-documents.saved-prompts.doc-1',
    ),
  ).toBeNull();
});

test('resizes the discussion rail with the keyboard and remembers the width', async () => {
  renderPage();
  const resizer = await screen.findByRole('separator', {
    name: 'Изменить ширину обсуждения',
  });

  expect(resizer).toHaveAttribute('aria-valuenow', '430');
  fireEvent.keyDown(resizer, { key: 'ArrowLeft' });

  expect(resizer).toHaveAttribute('aria-valuenow', '454');
  expect(
    window.localStorage.getItem(
      'ragflow.business-documents.protocol-pane-width',
    ),
  ).toBe('454');

  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    value: 800,
  });
  fireEvent(window, new Event('resize'));
  expect(resizer).toHaveAttribute('aria-valuenow', '360');
});

test('labels a general protocol comment as applying to the whole document', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    protocol: {
      ...projection.protocol,
      comments: [
        {
          comment_id: 'comment-general',
          section_id: null,
          text: 'Унифицировать терминологию во всём документе',
          anchor_status: 'GENERAL',
          anchor: null,
        },
      ],
    },
  });
  renderPage();

  expect(
    (await screen.findAllByTestId('business-document-comment-scope'))[0],
  ).toHaveTextContent('Весь документ');
});

test('preserves the comment draft and anchor when submission fails', async () => {
  mockedSubmit.mockRejectedValueOnce(new Error('network down'));
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const [firstSection] = await screen.findAllByTestId(
    'business-document-section',
  );
  const selectionSpy = mockTextSelection(
    'Сократить время перевода',
    firstSection,
  );
  fireEvent.mouseUp(markdown);

  const comment = screen.getByRole('textbox', { name: 'Комментарий' });
  fireEvent.change(comment, {
    target: { value: 'Не потерять этот комментарий' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  expect(
    await screen.findByTestId('business-document-command-error'),
  ).toHaveTextContent('network down');
  fireEvent.click(
    screen.getByRole('button', { name: 'Закрыть сообщение об ошибке' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByTestId('business-document-command-error'),
    ).not.toBeInTheDocument(),
  );
  expect(comment).toHaveValue('Не потерять этот комментарий');
  expect(
    screen.getByTestId('business-document-comment-composer'),
  ).toHaveTextContent('Сократить время перевода');
  selectionSpy.mockRestore();
});

test('anchors duplicate text to the selected canonical section', async () => {
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const sections = await screen.findAllByTestId('business-document-section');
  const selectionSpy = mockTextSelection('Повторяемая фраза', sections[1]);
  fireEvent.mouseUp(markdown);

  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий' }), {
    target: { value: 'Комментарий ко второму разделу' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  const startOffset = secondSectionText.indexOf('Повторяемая фраза');
  const endOffset = startOffset + 'Повторяемая фраза'.length;
  expect(mockedSubmit).toHaveBeenCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'ADD_COMMENT',
      payload: {
        text: 'Комментарий ко второму разделу',
        revision_id: 'revision-3',
        section_id: '2',
        anchor: {
          revision_id: 'revision-3',
          section_id: '2',
          selected_text: 'Повторяемая фраза',
          prefix: '',
          suffix: secondSectionText.slice(endOffset, endOffset + 64),
          start_offset: startOffset,
          end_offset: endOffset,
        },
      },
    }),
  );
  selectionSpy.mockRestore();
});

test('rejects a cross-section selection and submits a general comment', async () => {
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const sections = await screen.findAllByTestId('business-document-section');
  const selectionSpy = mockTextSelection(
    'Результат измеряется в минутах. Повторяемая фраза',
    sections[0],
    sections[1],
  );
  fireEvent.mouseUp(markdown);

  expect(
    screen.getByTestId('business-document-selection-error'),
  ).toHaveTextContent('Выделите фрагмент внутри одного раздела');
  expect(
    screen.getByTestId('business-document-comment-composer'),
  ).not.toHaveTextContent('Результат измеряется');

  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий' }), {
    target: { value: 'Общий комментарий после ошибки выделения' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'ADD_COMMENT',
      payload: {
        text: 'Общий комментарий после ошибки выделения',
        revision_id: 'revision-3',
        section_id: null,
        anchor: null,
      },
    }),
  );
  selectionSpy.mockRestore();
});

test('rejects text that is ambiguous inside its canonical section', async () => {
  const repeatedText = 'Одинаковый фрагмент. Одинаковый фрагмент.';
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    current_revision: {
      ...projection.current_revision!,
      document_ast: {
        ...projection.current_revision!.document_ast,
        sections: [
          {
            id: 'ambiguous',
            title: 'Повторы',
            blocks: [{ type: 'paragraph', text: repeatedText }],
          },
        ],
      },
      section_texts: { ambiguous: repeatedText },
      body_markdown: `## ambiguous. Повторы\n${repeatedText}`,
    },
  });
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const [section] = await screen.findAllByTestId('business-document-section');
  const selectionSpy = mockTextSelection('Одинаковый фрагмент', section);
  fireEvent.mouseUp(markdown);

  expect(
    screen.getByTestId('business-document-selection-error'),
  ).toHaveTextContent('Фрагмент повторяется в разделе');
  expect(
    screen.getByTestId('business-document-comment-composer'),
  ).not.toHaveTextContent('Одинаковый фрагмент');
  selectionSpy.mockRestore();
});

test('keeps emoji context windows within valid UTF-16 boundaries', async () => {
  const canonicalText = `${'A'.repeat(10)}😀${'B'.repeat(63)}TARGET${'C'.repeat(63)}😀tail`;
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    current_revision: {
      ...projection.current_revision!,
      document_ast: {
        ...projection.current_revision!.document_ast,
        sections: [
          {
            id: 'emoji',
            title: 'Составные символы',
            blocks: [{ type: 'paragraph', text: canonicalText }],
          },
        ],
      },
      section_texts: { emoji: canonicalText },
      body_markdown: `## emoji. Составные символы\n${canonicalText}`,
    },
  });
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const [section] = await screen.findAllByTestId('business-document-section');
  const selectionSpy = mockTextSelection('TARGET', section);
  fireEvent.mouseUp(markdown);

  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий' }), {
    target: { value: 'Проверить границы контекста' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  const payload = mockedSubmit.mock.calls[0][1].payload as {
    anchor: { prefix: string; suffix: string };
  };
  expect(payload.anchor.prefix).toBe('B'.repeat(63));
  expect(payload.anchor.suffix).toBe('C'.repeat(63));
  expect(payload.anchor.prefix).not.toContain('\ud83d');
  expect(payload.anchor.suffix).not.toContain('\ud83d');
  selectionSpy.mockRestore();
});

test('uses server-projected section text for exponent-float anchor parity', async () => {
  const canonicalText = '| Порог |\n| --- |\n| 1e-07 |';
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    current_revision: {
      ...projection.current_revision!,
      document_ast: {
        ...projection.current_revision!.document_ast,
        sections: [
          {
            id: 'numeric',
            title: 'Порог',
            blocks: [
              {
                type: 'table',
                headers: ['Порог'],
                rows: [[1e-7]],
              },
            ],
          },
        ],
      },
      section_texts: { numeric: canonicalText },
      body_markdown: `## numeric. Порог\n${canonicalText}`,
    },
  });
  renderPage();
  const markdown = await screen.findByTestId('business-document-markdown');
  const [section] = await screen.findAllByTestId('business-document-section');
  expect(section).toHaveAttribute('data-section-text', canonicalText);
  expect(section).toHaveTextContent('1e-07');

  const selectionSpy = mockTextSelection('1e-07', section);
  fireEvent.mouseUp(markdown);
  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий' }), {
    target: { value: 'Сохранить канонический формат числа' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить комментарий' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  const startOffset = canonicalText.indexOf('1e-07');
  expect(mockedSubmit.mock.calls[0][1]).toEqual(
    expect.objectContaining({
      type: 'ADD_COMMENT',
      payload: {
        text: 'Сохранить канонический формат числа',
        revision_id: 'revision-3',
        section_id: 'numeric',
        anchor: {
          revision_id: 'revision-3',
          section_id: 'numeric',
          selected_text: '1e-07',
          prefix: canonicalText.slice(0, startOffset),
          suffix: canonicalText.slice(startOffset + '1e-07'.length),
          start_offset: startOffset,
          end_offset: startOffset + '1e-07'.length,
        },
      },
    }),
  );
  selectionSpy.mockRestore();
});

test('shows a recoverable conflict instead of applying stale intent', async () => {
  mockedSubmit.mockRejectedValueOnce(
    new BusinessDocumentConflictError('stale state', 'STATE_VERSION_CONFLICT'),
  );
  renderPage();

  fireEvent.click(await screen.findByTestId('reject-proposal-proposal-12'));

  expect(
    await screen.findByTestId('business-document-conflict'),
  ).toHaveTextContent('Документ изменился в другой вкладке');
  expect(mockedFetch.mock.calls.length).toBeGreaterThanOrEqual(2);
});

test('shows domain conflicts without claiming the document changed elsewhere', async () => {
  mockedSubmit.mockRejectedValueOnce(
    new BusinessDocumentConflictError(
      'Есть открытые вопросы',
      'OPEN_REVIEW_QUESTIONS',
    ),
  );
  renderPage();

  fireEvent.click(await screen.findByTestId('reject-proposal-proposal-12'));

  expect(
    await screen.findByTestId('business-document-command-error'),
  ).toHaveTextContent('Есть открытые вопросы');
  expect(screen.queryByTestId('business-document-conflict')).toBeNull();
});

test('renders document and protocol empty states during intake', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'INTAKE',
    current_revision: null,
    active_review_cycle: 0,
    protocol: { questions: [], proposals: [], comments: [] },
    allowed_commands: [],
  });
  renderPage();

  expect(
    await screen.findByTestId('business-document-empty'),
  ).toHaveTextContent('Черновик ещё не создан');
  expect(
    screen.getByTestId('business-document-protocol-empty'),
  ).toHaveTextContent('Обсуждение пока пусто');
});

test('uses the canonical backend command to request a draft', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'INTAKE',
    current_revision: null,
    active_review_cycle: 0,
    protocol: { questions: [], proposals: [], comments: [] },
    allowed_commands: ['REQUEST_DRAFT'],
  });
  renderPage();

  fireEvent.click(
    await screen.findByRole('button', { name: 'Создать черновик' }),
  );

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenCalledWith(
    'doc-1',
    expect.objectContaining({ type: 'REQUEST_DRAFT', payload: {} }),
  );
});

test('uses an analysis action label for intake assessment', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    lifecycle_state: 'INTAKE',
    current_revision: null,
    active_review_cycle: 0,
    protocol: { questions: [], proposals: [], comments: [] },
    allowed_commands: ['REQUEST_INTAKE_ASSESSMENT'],
  });
  renderPage();

  expect(
    await screen.findByRole('button', {
      name: 'Проанализировать вводные',
    }),
  ).toBeInTheDocument();
});

test('requests review assessment before applying and exports EvaWiki explicitly', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    allowed_commands: ['REQUEST_REVIEW_ASSESSMENT'],
  });
  const { unmount } = renderPage();

  fireEvent.click(
    await screen.findByRole('button', { name: 'Проанализировать замечания' }),
  );
  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenLastCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'REQUEST_REVIEW_ASSESSMENT',
      payload: {},
    }),
  );

  unmount();
  jest.clearAllMocks();
  mockedFetch.mockResolvedValue({
    ...projection,
    lifecycle_state: 'AGREED',
    allowed_commands: ['REQUEST_EXPORT'],
  });
  mockedSubmit.mockResolvedValue(commandResult);
  renderPage();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Создать HTML для EvaWiki' }),
  );

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenLastCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'REQUEST_EXPORT',
      payload: { revision_id: 'revision-3', format: 'EVA_WIKI' },
    }),
  );
});

test('shows affected sections during review analysis and navigates to a section', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    operation_state: 'ANALYZING_REVIEW',
    allowed_commands: [],
  });
  renderPage();
  const activity = await screen.findByTestId(
    'business-document-review-activity',
  );
  expect(screen.getByTestId('business-document-activity')).toContainElement(
    activity,
  );
  expect(screen.getByTestId('business-document-activity')).toContainElement(
    screen.getByTestId('business-document-operation'),
  );
  expect(activity).toHaveTextContent('Замечания анализируются');
  expect(
    within(activity).getByRole('button', { name: '§ 1 · 1' }),
  ).toBeInTheDocument();
  fireEvent.click(within(activity).getByRole('button', { name: '§ 1 · 1' }));
  const section = screen.getAllByTestId('business-document-section')[0];
  expect(section).toHaveClass('ring-2');
  expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
});

test('shows streamed sections as preliminary and clears them on retry', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    operation_state: 'APPLYING_CHANGES',
    allowed_commands: [],
    latest_job: {
      job_id: 'job-stream',
      job_type: 'PLAN_CHANGES',
      status: 'RUNNING',
      attempt: 1,
      max_attempts: 3,
    },
  });
  let emit: ((event: BusinessDocumentJobStreamEvent) => void) | undefined;
  mockedWatch.mockImplementation(
    async (_documentId, _jobId, _signal, onEvent) => {
      emit = onEvent;
    },
  );
  renderPage();
  await waitFor(() => expect(emit).toBeDefined());
  act(() => {
    emit!({
      id: 1,
      job_id: 'job-stream',
      attempt: 1,
      base_revision_id: 'revision-3',
      type: 'section_preview',
      payload: {
        section_id: '1',
        title: 'Цель',
        before: firstSectionText,
        after: 'Новая цель.',
        source_event_ids: ['event-1'],
      },
    });
  });
  const preview = await screen.findByTestId('business-document-change-preview');
  expect(preview).toHaveTextContent('Предварительные изменения');
  const section = screen.getAllByTestId('business-document-section')[0];
  expect(section.querySelector('[data-section-body]')).toHaveTextContent(
    'Новая цель.',
  );
  expect(section.querySelector('[data-section-body]')).not.toHaveTextContent(
    firstSectionText,
  );
  expect(preview).not.toHaveTextContent('Новая цель.');
  expect(section).toHaveAttribute('data-section-preview', 'true');
  const selectionSpy = mockTextSelection('Новая цель.', section);
  fireEvent.mouseUp(screen.getByTestId('business-document-markdown'));
  expect(
    screen.getByTestId('business-document-selection-error'),
  ).toHaveTextContent('после применения изменений');
  selectionSpy.mockRestore();
  expect(
    within(preview).queryByRole('button', { name: 'Подтвердить применение' }),
  ).not.toBeInTheDocument();
  act(() => {
    emit!({
      id: 2,
      job_id: 'job-stream',
      attempt: 1,
      base_revision_id: 'revision-3',
      type: 'retry',
      payload: { reason: 'TEST' },
    });
  });
  expect(
    screen.queryByTestId('business-document-change-preview'),
  ).not.toBeInTheDocument();
  expect(section.querySelector('[data-section-body]')).toHaveTextContent(
    firstSectionText,
  );
  expect(section).toHaveAttribute('data-section-preview', 'false');
  act(() => {
    emit!({
      id: 3,
      job_id: 'job-stream',
      attempt: 2,
      base_revision_id: 'revision-old',
      type: 'section_preview',
      payload: {
        section_id: '1',
        title: 'Цель',
        before: firstSectionText,
        after: 'Правка устаревшей ревизии.',
        source_event_ids: ['event-1'],
      },
    });
  });
  expect(section.querySelector('[data-section-body]')).toHaveTextContent(
    firstSectionText,
  );
  expect(
    screen.queryByTestId('business-document-change-preview'),
  ).not.toBeInTheDocument();
});

test.each([false, true])(
  'accepts and applies a proposal without confirmation (retry: %s)',
  async (retry) => {
    const initial: BusinessDocumentProjection = {
      ...projection,
      protocol: {
        ...projection.protocol,
        proposals: projection.protocol.proposals.map((proposal) => ({
          ...proposal,
          decision: retry ? 'ACCEPTED' : 'PENDING',
          applied: false,
        })),
      },
    };
    const applying: BusinessDocumentProjection = {
      ...initial,
      state_version: 20,
      operation_state: 'APPLYING_CHANGES',
      allowed_commands: [],
      latest_job: {
        job_id: 'job-accept',
        job_type: 'PLAN_CHANGES',
        status: 'PENDING',
        attempt: 0,
        max_attempts: 3,
      },
      protocol: {
        ...initial.protocol,
        proposals: initial.protocol.proposals.map((proposal) => ({
          ...proposal,
          decision: 'ACCEPTED',
        })),
      },
    };
    const applied: BusinessDocumentProjection = {
      ...initial,
      state_version: 21,
      current_revision: {
        ...projection.current_revision!,
        revision_id: 'revision-4',
        revision_number: 4,
        document_ast: {
          ...projection.current_revision!.document_ast,
          sections: [
            {
              id: '1',
              title: 'Цель',
              blocks: [
                {
                  type: 'paragraph',
                  text: 'Нагрузка: 100 операций в секунду.',
                },
              ],
            },
          ],
        },
        body_markdown: '## 1. Цель\nНагрузка: 100 операций в секунду.',
        section_texts: { '1': 'Нагрузка: 100 операций в секунду.' },
      },
      protocol: {
        ...initial.protocol,
        proposals: initial.protocol.proposals.map((proposal) => ({
          ...proposal,
          decision: 'ACCEPTED',
          applied: true,
        })),
      },
    };
    mockedFetch.mockResolvedValue(initial);
    mockedSubmit.mockResolvedValueOnce({
      ...commandResult,
      operation_state: 'APPLYING_CHANGES',
      job_id: 'job-accept',
      document: applying,
    });
    let emit: ((event: BusinessDocumentJobStreamEvent) => void) | undefined;
    mockedWatch.mockImplementation(
      async (_document, _job, _signal, onEvent) => {
        emit = onEvent;
      },
    );
    renderPage();
    const accept = await screen.findByTestId('accept-proposal-proposal-12');
    expect(accept).toHaveTextContent(retry ? 'Применить' : 'Принять');
    fireEvent.click(accept);
    await waitFor(() => expect(emit).toBeDefined());
    expect(screen.getByTestId('proposal-status-proposal-12')).toHaveTextContent(
      'Принято к применению',
    );
    expect(screen.getByTestId('accept-proposal-proposal-12')).toBeDisabled();
    expect(screen.getByTestId('business-document-markdown')).toHaveTextContent(
      firstSectionText,
    );
    mockedFetch.mockResolvedValue(applied);
    act(() =>
      emit!({
        id: 1,
        job_id: 'job-accept',
        attempt: 1,
        base_revision_id: 'revision-3',
        type: 'applied',
        payload: { revision_id: 'revision-4' },
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId('business-document-markdown'),
      ).toHaveTextContent('Нагрузка: 100 операций в секунду.'),
    );
    expect(screen.getByTestId('proposal-status-proposal-12')).toHaveTextContent(
      'Применено',
    );
    expect(
      screen.queryByTestId('accept-proposal-proposal-12'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Подтвердить применение' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('business-document-change-preview'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('Какие события необходимо отслеживать?'),
    ).toBeInTheDocument();
    expect(mockedSubmit).toHaveBeenCalledTimes(1);
    expect(mockedSubmit).toHaveBeenCalledWith(
      'doc-1',
      expect.objectContaining({
        type: 'ACCEPT_PROPOSAL',
        payload: { proposal_id: 'proposal-12' },
      }),
    );
  },
);

test('shows a new optional reporting section before confirming its application', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    change_preview: { job_id: 'job-reporting', base_revision_id: 'revision-3' },
    allowed_commands: ['CONFIRM_PREPARED_CHANGES', 'DISCARD_PREPARED_CHANGES'],
  });
  mockedFetchPreview.mockResolvedValue({
    job_id: 'job-reporting',
    base_revision_id: 'revision-3',
    state_version: 18,
    sections: [
      {
        section_id: '5.4',
        title: 'Требования к отчетности',
        before: '',
        after:
          'Отчёт содержит сотрудника, новость и время первого подтверждения.',
        source_event_ids: ['event-reporting'],
        sources: [],
      },
    ],
    acknowledged_no_change_event_ids: [],
  });
  mockedSubmit.mockResolvedValue(commandResult);
  renderPage();
  const preview = await screen.findByTestId('business-document-change-preview');
  const section = screen.getAllByTestId('business-document-section').at(-1)!;
  expect(section).toHaveAttribute('data-section-id', '5.4');
  expect(section).toHaveTextContent('Требования к отчетности');
  expect(section.querySelector('[data-section-body]')).toHaveTextContent(
    'время первого подтверждения',
  );
  expect(mockedSubmit).not.toHaveBeenCalled();
  fireEvent.click(
    within(preview).getByRole('button', { name: 'Подтвердить применение' }),
  );
  await waitFor(() =>
    expect(mockedSubmit).toHaveBeenCalledWith(
      'doc-1',
      expect.objectContaining({
        type: 'CONFIRM_PREPARED_CHANGES',
        payload: { job_id: 'job-reporting' },
      }),
    ),
  );
});

test('prepares changes, then shows new text in the document with expandable context', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    allowed_commands: ['PREPARE_CHANGES'],
  });
  const { unmount } = renderPage();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Подготовить исправления' }),
  );
  await waitFor(() =>
    expect(mockedSubmit).toHaveBeenCalledWith(
      'doc-1',
      expect.objectContaining({
        type: 'PREPARE_CHANGES',
        payload: { base_revision_id: 'revision-3' },
      }),
    ),
  );
  unmount();
  jest.clearAllMocks();
  mockedFetch.mockResolvedValue({
    ...projection,
    change_preview: { job_id: 'job-preview', base_revision_id: 'revision-3' },
    allowed_commands: ['CONFIRM_PREPARED_CHANGES', 'DISCARD_PREPARED_CHANGES'],
  });
  mockedFetchPreview.mockResolvedValue({
    job_id: 'job-preview',
    base_revision_id: 'revision-3',
    state_version: 18,
    sections: [
      {
        section_id: '1',
        title: 'Цель',
        before: firstSectionText,
        after: 'Новая измеримая цель.',
        source_event_ids: ['event-comment-4'],
        sources: [
          {
            event_id: 'event-comment-4',
            kind: 'comment',
            entity_id: 'comment-4',
            label: 'Комментарий автора',
            text: 'Уточнить цель',
          },
        ],
      },
    ],
    acknowledged_no_change_event_ids: [],
  });
  mockedSubmit.mockResolvedValue(commandResult);
  renderPage();
  const preview = await screen.findByTestId('business-document-change-preview');
  expect(preview).toHaveTextContent('ещё не применено');
  const section = screen.getAllByTestId('business-document-section')[0];
  expect(section.querySelector('[data-section-body]')).toHaveTextContent(
    'Новая измеримая цель.',
  );
  expect(section.querySelector('details')).not.toHaveAttribute('open');
  fireEvent.click(within(section).getByText(/Прежний текст и основания/));
  expect(section).toHaveTextContent(firstSectionText);
  fireEvent.click(
    within(section).getByRole('button', {
      name: /Комментарий автора: Уточнить цель/,
    }),
  );
  expect(screen.getByTestId('business-document-comment')).toHaveClass('ring-2');
  fireEvent.click(
    within(preview).getByRole('button', { name: 'Подтвердить применение' }),
  );
  await waitFor(() =>
    expect(mockedSubmit).toHaveBeenCalledWith(
      'doc-1',
      expect.objectContaining({
        type: 'CONFIRM_PREPARED_CHANGES',
        payload: { job_id: 'job-preview' },
      }),
    ),
  );
});

test('discards inline changes and restores the saved text without changing its revision', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    change_preview: { job_id: 'job-preview', base_revision_id: 'revision-3' },
    allowed_commands: ['DISCARD_PREPARED_CHANGES'],
  });
  mockedSubmit.mockResolvedValue({ ...commandResult, document: projection });
  renderPage();
  const preview = await screen.findByTestId('business-document-change-preview');
  const sections = screen.getAllByTestId('business-document-section');
  expect(sections[1].querySelector('[data-section-body]')).toHaveTextContent(
    secondSectionText,
  );
  expect(sections[1]).toHaveAttribute('data-section-preview', 'false');
  fireEvent.click(within(preview).getByRole('button', { name: 'Отказаться' }));
  await waitFor(() =>
    expect(
      screen.queryByTestId('business-document-change-preview'),
    ).not.toBeInTheDocument(),
  );
  expect(sections[0].querySelector('[data-section-body]')).toHaveTextContent(
    firstSectionText,
  );
  expect(screen.getByTestId('business-document-pane')).toHaveTextContent(
    'Ревизия 3',
  );
  expect(mockedSubmit).toHaveBeenCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'DISCARD_PREPARED_CHANGES',
      payload: { job_id: 'job-preview' },
    }),
  );
});

test('places new preview subsections in numeric document order', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    change_preview: { job_id: 'job-preview', base_revision_id: 'revision-3' },
    allowed_commands: [],
  });
  mockedFetchPreview.mockResolvedValue({
    job_id: 'job-preview',
    base_revision_id: 'revision-3',
    state_version: 18,
    sections: ['1.10', '1.2'].map((section_id) => ({
      section_id,
      title: 'Новый подраздел',
      before: '',
      after: `Текст ${section_id}`,
      source_event_ids: ['event-1'],
    })),
    acknowledged_no_change_event_ids: [],
  });
  renderPage();
  await screen.findByTestId('business-document-change-preview');
  expect(
    screen
      .getAllByTestId('business-document-section')
      .map((section) => section.dataset.sectionId),
  ).toEqual(['1', '1.2', '1.10', '2', '3']);
});

test('does not overlay a prepared plan from a different revision', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    change_preview: { job_id: 'job-preview', base_revision_id: 'revision-old' },
    allowed_commands: [],
  });
  mockedFetchPreview.mockResolvedValue({
    job_id: 'job-preview',
    base_revision_id: 'revision-old',
    state_version: 18,
    sections: [
      {
        section_id: '1',
        title: 'Цель',
        before: firstSectionText,
        after: 'Устаревшая правка.',
        source_event_ids: [],
      },
    ],
    acknowledged_no_change_event_ids: [],
  });
  renderPage();
  await waitFor(() => expect(mockedFetchPreview).toHaveBeenCalledTimes(1));
  expect(
    screen
      .getAllByTestId('business-document-section')[0]
      .querySelector('[data-section-body]'),
  ).toHaveTextContent(firstSectionText);
  expect(
    screen.queryByTestId('business-document-change-preview'),
  ).not.toBeInTheDocument();
});

test.each([
  [false, false],
  [true, false],
  [false, true],
])(
  'renders the saved confirmation response without another GET (history open: %s, earlier GET pending: %s)',
  async (historyOpen, earlierGetPending) => {
    mockedFetch.mockResolvedValue({
      ...projection,
      change_preview: { job_id: 'job-preview', base_revision_id: 'revision-3' },
      allowed_commands: [
        'CONFIRM_PREPARED_CHANGES',
        'DISCARD_PREPARED_CHANGES',
      ],
    });
    const updatedDocument: BusinessDocumentProjection = {
      ...projection,
      state_version: 19,
      lifecycle_state: 'AGREED',
      change_preview: null,
      allowed_commands: ['START_REVIEW', 'REQUEST_EXPORT'],
      current_revision: {
        ...projection.current_revision!,
        revision_id: 'revision-4',
        revision_number: 4,
        document_ast: {
          ...projection.current_revision!.document_ast,
          sections: [
            {
              id: '1',
              title: 'Цель',
              blocks: [{ type: 'paragraph', text: 'Новая измеримая цель.' }],
            },
          ],
        },
        section_texts: { '1': 'Новая измеримая цель.' },
        body_markdown: '## 1. Цель\nНовая измеримая цель.',
      },
    };
    let confirm!: (result: BusinessDocumentCommandResult) => void;
    mockedSubmit.mockReturnValueOnce(
      new Promise((resolve) => {
        confirm = resolve;
      }),
    );
    const { client } = renderPage();
    const preview = await screen.findByTestId(
      'business-document-change-preview',
    );
    if (historyOpen) {
      fireEvent.click(screen.getByTestId('business-document-history-toggle'));
      await waitFor(() => expect(mockedListRevisions).toHaveBeenCalledTimes(1));
    }
    let finishEarlierGet!: (document: BusinessDocumentProjection) => void;
    let earlierGet: Promise<void> | undefined;
    if (earlierGetPending) {
      mockedFetch.mockReturnValueOnce(
        new Promise((resolve) => {
          finishEarlierGet = resolve;
        }),
      );
      earlierGet = client.refetchQueries({
        queryKey: ['business-document', 'doc-1'],
      });
      await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2));
    }
    fireEvent.click(
      within(preview).getByRole('button', { name: 'Подтвердить применение' }),
    );
    const markdown = screen.getByTestId('business-document-markdown');
    expect(markdown).toHaveTextContent(
      historyOpen ? firstSectionText : 'Новая измеримая цель.',
    );
    await act(async () => {
      confirm({
        ...commandResult,
        operation_state: 'IDLE',
        lifecycle_state: 'AGREED',
        job_id: null,
        document: updatedDocument,
      });
    });
    await waitFor(() =>
      expect(markdown).toHaveTextContent('Новая измеримая цель.'),
    );
    expect(markdown).not.toHaveTextContent(firstSectionText);
    expect(
      screen.queryByTestId('business-document-change-preview'),
    ).not.toBeInTheDocument();
    if (earlierGetPending) {
      await act(async () => {
        finishEarlierGet(projection);
        await earlierGet;
      });
      expect(markdown).toHaveTextContent('Новая измеримая цель.');
    }
    expect(mockedFetch).toHaveBeenCalledTimes(earlierGetPending ? 2 : 1);
    expect(mockedFetchPreview).toHaveBeenCalledTimes(1);
    expect(mockedListRevisions).toHaveBeenCalledTimes(historyOpen ? 1 : 0);
    if (historyOpen) {
      fireEvent.click(screen.getByTestId('business-document-history-toggle'));
      await waitFor(() => expect(mockedListRevisions).toHaveBeenCalledTimes(2));
    }
  },
);

test.each([
  { includeDocument: true, navigateDuringCacheUpdate: false },
  { includeDocument: false, navigateDuringCacheUpdate: false },
  { includeDocument: true, navigateDuringCacheUpdate: true },
])(
  'keeps document B and its history intact when confirmation of A completes: %j',
  async ({ includeDocument, navigateDuringCacheUpdate }) => {
    const documentA: BusinessDocumentProjection = {
      ...projection,
      change_preview: { job_id: 'job-preview', base_revision_id: 'revision-3' },
      allowed_commands: ['CONFIRM_PREPARED_CHANGES'],
    };
    const documentB: BusinessDocumentProjection = {
      ...projection,
      document_id: 'doc-2',
      state_version: 1,
      title: 'Другой документ',
      allowed_commands: [],
    };
    const confirmedA: BusinessDocumentProjection = {
      ...projection,
      state_version: 19,
      title: 'Подтверждённый документ',
      change_preview: null,
    };
    mockedFetch.mockImplementation(async (id) =>
      id === 'doc-2' ? documentB : documentA,
    );
    let finish!: (result: BusinessDocumentCommandResult) => void;
    mockedSubmit.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    let finishCacheUpdate!: () => void;
    if (navigateDuringCacheUpdate) {
      const cancelQueries = client.cancelQueries.bind(client);
      const pendingCacheUpdate = new Promise<void>((resolve) => {
        finishCacheUpdate = resolve;
      });
      jest
        .spyOn(client, 'cancelQueries')
        .mockImplementationOnce(async (...args) => {
          await pendingCacheUpdate;
          await cancelQueries(...args);
        });
    }
    function SwitchDocument() {
      const navigate = useNavigate();
      return (
        <button onClick={() => navigate('/business-documents/doc-2')}>
          Другой документ
        </button>
      );
    }
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/business-documents/doc-1']}>
          <SwitchDocument />
          <RouterRoutes>
            <Route
              path="/business-documents/:id"
              element={<BusinessDocumentsPage />}
            />
          </RouterRoutes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const preview = await screen.findByTestId(
      'business-document-change-preview',
    );
    fireEvent.click(
      within(preview).getByRole('button', { name: 'Подтвердить применение' }),
    );
    await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
    const result: BusinessDocumentCommandResult = {
      ...commandResult,
      operation_state: 'IDLE',
      job_id: null,
      document: includeDocument ? confirmedA : undefined,
    };
    if (navigateDuringCacheUpdate) {
      await act(async () => finish(result));
      await waitFor(() =>
        expect(client.cancelQueries).toHaveBeenCalledTimes(1),
      );
    }
    fireEvent.click(screen.getByRole('button', { name: 'Другой документ' }));
    await waitFor(() =>
      expect(screen.getByTestId('business-document-header')).toHaveTextContent(
        'Другой документ',
      ),
    );
    fireEvent.click(screen.getByTestId('business-document-history-toggle'));
    await waitFor(() =>
      expect(mockedListRevisions).toHaveBeenCalledWith('doc-2'),
    );
    await act(async () => {
      if (navigateDuringCacheUpdate) finishCacheUpdate();
      else finish(result);
    });
    await waitFor(() =>
      expect(client.getMutationCache().getAll()[0].state.status).toBe(
        'success',
      ),
    );
    expect(client.getQueryData(['business-document', 'doc-2'])).toEqual(
      documentB,
    );
    expect(screen.getByTestId('business-document-header')).toHaveTextContent(
      'Другой документ',
    );
    expect(screen.getByTestId('business-document-history')).toBeInTheDocument();
    expect(mockedFetch).toHaveBeenCalledTimes(2);
    if (includeDocument) {
      expect(client.getQueryData(['business-document', 'doc-1'])).toEqual(
        confirmedA,
      );
    }
  },
);

test.each(['permission', 'version'])(
  'keeps the document unchanged when confirmation fails: %s',
  async (kind) => {
    const error =
      kind === 'permission'
        ? new Error('Нет прав на изменение документа')
        : new BusinessDocumentConflictError(
            'Версия устарела',
            'STATE_VERSION_CONFLICT',
          );
    mockedFetch.mockResolvedValue({
      ...projection,
      change_preview: { job_id: 'job-preview', base_revision_id: 'revision-3' },
      allowed_commands: ['CONFIRM_PREPARED_CHANGES'],
    });
    mockedSubmit.mockRejectedValueOnce(error);
    renderPage();
    const preview = await screen.findByTestId(
      'business-document-change-preview',
    );
    fireEvent.click(
      within(preview).getByRole('button', { name: 'Подтвердить применение' }),
    );
    await screen.findByTestId(
      error instanceof BusinessDocumentConflictError
        ? 'business-document-conflict'
        : 'business-document-command-error',
    );
    const section = screen.getAllByTestId('business-document-section')[0];
    expect(section).toHaveAttribute('data-section-text', firstSectionText);
    expect(section.querySelector('[data-section-body]')).toHaveTextContent(
      'Новая измеримая цель.',
    );
    expect(section).toHaveTextContent('ещё не применены');
    expect(screen.getByTestId('business-document-pane')).toHaveTextContent(
      'Ревизия 3',
    );
  },
);

test('shows evidence changes and named no-change inputs before confirmation', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    change_preview: { job_id: 'job-preview', base_revision_id: 'revision-3' },
    allowed_commands: ['CONFIRM_PREPARED_CHANGES', 'DISCARD_PREPARED_CHANGES'],
  });
  mockedFetchPreview.mockResolvedValueOnce({
    job_id: 'job-preview',
    base_revision_id: 'revision-3',
    state_version: 18,
    sections: [
      {
        section_id: '1',
        title: 'Цель',
        before: firstSectionText,
        after: firstSectionText,
        before_evidence_refs: ['ragflow://dataset/d/document/old/chunk/1'],
        after_evidence_refs: ['ragflow://dataset/d/document/new/chunk/1'],
        source_event_ids: ['event-comment-4'],
      },
    ],
    acknowledged_no_change_event_ids: ['event-comment-5'],
    acknowledged_no_change_sources: [
      {
        event_id: 'event-comment-5',
        kind: 'comment',
        entity_id: 'comment-4',
        label: 'Комментарий автора',
        text: 'Проверить метрику',
      },
    ],
  });
  renderPage();
  const preview = await screen.findByTestId('business-document-change-preview');
  const section = screen.getAllByTestId('business-document-section')[0];
  fireEvent.click(within(section).getByText(/Прежний текст и основания/));
  expect(section).toHaveTextContent('Источники до');
  expect(section).toHaveTextContent('ragflow://dataset/d/document/new/chunk/1');
  expect(preview).toHaveTextContent('Учтено без изменения текста: 1');
  fireEvent.click(
    within(preview).getByRole('button', { name: /Проверить метрику/ }),
  );
  expect(screen.getByTestId('business-document-comment')).toHaveClass('ring-2');
});

test('keeps a prepared diff readable without offering edit actions to a viewer', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    change_preview: { job_id: 'job-preview', base_revision_id: 'revision-3' },
    permissions: { read: true, edit: false, delete: false, assign: false },
    allowed_commands: [],
  });
  renderPage();
  const preview = await screen.findByTestId('business-document-change-preview');
  expect(screen.getByTestId('business-document-markdown')).toHaveTextContent(
    'Новая измеримая цель.',
  );
  expect(
    within(preview).queryByRole('button', { name: 'Подтвердить применение' }),
  ).not.toBeInTheDocument();
  expect(
    within(preview).queryByRole('button', { name: 'Отказаться' }),
  ).not.toBeInTheDocument();
});

test('renders loading and retryable error states', async () => {
  let rejectRequest: (error: Error) => void = () => undefined;
  mockedFetch.mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        rejectRequest = reject;
      }),
  );
  renderPage();
  expect(screen.getByTestId('business-document-loading')).toBeInTheDocument();

  rejectRequest(new Error('network down'));
  expect(
    await screen.findByTestId('business-document-error'),
  ).toHaveTextContent('network down');
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2));
});

test.each(['FAILED', 'APPLYING_CHANGES'] as const)(
  'shows concrete requirement failures in the document when %s',
  async (operation_state) => {
    const error = {
      code: 'REQUIREMENT_COVERAGE_FAILED',
      message:
        'Generated changes omit, contradict or misplace confirmed requirements',
      details: {
        requirements: [
          {
            text: 'Время появления тикета: от создания инцидента SOAR до появления связанного тикета в ЕЛКа.',
            section_id: '5.4',
            reason: 'В новом разделе отсутствует определение метрики.',
          },
        ],
      },
    };
    mockedFetch.mockResolvedValue({
      ...projection,
      operation_state,
      last_error: error,
      latest_job: {
        job_id: 'soar-plan',
        job_type: 'PLAN_CHANGES',
        status: operation_state === 'FAILED' ? 'DEAD' : 'RETRY',
        attempt: 3,
        max_attempts: 3,
        error,
      },
    });
    renderPage();
    const panel = await screen.findByTestId(
      operation_state === 'FAILED'
        ? 'business-document-operation-error'
        : 'business-document-operation',
    );
    expect(panel).toHaveTextContent(
      'План изменений не сохранил подтверждённые требования.',
    );
    expect(panel).toHaveTextContent('Раздел 5.4');
    expect(panel).toHaveTextContent('от создания инцидента SOAR');
    expect(panel).toHaveTextContent(
      'Причина: В новом разделе отсутствует определение метрики.',
    );
  },
);

test('dismisses a failed operation error across refreshes and shows the next failure', async () => {
  const failed: BusinessDocumentProjection = {
    ...projection,
    operation_state: 'FAILED',
    last_error: {
      code: 'INVALID_DOCUMENT_DRAFT',
      message: 'Черновик не соответствует контракту',
    },
    latest_job: {
      job_id: 'failed-job-1',
      job_type: 'PLAN_CHANGES',
      status: 'DEAD',
      attempt: 3,
      max_attempts: 3,
    },
  };
  mockedFetch.mockResolvedValue(failed);
  const { client } = renderPage();
  expect(
    await screen.findByTestId('business-document-operation-error'),
  ).toHaveTextContent('Черновик не соответствует контракту');
  fireEvent.click(
    screen.getByRole('button', { name: 'Закрыть сообщение об ошибке' }),
  );
  expect(
    screen.queryByTestId('business-document-operation-error'),
  ).not.toBeInTheDocument();
  expect(mockedSubmit).not.toHaveBeenCalled();

  await act(() =>
    client.refetchQueries({ queryKey: ['business-document', 'doc-1'] }),
  );
  expect(
    screen.queryByTestId('business-document-operation-error'),
  ).not.toBeInTheDocument();

  mockedFetch.mockResolvedValue({
    ...failed,
    latest_job: { ...failed.latest_job!, job_id: 'failed-job-2' },
  });
  await act(() =>
    client.refetchQueries({ queryKey: ['business-document', 'doc-1'] }),
  );
  expect(
    await screen.findByTestId('business-document-operation-error'),
  ).toBeVisible();
});

test('shows the active attempt and previous validation error while retrying', async () => {
  mockedFetch.mockResolvedValue({
    ...projection,
    operation_state: 'GENERATING_DRAFT',
    latest_job: {
      job_id: 'job-draft',
      job_type: 'GENERATE_DRAFT',
      status: 'RUNNING',
      progress: 0.62,
      progress_stage: 'GENERATING',
      progress_message: 'Формируем структуру и разделы',
      attempt: 2,
      max_attempts: 3,
      error: {
        code: 'INVALID_DRAFT_BUNDLE',
        message: 'Черновик не соответствует контракту',
      },
    },
  });

  renderPage();

  expect(
    await screen.findByTestId('business-document-operation'),
  ).toHaveTextContent('Попытка 2 из 3');
  expect(screen.getByTestId('business-document-operation')).toHaveTextContent(
    '62%',
  );
  expect(screen.getByTestId('business-document-operation')).toHaveTextContent(
    'Предыдущая ошибка: Черновик не соответствует контракту',
  );
});

test('creates a new business requirements document', async () => {
  mockedCreate.mockResolvedValueOnce({
    ...projection,
    state_version: 1,
    lifecycle_state: 'INTAKE',
    current_revision: null,
    active_review_cycle: 0,
  });
  renderPage('/business-documents');

  expect(screen.queryByText('Источники RAGFlow')).not.toBeInTheDocument();
  expect(
    screen.queryByTestId('business-document-datasets'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('textbox', { name: 'URL страницы EVA' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText('Доработать EVA')).not.toBeInTheDocument();

  await selectCatalogEntry();
  fireEvent.change(screen.getByRole('textbox', { name: 'Описание идеи' }), {
    target: { value: 'Нужен новый клиентский сценарий.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Начать работу' }));

  await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
  expect(mockedCreate.mock.calls[0][0]).toEqual({
    schema_version: '4',
    document_type: 'business_requirements',
    catalog_entry_id: catalogEntry.id,
    idea: 'Нужен новый клиентский сценарий.',
    dataset_ids: [],
  });
  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenCalledWith(
    'doc-1',
    expect.objectContaining({
      schema_version: '1',
      expected_state_version: 1,
      type: 'REQUEST_INTAKE_ASSESSMENT',
      payload: {},
    }),
  );
  expect(mockedSubmit.mock.calls[0][1].command_id).toMatch(
    /^cmd-initial-analysis-/,
  );
  expect(mockedSubmit.mock.calls[0][1].idempotency_key).toMatch(
    /^idem-initial-analysis-/,
  );
  expect(
    await screen.findByTestId('business-document-workbench'),
  ).toBeVisible();
});

test('can explicitly create local requirements without reading EVA when personal access is missing', async () => {
  mockedCheckEvaAccess.mockResolvedValue({
    state: 'MISSING',
    connector_id: 'connector-1',
  });
  mockedCreate.mockResolvedValueOnce(projection);
  renderPage('/business-documents');
  await selectCatalogEntry();
  fireEvent.click(await screen.findByTestId('create-without-eva-access'));
  await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
  expect(mockedCreate.mock.calls[0][0]).toMatchObject({
    eva_decision: { mode: 'SKIP' },
  });
});

test.each(['', '   '])(
  'creates a structure by title without starting analysis (idea: %j)',
  async (idea) => {
    mockedCreate.mockResolvedValueOnce(projection);
    renderPage('/business-documents');

    expect(
      await screen.findByRole('button', { name: 'Начать работу' }),
    ).toBeDisabled();
    await selectCatalogEntry();
    fireEvent.change(screen.getByRole('textbox', { name: 'Описание идеи' }), {
      target: { value: idea },
    });
    expect(screen.getByRole('button', { name: 'Начать работу' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Начать работу' }));

    expect(
      await screen.findByTestId('business-document-workbench'),
    ).toBeVisible();
    expect(mockedCreate.mock.calls[0][0]).toEqual({
      schema_version: '4',
      document_type: 'business_requirements',
      catalog_entry_id: catalogEntry.id,
      dataset_ids: [],
    });
    expect(mockedSubmit).not.toHaveBeenCalled();
  },
);

test('opens the created document when automatic analysis cannot be started', async () => {
  mockedCreate.mockResolvedValueOnce({
    ...projection,
    state_version: 1,
    lifecycle_state: 'INTAKE',
    current_revision: null,
    active_review_cycle: 0,
  });
  mockedSubmit.mockRejectedValueOnce(new Error('analysis unavailable'));
  renderPage('/business-documents');

  await selectCatalogEntry();
  fireEvent.change(screen.getByRole('textbox', { name: 'Описание идеи' }), {
    target: { value: 'Нужен новый клиентский сценарий.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Начать работу' }));

  expect(
    await screen.findByTestId('business-document-workbench'),
  ).toBeVisible();
  expect(mockedCreate).toHaveBeenCalledTimes(1);
  expect(mockedSubmit).toHaveBeenCalledTimes(1);
});

test('does not show linked RAGFlow source counts in a document', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    dataset_ids: ['dataset-1'],
  });
  renderPage();

  expect(
    await screen.findByTestId('business-document-workbench'),
  ).toBeVisible();
  expect(screen.queryByText('Источников: 1')).not.toBeInTheDocument();
});

test('opens a selected free EVA page in the document editor without replacement confirmation', async () => {
  const importedProjection = {
    ...projection,
    state_version: 2,
    active_review_cycle: 1,
    protocol: { questions: [], proposals: [], comments: [] },
    allowed_commands: [
      'ADD_COMMENT',
      'REQUEST_REVIEW_ASSESSMENT',
    ] as BusinessDocumentProjection['allowed_commands'],
  };
  mockedFetch.mockResolvedValueOnce(importedProjection);
  mockedCreate
    .mockRejectedValueOnce(
      new BusinessDocumentConflictError(
        'Выберите страницу',
        'EVA_BINDING_DECISION_REQUIRED',
        {
          matches: [
            {
              connector_id: 'connector-1',
              connector_name: 'EVA Wiki',
              eva_origin: 'https://eva-api.example.com',
              id: 'CmfDocument:doc-42',
              name: 'Новый продукт',
              code: 'BR-42',
              project_id: 'CmfProject:portal',
              web_url: 'https://eva.example.com/project/Document/BR-42',
              breadcrumbs: [
                {
                  id: 'CmfDocument:folder',
                  name: 'Проекты',
                  web_url: 'https://eva.example.com/project/Document/PROJECTS',
                },
                {
                  id: 'CmfDocument:doc-42',
                  name: 'Новый продукт',
                  web_url: 'https://eva.example.com/project/Document/BR-42',
                },
              ],
              hierarchy: 'Проекты › Новый продукт',
              binding_available: true,
            },
            {
              connector_id: 'connector-1',
              connector_name: 'EVA Wiki',
              eva_origin: 'https://eva-api.example.com',
              id: 'CmfDocument:doc-occupied',
              name: 'Новый продукт',
              code: 'BR-99',
              project_id: 'CmfProject:archive',
              web_url: 'https://eva.example.com/archive/Document/BR-99',
              breadcrumbs: [
                {
                  id: 'CmfDocument:archive',
                  name: 'Архив',
                  web_url: 'https://eva.example.com/archive/Document/ARCHIVE',
                },
                {
                  id: 'CmfDocument:doc-occupied',
                  name: 'Новый продукт',
                  web_url: 'https://eva.example.com/archive/Document/BR-99',
                },
              ],
              hierarchy: 'Архив › Новый продукт',
              binding_available: false,
              linked_document: {
                document_id: 'existing-document',
                title: 'Уже созданный документ',
              },
            },
          ],
        },
      ),
    )
    .mockResolvedValueOnce(importedProjection);
  renderPage('/business-documents');

  await selectCatalogEntry();
  fireEvent.change(screen.getByRole('textbox', { name: 'Описание идеи' }), {
    target: { value: 'Нужен новый клиентский сценарий.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Начать работу' }));

  expect(await screen.findByTestId('eva-title-match-banner')).toHaveTextContent(
    'Проекты',
  );
  expect(screen.getByTestId('eva-title-match-banner')).toHaveTextContent(
    'Уже привязана к документу «Уже созданный документ»',
  );
  expect(screen.getByRole('link', { name: 'Проекты' })).toHaveAttribute(
    'href',
    'https://eva.example.com/project/Document/PROJECTS',
  );
  expect(screen.getByRole('link', { name: 'Архив' })).toHaveAttribute(
    'href',
    'https://eva.example.com/archive/Document/ARCHIVE',
  );
  const bindButtons = screen.getAllByRole('button', {
    name: 'Открыть в редакторе',
  });
  expect(bindButtons).toHaveLength(2);
  expect(bindButtons[1]).toBeDisabled();
  fireEvent.click(bindButtons[0]);

  await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(2));
  expect(mockedCreate.mock.calls[1][0]).toEqual(
    expect.objectContaining({
      schema_version: '4',
      catalog_entry_id: catalogEntry.id,
      eva_page_url: 'https://eva.example.com/project/Document/BR-42',
      eva_decision: { mode: 'BIND' },
    }),
  );
  expect(
    screen.queryByTestId('eva-replace-confirmation'),
  ).not.toBeInTheDocument();
  expect(
    await screen.findByTestId('business-document-markdown'),
  ).toHaveTextContent('Сократить время перевода');
  expect(screen.getByRole('textbox', { name: 'Комментарий' })).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Проанализировать замечания' }),
  ).toBeVisible();
  expect(mockedSubmit).not.toHaveBeenCalled();
});

test('creates without EVA binding after the user explicitly skips matches', async () => {
  mockedCreate
    .mockRejectedValueOnce(
      new BusinessDocumentConflictError(
        'Выберите страницу',
        'EVA_BINDING_DECISION_REQUIRED',
        {
          matches: [
            {
              connector_id: 'connector-1',
              connector_name: 'EVA Wiki',
              eva_origin: 'https://eva-api.example.com',
              id: 'CmfDocument:doc-42',
              name: 'Новый продукт',
              code: 'BR-42',
              project_id: 'CmfProject:portal',
              web_url: 'https://eva.example.com/project/Document/BR-42',
              breadcrumbs: [],
              hierarchy: 'Проекты › Новый продукт',
              binding_available: true,
            },
          ],
        },
      ),
    )
    .mockResolvedValueOnce(projection);
  renderPage('/business-documents');

  await selectCatalogEntry();
  fireEvent.change(screen.getByRole('textbox', { name: 'Описание идеи' }), {
    target: { value: 'Нужен новый клиентский сценарий.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Начать работу' }));
  fireEvent.click(await screen.findByTestId('create-without-eva-binding'));

  await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(2));
  expect(mockedCreate.mock.calls[1][0]).toEqual(
    expect.objectContaining({
      schema_version: '4',
      catalog_entry_id: catalogEntry.id,
      eva_decision: { mode: 'SKIP' },
    }),
  );
  expect(mockedCreate.mock.calls[1][0]).not.toHaveProperty('eva_page_url');
});

test('keeps voice input for the idea while the title comes from the catalog', async () => {
  renderPage('/business-documents');

  const idea = await screen.findByRole('textbox', { name: 'Описание идеи' });
  await selectCatalogEntry();
  fireEvent.change(idea, { target: { value: 'Исходная идея.' } });

  fireEvent.click(screen.getByTestId('voice-input-document-idea'));

  expect(
    screen.getByRole('combobox', { name: 'Название документа' }),
  ).toHaveTextContent(catalogEntry.title);
  expect(
    screen.queryByTestId('voice-input-document-title'),
  ).not.toBeInTheDocument();
  expect(idea).toHaveValue('Исходная идея. текст из диктовки');
});

test('lists saved documents so work can be resumed', async () => {
  mockedList.mockResolvedValueOnce({
    items: [
      {
        document_id: 'saved-1',
        title: 'Сохранённые требования',
        lifecycle_state: 'AGREED',
        operation_state: 'IDLE',
        state_version: 9,
        current_revision_number: 2,
        update_time: 1_787_695_200,
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  });
  renderPage('/business-documents');

  expect(
    await screen.findByTestId('business-document-list-item'),
  ).toHaveTextContent('Сохранённые требования');
  expect(screen.getByTestId('business-document-list-item')).toHaveTextContent(
    'Ревизия 2',
  );
  fireEvent.click(screen.getByTestId('business-document-list-item'));
  await waitFor(() => expect(mockedFetch).toHaveBeenCalledWith('saved-1'));
});

test('shows compact processing progress in the document list', async () => {
  mockedList.mockResolvedValueOnce({
    items: [
      {
        document_id: 'running-1',
        title: 'Регламент закупочной деятельности',
        lifecycle_state: 'INTAKE',
        operation_state: 'GENERATING_DRAFT',
        state_version: 4,
        current_revision_number: null,
        update_time: 1_788_451_200,
        latest_job: {
          job_id: 'job-running',
          job_type: 'GENERATE_DRAFT',
          status: 'RUNNING',
          progress: 0.62,
          progress_stage: 'GENERATING',
          progress_message: 'Формируем структуру и разделы',
          attempt: 1,
          max_attempts: 3,
        },
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  });
  renderPage('/business-documents');

  const progress = await screen.findByTestId('business-document-list-progress');
  expect(progress).toHaveTextContent('Формируем структуру и разделы');
  expect(progress).toHaveTextContent('62%');
  expect(
    screen.getByRole('progressbar', { name: 'Прогресс обработки: 62%' }),
  ).toBeInTheDocument();
});

test('switches between my and all documents', async () => {
  renderPage('/business-documents');

  await waitFor(() => expect(mockedList).toHaveBeenCalledWith(1, 20, 'mine'));
  fireEvent.click(screen.getByTestId('business-documents-filter-all'));
  await waitFor(() => expect(mockedList).toHaveBeenCalledWith(1, 20, 'all'));
});

test.each(['AUTHOR_EDITOR', 'READER'] as const)(
  'hides the creation sidebar from %s',
  async (role) => {
    mockedList.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
      access_role: role,
      capabilities: {
        read: true,
        create: false,
        edit_own: role !== 'READER',
        edit_all: false,
        delete: false,
        assign: false,
      },
    });
    renderPage('/business-documents');

    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.queryByTestId('business-document-create-panel'),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole('button', { name: 'Начать работу' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('open-document-constructor'),
    ).not.toBeInTheDocument();
  },
);

test('hides the constructor entry when its section is disabled', async () => {
  mockUseSystemConfig.mockReturnValue({
    config: { visibleSections: ['business_documents'] },
    loading: false,
  });
  renderPage('/business-documents');

  expect(
    await screen.findByTestId('business-document-create-panel'),
  ).toBeVisible();
  expect(
    screen.queryByTestId('open-document-constructor'),
  ).not.toBeInTheDocument();
});

test('allows an extended moderator to assign a document owner', async () => {
  const assigned = {
    ...projection,
    owner_id: 'author-2',
    owner_name: 'Второй автор',
    access_role: 'EXTENDED_MODERATOR' as const,
    permissions: { read: true, edit: true, delete: true, assign: true },
  };
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    owner_id: 'author-1',
    owner_name: 'Первый автор',
    access_role: 'EXTENDED_MODERATOR',
    permissions: { read: true, edit: true, delete: true, assign: true },
  });
  mockedListAccessUsers.mockResolvedValueOnce({
    items: [
      {
        user_id: 'author-1',
        nickname: 'Первый автор',
        email: 'author-1@example.com',
        role: 'AUTHOR_CREATOR',
      },
      {
        user_id: 'author-2',
        nickname: 'Второй автор',
        email: 'author-2@example.com',
        role: 'AUTHOR_EDITOR',
      },
    ],
  });
  mockedAssignOwner.mockResolvedValueOnce(assigned);
  renderPage('/business-documents/doc-1');

  fireEvent.click(
    await screen.findByTestId('business-document-details-toggle'),
  );
  fireEvent.click(await screen.findByTestId('business-document-owner-select'));
  expect(screen.getByText('Владелец: Первый автор')).toBeVisible();
  expect(
    screen.getByTestId('business-document-owner-option-author-2'),
  ).toHaveTextContent('Второй автор (author-2@example.com)');
  fireEvent.click(
    await screen.findByTestId('business-document-owner-option-author-2'),
  );
  fireEvent.click(screen.getByTestId('business-document-assign-owner'));

  await waitFor(() =>
    expect(mockedAssignOwner).toHaveBeenCalledWith('doc-1', 'author-2', 18),
  );
  expect(await screen.findByText('Владелец: Второй автор')).toBeVisible();
});

test('shows hard delete only for an administrator and confirms it', async () => {
  mockedList.mockResolvedValueOnce({
    items: [
      {
        document_id: 'saved-1',
        owner_id: 'author-1',
        owner_name: 'Первый автор',
        access_role: 'ADMIN',
        permissions: { read: true, edit: true, delete: true },
        title: 'Документ другого автора',
        lifecycle_state: 'AGREED',
        operation_state: 'IDLE',
        state_version: 9,
        current_revision_number: 2,
        update_time: 1_787_695_200,
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  });
  renderPage('/business-documents');

  expect(await screen.findByText('Владелец: Первый автор')).toBeVisible();
  expect(screen.queryByText('Владелец: author-1')).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Удалить документ «Документ другого автора»',
    }),
  );
  fireEvent.click(await screen.findByRole('button', { name: /^Удалить$/ }));

  await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith('saved-1'));
});

test('keeps a foreign document editable for admin and allows deletion from its workbench', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    owner_id: 'author-1',
    access_role: 'ADMIN',
    permissions: { read: true, edit: true, delete: true },
  });
  renderPage('/business-documents/doc-1');

  expect(
    await screen.findByTestId('business-document-delete-detail'),
  ).toBeVisible();
  expect(screen.getByText('Применить исправления')).toBeVisible();
  fireEvent.click(screen.getByTestId('business-document-delete-detail'));
  const dialog = await screen.findByRole('alertdialog');
  fireEvent.click(within(dialog).getByRole('button', { name: /^Удалить$/ }));

  await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith('doc-1'));
});

test.each([
  { role: 'AUTHOR_CREATOR', ownerId: 'author-1' },
  { role: 'READER', ownerId: 'user-1' },
  { role: 'READER', ownerId: 'author-1' },
] as const)(
  'keeps owner $ownerId read-only for $role',
  async ({ role, ownerId }) => {
    mockedFetch.mockResolvedValueOnce({
      ...projection,
      owner_id: ownerId,
      access_role: role,
      permissions: { read: true, edit: false, delete: false },
    });
    renderPage('/business-documents/doc-1');

    expect(await screen.findByText(projection.title)).toBeVisible();
    expect(screen.queryByText('Применить исправления')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('business-document-delete-detail'),
    ).not.toBeInTheDocument();
    expect(mockedSubmit).not.toHaveBeenCalled();
  },
);

test('shows the reviewed lifecycle and review operation labels', async () => {
  mockedList.mockResolvedValueOnce({
    items: [
      {
        document_id: 'review-1',
        title: 'Документ на внесении изменений',
        lifecycle_state: 'REVIEW',
        operation_state: 'ANALYZING_REVIEW',
        state_version: 3,
        current_revision_number: 1,
        update_time: 1_787_695_200,
      },
      {
        document_id: 'agreed-1',
        title: 'Проверенный документ',
        lifecycle_state: 'AGREED',
        operation_state: 'IDLE',
        state_version: 4,
        current_revision_number: 2,
        update_time: 1_787_695_200,
      },
      {
        document_id: 'archived-1',
        title: 'Архивный документ',
        lifecycle_state: 'ARCHIVED',
        operation_state: 'IDLE',
        state_version: 5,
        current_revision_number: 2,
        update_time: 1_787_695_200,
      },
    ],
    total: 3,
    page: 1,
    page_size: 20,
  });
  renderPage('/business-documents');

  expect(await screen.findByText('Внесение изменений')).toBeInTheDocument();
  expect(screen.getByText('Правки внесены')).toBeInTheDocument();
  expect(screen.getByText('В архиве')).toBeInTheDocument();
  expect(screen.getByText('Анализ замечаний')).toBeInTheDocument();
});

test('shows an EVA change with no mutation controls for a reader', async () => {
  mockedFetchEva.mockResolvedValueOnce({ ...evaChange, allowed_actions: [] });
  renderPage('/business-documents/eva/change-1');

  expect(await screen.findByTestId('eva-change-workbench')).toBeVisible();
  expect(screen.getByTestId('eva-change-diff')).toHaveTextContent(
    'Новый текст.',
  );
  expect(
    screen.queryByTestId('generate-eva-change-draft'),
  ).not.toBeInTheDocument();
  expect(screen.queryByTestId('prepare-eva-draft')).not.toBeInTheDocument();
  expect(screen.queryByTestId('publish-eva-document')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Подтвердить изменения' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText('Уточнение доработки EVA'),
  ).not.toBeInTheDocument();
});

test('shows a read-only agent draft and regenerates it only from instructions', async () => {
  renderPage('/business-documents/eva/change-1');

  expect(await screen.findByTestId('eva-change-workbench')).toBeVisible();
  expect(screen.getByTestId('eva-change-diff')).toHaveTextContent(
    'Старый текст.',
  );
  expect(screen.getByTestId('eva-change-diff')).toHaveTextContent(
    'Новый текст.',
  );
  const confirmationPane = screen.getByRole('complementary', {
    name: 'Подтвердить запись в EVA',
  });
  expect(
    within(confirmationPane).getByRole('heading', {
      name: 'Подтвердить запись в EVA',
    }),
  ).toBeVisible();
  expect(
    within(
      within(confirmationPane).getByRole('list', {
        name: 'Этапы публикации в EVA',
      }),
    ).getAllByRole('listitem'),
  ).toHaveLength(3);
  expect(
    within(confirmationPane).getByText('Сформировать черновик'),
  ).toBeVisible();
  expect(
    within(confirmationPane).getByText('Сохранить как черновик в EVA'),
  ).toBeVisible();
  expect(
    within(confirmationPane).getByRole('button', {
      name: 'Подтвердить изменения',
    }),
  ).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'Вариант агента' }));
  expect(screen.getByTestId('eva-agent-draft')).toHaveTextContent(
    'Новый текст.',
  );
  expect(
    screen.queryByRole('textbox', { name: 'Черновик документа EVA' }),
  ).not.toBeInTheDocument();
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Уточнение доработки EVA' }),
    { target: { value: 'Сделать цель измеримой.' } },
  );
  fireEvent.click(screen.getByTestId('generate-eva-change-draft'));

  await waitFor(() =>
    expect(mockedGenerateEva).toHaveBeenCalledWith('change-1', {
      expected_state_version: 2,
      refinement: 'Сделать цель измеримой.',
    }),
  );
  expect(mockedPrepareEva).not.toHaveBeenCalled();
  expect(mockedPublishEva).not.toHaveBeenCalled();
  expect(mockedApproveEva).not.toHaveBeenCalled();
});

test.each(
  ['EVA_SOURCE_VERSION_CONFLICT', 'EVA_DRAFT_CONFLICT'].flatMap(
    (conflictCode) =>
      ['prepare', 'publish'].map((action) => ({ conflictCode, action })),
  ),
)(
  'asks for confirmation and force-overwrites $action after $conflictCode with a refreshed version',
  async ({ conflictCode, action }) => {
    const actionMock =
      action === 'prepare' ? mockedPrepareEva : mockedPublishEva;
    const approvedChange: EvaDocumentChange = {
      ...evaChange,
      state_version: 4,
      workflow_state: action === 'prepare' ? 'APPROVED' : 'EVA_DRAFT_READY',
      allowed_actions: [
        action === 'prepare' ? 'PREPARE_EVA_DRAFT' : 'PUBLISH_EVA',
      ],
    };
    mockedFetchEva.mockResolvedValueOnce(approvedChange).mockResolvedValue({
      ...approvedChange,
      state_version: 6,
    });
    actionMock
      .mockRejectedValueOnce(
        new BusinessDocumentConflictError(
          'The published EVA document changed after this change request was created',
          conflictCode,
          {
            confirmation_required: true,
            confirmation_action: 'OVERWRITE_EVA_DOCUMENT',
          },
        ),
      )
      .mockResolvedValueOnce({
        ...approvedChange,
        state_version: 6,
        workflow_state: 'EVA_DRAFT_READY',
        allowed_actions: ['PUBLISH_EVA'],
      });

    renderPage('/business-documents/eva/change-1');
    const prepareDraftButton = await screen.findByTestId(
      action === 'prepare' ? 'prepare-eva-draft' : 'publish-eva-document',
    );
    await waitFor(() => expect(prepareDraftButton).toBeEnabled());
    fireEvent.click(prepareDraftButton);
    if (action === 'publish') {
      fireEvent.click(
        await screen.findByRole('button', {
          name: 'Опубликовать',
        }),
      );
    }

    expect(await screen.findByText('Перезаписать документ EVA?')).toBeVisible();
    expect(screen.getByText(/в том числе чужие изменения/)).toBeVisible();
    expect(actionMock).toHaveBeenNthCalledWith(1, 'change-1', 4, false);

    fireEvent.click(screen.getByTestId('confirm-eva-overwrite'));

    await waitFor(() =>
      expect(actionMock).toHaveBeenNthCalledWith(2, 'change-1', 6, true),
    );
  },
);

test.each(['prepare', 'publish'])(
  'cancelling overwrite during %s preserves the foreign EVA draft without another request',
  async (action) => {
    const actionMock =
      action === 'prepare' ? mockedPrepareEva : mockedPublishEva;
    mockedFetchEva.mockResolvedValue({
      ...evaChange,
      state_version: 4,
      workflow_state: action === 'prepare' ? 'APPROVED' : 'EVA_DRAFT_READY',
      allowed_actions: [
        action === 'prepare' ? 'PREPARE_EVA_DRAFT' : 'PUBLISH_EVA',
      ],
    });
    actionMock.mockRejectedValueOnce(
      new BusinessDocumentConflictError(
        'Different unpublished draft',
        'EVA_DRAFT_CONFLICT',
        {
          confirmation_required: true,
          confirmation_action: 'OVERWRITE_EVA_DOCUMENT',
        },
      ),
    );
    renderPage('/business-documents/eva/change-1');
    const button = await screen.findByTestId(
      action === 'prepare' ? 'prepare-eva-draft' : 'publish-eva-document',
    );
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    if (action === 'publish') {
      fireEvent.click(
        await screen.findByRole('button', {
          name: 'Опубликовать',
        }),
      );
    }
    const dialog = await screen.findByTestId('eva-overwrite-confirmation');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }));
    await waitFor(() =>
      expect(
        screen.queryByTestId('eva-overwrite-confirmation'),
      ).not.toBeInTheDocument(),
    );
    expect(actionMock).toHaveBeenCalledTimes(1);
  },
);
