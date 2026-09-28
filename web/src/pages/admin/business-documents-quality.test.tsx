import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import {
  getDocumentQuality, getDocumentQualityCampaign, getDocumentQualityJobs, getDocumentQualityRun,
  listDocumentQualityCampaigns, listDocumentQualityModels, listDocumentQualityRuns,
  startDocumentQualityRun,
} from '@/services/admin-service';
import AdminBusinessDocumentsQuality from './business-documents-quality';

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key.split('.').pop(), i18n: { language: 'en' } }),
}));
jest.mock('@/services/admin-service', () => ({
  getDocumentQuality: jest.fn(), getDocumentQualityCampaign: jest.fn(), getDocumentQualityJobs: jest.fn(), getDocumentQualityRun: jest.fn(),
  listDocumentQualityCampaigns: jest.fn(), listDocumentQualityModels: jest.fn(), listDocumentQualityRuns: jest.fn(),
  startDocumentQualityRun: jest.fn(),
}));

const report = {
  status: 'FAIL', model: 'qwen', expected_cases: 5, executed_cases: 5,
  weighted_score: 3.9, p0_case_pass_rate: 0.8, duration_ms: 120000, total_tokens: 4000,
  cases: [{ case_id: 'M04', priority: 'P0', status: 'FAIL', failure_count: 1,
    diagnostics: [{ code: 'MISSING_FACT', fact_id: 'scenario_rule' }], gate_checks: [] }],
};
const run = {
  id: 'run-1', trigger: 'NIGHTLY', status: 'FAIL', scope: 'FULL', campaign_id: 'campaign-1',
  model_name: 'qwen', model_digest: 'a'.repeat(64), case_id: null, source_revision: 'rev',
  requested_at: 1789855254167, started_at: 1789855254167, finished_at: 1789855354167,
  reason_code: 'QUALITY_GATE', reason: null, report,
};
const campaign = {
  id: 'campaign-1', status: 'COMPLETE', source_revision: 'rev', requested_at: 1789855254167,
  started_at: 1789855254167, finished_at: 1789855354167, baseline_status: 'PASS',
  baseline_report: null, reason_code: null, models: null, runs: [run],
};
const operations = {
  updated_at: 1789855354167, days: 7, sampled_jobs: 3, sampled_completed_jobs: 0, truncated: false, terminal_jobs: 3,
  completed: 0, failed: 3, pending: 1, retrying: 0, running: 0, failure_rate: 1,
  failed_documents: 1, terminal_documents: 1, affected_tenants: 1,
  latency_p95_ms: 1250, measured_latency_jobs: 3, model_latency_p95_ms: null,
  measured_model_latency_jobs: 0, total_tokens: 0, measured_token_jobs: 0,
  tasks: [{ task_type: 'ASSESS_REVIEW', category: 'DOCUMENT_AI', completed: 0, dead: 3, pending: 1, retry: 0, running: 0 }],
  models: [], errors: [{ task_type: 'ASSESS_REVIEW', error_code: 'INVALID_AI_JSON', count: 2 }],
};
const failedJob = { id: 'job-1', document_id: 'doc-1', task_type: 'ASSESS_REVIEW', category: 'DOCUMENT_AI',
  status: 'DEAD', finished_at: 1789855354167, error_code: 'INVALID_AI_JSON', attempt: 3, max_attempts: 3 };

function response<T>(data: T) { return { data: { code: 0, data } } as never; }

function mount(path = '/admin/document-quality') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client}><AdminBusinessDocumentsQuality /></QueryClientProvider></MemoryRouter>);
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(getDocumentQuality).mockResolvedValue(response(operations));
  jest.mocked(getDocumentQualityJobs).mockResolvedValue(response({ days: 7, updated_at: 1789855354167,
    total: 1, offset: 0, limit: 50, error_codes: ['INVALID_AI_JSON'], jobs: [failedJob] }));
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(response({ configured: true, source_revision: 'rev', runs: [run] }));
  jest.mocked(listDocumentQualityCampaigns).mockResolvedValue(response({ campaigns: [campaign] }));
  jest.mocked(getDocumentQualityCampaign).mockResolvedValue(response(campaign));
  jest.mocked(getDocumentQualityRun).mockResolvedValue(response(run));
  jest.mocked(listDocumentQualityModels).mockResolvedValue(response({ configured: true, case_ids: ['M04'], models: [{ name: 'qwen', digest: 'a'.repeat(64), aliases: ['qwen'] }], errors: [] }));
  jest.mocked(startDocumentQualityRun).mockResolvedValue(response(run));
});

test('separates model quality and exact operational denominators', async () => {
  mount();
  expect(await screen.findByText(/3\/3/)).toBeInTheDocument();
  expect(screen.getAllByText(/1\/1/).length).toBeGreaterThanOrEqual(2);
  expect(screen.getByRole('tab', { name: 'comparisons' })).toBeInTheDocument();
  expect(screen.getByText(/rev/)).toBeInTheDocument();
});

test('shows the model that is currently being checked', async () => {
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(response({ configured: true, source_revision: 'rev', runs: [{ ...run, status: 'RUNNING' }] }));
  mount();
  expect(await screen.findByRole('status')).toHaveTextContent('testInProgress: qwen');
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
  jest.mocked(listDocumentQualityCampaigns).mockResolvedValue(response({ campaigns: [{ ...campaign, status: 'PARTIAL', runs: [{ ...run, status: 'INCOMPLETE', report: { ...report, executed_cases: 1 } }] }] }));
  jest.mocked(getDocumentQualityCampaign).mockResolvedValue(response({ ...campaign, status: 'PARTIAL', runs: [{ ...run, status: 'INCOMPLETE', report: { ...report, executed_cases: 1 } }] }));
  mount('/admin/document-quality?tab=comparisons');
  await screen.findByText('1/5');
  expect(screen.queryByText('3.90/4')).not.toBeInTheDocument();
});

test('manual one-case run is sent as a diagnostic request', async () => {
  mount();
  await screen.findByRole('button', { name: 'runNow' });
  fireEvent.change(screen.getByLabelText('runScope'), { target: { value: 'CASE' } });
  fireEvent.change(screen.getByLabelText('cases'), { target: { value: 'M04' } });
  fireEvent.click(screen.getByRole('button', { name: 'runNow' }));
  await waitFor(() => expect(startDocumentQualityRun).toHaveBeenCalledWith({ scope: 'CASE', case_id: 'M04' }));
});

test('shows old records without inventing missing details', async () => {
  jest.mocked(getDocumentQualityRun).mockResolvedValue(response({ ...run, report: { ...report, cases: undefined } }));
  mount('/admin/document-quality?tab=history');
  fireEvent.click(await screen.findByRole('button', { name: /qwen/ }));
  expect(await screen.findByText('detailsUnavailable')).toBeInTheDocument();
});

test('shows sample limitations for operations', async () => {
  jest.mocked(getDocumentQuality).mockResolvedValue(response({ ...operations, truncated: true }));
  mount('/admin/document-quality?tab=operations&days=30');
  expect(await screen.findByText('truncated')).toBeInTheDocument();
  expect(getDocumentQuality).toHaveBeenCalledWith(30);
});

test('marks a source boundary when following one model through history', async () => {
  const older = { ...run, id: 'run-old', source_revision: 'old-revision', requested_at: run.requested_at - 1000 };
  jest.mocked(listDocumentQualityRuns).mockResolvedValue(response({ configured: true, source_revision: 'rev', runs: [run, older] }));
  mount(`/admin/document-quality?tab=history&model=${run.model_digest}`);
  expect(await screen.findByText('comparisonBoundary')).toBeInTheDocument();
  expect(screen.getByText('historyBoundaryNote')).toBeInTheDocument();
});

test('loads failed jobs with server-side type and code filters', async () => {
  mount('/admin/document-quality?tab=operations&days=7');
  expect(await screen.findByText(/job-1/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('taskTypeFilter'), { target: { value: 'ASSESS_REVIEW' } });
  await waitFor(() => expect(getDocumentQualityJobs).toHaveBeenCalledWith(expect.objectContaining({ task_type: 'ASSESS_REVIEW', offset: 0 })));
});
