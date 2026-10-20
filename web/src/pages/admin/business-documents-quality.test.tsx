import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
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
  useTranslation: () => ({
    t: (key: string) => key.split('.').pop(),
    i18n: { language: 'en' },
  }),
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

test('separates model quality and exact operational denominators', async () => {
  mount();
  expect(await screen.findByText(/3\/3/)).toBeInTheDocument();
  expect(screen.getAllByText(/1\/1/).length).toBeGreaterThanOrEqual(2);
  expect(screen.getByRole('tab', { name: 'comparisons' })).toBeInTheDocument();
  expect(screen.getByText(/rev/)).toBeInTheDocument();
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
  expect(screen.getByText('1')).toBeInTheDocument();
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

test('marks a source boundary when following one model through history', async () => {
  const older = {
    ...run,
    id: 'run-old',
    source_revision: 'old-revision',
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
  expect(await screen.findByText('comparisonBoundary')).toBeInTheDocument();
  expect(screen.getByText('historyBoundaryNote')).toBeInTheDocument();
});

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
    expect(recall).toHaveTextContent(complete ? '50.0%' : '—');
    expect(
      within(region).getByRole('row', { name: /unsupported_rule_recall/ }),
    ).toHaveTextContent('—0/0');
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
  expect(region.parentElement).toHaveTextContent('historicalSource');
});

test.each([
  null,
  'parameters',
  'prompt',
  'source',
  'evidence',
  'partial',
  'baseline',
  'unknown-source',
  'partial-catalog',
])(
  'ranks technical models only within a complete verified experiment: %s',
  async (defect) => {
    const first = {
      ...run,
      id: 'first',
      model_name: 'rank-a',
      source_revision: 'a'.repeat(40),
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
    expect(within(firstRow).getAllByRole('cell')[0]).toHaveTextContent(
      defect ? '—' : '1',
    );
    expect(within(secondRow).getAllByRole('cell')[0]).toHaveTextContent(
      defect ? '—' : '2',
    );
    if (defect)
      expect(screen.getByText('comparisonNotQualified')).toBeInTheDocument();
    else
      expect(
        screen.queryByText('comparisonNotQualified'),
      ).not.toBeInTheDocument();
  },
);
