import en from '@/locales/en';
import ru from '@/locales/ru';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { createInstance } from 'i18next';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { useTranslation } from 'react-i18next';
import { MemoryRouter } from 'react-router';

import {
  getDocumentQuality,
  getDocumentQualityCampaign,
  getDocumentQualityHealth,
  getDocumentQualityJobs,
  getDocumentQualityRun,
  getSpecificationOperations,
  listDocumentQualityCampaigns,
  listDocumentQualityModels,
  listDocumentQualityRuns,
  startDocumentQualityRun,
} from '@/services/admin-service';
import AdminBusinessDocumentsQuality from './business-documents-quality';

jest.mock('react-i18next', () => ({
  useTranslation: jest.fn(() => ({
    t: (key: string) => key.split('.').pop(),
    i18n: { language: 'en' },
  })),
}));
jest.mock('@/services/admin-service', () => ({
  getDocumentQuality: jest.fn(),
  getDocumentQualityHealth: jest.fn().mockResolvedValue({
    data: {
      code: 0,
      data: { status: 'OK', reason_code: null, slot: '2026-10-20' },
    },
  }),
  getDocumentQualityCampaign: jest.fn(),
  getDocumentQualityJobs: jest.fn(),
  getDocumentQualityRun: jest.fn(),
  getSpecificationOperations: jest.fn(),
  listDocumentQualityCampaigns: jest.fn(),
  listDocumentQualityModels: jest.fn(),
  listDocumentQualityRuns: jest.fn(),
  startDocumentQualityRun: jest.fn(),
}));

const report = {
  status: 'FAIL',
  model: 'qwen',
  expected_cases: 5,
  executed_cases: 5,
  weighted_score: 3.9,
  p0_case_pass_rate: 0.8,
  duration_ms: 120000,
  total_tokens: 4000,
  cases: [
    {
      case_id: 'M04',
      priority: 'P0',
      status: 'FAIL',
      failure_count: 1,
      diagnostics: [{ code: 'MISSING_FACT', fact_id: 'scenario_rule' }],
      gate_checks: [],
    },
  ],
};
const run = {
  id: 'run-1',
  trigger: 'NIGHTLY',
  status: 'FAIL',
  scope: 'FULL',
  campaign_id: 'campaign-1',
  model_name: 'qwen',
  model_digest: 'a'.repeat(64),
  case_id: null,
  source_revision: 'rev',
  requested_at: 1789855254167,
  started_at: 1789855254167,
  finished_at: 1789855354167,
  reason_code: 'QUALITY_GATE',
  reason: null,
  report,
};
const campaign = {
  id: 'campaign-1',
  status: 'COMPLETE',
  source_revision: 'rev',
  requested_at: 1789855254167,
  started_at: 1789855254167,
  finished_at: 1789855354167,
  baseline_status: 'PASS',
  baseline_report: null,
  reason_code: null,
  models: null,
  runs: [run],
};
const operations = {
  updated_at: 1789855354167,
  days: 7,
  sampled_jobs: 3,
  sampled_completed_jobs: 0,
  truncated: false,
  terminal_jobs: 3,
  completed: 0,
  failed: 3,
  pending: 1,
  retrying: 0,
  running: 0,
  failure_rate: 1,
  failed_documents: 1,
  terminal_documents: 1,
  affected_tenants: 1,
  latency_p95_ms: 1250,
  measured_latency_jobs: 3,
  model_latency_p95_ms: null,
  measured_model_latency_jobs: 0,
  total_tokens: 0,
  measured_token_jobs: 0,
  tasks: [
    {
      task_type: 'ASSESS_REVIEW',
      category: 'DOCUMENT_AI',
      completed: 0,
      dead: 3,
      pending: 1,
      retry: 0,
      running: 0,
    },
  ],
  models: [],
  errors: [
    { task_type: 'ASSESS_REVIEW', error_code: 'INVALID_AI_JSON', count: 2 },
  ],
};
const failedJob = {
  id: 'job-1',
  document_id: 'doc-1',
  task_type: 'ASSESS_REVIEW',
  category: 'DOCUMENT_AI',
  status: 'DEAD',
  finished_at: 1789855354167,
  error_code: 'INVALID_AI_JSON',
  attempt: 3,
  max_attempts: 3,
};

const specificationOperations = {
  document_type: 'technical_specification',
  scope: 'RECORDED_ALIGNMENT_ATTEMPTS',
  days: 7,
  updated_at: 1789855354167,
  earliest_observation_at: null,
  counts: { total: 0, execution_complete: 0, reports_complete: 0 },
  terminal_attempts: 0,
  execution_completion_rate: null,
  report_completion_rate: null,
  total_tokens: null,
  known_spent_tokens: 0,
  reserved_tokens: null,
  known_reserved_tokens: 0,
  durations: {
    check: { measured: 0, mean_ms: null, p95_ms: null },
    model: { measured: 0, mean_ms: null, p95_ms: null },
  },
  reasons: [],
};

function response<T>(data: T) {
  return { data: { code: 0, data } } as never;
}

function mount(path = '/admin/document-quality') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={client}>
        <AdminBusinessDocumentsQuality />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(useTranslation).mockReturnValue({
    t: (key: string) => key.split('.').pop(),
    i18n: { language: 'en' },
  } as never);
  jest
    .mocked(getDocumentQualityHealth)
    .mockResolvedValue(
      response({ status: 'OK', reason_code: null, slot: '2026-10-20' }),
    );
  jest.mocked(getDocumentQuality).mockResolvedValue(response(operations));
  jest
    .mocked(getSpecificationOperations)
    .mockResolvedValue(response(specificationOperations));
  jest.mocked(getDocumentQualityJobs).mockResolvedValue(
    response({
      days: 7,
      updated_at: 1789855354167,
      total: 1,
      offset: 0,
      limit: 50,
      error_codes: ['INVALID_AI_JSON'],
      jobs: [failedJob],
    }),
  );
  jest
    .mocked(listDocumentQualityRuns)
    .mockResolvedValue(
      response({ configured: true, source_revision: 'rev', runs: [run] }),
    );
  jest
    .mocked(listDocumentQualityCampaigns)
    .mockResolvedValue(response({ campaigns: [campaign] }));
  jest.mocked(getDocumentQualityCampaign).mockResolvedValue(response(campaign));
  jest.mocked(getDocumentQualityRun).mockResolvedValue(response(run));
  jest.mocked(listDocumentQualityModels).mockResolvedValue(
    response({
      configured: true,
      case_ids: ['M04'],
      models: [{ name: 'qwen', digest: 'a'.repeat(64), aliases: ['qwen'] }],
      errors: [],
    }),
  );
  jest.mocked(startDocumentQualityRun).mockResolvedValue(response(run));
});

test('technical specifications isolate queries and do not display cached BT operations', async () => {
  mount();
  expect(await screen.findByText(/3\/3/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: 'product' }), {
    target: { value: 'technical_specification' },
  });
  expect(
    await screen.findByRole('heading', { name: 'technicalTitle' }),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(listDocumentQualityRuns).toHaveBeenLastCalledWith(
      'technical_specification',
    ),
  );
  expect(listDocumentQualityCampaigns).toHaveBeenLastCalledWith(
    'technical_specification',
  );
  expect(listDocumentQualityModels).toHaveBeenLastCalledWith(
    'technical_specification',
  );
  expect(screen.queryByText(/3\/3/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole('tab', { name: 'operations' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('tab', { name: 'technicalOperationsTab' }),
  ).toBeInTheDocument();
  expect(getSpecificationOperations).not.toHaveBeenCalled();
  jest.mocked(getDocumentQuality).mockClear();
  jest.mocked(getDocumentQualityJobs).mockClear();
  fireEvent.click(screen.getByRole('button', { name: 'refresh' }));
  await waitFor(() =>
    expect(getDocumentQualityHealth).toHaveBeenLastCalledWith(
      'technical_specification',
    ),
  );
  expect(getDocumentQuality).not.toHaveBeenCalled();
  expect(getDocumentQualityJobs).not.toHaveBeenCalled();
});

test('technical operations use their own cohort and preserve unknown usage across window changes', async () => {
  jest.mocked(getSpecificationOperations).mockResolvedValue(
    response({
      ...specificationOperations,
      counts: {
        total: 2,
        execution_complete: 1,
        reports_complete: 1,
        usage_unknown: 1,
      },
      terminal_attempts: 2,
      execution_completion_rate: 0.5,
      report_completion_rate: 0.5,
      known_spent_tokens: 1234,
      known_reserved_tokens: 30,
      reserved_tokens: 30,
      reasons: [{ code: 'EXECUTION_CANCELLED', count: 1 }],
    }),
  );
  mount('/admin/document-quality?product=technical_specification');
  fireEvent.click(screen.getByRole('tab', { name: 'technicalOperationsTab' }));
  const panel = await screen.findByRole('region', { name: 'title' });
  expect(getSpecificationOperations).toHaveBeenLastCalledWith(7);
  expect(within(panel).getByText('cohortNote')).toBeInTheDocument();
  expect(
    within(panel).getByText('total_tokens').nextElementSibling,
  ).toHaveTextContent('—');
  expect(
    within(panel).getByText('known_spent_tokens').nextElementSibling,
  ).toHaveTextContent('1,234');
  expect(within(panel).getAllByText(/50.0%.*1\/2/)).toHaveLength(2);
  expect(within(panel).getByText('EXECUTION_CANCELLED: 1')).toBeInTheDocument();
  for (const days of [1, 30]) {
    fireEvent.click(screen.getByRole('button', { name: String(days) }));
    await waitFor(() =>
      expect(getSpecificationOperations).toHaveBeenLastCalledWith(days),
    );
  }
  jest.mocked(getSpecificationOperations).mockClear();
  fireEvent.click(screen.getByRole('button', { name: 'refresh' }));
  await waitFor(() =>
    expect(getSpecificationOperations).toHaveBeenCalledWith(30),
  );
  expect(getDocumentQuality).not.toHaveBeenCalled();
  expect(getDocumentQualityJobs).not.toHaveBeenCalled();
});

test('technical operations show an empty cohort and hide stale values after failure', async () => {
  mount('/admin/document-quality?product=technical_specification');
  fireEvent.click(screen.getByRole('tab', { name: 'technicalOperationsTab' }));
  expect(
    await screen.findByText('noTechnicalObservations'),
  ).toBeInTheDocument();
  jest
    .mocked(getSpecificationOperations)
    .mockRejectedValue(new Error('synthetic unavailable'));
  fireEvent.click(screen.getByRole('button', { name: 'refresh' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('loadError');
  expect(screen.queryByText('noTechnicalObservations')).not.toBeInTheDocument();
});

test('a failed history request does not claim the QA tenant is unconfigured', async () => {
  jest
    .mocked(listDocumentQualityRuns)
    .mockRejectedValue(new Error('unavailable'));
  mount();
  expect(await screen.findByRole('alert')).toHaveTextContent('runsLoadError');
  expect(screen.queryByText('notConfigured')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'runNow' })).toBeDisabled();
});

test('separates model quality and exact operational denominators', async () => {
  mount();
  expect(await screen.findByText(/3\/3/)).toBeInTheDocument();
  expect(screen.getAllByText(/1\/1/).length).toBeGreaterThanOrEqual(2);
  expect(screen.getByRole('tab', { name: 'comparisons' })).toBeInTheDocument();
  expect(
    screen.getByText('sourceVersion: identityUnknown · sourceBuild: rev'),
  ).toBeInTheDocument();
});

test('shows the model that is currently being checked', async () => {
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(
    response({
      configured: true,
      source_revision: 'rev',
      runs: [{ ...run, status: 'RUNNING' }],
    }),
  );
  mount();
  expect(await screen.findByRole('status')).toHaveTextContent(
    'testInProgress: qwen',
  );
});

test('shows complete nightly comparison and safe failed-case diagnosis', async () => {
  mount('/admin/document-quality?tab=comparisons');
  expect(await screen.findByText('3.90/4')).toBeInTheDocument();
  const row = screen.getByRole('button', { name: 'qwen' }).closest('tr')!;
  expect(within(row).getAllByRole('cell')[0]).toHaveTextContent('1');
  fireEvent.click(screen.getByRole('button', { name: 'qwen' }));
  expect(await screen.findByText(/scenario_rule/)).toBeInTheDocument();
  expect(getDocumentQualityRun).toHaveBeenCalledWith('run-1');
});

test('does not score incomplete model runs', async () => {
  jest.mocked(listDocumentQualityCampaigns).mockResolvedValue(
    response({
      campaigns: [
        {
          ...campaign,
          status: 'PARTIAL',
          runs: [
            {
              ...run,
              status: 'INCOMPLETE',
              report: { ...report, executed_cases: 1 },
            },
          ],
        },
      ],
    }),
  );
  jest.mocked(getDocumentQualityCampaign).mockResolvedValue(
    response({
      ...campaign,
      status: 'PARTIAL',
      runs: [
        {
          ...run,
          status: 'INCOMPLETE',
          report: { ...report, executed_cases: 1 },
        },
      ],
    }),
  );
  mount('/admin/document-quality?tab=comparisons');
  await screen.findByText('1/5');
  expect(screen.queryByText('3.90/4')).not.toBeInTheDocument();
});

test.each(['business_requirements', 'technical_specification'])(
  'an incomplete %s series shows execution and reasons without empty ranking columns',
  async (product) => {
    await useLocale('ru');
    const partial = {
      ...run,
      id: 'partial-model',
      status: 'INCOMPLETE',
      model_name: 'partial-model',
      reason_code: 'MODEL_TIMEOUT',
      report: {
        ...report,
        executed_cases: 2,
        expected_cases: 6,
        duration_ms: 352000,
        total_tokens: 72554,
      },
    };
    const missing = {
      ...run,
      id: 'missing-report',
      status: 'INCOMPLETE',
      model_name: 'missing-report',
      reason_code: 'WORKER_STOPPED',
      report: null,
    };
    const series = { ...campaign, status: 'PARTIAL', runs: [partial, missing] };
    jest
      .mocked(listDocumentQualityCampaigns)
      .mockResolvedValue(response({ campaigns: [series] }));
    jest.mocked(getDocumentQualityCampaign).mockResolvedValue(response(series));
    mount(`/admin/document-quality?product=${product}&tab=comparisons`);
    const table = await screen.findByRole('table', {
      name: 'Сравнение моделей',
    });
    expect(
      within(table).queryByRole('columnheader', { name: 'Место' }),
    ).not.toBeInTheDocument();
    expect(
      within(table).queryByRole('columnheader', { name: 'Балл' }),
    ).not.toBeInTheDocument();
    expect(
      within(table).queryByRole('columnheader', {
        name: 'Критические сценарии',
      }),
    ).not.toBeInTheDocument();
    expect(
      within(table).getByRole('columnheader', { name: 'Выполнено' }),
    ).toBeInTheDocument();
    const row = within(table)
      .getByRole('button', { name: 'partial-model' })
      .closest('tr')!;
    expect(row).toHaveTextContent('2/6');
    expect(row).toHaveTextContent(/5 мин.*52 с/);
    expect(row).toHaveTextContent(/72\s554/);
    const reasonCell = within(row)
      .getByText('Модель не ответила за отведённое время.')
      .closest('td')!;
    expect(reasonCell).toHaveTextContent('Не завершена');
    expect(reasonCell).not.toHaveTextContent('partial-model');
    const unknown = within(table)
      .getByRole('button', { name: 'missing-report' })
      .closest('tr')!;
    expect(unknown).toHaveTextContent('Нет отчёта');
    expect(unknown).not.toHaveTextContent('0/—');
    expect(screen.getByText('Рейтинг недоступен')).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: 'partial-model' }));
    await waitFor(() => expect(getDocumentQualityRun).toHaveBeenCalled());
  },
);

test('an unknown planned case count preserves a recorded execution count', async () => {
  await useLocale('ru');
  const series = {
    ...campaign,
    status: 'PARTIAL',
    runs: [{ ...run, status: 'INCOMPLETE', report: { executed_cases: 2 } }],
  };
  jest
    .mocked(listDocumentQualityCampaigns)
    .mockResolvedValue(response({ campaigns: [series] }));
  jest.mocked(getDocumentQualityCampaign).mockResolvedValue(response(series));
  mount('/admin/document-quality?tab=comparisons');
  expect(await screen.findByText('2 · план неизвестен')).toBeInTheDocument();
});

async function useLocale(language: 'ru' | 'en') {
  const i18n = createInstance();
  await i18n.init({
    lng: language,
    resources: { ru, en },
    interpolation: { escapeValue: false },
  });
  jest
    .mocked(useTranslation)
    .mockReturnValue({ t: i18n.t.bind(i18n), i18n } as never);
}

test('Russian comparison separates processed runs from complete reports and explains a 6/6 incomplete run', async () => {
  await useLocale('ru');
  const incomplete = {
    ...run,
    id: 'incomplete',
    model_name: 'incomplete-model',
    status: 'INCOMPLETE',
    reason_code: 'RUN_INCOMPLETE',
    report: { ...report, executed_cases: 6, expected_cases: 6 },
  };
  const running = {
    ...run,
    id: 'running',
    model_name: 'running-model',
    status: 'RUNNING',
    report: null,
  };
  const series = {
    ...campaign,
    status: 'PARTIAL',
    runs: [run, incomplete, running],
  };
  jest
    .mocked(listDocumentQualityCampaigns)
    .mockResolvedValue(response({ campaigns: [series] }));
  jest.mocked(getDocumentQualityCampaign).mockResolvedValue(response(series));
  jest
    .mocked(getDocumentQualityHealth)
    .mockResolvedValue(
      response({ status: 'ALERT', reason_code: 'NIGHTLY_INCOMPLETE' }),
    );
  mount('/admin/document-quality?tab=comparisons');
  const row = (
    await screen.findByRole('button', { name: 'incomplete-model' })
  ).closest('tr')!;
  expect(
    screen.getByRole('columnheader', { name: 'Место' }),
  ).toBeInTheDocument();
  const totals = screen.getByLabelText('Итоги серии');
  expect(
    within(totals).getByText('Обработано запусков').nextElementSibling,
  ).toHaveTextContent('2/3');
  expect(
    within(totals).getByText('Полных результатов').nextElementSibling,
  ).toHaveTextContent('1');
  expect(
    within(totals).getByText('Неполных отчётов').nextElementSibling,
  ).toHaveTextContent('1');
  expect(row).toHaveTextContent('6/6');
  expect(row).toHaveTextContent('один или несколько сценариев не завершились');
  expect(within(row).getAllByRole('cell')[0]).toHaveTextContent('Нет оценки');
  expect(within(row).getAllByRole('cell')[5]).toHaveTextContent('нет данных');
  expect(within(row).getByText(/2 мин.*0 с/)).toBeInTheDocument();
  expect(
    screen.getByRole('combobox', { name: 'Ночная серия' }),
  ).toHaveTextContent('Серия завершена частично');
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Ночная серия не дала полного результата.',
  );
  expect(document.body.textContent).not.toMatch(
    /admin\.businessDocumentsQualityPage|PARTIAL|NIGHTLY_INCOMPLETE/,
  );
  expect(getDocumentQualityRun).not.toHaveBeenCalled();
  fireEvent.click(
    within(row).getByRole('button', { name: 'incomplete-model' }),
  );
  expect(
    await screen.findByRole('heading', { name: 'Результаты сценариев' }),
  ).toBeInTheDocument();
});

test.each(['ru', 'en'] as const)(
  'unknown reason and missing report have a readable %s fallback without a score',
  async (language) => {
    await useLocale(language);
    const unknown = {
      ...run,
      status: 'INCOMPLETE',
      report: null,
      reason_code: 'NEW_REASON_CODE',
    };
    const series = { ...campaign, runs: [unknown] };
    jest
      .mocked(listDocumentQualityCampaigns)
      .mockResolvedValue(response({ campaigns: [series] }));
    jest.mocked(getDocumentQualityCampaign).mockResolvedValue(response(series));
    mount('/admin/document-quality?tab=comparisons');
    const row = (await screen.findByRole('button', { name: 'qwen' })).closest(
      'tr',
    )!;
    expect(row).toHaveTextContent(
      language === 'ru'
        ? 'отчёт не завершён или не подтверждён'
        : 'report is incomplete or unverified',
    );
    expect(row).not.toHaveTextContent('NEW_REASON_CODE');
    expect(within(row).getByTitle('NEW_REASON_CODE')).toBeInTheDocument();
    expect(row).toHaveTextContent(
      language === 'ru' ? 'Нет отчёта' : 'No execution report',
    );
    expect(row).not.toHaveTextContent('0/—');
    expect(
      screen.queryByRole('columnheader', {
        name: language === 'ru' ? 'Балл' : 'Score',
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('columnheader', {
        name: language === 'ru' ? 'Место' : 'Rank',
      }),
    ).not.toBeInTheDocument();
  },
);

test('an empty comparison does not invent completed or incomplete results', async () => {
  await useLocale('ru');
  const series = { ...campaign, runs: [] };
  jest
    .mocked(listDocumentQualityCampaigns)
    .mockResolvedValue(response({ campaigns: [series] }));
  jest.mocked(getDocumentQualityCampaign).mockResolvedValue(response(series));
  mount('/admin/document-quality?tab=comparisons');
  expect(await screen.findByLabelText('Итоги серии')).toHaveTextContent(
    'Обработано запусков0/0Полных результатов0Неполных отчётов0',
  );
});

test('shows the safe M06 job error and failed requirement check', async () => {
  jest.mocked(getDocumentQualityRun).mockResolvedValue(
    response({
      ...run,
      report: {
        ...report,
        cases: [
          {
            case_id: 'M06',
            status: 'FAIL',
            failure_count: 2,
            diagnostics: [
              { code: 'JOB_FAILED', job_code: 'REQUIREMENT_COVERAGE_FAILED' },
              {
                code: 'REQUIREMENT',
                requirement_id: 'latency',
                check_id: 'threshold',
              },
            ],
          },
        ],
      },
    }),
  );
  mount('/admin/document-quality?tab=history');
  fireEvent.click(await screen.findByRole('button', { name: /qwen/ }));
  expect(
    await screen.findByText(/JOB_FAILED: REQUIREMENT_COVERAGE_FAILED/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/REQUIREMENT: latency: threshold/),
  ).toBeInTheDocument();
});

test('manual one-case run is sent as a diagnostic request', async () => {
  mount();
  await screen.findByRole('button', { name: 'runNow' });
  fireEvent.change(screen.getByLabelText('runScope'), {
    target: { value: 'CASE' },
  });
  fireEvent.change(screen.getByLabelText('cases'), {
    target: { value: 'M04' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'runNow' }));
  await waitFor(() =>
    expect(startDocumentQualityRun).toHaveBeenCalledWith({
      scope: 'CASE',
      case_id: 'M04',
    }),
  );
});

test('shows old records without inventing missing details', async () => {
  jest
    .mocked(getDocumentQualityRun)
    .mockResolvedValue(
      response({ ...run, report: { ...report, cases: undefined } }),
    );
  mount('/admin/document-quality?tab=history');
  fireEvent.click(await screen.findByRole('button', { name: /qwen/ }));
  expect(await screen.findByText('detailsUnavailable')).toBeInTheDocument();
});

test('shows sample limitations for operations', async () => {
  jest
    .mocked(getDocumentQuality)
    .mockResolvedValue(response({ ...operations, truncated: true }));
  mount('/admin/document-quality?tab=operations&days=30');
  expect(await screen.findByText('truncated')).toBeInTheDocument();
  expect(getDocumentQuality).toHaveBeenCalledWith(30);
});

test.each(['source', 'prompt'])(
  'history comparison boundary depends on evaluation assets, not code: %s',
  async (change) => {
    const older = {
      ...run,
      id: 'run-old',
      source_revision: 'old-revision',
      report:
        change === 'prompt'
          ? { ...run.report!, prompt_hashes: { alignment: 'different' } }
          : run.report,
      requested_at: run.requested_at - 1000,
    };
    jest.mocked(listDocumentQualityRuns).mockResolvedValue(
      response({
        configured: true,
        source_revision: 'rev',
        runs: [run, older],
      }),
    );
    mount(`/admin/document-quality?tab=history&model=${run.model_digest}`);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /qwen/ })).toHaveLength(2),
    );
    if (change === 'prompt') {
      expect(screen.getByText('comparisonBoundary')).toBeInTheDocument();
      expect(screen.getByText('historyBoundaryNote')).toBeInTheDocument();
    } else {
      expect(screen.queryByText('comparisonBoundary')).not.toBeInTheDocument();
    }
  },
);

test('loads failed jobs with server-side type and code filters', async () => {
  mount('/admin/document-quality?tab=operations&days=7');
  expect(await screen.findByText(/job-1/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('taskTypeFilter'), {
    target: { value: 'ASSESS_REVIEW' },
  });
  await waitFor(() =>
    expect(getDocumentQualityJobs).toHaveBeenCalledWith(
      expect.objectContaining({ task_type: 'ASSESS_REVIEW', offset: 0 }),
    ),
  );
});

test('independent missing-night warning remains visible on the TZ dashboard', async () => {
  jest.mocked(getDocumentQualityHealth).mockResolvedValue(
    response({
      status: 'ALERT',
      reason_code: 'NIGHTLY_MISSING',
      slot: '2026-10-20',
    }),
  );
  mount('/admin/document-quality?product=technical_specification');
  expect(await screen.findByRole('alert')).toHaveTextContent('NIGHTLY_MISSING');
  expect(getDocumentQualityHealth).toHaveBeenCalledWith(
    'technical_specification',
  );
});

test.each([true, false])(
  'shows semantic fractions with explicit denominators; complete=%s',
  async (complete) => {
    jest.mocked(listDocumentQualityRuns).mockResolvedValue(
      response({
        configured: true,
        source_revision: 'rev',
        runs: [
          {
            ...run,
            report: {
              ...report,
              quality_status: 'FAIL',
              evidence_status: 'INCOMPLETE',
              alignment_quality: {
                expected_fragments: 4,
                scored_fragments: complete ? 4 : 2,
                unscored_fragments: complete ? 0 : 2,
                undetermined_fragments: 1,
                contradiction_tp: 1,
                contradiction_fp: 0,
                contradiction_fn: complete ? 1 : 0,
                contradiction_tn: complete ? 2 : 1,
                measures: {
                  contradiction_recall: {
                    numerator: 1,
                    denominator: 2,
                    value: complete ? 0.5 : null,
                  },
                  unsupported_rule_recall: {
                    numerator: 0,
                    denominator: 0,
                    value: null,
                  },
                },
              },
            },
          },
        ],
      }),
    );
    mount('/admin/document-quality?product=technical_specification');
    const region = await screen.findByRole('region', {
      name: 'semanticQuality',
    });
    const recall = within(region).getByRole('row', {
      name: /contradiction_recall/,
    });
    expect(recall).toHaveTextContent('1/2');
    expect(recall).toHaveTextContent(complete ? '50.0%' : 'metricIncomplete');
    expect(
      within(region).getByRole('row', { name: /unsupported_rule_recall/ }),
    ).toHaveTextContent('metricNotApplicable0/0');
    expect(region).toHaveTextContent('semanticQualityNote');
  },
);

test.each([false, true])(
  'nightly experiments retain unknown usage and selected-run scope; history=%s',
  async (history) => {
    const latest = {
      ...run,
      report: {
        ...report,
        nightly_experiments: [
          {
            experiment: 'REPEATS',
            quality_status: 'PASS',
            evidence_status: 'INCOMPLETE',
            expected_cases: 112,
            executed_cases: 112,
            total_tokens: 100,
          },
        ],
      },
    };
    const older = {
      ...run,
      id: 'old-night',
      model_name: 'old-night-model',
      report: {
        ...report,
        nightly_experiments: [
          {
            experiment: 'REPEATS',
            quality_status: 'UNKNOWN',
            evidence_status: 'INCOMPLETE',
            expected_cases: 112,
            executed_cases: 6,
            total_tokens: null,
          },
        ],
      },
    };
    jest.mocked(listDocumentQualityRuns).mockResolvedValue(
      response({
        configured: true,
        source_revision: 'rev',
        runs: [latest, older],
      }),
    );
    jest.mocked(getDocumentQualityRun).mockResolvedValue(response(older));
    mount(
      `/admin/document-quality?product=technical_specification${history ? '&tab=history' : ''}`,
    );
    if (history)
      fireEvent.click(
        await screen.findByRole('button', { name: /old-night-model/ }),
      );
    const region = await screen.findByRole('region', {
      name: 'nightlyExperiments',
    });
    const row = within(region).getByRole('row', { name: /REPEATS/ });
    expect(row).toHaveTextContent(history ? '6/112' : '112/112');
    expect(row).toHaveTextContent(history ? 'UNKNOWN' : 'PASS');
    expect(row).toHaveTextContent('INCOMPLETE');
    expect(within(row).getAllByRole('cell').at(-1)).toHaveTextContent(
      history ? '—' : '100',
    );
  },
);

test('historical semantic metrics belong to the selected run', async () => {
  const older = {
    ...run,
    id: 'old-run',
    model_name: 'old-model',
    source_revision: 'old-source',
    report: {
      ...report,
      quality_status: 'FAIL',
      evidence_status: 'INCOMPLETE',
      alignment_quality: {
        expected_fragments: 1,
        scored_fragments: 1,
        unscored_fragments: 0,
        undetermined_fragments: 0,
        contradiction_tp: 0,
        contradiction_fp: 0,
        contradiction_fn: 1,
        contradiction_tn: 0,
        measures: {
          contradiction_recall: { numerator: 0, denominator: 1, value: 0 },
        },
      },
    },
  };
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(
    response({
      configured: true,
      source_revision: 'rev',
      runs: [run, older],
    }),
  );
  jest.mocked(getDocumentQualityRun).mockResolvedValue(response(older));
  mount('/admin/document-quality?product=technical_specification&tab=history');
  fireEvent.click(await screen.findByRole('button', { name: /old-model/ }));
  const region = await screen.findByRole('region', { name: 'semanticQuality' });
  expect(
    within(region).getByRole('row', { name: /contradiction_recall/ }),
  ).toHaveTextContent('0.0%0/1');
  expect(getDocumentQualityRun).toHaveBeenCalledWith(
    'old-run',
    'technical_specification',
  );
  expect(region.parentElement).toHaveTextContent('sourceVersion');
});

test('history details stay beside their selected row, expose context and return focus on close', async () => {
  await useLocale('ru');
  const second = { ...run, id: 'second-run', model_name: 'second-model' };
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(
    response({
      configured: true,
      source_revision: 'rev',
      runs: [run, second],
    }),
  );
  mount('/admin/document-quality?product=technical_specification&tab=history');
  const firstButton = await screen.findByRole('button', { name: /qwen/ });
  expect(firstButton).toHaveTextContent('Выполнено: 5/5');
  expect(screen.getByText('Выполнение / сценарий')).toBeInTheDocument();
  expect(firstButton).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(firstButton);
  const panel = await screen.findByRole('region', {
    name: 'Результаты сценариев',
  });
  expect(firstButton.parentElement).toContainElement(panel);
  expect(panel).toHaveTextContent('qwen');
  expect(panel).toHaveTextContent('20.09.2026');
  expect(firstButton).toHaveAttribute('aria-controls', panel.id);
  expect(firstButton).toHaveAttribute('data-selected', 'true');
  await waitFor(() =>
    expect(
      within(panel).getByRole('heading', { name: 'Результаты сценариев' }),
    ).toHaveFocus(),
  );
  expect(
    within(panel).queryByRole('button', { name: /second-model/ }),
  ).not.toBeInTheDocument();
  fireEvent.click(within(panel).getByRole('button', { name: 'Закрыть' }));
  expect(
    screen.queryByRole('region', { name: 'Результаты сценариев' }),
  ).not.toBeInTheDocument();
  expect(firstButton).toHaveFocus();
  expect(firstButton).toHaveAttribute('aria-expanded', 'false');
});

test.each([null, 'other-campaign'])(
  'only repeated reasons from the same series are summarized; third campaign=%s',
  async (otherCampaign) => {
    await useLocale('ru');
    const repeated = {
      ...run,
      status: 'INCOMPLETE',
      reason_code: 'BASELINE_FAILED',
      report: null,
    };
    jest.mocked(getDocumentQualityRun).mockResolvedValue(response(repeated));
    const rows = [
      repeated,
      { ...repeated, id: 'same-series', model_name: 'same-series' },
      {
        ...repeated,
        id: 'other-series',
        model_name: 'other-series',
        campaign_id: otherCampaign,
      },
    ];
    jest
      .mocked(listDocumentQualityRuns)
      .mockResolvedValue(
        response({ configured: true, source_revision: 'rev', runs: rows }),
      );
    mount(
      '/admin/document-quality?product=technical_specification&tab=history',
    );
    const first = await screen.findByRole('button', { name: /qwen/ });
    expect(first).not.toHaveTextContent('Базовая проверка не пройдена');
    expect(
      screen.getByRole('button', { name: /same-series/ }),
    ).not.toHaveTextContent('Базовая проверка не пройдена');
    expect(
      screen.getByRole('button', { name: /other-series/ }),
    ).toHaveTextContent('Базовая проверка не пройдена');
    expect(screen.getByText(/запусков: 2/)).toHaveTextContent(
      'Базовая проверка не пройдена',
    );
    fireEvent.click(first);
    const panel = await screen.findByRole('region', {
      name: 'Результаты сценариев',
    });
    await waitFor(() =>
      expect(panel).toHaveTextContent('Базовая проверка не пройдена'),
    );
  },
);

test('history pages contain 25 runs, close old details and reset on filter changes', async () => {
  await useLocale('ru');
  const rows = Array.from({ length: 52 }, (_, index) => ({
    ...run,
    id: `history-${index}`,
    model_name: `history-model-${index}`,
    status: index % 2 ? 'FAIL' : 'PASS',
  }));
  jest
    .mocked(listDocumentQualityRuns)
    .mockResolvedValue(
      response({ configured: true, source_revision: 'rev', runs: rows }),
    );
  mount('/admin/document-quality?product=technical_specification&tab=history');
  const history = await screen.findByRole('region', {
    name: 'История проверок',
  });
  await within(history).findByRole('button', { name: /^history-model-0 / });
  const pageRows = () =>
    within(history).getAllByRole('button', { name: /^history-model-/ });
  expect(pageRows()).toHaveLength(25);
  expect(screen.getByText('1–25/52')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Назад' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
  expect(pageRows()).toHaveLength(25);
  expect(screen.getByText('26–50/52')).toBeInTheDocument();
  jest.mocked(getDocumentQualityRun).mockResolvedValue(response(rows[25]));
  fireEvent.click(
    within(history).getByRole('button', { name: /^history-model-25 / }),
  );
  expect(
    await screen.findByRole('region', { name: 'Результаты сценариев' }),
  ).toHaveTextContent('history-model-25');
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
  expect(pageRows()).toHaveLength(2);
  expect(screen.getByText('51–52/52')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled();
  expect(
    screen.queryByRole('region', { name: 'Результаты сценариев' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
  expect(screen.getByText('26–50/52')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: 'Статус запуска' }), {
    target: { value: 'PASS' },
  });
  expect(screen.getByText('1–25/26')).toBeInTheDocument();
  expect(pageRows()).toHaveLength(25);
});

test('a page beyond the history end shows the last available page', async () => {
  await useLocale('ru');
  const rows = Array.from({ length: 26 }, (_, index) => ({
    ...run,
    id: `history-${index}`,
    model_name: `history-model-${index}`,
  }));
  jest
    .mocked(listDocumentQualityRuns)
    .mockResolvedValue(
      response({ configured: true, source_revision: 'rev', runs: rows }),
    );
  mount('/admin/document-quality?tab=history&historyPage=999');
  expect(await screen.findByText('26–26/26')).toBeInTheDocument();
  expect(
    screen.getAllByRole('button', { name: /^history-model-/ }),
  ).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled();
});

test('details selected in comparisons remain inside the bounded history when its page excludes the run', async () => {
  await useLocale('ru');
  const rows = Array.from({ length: 30 }, (_, index) => ({
    ...run,
    id: `history-${index}`,
    model_name: `history-model-${index}`,
  }));
  jest
    .mocked(listDocumentQualityRuns)
    .mockResolvedValue(
      response({ configured: true, source_revision: 'rev', runs: rows }),
    );
  jest
    .mocked(getDocumentQualityCampaign)
    .mockResolvedValue(response({ ...campaign, runs: [rows[0]] }));
  jest.mocked(getDocumentQualityRun).mockResolvedValue(response(rows[0]));
  mount('/admin/document-quality?tab=comparisons&historyPage=2');
  fireEvent.click(
    await screen.findByRole('button', { name: 'history-model-0' }),
  );
  await screen.findByRole('region', { name: 'Результаты сценариев' });
  fireEvent.click(screen.getByRole('tab', { name: 'История проверок' }));
  const history = await screen.findByRole('region', {
    name: 'История проверок',
  });
  expect(
    within(history).getByRole('region', { name: 'Результаты сценариев' }),
  ).toHaveTextContent('history-model-0');
  expect(
    within(history).getAllByRole('button', { name: /^history-model-/ }),
  ).toHaveLength(5);
  expect(screen.getByText('26–30/30')).toBeInTheDocument();
});

test('a repeated series reason is visible again when the series spans history pages', async () => {
  await useLocale('ru');
  const rows = Array.from({ length: 30 }, (_, index) => ({
    ...run,
    id: `history-${index}`,
    model_name: `history-model-${index}`,
    status: 'INCOMPLETE',
    reason_code: 'BASELINE_FAILED',
    report: null,
  }));
  jest
    .mocked(listDocumentQualityRuns)
    .mockResolvedValue(
      response({ configured: true, source_revision: 'rev', runs: rows }),
    );
  mount('/admin/document-quality?tab=history');
  expect(await screen.findByText(/запусков: 25/)).toHaveTextContent(
    'Базовая проверка не пройдена',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
  expect(screen.getByText(/запусков: 5/)).toHaveTextContent(
    'Базовая проверка не пройдена',
  );
});

test('TZ model comparisons page 52 models without changing campaign totals', async () => {
  await useLocale('ru');
  const rows = Array.from({ length: 52 }, (_, index) => ({
    ...run,
    id: `model-${index}`,
    model_name: `model-${index}`,
  }));
  jest
    .mocked(getDocumentQualityCampaign)
    .mockResolvedValue(response({ ...campaign, runs: rows }));
  mount(
    '/admin/document-quality?product=technical_specification&tab=comparisons',
  );
  const table = await screen.findByRole('table', { name: 'Сравнение моделей' });
  const pager = await screen.findByRole('navigation', {
    name: 'Сравнение моделей',
  });
  expect(within(table).getAllByRole('row')).toHaveLength(26);
  expect(pager).toHaveTextContent('1–25/52');
  expect(
    screen.getByText('Обработано запусков').nextElementSibling,
  ).toHaveTextContent('52/52');
  fireEvent.click(within(pager).getByRole('button', { name: 'Далее' }));
  expect(
    within(table).getByRole('button', { name: 'model-25' }),
  ).toBeInTheDocument();
  expect(pager).toHaveTextContent('26–50/52');
  fireEvent.click(within(pager).getByRole('button', { name: 'Далее' }));
  expect(within(table).getAllByRole('row')).toHaveLength(3);
  expect(within(pager).getByRole('button', { name: 'Далее' })).toBeDisabled();
});

test('TZ scenario details page by 25 and reset when a different run is selected', async () => {
  await useLocale('ru');
  const cases = Array.from({ length: 56 }, (_, index) => ({
    ...report.cases[0],
    case_id: `BTZ-${String(index + 1).padStart(2, '0')}`,
  }));
  const first = {
    ...run,
    report: { ...report, expected_cases: 56, executed_cases: 56, cases },
  };
  const second = {
    ...run,
    id: 'second',
    model_name: 'second-model',
    report: { ...report, cases: cases.slice(0, 3) },
  };
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(
    response({
      configured: true,
      source_revision: 'rev',
      runs: [first, second],
    }),
  );
  jest
    .mocked(getDocumentQualityRun)
    .mockImplementation(async (id) =>
      response(id === first.id ? first : second),
    );
  mount('/admin/document-quality?product=technical_specification&tab=history');
  fireEvent.click(await screen.findByRole('button', { name: /^qwen / }));
  const block = await screen.findByRole('region', { name: 'сценариев' });
  const pager = screen.getByRole('navigation', { name: 'сценариев' });
  expect(within(block).getAllByTitle(/^BTZ-/)).toHaveLength(25);
  fireEvent.click(within(pager).getByRole('button', { name: 'Далее' }));
  expect(within(block).getByTitle('BTZ-26')).toBeInTheDocument();
  expect(block).toHaveFocus();
  fireEvent.click(within(pager).getByRole('button', { name: 'Далее' }));
  expect(within(block).getAllByTitle(/^BTZ-/)).toHaveLength(6);
  expect(pager).toHaveTextContent('51–56/56');
  fireEvent.click(screen.getByRole('button', { name: /^second-model / }));
  await waitFor(() =>
    expect(
      screen.queryByRole('navigation', { name: 'сценариев' }),
    ).not.toBeInTheDocument(),
  );
  const nextBlock = await screen.findByRole('region', { name: 'сценариев' });
  expect(within(nextBlock).getByTitle('BTZ-01')).toBeInTheDocument();
  expect(within(nextBlock).getAllByTitle(/^BTZ-/)).toHaveLength(3);
});

test('TZ operational reasons page independently and reset when the time window changes', async () => {
  await useLocale('ru');
  jest.mocked(getSpecificationOperations).mockResolvedValue(
    response({
      ...specificationOperations,
      counts: { total: 32, execution_complete: 0, reports_complete: 0 },
      reasons: Array.from({ length: 32 }, (_, index) => ({
        code: `REASON_${index}`,
        count: index + 1,
      })),
    }),
  );
  mount(
    '/admin/document-quality?product=technical_specification&tab=operations',
  );
  const block = await screen.findByRole('region', {
    name: 'Причины ограничения результата',
  });
  const pager = screen.getByRole('navigation', {
    name: 'Причины ограничения результата',
  });
  expect(within(block).getAllByRole('listitem')).toHaveLength(25);
  fireEvent.click(within(pager).getByRole('button', { name: 'Далее' }));
  expect(within(block).getAllByRole('listitem')).toHaveLength(7);
  expect(within(block).getByTitle('REASON_25')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '24 часа' }));
  await waitFor(() =>
    expect(getSpecificationOperations).toHaveBeenLastCalledWith(1),
  );
  expect(
    within(
      await screen.findByRole('region', {
        name: 'Причины ограничения результата',
      }),
    ).getByTitle('REASON_0'),
  ).toBeInTheDocument();
});

test('history does not turn missing execution counts into zero or successful cases', async () => {
  await useLocale('ru');
  const partial = {
    ...run,
    report: { ...report, expected_cases: 56, executed_cases: 55 },
  };
  const unknown = {
    ...run,
    id: 'missing-count',
    model_name: 'missing-count',
    report: { ...report, executed_cases: undefined, expected_cases: 56 },
  };
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(
    response({
      configured: true,
      source_revision: 'rev',
      runs: [partial, unknown],
    }),
  );
  mount('/admin/document-quality?product=technical_specification&tab=history');
  expect(await screen.findByRole('button', { name: /qwen/ })).toHaveTextContent(
    'Выполнено: 55/56',
  );
  const unknownRow = screen.getByRole('button', { name: /missing-count/ });
  expect(unknownRow).toHaveTextContent('Выполнено: Нет отчёта');
  expect(unknownRow).not.toHaveTextContent('0/56');
});

test.each(['business_requirements', 'technical_specification'])(
  'history and details show the selected run version and build for %s',
  async (product) => {
    await useLocale('ru');
    const historical = {
      ...run,
      source_revision: 'a'.repeat(40),
      source_version: '1.52.0',
      source_build: 'a'.repeat(40),
    };
    jest.mocked(listDocumentQualityRuns).mockResolvedValue(
      response({
        configured: true,
        source_revision: 'b'.repeat(40),
        runs: [historical],
      }),
    );
    jest.mocked(getDocumentQualityRun).mockResolvedValue(response(historical));
    mount(`/admin/document-quality?product=${product}&tab=history`);
    const row = await screen.findByRole('button', { name: /qwen/ });
    expect(row).toHaveTextContent('Версия: 1.52.0 · Билд: aaaaaaaaaaaa');
    expect(within(row).getByTitle('a'.repeat(40)).tagName).toBe('SPAN');
    expect(row).not.toHaveTextContent('Другой или неподтверждённый билд');
    fireEvent.click(row);
    await waitFor(() =>
      expect(
        screen.getAllByText('Версия: 1.52.0 · Билд: aaaaaaaaaaaa'),
      ).toHaveLength(2),
    );
  },
);

test.each([
  { revision: 'unverified', build: null, expected: 'нет данных' },
  { revision: null, build: null, expected: 'нет данных' },
  { revision: 'c'.repeat(40), build: null, expected: 'cccccccccccc' },
  { revision: 'unverified', build: 'd'.repeat(64), expected: 'dddddddddddd' },
])(
  'legacy identity has no inferred current version: $revision / $build',
  async ({ revision, build, expected }) => {
    await useLocale('ru');
    jest.mocked(listDocumentQualityRuns).mockResolvedValue(
      response({
        configured: true,
        source_revision: 'current-build',
        runs: [{ ...run, source_revision: revision, source_build: build }],
      }),
    );
    mount(
      '/admin/document-quality?product=technical_specification&tab=history',
    );
    const row = await screen.findByRole('button', { name: /qwen/ });
    expect(row).toHaveTextContent(`Версия: нет данных · Билд: ${expected}`);
    expect(row).not.toHaveTextContent('current-build');
  },
);

test.each([
  null,
  'parameters',
  'prompt',
  'source',
  'evidence',
  'partial',
  'baseline',
  'unknown-source',
  'different-revision',
  'missing-source',
  'partial-catalog',
])(
  'ranks technical models only within a complete verified experiment: %s',
  async (defect) => {
    const first = {
      ...run,
      id: 'first',
      model_name: 'rank-a',
      source_revision: 'a'.repeat(40) as string | null,
      report: {
        ...report,
        quality_status: 'FAIL',
        evidence_status: 'VERIFIED',
        source_dirty: false,
        source_fingerprint: undefined as string | undefined,
        suite_sha256: 'b'.repeat(64),
        prompt_hashes: { alignment: 'c'.repeat(64) },
        parameter_profiles: [
          { temperature: 0, num_ctx: 4096, num_predict: 1024 },
        ],
      },
    };
    const second = {
      ...first,
      id: 'second',
      model_name: 'rank-b',
      report: { ...first.report, p0_case_pass_rate: 0.6 },
    };
    if (defect === 'parameters')
      second.report.parameter_profiles = [
        { temperature: 0, num_ctx: 8192, num_predict: 1024 },
      ];
    if (defect === 'prompt')
      second.report.prompt_hashes = { alignment: 'd'.repeat(64) };
    if (defect === 'source') {
      second.report.source_dirty = true;
      second.report.source_fingerprint = 'e'.repeat(64);
    }
    if (defect === 'evidence') second.report.evidence_status = 'INCOMPLETE';
    if (defect === 'partial') second.report.executed_cases = 1;
    if (defect === 'unknown-source') second.source_revision = 'unverified';
    if (defect === 'different-revision')
      second.source_revision = 'f'.repeat(40);
    if (defect === 'missing-source') {
      second.source_revision = null;
      delete (second.report as Partial<typeof second.report>).source_dirty;
    }
    const experiment = {
      ...campaign,
      status: defect === 'partial-catalog' ? 'PARTIAL' : 'COMPLETE',
      reason_code: defect === 'partial-catalog' ? 'CATALOG_PARTIAL' : null,
      baseline_status: defect === 'baseline' ? null : 'PASS',
      source_revision: first.source_revision,
      runs: [first, second],
    };
    jest
      .mocked(listDocumentQualityCampaigns)
      .mockResolvedValue(response({ campaigns: [experiment] }));
    jest
      .mocked(getDocumentQualityCampaign)
      .mockResolvedValue(response(experiment));
    mount(
      '/admin/document-quality?product=technical_specification&tab=comparisons',
    );
    const firstRow = (
      await screen.findByRole('button', { name: 'rank-a' })
    ).closest('tr')!;
    const secondRow = screen
      .getByRole('button', { name: 'rank-b' })
      .closest('tr')!;
    const blocked = ![
      null,
      'source',
      'unknown-source',
      'different-revision',
      'missing-source',
    ].includes(defect);
    if (blocked) {
      expect(
        screen.queryByRole('columnheader', { name: 'rank' }),
      ).not.toBeInTheDocument();
      expect(within(firstRow).getAllByRole('cell')[0]).toHaveTextContent(
        'rank-a',
      );
      expect(within(secondRow).getAllByRole('cell')[0]).toHaveTextContent(
        'rank-b',
      );
    } else {
      expect(within(firstRow).getAllByRole('cell')[0]).toHaveTextContent('1');
      expect(within(secondRow).getAllByRole('cell')[0]).toHaveTextContent('2');
    }
    if (blocked)
      expect(
        screen.getByText('comparisonNotQualifiedShort'),
      ).toBeInTheDocument();
    else
      expect(
        screen.queryByText('comparisonNotQualifiedShort'),
      ).not.toBeInTheDocument();
  },
);

test.each(['ru', 'en'] as const)(
  'overview and job codes use %s translations',
  async (language) => {
    await useLocale(language);
    jest
      .mocked(listDocumentQualityCampaigns)
      .mockResolvedValue(
        response({ campaigns: [{ ...campaign, status: 'PARTIAL' }] }),
      );
    mount();
    await screen.findAllByText(/qwen/);
    expect(document.body.textContent).not.toMatch(/QUALITY_GATE|PARTIAL/);
    fireEvent.click(
      screen.getByRole('tab', {
        name: language === 'ru' ? 'Задания БТ' : 'Document jobs',
      }),
    );
    await screen.findByText(/doc-1/);
    expect(document.body.textContent).not.toMatch(/ASSESS_REVIEW|DEAD/);
    expect(
      within(
        screen.getByRole('combobox', {
          name: language === 'ru' ? 'Код ошибки' : 'Error code',
        }),
      ).getByRole('option', { name: /INVALID_AI_JSON/ }),
    ).toHaveTextContent(
      language === 'ru'
        ? 'Модель вернула некорректный JSON.'
        : 'The model returned invalid JSON.',
    );
  },
);

test('TZ overview has no BT operations card and shows successful fetch time', async () => {
  await useLocale('ru');
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(
    response({
      configured: true,
      source_revision: 'rev',
      runs: [
        {
          ...run,
          report: {
            ...report,
            quality_status: 'UNKNOWN',
            evidence_status: 'INCOMPLETE',
            nightly_experiments: [
              {
                experiment: 'REPEATS',
                quality_status: 'UNKNOWN',
                evidence_status: 'INCOMPLETE',
                expected_cases: 6,
                executed_cases: 2,
                total_tokens: null,
              },
            ],
          },
        },
      ],
    }),
  );
  mount('/admin/document-quality?product=technical_specification');
  await screen.findByRole('heading', { name: 'Ручная проверка' });
  await waitFor(() =>
    expect(screen.getByTestId('quality-updated-at')).not.toHaveTextContent('—'),
  );
  expect(
    screen.queryByRole('heading', { name: 'Операционные показатели' }),
  ).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/UNKNOWN|INCOMPLETE/);
  expect(getDocumentQuality).not.toHaveBeenCalled();
});

test('filtered-empty history resets only history filters and keeps the product', async () => {
  await useLocale('ru');
  mount(
    '/admin/document-quality?product=technical_specification&tab=history&status=PENDING&model=missing&trigger=MONTHLY',
  );
  expect(
    await screen.findByText('Проверок с выбранными фильтрами нет.'),
  ).toBeInTheDocument();
  expect(screen.queryByText('Проверок пока не было.')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));
  expect(
    await screen.findByRole('button', { name: /qwen/ }),
  ).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Тип документа' })).toHaveValue(
    'technical_specification',
  );
});

test('genuinely empty history keeps the no-runs message', async () => {
  await useLocale('ru');
  jest
    .mocked(listDocumentQualityRuns)
    .mockResolvedValue(
      response({ configured: true, source_revision: 'rev', runs: [] }),
    );
  mount('/admin/document-quality?tab=history');
  expect(await screen.findByText('Проверок пока не было.')).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Сбросить фильтры' }),
  ).not.toBeInTheDocument();
});

test('manual case names and operation reasons are localized and raw reasons remain inspectable', async () => {
  await useLocale('ru');
  jest.mocked(listDocumentQualityModels).mockResolvedValue(
    response({
      configured: true,
      case_ids: ['M03_confirmed_review_change'],
      models: [],
      errors: [],
    }),
  );
  jest.mocked(getSpecificationOperations).mockResolvedValue(
    response({
      ...specificationOperations,
      counts: { total: 2, execution_complete: 0, reports_complete: 0 },
      reasons: [{ code: 'MODEL_TIMEOUT', count: 2 }],
    }),
  );
  mount();
  fireEvent.change(screen.getByRole('combobox', { name: 'Объём проверки' }), {
    target: { value: 'CASE' },
  });
  expect(
    await screen.findByRole('option', {
      name: 'Внесение согласованной правки',
    }),
  ).toHaveValue('M03_confirmed_review_change');
  fireEvent.change(screen.getByRole('combobox', { name: 'Тип документа' }), {
    target: { value: 'technical_specification' },
  });
  fireEvent.click(screen.getByRole('tab', { name: 'Рабочие проверки ТЗ' }));
  expect(await screen.findByTitle('MODEL_TIMEOUT')).toHaveTextContent(
    'Модель не ответила за отведённое время: 2',
  );
});

test.each(['business_requirements', 'technical_specification'])(
  'overview distinguishes processed, complete and incomplete %s runs',
  async (product) => {
    await useLocale('ru');
    jest.mocked(listDocumentQualityCampaigns).mockResolvedValue(
      response({
        campaigns: [
          {
            ...campaign,
            status: 'PARTIAL',
            runs: [
              run,
              {
                ...run,
                id: 'incomplete-summary',
                status: 'INCOMPLETE',
                report: null,
              },
              {
                ...run,
                id: 'running-summary',
                status: 'RUNNING',
                report: null,
              },
            ],
          },
        ],
      }),
    );
    mount('/admin/document-quality?product=' + product);
    const summary = await screen.findByLabelText('Итоги серии');
    expect(
      within(summary).getByText('Обработано запусков').nextElementSibling,
    ).toHaveTextContent('2/3');
    expect(
      within(summary).getByText('Полных результатов').nextElementSibling,
    ).toHaveTextContent('1');
    expect(
      within(summary).getByText('Неполных отчётов').nextElementSibling,
    ).toHaveTextContent('1');
  },
);

test('overview period follows the selected operational window across tabs', async () => {
  await useLocale('ru');
  jest
    .mocked(getDocumentQuality)
    .mockImplementation(async (days) => response({ ...operations, days }));
  mount('/admin/document-quality?days=30');
  expect(await screen.findByText('За последние 30 дней')).toBeInTheDocument();
  expect(getDocumentQuality).toHaveBeenLastCalledWith(30);
  fireEvent.click(screen.getByRole('tab', { name: 'Задания БТ' }));
  fireEvent.click(screen.getByRole('button', { name: '24 часа' }));
  await waitFor(() => expect(getDocumentQuality).toHaveBeenLastCalledWith(1));
  fireEvent.click(screen.getByRole('tab', { name: 'Обзор' }));
  expect(await screen.findByText('За последний 1 день')).toBeInTheDocument();
});

test('empty technical operations keep the empty state and omit unmeasured detail grids', async () => {
  await useLocale('ru');
  mount(
    '/admin/document-quality?product=technical_specification&tab=operations',
  );
  const panel = await screen.findByRole('region', {
    name: 'Эксплуатационные показатели проверок ТЗ',
  });
  expect(within(panel).getByRole('status')).toHaveTextContent(
    'За выбранный период зарегистрированных проверок ТЗ нет.',
  );
  expect(
    within(panel).getByText('Зарегистрировано проверок').nextElementSibling,
  ).toHaveTextContent('0');
  expect(
    within(panel).queryByText('Полный расход токенов'),
  ).not.toBeInTheDocument();
  expect(within(panel).queryByRole('table')).not.toBeInTheDocument();
});

test.each(['ru', 'en'] as const)(
  'known job types and latency units are localized in %s while unknown types remain distinct',
  async (language) => {
    await useLocale(language);
    const known = [
      'GENERATE_EXPORT',
      'SQL_AGENT_QUERY',
      'SQL_AGENT_REQUIREMENTS',
      'SQL_AGENT_REVISION',
      'SQL_AGENT_SCHEMA',
      'SQL_QUERY_RUN',
    ];
    jest.mocked(getDocumentQuality).mockResolvedValue(
      response({
        ...operations,
        latency_p95_ms: 184141,
        tasks: [...known, 'NEW_TASK_TYPE'].map((task_type) => ({
          task_type,
          category: 'EXPORT',
          completed: 1,
          dead: 0,
          pending: 0,
          retry: 0,
          running: 0,
        })),
      }),
    );
    mount('/admin/document-quality?tab=operations');
    const title = language === 'ru' ? 'Экспорт документа' : 'Document export';
    expect(
      (await screen.findAllByText(new RegExp(title))).length,
    ).toBeGreaterThan(0);
    for (const code of known)
      expect(document.body.textContent).not.toContain(code);
    expect(document.body.textContent).toContain('NEW_TASK_TYPE');
    expect(document.body.textContent).toContain(
      language === 'ru' ? 'мс' : 'ms',
    );
    expect(document.body.textContent).toContain(
      language === 'ru'
        ? '95% измеренных длительностей'
        : '95% of measured durations',
    );
  },
);

test.each(['ru', 'en'] as const)(
  'error filter keeps distinct codes and submits the selected code in %s',
  async (language) => {
    await useLocale(language);
    const codes = [
      'EMPTY_AI_RESPONSE',
      'JOB_LEASE_EXPIRED',
      'INVALID_AI_JSON',
      'INVALID_CHANGE_PLAN',
      'INVALID_DOCUMENT_DRAFT',
      'INVALID_EXECUTION_AUDIT',
      'ACCEPTED_PROPOSAL_OMITTED',
      'STALE_AI_RESULT',
    ];
    jest.mocked(getDocumentQualityJobs).mockResolvedValue(
      response({
        days: 7,
        updated_at: operations.updated_at,
        total: 1,
        offset: 50,
        limit: 50,
        error_codes: codes,
        jobs: [failedJob],
      }),
    );
    mount(
      '/admin/document-quality?tab=operations&category=DOCUMENT_AI&error=RETIRED_ERROR&offset=50',
    );
    const select = await screen.findByRole('combobox', {
      name: language === 'ru' ? 'Код ошибки' : 'Error code',
    });
    await waitFor(() =>
      expect(within(select).getAllByRole('option')).toHaveLength(
        codes.length + 2,
      ),
    );
    const options = within(select).getAllByRole('option').slice(1);
    expect(new Set(options.map((o) => o.textContent)).size).toBe(
      codes.length + 1,
    );
    for (const code of [...codes, 'RETIRED_ERROR']) {
      expect(
        within(select).getByRole('option', { name: new RegExp(code) }),
      ).toHaveValue(code);
    }
    expect(select).toHaveValue('RETIRED_ERROR');
    fireEvent.change(select, { target: { value: 'JOB_LEASE_EXPIRED' } });
    await waitFor(() =>
      expect(getDocumentQualityJobs).toHaveBeenLastCalledWith({
        days: 7,
        category: 'DOCUMENT_AI',
        task_type: '',
        error_code: 'JOB_LEASE_EXPIRED',
        offset: 0,
      }),
    );
    fireEvent.change(select, { target: { value: '' } });
    await waitFor(() =>
      expect(getDocumentQualityJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ error_code: '', offset: 0 }),
      ),
    );
  },
);

test.each(['ru', 'en'] as const)(
  'real TZ cases have names and retain their request identifiers in %s',
  async (language) => {
    await useLocale(language);
    jest.mocked(listDocumentQualityModels).mockResolvedValue(
      response({
        configured: true,
        case_ids: ['BTZ-01', 'BTZ-02', 'BTZ-53', 'BTZ-FUTURE'],
        models: [],
        errors: [],
      }),
    );
    mount('/admin/document-quality?product=technical_specification');
    fireEvent.change(
      screen.getByRole('combobox', {
        name: language === 'ru' ? 'Объём проверки' : 'Run scope',
      }),
      { target: { value: 'CASE' } },
    );
    expect(
      await screen.findByRole('option', {
        name:
          language === 'ru'
            ? 'Запрет разрешённого действия'
            : 'Prohibiting an allowed action',
      }),
    ).toHaveValue('BTZ-02');
    expect(
      screen.getByRole('option', {
        name:
          language === 'ru'
            ? 'Противоречие в начале длинного контекста'
            : 'Contradiction at the start of a long context',
      }),
    ).toHaveValue('BTZ-53');
    expect(
      screen.getByRole('option', {
        name: language === 'ru' ? 'Сценарий BTZ-FUTURE' : 'Scenario BTZ-FUTURE',
      }),
    ).toHaveValue('BTZ-FUTURE');
    fireEvent.change(
      screen.getByRole('combobox', {
        name: language === 'ru' ? 'сценариев' : 'cases',
      }),
      { target: { value: 'BTZ-02' } },
    );
    fireEvent.click(
      screen.getByRole('button', {
        name: language === 'ru' ? 'Запустить проверку' : 'Run evaluation',
      }),
    );
    await waitFor(() =>
      expect(startDocumentQualityRun).toHaveBeenCalledWith({
        document_type: 'technical_specification',
        scope: 'CASE',
        case_id: 'BTZ-02',
      }),
    );
    expect(listDocumentQualityModels).toHaveBeenCalledWith(
      'technical_specification',
    );
  },
);

test('TZ history and details use the same case titles and retain unknown IDs', async () => {
  await useLocale('ru');
  const diagnostic = {
    ...run,
    scope: 'CASE',
    case_id: 'BTZ-02',
    status: 'DIAGNOSTIC',
    report: {
      ...report,
      cases: [
        { ...report.cases[0], case_id: 'BTZ-02' },
        { ...report.cases[0], case_id: 'BTZ-RETIRED' },
      ],
    },
  };
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(
    response({
      configured: true,
      source_revision: 'rev',
      runs: [diagnostic],
    }),
  );
  jest.mocked(getDocumentQualityRun).mockResolvedValue(response(diagnostic));
  mount('/admin/document-quality?product=technical_specification&tab=history');
  fireEvent.click(
    await screen.findByRole('button', { name: /Запрет разрешённого действия/ }),
  );
  expect(await screen.findByTitle('BTZ-02')).toHaveTextContent(
    'Запрет разрешённого действия',
  );
  expect(screen.getByTitle('BTZ-RETIRED')).toHaveTextContent(
    'Сценарий BTZ-RETIRED',
  );
});

test.each(['ru', 'en'] as const)(
  'every current TZ corpus case has a unique human title in %s',
  (language) => {
    const corpus = readFileSync(
      resolve(
        __dirname,
        '../../../../test/evals/technical_specifications/alignment/corpus.py',
      ),
      'utf8',
    );
    // The three long-context cases are constructed by _long_case.
    const ids = new Set([
      ...Array.from(
        corpus.matchAll(/["'](BTZ-[0-9]{2}[a-z]?)["']/g),
        (match) => match[1],
      ),
      'BTZ-53',
      'BTZ-54',
      'BTZ-55',
    ]);
    const names: Record<string, string> = (language === 'ru' ? ru : en)
      .translation.admin.businessDocumentsQualityPage.caseNames;
    expect(
      Object.keys(names)
        .filter((id) => id.startsWith('BTZ-'))
        .sort(),
    ).toEqual([...ids].sort());
    expect(new Set([...ids].map((id) => names[id])).size).toBe(ids.size);
    for (const id of ids) expect(names[id]).toMatch(/.{10,}/);
  },
);
