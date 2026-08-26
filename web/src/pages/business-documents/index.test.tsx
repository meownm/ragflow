import {
  BusinessDocumentConflictError,
  approveEvaDocumentChange,
  createBusinessDocument,
  createEvaDocumentChange,
  fetchBusinessDocument,
  fetchEvaDocumentChange,
  listBusinessDocuments,
  listEvaDocumentChanges,
  prepareEvaDocumentChange,
  publishEvaDocumentChange,
  saveEvaDocumentChangeDraft,
  searchEvaDocumentSources,
  submitBusinessDocumentCommand,
} from '@/services/business-document-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes as RouterRoutes } from 'react-router';
import BusinessDocumentsPage from '.';

type BusinessDocumentProjection = import('./types').BusinessDocumentProjection;
type BusinessDocumentCommandResult =
  import('./types').BusinessDocumentCommandResult;
type EvaDocumentChange = import('./types').EvaDocumentChange;

jest.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ children }: { children?: string }) => children ?? null,
}));

jest.mock('remark-gfm', () => ({
  __esModule: true,
  default: () => undefined,
}));

jest.mock('@/hooks/use-knowledge-request', () => ({
  useFetchKnowledgeList: () => ({ list: [], loading: false }),
}));

jest.mock('@/services/business-document-service', () => {
  const actual = jest.requireActual('@/services/business-document-service');
  return {
    ...actual,
    createBusinessDocument: jest.fn(),
    fetchBusinessDocument: jest.fn(),
    listBusinessDocuments: jest.fn(),
    submitBusinessDocumentCommand: jest.fn(),
    searchEvaDocumentSources: jest.fn(),
    createEvaDocumentChange: jest.fn(),
    listEvaDocumentChanges: jest.fn(),
    fetchEvaDocumentChange: jest.fn(),
    saveEvaDocumentChangeDraft: jest.fn(),
    approveEvaDocumentChange: jest.fn(),
    prepareEvaDocumentChange: jest.fn(),
    publishEvaDocumentChange: jest.fn(),
  };
});

const mockedCreate = jest.mocked(createBusinessDocument);
const mockedFetch = jest.mocked(fetchBusinessDocument);
const mockedList = jest.mocked(listBusinessDocuments);
const mockedSubmit = jest.mocked(submitBusinessDocumentCommand);
const mockedSearchEva = jest.mocked(searchEvaDocumentSources);
const mockedCreateEva = jest.mocked(createEvaDocumentChange);
const mockedListEva = jest.mocked(listEvaDocumentChanges);
const mockedFetchEva = jest.mocked(fetchEvaDocumentChange);
const mockedSaveEva = jest.mocked(saveEvaDocumentChangeDraft);
const mockedApproveEva = jest.mocked(approveEvaDocumentChange);
const mockedPrepareEva = jest.mocked(prepareEvaDocumentChange);
const mockedPublishEva = jest.mocked(publishEvaDocumentChange);

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
  allowed_actions: ['SAVE_DRAFT', 'APPROVE'],
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
  return render(
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
  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

beforeEach(() => {
  jest.clearAllMocks();
  mockedFetch.mockResolvedValue(projection);
  mockedList.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 20,
  });
  mockedListEva.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 20,
  });
  mockedFetchEva.mockResolvedValue(evaChange);
  mockedSaveEva.mockResolvedValue(evaChange);
  mockedApproveEva.mockResolvedValue(evaChange);
  mockedPrepareEva.mockResolvedValue(evaChange);
  mockedPublishEva.mockResolvedValue(evaChange);
  mockedSubmit.mockResolvedValue(commandResult);
  mockedCreate.mockResolvedValue(projection);
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
  expect(screen.getByTestId('apply-changes-button')).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'Документ' })).toBeNull();
  expect(screen.getAllByTestId('business-document-section')[2]).toHaveAttribute(
    'data-section-text',
    '| Флаг | Значение |\n| --- | --- |\n| True | None |\n| False | 7 |',
  );
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

test('renders completed exports with their own revision and canonical download URL', async () => {
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
  expect(screen.getByRole('button', { name: 'EvaWiki' })).toBeInTheDocument();
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

test('records proposal decisions and author comments as commands', async () => {
  renderPage();

  fireEvent.click(await screen.findByTestId('accept-proposal-proposal-12'));
  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenLastCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'DECIDE_PROPOSAL',
      payload: { proposal_id: 'proposal-12', decision: 'ACCEPTED' },
    }),
  );

  await waitFor(() =>
    expect(screen.getByRole('textbox', { name: 'Комментарий' })).toBeEnabled(),
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
  ).toHaveTextContent('Протокол пока пуст');
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

test('requests review assessment before applying and exports EvaWiki explicitly', async () => {
  mockedFetch.mockResolvedValueOnce({
    ...projection,
    allowed_commands: ['REQUEST_REVIEW_ASSESSMENT'],
  });
  const { unmount } = renderPage();

  fireEvent.click(
    await screen.findByRole('button', { name: 'Проверить согласование' }),
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
  fireEvent.click(await screen.findByRole('button', { name: 'EvaWiki' }));

  await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1));
  expect(mockedSubmit).toHaveBeenLastCalledWith(
    'doc-1',
    expect.objectContaining({
      type: 'REQUEST_EXPORT',
      payload: { revision_id: 'revision-3', format: 'EVA_WIKI' },
    }),
  );
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

test('creates a new business requirements document', async () => {
  renderPage('/business-documents');

  fireEvent.change(
    screen.getByRole('textbox', { name: 'Название документа' }),
    {
      target: { value: 'Новый продукт' },
    },
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Описание идеи' }), {
    target: { value: 'Нужен новый клиентский сценарий.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Начать работу' }));

  await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
  expect(mockedCreate.mock.calls[0][0]).toEqual({
    schema_version: '1',
    document_type: 'business_requirements',
    title: 'Новый продукт',
    idea: 'Нужен новый клиентский сценарий.',
    dataset_ids: [],
  });
  expect(
    await screen.findByTestId('business-document-workbench'),
  ).toBeVisible();
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

test('finds an existing EVA document and opens a pinned change request', async () => {
  mockedSearchEva.mockResolvedValueOnce({
    connectors: [{ connector_id: 'connector-1', connector_name: 'EVA Wiki' }],
    items: [
      {
        connector_id: 'connector-1',
        connector_name: 'EVA Wiki',
        id: 'CmfDocument:doc-1',
        name: 'Переводы одной кнопкой',
        code: 'BR-42',
        project_id: 'project-1',
        version: '1|version-1|2026-08-26',
        modified_at: '2026-08-26T09:00:00+03:00',
        web_url: 'https://eva.example.com/project/Document/BR-42',
        excerpt: 'Существующие бизнес-требования',
      },
    ],
  });
  mockedCreateEva.mockResolvedValueOnce(evaChange);
  renderPage('/business-documents');

  fireEvent.click(screen.getByTestId('eva-change-mode'));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Поиск документа EVA' }),
    { target: { value: 'BR-42' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Найти документ EVA' }));
  fireEvent.click(await screen.findByTestId('eva-source-result'));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Описание доработки' }),
    { target: { value: 'Уточнить ожидаемый результат.' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Открыть доработку' }));

  await waitFor(() => expect(mockedCreateEva).toHaveBeenCalledTimes(1));
  expect(mockedCreateEva.mock.calls[0][0]).toEqual({
    connector_id: 'connector-1',
    document_id: 'CmfDocument:doc-1',
    change_summary: 'Уточнить ожидаемый результат.',
  });
  expect(await screen.findByTestId('eva-change-workbench')).toBeVisible();
  expect(mockedFetchEva).toHaveBeenCalledWith('change-1');
});

test('shows a section diff and saves EVA changes only in the local draft', async () => {
  renderPage('/business-documents/eva/change-1');

  expect(await screen.findByTestId('eva-change-workbench')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: /Diff/ }));
  expect(screen.getByTestId('eva-change-diff')).toHaveTextContent(
    'Старый текст.',
  );
  expect(screen.getByTestId('eva-change-diff')).toHaveTextContent(
    'Новый текст.',
  );

  fireEvent.click(screen.getByRole('button', { name: 'Черновик' }));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Черновик документа EVA' }),
    { target: { value: '# Требования\n\n## Цель\n\nЕщё точнее.' } },
  );
  fireEvent.click(screen.getByTestId('save-eva-change-draft'));

  await waitFor(() =>
    expect(mockedSaveEva).toHaveBeenCalledWith('change-1', {
      expected_state_version: 2,
      draft_markdown: '# Требования\n\n## Цель\n\nЕщё точнее.',
    }),
  );
  expect(mockedPrepareEva).not.toHaveBeenCalled();
  expect(mockedPublishEva).not.toHaveBeenCalled();
  expect(mockedApproveEva).not.toHaveBeenCalled();
});
