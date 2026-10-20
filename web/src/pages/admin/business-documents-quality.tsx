import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';

import { Button } from '@/components/ui/button';
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

type Tab = 'overview' | 'comparisons' | 'history' | 'operations';
const tabs: Tab[] = ['overview', 'comparisons', 'history', 'operations'];
type WindowDays = 1 | 7 | 30;

function AlignmentOperations({
  data,
  label,
  number,
  date,
}: {
  data: AdminService.SpecificationOperations;
  label: (key: string) => string;
  number: (value: number) => string;
  date: (value?: number | null) => string;
}) {
  return (
    <section
      aria-label={label('technicalOperations.title')}
      className="space-y-4"
    >
      <p className="text-sm text-text-secondary">
        {label('technicalOperations.cohortNote')}
      </p>
      <p>
        {label('technicalOperations.firstObservation')}:{' '}
        {date(data.earliest_observation_at)}
      </p>
      <p>
        {label('technicalOperations.executionRate')}:{' '}
        {data.execution_completion_rate == null
          ? '—'
          : `${(data.execution_completion_rate * 100).toFixed(1)}%`}{' '}
        · {data.counts.execution_complete}/{data.terminal_attempts}
      </p>
      <p>
        {label('technicalOperations.reportRate')}:{' '}
        {data.report_completion_rate == null
          ? '—'
          : `${(data.report_completion_rate * 100).toFixed(1)}%`}{' '}
        · {data.counts.reports_complete}/{data.terminal_attempts}
      </p>
      {!data.counts.total && (
        <p role="status">{label('noTechnicalObservations')}</p>
      )}
      <dl className="flex flex-wrap gap-4">
        {Object.entries(data.counts).map(([key, value]) => (
          <div key={key}>
            <dt>{label(`technicalOperations.counts.${key}`)}</dt>
            <dd>{number(value)}</dd>
          </div>
        ))}
      </dl>
      <dl className="flex flex-wrap gap-4">
        {(
          [
            'total_tokens',
            'known_spent_tokens',
            'reserved_tokens',
            'known_reserved_tokens',
          ] as const
        ).map((key) => (
          <div key={key}>
            <dt>{label(`technicalOperations.${key}`)}</dt>
            <dd>{data[key] == null ? '—' : number(data[key])}</dd>
          </div>
        ))}
      </dl>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th>{label('duration')}</th>
              <th>{label('measured')}</th>
              <th>{label('technicalOperations.meanMs')}</th>
              <th>{label('technicalOperations.p95Ms')}</th>
            </tr>
          </thead>
          <tbody>
            {(['check', 'model'] as const).map((key) => (
              <tr key={key}>
                <td>{label(`technicalOperations.durations.${key}`)}</td>
                <td>{number(data.durations[key].measured)}</td>
                <td>
                  {data.durations[key].mean_ms == null
                    ? '—'
                    : number(data.durations[key].mean_ms)}
                </td>
                <td>
                  {data.durations[key].p95_ms == null
                    ? '—'
                    : number(data.durations[key].p95_ms)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className="font-medium">{label('technicalOperations.reasons')}</h3>
      <ul>
        {data.reasons.map((item) => (
          <li key={item.code}>
            {item.code}: {number(item.count)}
          </li>
        ))}
      </ul>
    </section>
  );
}

function unwrap<T>(response: {
  data: { code: number; data: T; message?: string };
}): T {
  if (response.data.code !== 0)
    throw new Error(response.data.message || 'Request failed');
  return response.data.data;
}

function statusColor(status: string) {
  if (status === 'PASS') return 'text-state-success';
  if (status === 'FAIL' || status === 'DEAD') return 'text-state-error';
  if (status === 'INCOMPLETE' || status === 'PARTIAL')
    return 'text-state-warning';
  return 'text-text-secondary';
}

function comparisonFingerprint(run: AdminService.DocumentQualityRun) {
  return [
    run.source_revision,
    run.report?.source_fingerprint,
    run.report?.source_dirty,
    run.report?.suite_sha256 || run.report?.suite_version,
    run.report?.rubric_version,
    run.report?.template_version,
    JSON.stringify(run.report?.prompt_hashes || {}),
    JSON.stringify(run.report?.parameter_profiles || []),
  ].join('|');
}

function NightlyExperiments({
  experiments,
  label,
}: {
  experiments: NonNullable<
    AdminService.DocumentQualityReport['nightly_experiments']
  >;
  label: (key: string) => string;
}) {
  return (
    <section
      aria-label={label('nightlyExperiments')}
      className="mt-4 space-y-2"
    >
      <h3 className="font-medium">{label('nightlyExperiments')}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th>{label('experiment')}</th>
              <th>{label('qualityStatus')}</th>
              <th>{label('executionEvidence')}</th>
              <th>{label('experimentCases')}</th>
              <th>{label('tokens')}</th>
            </tr>
          </thead>
          <tbody>
            {experiments.map((item) => (
              <tr key={item.experiment}>
                <td>{label(`experiments.${item.experiment}`)}</td>
                <td>{item.quality_status}</td>
                <td>{item.evidence_status}</td>
                <td>
                  {item.executed_cases ?? '—'}/{item.expected_cases ?? '—'}
                </td>
                <td>{item.total_tokens ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SemanticQuality({
  quality,
  label,
  number,
}: {
  quality: NonNullable<AdminService.DocumentQualityReport['alignment_quality']>;
  label: (key: string) => string;
  number: (value: number) => string;
}) {
  return (
    <section aria-label={label('semanticQuality')} className="mt-4 space-y-2">
      <h3 className="font-medium">{label('semanticQuality')}</h3>
      <p>
        {label('scoredFragments')}: {quality.scored_fragments}/
        {quality.expected_fragments} · {label('unscoredFragments')}:{' '}
        {quality.unscored_fragments}
      </p>
      <p className="text-sm text-text-secondary">
        {label('semanticQualityNote')}
      </p>
      <dl className="flex flex-wrap gap-4">
        {(
          [
            'contradiction_tp',
            'contradiction_fp',
            'contradiction_fn',
            'contradiction_tn',
            'undetermined_fragments',
          ] as const
        ).map((key) => (
          <div key={key}>
            <dt>{label(`semanticCounters.${key}`)}</dt>
            <dd>{number(quality[key])}</dd>
          </div>
        ))}
      </dl>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th>{label('semanticMeasure')}</th>
              <th>{label('semanticValue')}</th>
              <th>{label('semanticFraction')}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(quality.measures).map(([key, measure]) => (
              <tr key={key}>
                <th scope="row">{label(`semanticMeasures.${key}`)}</th>
                <td>
                  {measure.value == null
                    ? '—'
                    : `${(measure.value * 100).toFixed(1)}%`}
                </td>
                <td>
                  {number(measure.numerator)}/{number(measure.denominator)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function AdminBusinessDocumentsQuality() {
  const { t, i18n } = useTranslation();
  const label = (key: string) => t(`admin.businessDocumentsQualityPage.${key}`);
  const [params, setParams] = useSearchParams();
  const product =
    params.get('product') === 'technical_specification'
      ? ('technical_specification' as const)
      : ('business_requirements' as const);
  const technical = product === 'technical_specification';
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selectedRun)
      detailRef.current?.scrollIntoView?.({
        behavior: 'smooth',
        block: 'nearest',
      });
  }, [selectedRun]);
  const [manualModel, setManualModel] = useState('');
  const [manualScope, setManualScope] = useState<'FULL' | 'CASE'>('FULL');
  const [manualCase, setManualCase] = useState('');
  const client = useQueryClient();
  const tab = tabs.includes(params.get('tab') as Tab)
    ? (params.get('tab') as Tab)
    : 'overview';
  const days = (
    [1, 7, 30].includes(Number(params.get('days')))
      ? Number(params.get('days'))
      : 7
  ) as WindowDays;
  const change = (key: string, value: string) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });
  const changeJobFilter = (key: string, value: string) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) next.set(key, value);
      else next.delete(key);
      next.delete('offset');
      return next;
    });
  const number = (value: number) =>
    new Intl.NumberFormat(i18n.language).format(value);
  const date = (value?: number | null) =>
    value == null
      ? '—'
      : new Intl.DateTimeFormat(i18n.language, {
          dateStyle: 'short',
          timeStyle: 'short',
        }).format(value);

  const runs = useQuery({
    queryKey: ['admin', 'business-documents-quality-runs', product],
    queryFn: async () => unwrap(await listDocumentQualityRuns(product)),
    refetchInterval: 10000,
    retry: false,
  });
  const campaigns = useQuery({
    queryKey: ['admin', 'business-documents-quality-campaigns', product],
    queryFn: async () => unwrap(await listDocumentQualityCampaigns(product)),
    refetchInterval: 10000,
    retry: false,
  });
  const models = useQuery({
    queryKey: ['admin', 'business-documents-quality-models', product],
    queryFn: async () => unwrap(await listDocumentQualityModels(product)),
    retry: false,
  });
  const health = useQuery({
    queryKey: ['admin', 'document-quality-health', product],
    queryFn: async () => unwrap(await getDocumentQualityHealth(product)),
    refetchInterval: 60000,
    retry: false,
  });
  const operations = useQuery({
    queryKey: ['admin', 'business-documents-quality', days],
    queryFn: async () => unwrap(await getDocumentQuality(days)),
    enabled: !technical,
    retry: false,
    refetchInterval: tab === 'operations' ? 30000 : false,
  });
  const technicalOperations = useQuery({
    queryKey: ['admin', 'technical-specification-operations', days],
    queryFn: async () => unwrap(await getSpecificationOperations(days)),
    enabled: technical && tab === 'operations',
    retry: false,
    refetchInterval: technical && tab === 'operations' ? 30000 : false,
  });
  const category = params.get('category') || '';
  const taskType = params.get('task') || '';
  const errorCode = params.get('error') || '';
  const requestedOffset = Number(params.get('offset'));
  const offset =
    Number.isSafeInteger(requestedOffset) && requestedOffset >= 0
      ? requestedOffset
      : 0;
  const jobsPage = useQuery({
    queryKey: [
      'admin',
      'business-documents-quality-jobs',
      days,
      category,
      taskType,
      errorCode,
      offset,
    ],
    queryFn: async () =>
      unwrap(
        await getDocumentQualityJobs({
          days,
          category,
          task_type: taskType,
          error_code: errorCode,
          offset,
        }),
      ),
    enabled: tab === 'operations' && !technical,
    retry: false,
    refetchInterval: tab === 'operations' ? 30000 : false,
  });
  const detail = useQuery({
    queryKey: ['admin', 'business-documents-quality-run', product, selectedRun],
    queryFn: async () =>
      unwrap(
        await (technical
          ? getDocumentQualityRun(selectedRun!, product)
          : getDocumentQualityRun(selectedRun!)),
      ),
    enabled: Boolean(selectedRun),
    retry: false,
    refetchInterval: (query) =>
      ['PENDING', 'RUNNING'].includes(query.state.data?.status || '')
        ? 10000
        : false,
  });
  const campaignDetail = useQuery({
    queryKey: [
      'admin',
      'business-documents-quality-campaign',
      product,
      params.get('campaign') || campaigns.data?.campaigns[0]?.id,
    ],
    queryFn: async () =>
      unwrap(
        await (technical
          ? getDocumentQualityCampaign(
              (params.get('campaign') || campaigns.data?.campaigns[0]?.id)!,
              product,
            )
          : getDocumentQualityCampaign(
              (params.get('campaign') || campaigns.data?.campaigns[0]?.id)!,
            )),
      ),
    enabled:
      tab === 'comparisons' &&
      Boolean(params.get('campaign') || campaigns.data?.campaigns[0]?.id),
    retry: false,
    refetchInterval: (query) =>
      tab === 'comparisons' &&
      ['PENDING', 'RUNNING'].includes(query.state.data?.status || '')
        ? 10000
        : false,
  });
  const start = useMutation({
    mutationFn: async () =>
      unwrap(
        await startDocumentQualityRun({
          ...(technical ? { document_type: product } : {}),
          ...(manualModel ? { model: manualModel } : {}),
          scope: manualScope,
          ...(manualScope === 'CASE' ? { case_id: manualCase } : {}),
        }),
      ),
    onSuccess: (result) => {
      setSelectedRun(result.id);
      client.invalidateQueries({
        queryKey: ['admin', 'business-documents-quality-runs'],
      });
      change('tab', 'history');
    },
  });
  const allRuns = runs.data?.runs ?? [];
  const allCampaigns = campaigns.data?.campaigns ?? [];
  const runningRun = allRuns.find((run) => run.status === 'RUNNING');
  const runningBaseline = allCampaigns.find(
    (item) => item.status === 'RUNNING' && item.baseline_status == null,
  );
  const latestRun = allRuns[0];
  const latestFull = allRuns.find(
    (run) =>
      run.scope === 'FULL' &&
      ['PASS', 'FAIL'].includes(run.status) &&
      run.report?.expected_cases != null &&
      run.report.expected_cases === run.report.executed_cases,
  );
  const chosenCampaign =
    campaignDetail.data ??
    allCampaigns.find((item) => item.id === params.get('campaign')) ??
    allCampaigns[0];
  const eligibleRuns = (chosenCampaign?.runs || [])
    .filter(
      (run) =>
        run.scope === 'FULL' &&
        ['PASS', 'FAIL'].includes(run.status) &&
        run.report?.expected_cases != null &&
        run.report.expected_cases === run.report.executed_cases &&
        (technical
          ? run.report.p0_case_pass_rate != null
          : run.report.weighted_score != null),
    )
    .sort(
      (left, right) =>
        (technical
          ? right.report?.p0_case_pass_rate || 0
          : right.report?.weighted_score || 0) -
        (technical
          ? left.report?.p0_case_pass_rate || 0
          : left.report?.weighted_score || 0),
    );
  const technicalComparable =
    !technical ||
    (eligibleRuns.length > 0 &&
      eligibleRuns.length === chosenCampaign?.runs.length &&
      chosenCampaign.status === 'COMPLETE' &&
      !chosenCampaign.reason_code &&
      chosenCampaign.baseline_status === 'PASS' &&
      eligibleRuns.every(
        (run) =>
          run.report?.evidence_status === 'VERIFIED' &&
          typeof run.report.source_dirty === 'boolean' &&
          (run.report.source_dirty
            ? /^[0-9a-f]{64}$/.test(run.report.source_fingerprint || '')
            : /^[0-9a-f]{40}$/.test(run.source_revision || '')) &&
          /^[0-9a-f]{64}$/.test(run.report.suite_sha256 || '') &&
          /^[0-9a-f]{64}$/.test(run.report.prompt_hashes?.alignment || '') &&
          Boolean(run.report.parameter_profiles?.length),
      ) &&
      new Set(eligibleRuns.map(comparisonFingerprint)).size === 1);
  const rankedRuns = technicalComparable ? eligibleRuns : [];
  const rankByRun = new Map(
    rankedRuns.map((run, index) => [run.id, index + 1]),
  );
  const selectedDetail = detail.data;
  const op = technical ? undefined : operations.data;
  const modelFilter = params.get('model') || '';
  const visibleJobs = jobsPage.data?.jobs ?? [];
  const historicalModels = [
    ...new Map(
      allRuns
        .filter((run) => run.model_digest)
        .map((run) => [run.model_digest!, run.model_name || run.model_digest!]),
    ).entries(),
  ];
  const visibleHistory = allRuns.filter(
    (run) =>
      (!params.get('trigger') || run.trigger === params.get('trigger')) &&
      (!params.get('status') || run.status === params.get('status')) &&
      (!modelFilter || run.model_digest === modelFilter),
  );

  const runButton = (
    run: AdminService.DocumentQualityRun,
    boundary = false,
  ) => (
    <button
      key={run.id}
      type="button"
      onClick={() => setSelectedRun(run.id)}
      className="grid w-full gap-2 border-b border-border-button py-3 text-left text-sm hover:bg-bg-base sm:grid-cols-[9rem_9rem_minmax(0,1fr)_9rem]"
    >
      <span className="text-text-primary">
        {run.model_name ||
          run.report?.model ||
          label(`triggers.${run.trigger}`)}
      </span>
      <span className={statusColor(run.status)}>
        {run.scope === 'CASE'
          ? label('diagnostic')
          : label(`statuses.${run.status}`)}
      </span>
      <span className="min-w-0 truncate text-text-secondary">
        {run.scope === 'CASE'
          ? run.case_id
          : run.report?.expected_cases != null
            ? `${run.report.executed_cases}/${run.report.expected_cases} ${label('cases')}`
            : run.reason_code || run.reason || '—'}
        {run.report?.weighted_score != null &&
          run.report.expected_cases != null &&
          run.scope === 'FULL' &&
          ['PASS', 'FAIL'].includes(run.status) &&
          run.report.expected_cases === run.report.executed_cases &&
          ` · ${run.report.weighted_score.toFixed(2)}/4`}
        {boundary && (
          <strong className="ml-2 text-state-warning">
            {label('comparisonBoundary')}
          </strong>
        )}
      </span>
      <span className="text-text-secondary">
        {date(run.started_at || run.requested_at)}
        {(run.source_revision !== runs.data?.source_revision ||
          run.source_revision === 'unverified' ||
          run.report?.source_dirty) && (
          <strong className="block text-xs">{label('historicalSource')}</strong>
        )}
      </span>
    </button>
  );

  return (
    <section
      className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-border-button bg-bg-base"
      data-testid="business-documents-quality-admin"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border-button px-6 py-5">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">
            {technical ? label('technicalTitle') : label('title')}
          </h1>
          <label className="mt-2 block text-sm">
            {label('product')}
            <select
              aria-label={label('product')}
              value={product}
              onChange={(event) => {
                setSelectedRun(null);
                setManualModel('');
                setManualCase('');
                setParams({ product: event.target.value, tab: 'overview' });
              }}
            >
              <option value="business_requirements">
                {label('businessProduct')}
              </option>
              <option value="technical_specification">
                {label('technicalProduct')}
              </option>
            </select>
          </label>
          <p className="mt-1 text-sm text-text-secondary">
            {label(technical ? 'technicalDescription' : 'description')}
          </p>
          {(health.isError || health.data?.status === 'ALERT') && (
            <p role="alert" className="text-state-warning">
              {label('nightlyAlert')}:{' '}
              {health.data?.reason_code || label('healthUnavailable')}
            </p>
          )}
          <p className="mt-2 text-xs text-text-secondary">
            {label('source')}: {runs.data?.source_revision || '—'} ·{' '}
            {label('updated')}: {date(op?.updated_at)}
            {runs.data?.source_revision === 'unverified' && (
              <span className="ml-2 text-state-warning">
                {label('unverified')}
              </span>
            )}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            runs.refetch();
            campaigns.refetch();
            if (!technical) {
              operations.refetch();
              jobsPage.refetch();
            } else if (tab === 'operations') {
              technicalOperations.refetch();
            }
            models.refetch();
            health.refetch();
          }}
        >
          <RefreshCw className="mr-2 size-4" />
          {label('refresh')}
        </Button>
      </header>

      {(runningRun || runningBaseline) && (
        <p
          role="status"
          aria-live="polite"
          className="border-b border-border-button bg-bg-card px-6 py-3 text-sm text-state-warning"
        >
          {runningRun
            ? `${label('testInProgress')}: ${runningRun.model_name || runningRun.report?.model || label('defaultModel')}`
            : label('baselineInProgress')}
        </p>
      )}

      <nav
        className="flex gap-1 overflow-x-auto border-b border-border-button px-6"
        role="tablist"
        aria-label={label(technical ? 'technicalTitle' : 'title')}
      >
        {tabs.map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            className={`shrink-0 border-b-2 px-4 py-3 text-sm ${tab === value ? 'border-accent-primary text-text-primary' : 'border-transparent text-text-secondary'}`}
            onClick={() => change('tab', value)}
          >
            {label(
              technical && value === 'operations'
                ? 'technicalOperationsTab'
                : `tabs.${value}`,
            )}
          </button>
        ))}
      </nav>

      <div className="min-h-0 flex-1 overflow-auto p-6" role="tabpanel">
        {technical && tab === 'overview' && latestRun?.report && (
          <div role="status" className="mb-4 space-y-1 text-sm">
            <p>
              {label('qualityStatus')}:{' '}
              <span
                className={statusColor(
                  latestRun.report.quality_status || 'UNKNOWN',
                )}
              >
                {latestRun.report.quality_status || 'UNKNOWN'}
              </span>
            </p>
            <p>
              {label('executionEvidence')}:{' '}
              {latestRun.report.evidence_status || 'INCOMPLETE'} ·{' '}
              {latestRun.report.executed_cases ?? 0}/
              {latestRun.report.expected_cases ?? '—'}
            </p>
            {latestRun.report.alignment_metrics && (
              <dl>
                {Object.entries(latestRun.report.alignment_metrics).map(
                  ([key, value]) => (
                    <div key={key}>
                      <dt>{label(`alignmentMetrics.${key}`)}</dt>
                      <dd>{value == null ? '—' : number(value)}</dd>
                    </div>
                  ),
                )}
              </dl>
            )}
            {latestRun.report.alignment_quality && (
              <SemanticQuality
                quality={latestRun.report.alignment_quality}
                label={label}
                number={number}
              />
            )}
            {latestRun.report.nightly_experiments && (
              <NightlyExperiments
                experiments={latestRun.report.nightly_experiments}
                label={label}
              />
            )}
          </div>
        )}
        {technical && tab === 'operations' && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-3">
              {([1, 7, 30] as const).map((value) => (
                <Button
                  key={value}
                  size="sm"
                  variant={days === value ? 'secondary' : 'ghost'}
                  aria-pressed={days === value}
                  onClick={() => change('days', String(value))}
                >
                  {label(`days.${value}`)}
                </Button>
              ))}
            </div>
            {technicalOperations.isLoading && (
              <p role="status">{label('loading')}</p>
            )}
            {technicalOperations.error && (
              <p role="alert">{label('loadError')}</p>
            )}
            {technicalOperations.data && !technicalOperations.error && (
              <AlignmentOperations
                data={technicalOperations.data}
                label={label}
                number={number}
                date={date}
              />
            )}
          </div>
        )}
        {(runs.error || campaigns.error) && (
          <p role="alert" className="text-state-error">
            {label('runsLoadError')}
          </p>
        )}
        {(runs.isLoading || campaigns.isLoading || operations.isLoading) && (
          <p className="mb-4 text-sm text-text-secondary">{label('loading')}</p>
        )}
        {tab === 'overview' && (
          <div className="space-y-6">
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-lg border border-border-button bg-bg-card p-5">
                <h2 className="font-medium text-text-primary">
                  {label('evaluationTitle')}
                </h2>
                <p className="mt-2 text-xs text-text-secondary">
                  {label('evaluationDescription')}
                </p>
                {!runs.data?.configured && (
                  <p className="mt-3 text-state-warning">
                    {label('notConfigured')}
                  </p>
                )}
                {latestRun ? (
                  <div className="mt-4 space-y-2 text-sm">
                    <p>
                      {label('latestRun')}:{' '}
                      <span className={statusColor(latestRun.status)}>
                        {label(`statuses.${latestRun.status}`)}
                      </span>{' '}
                      · {date(latestRun.started_at || latestRun.requested_at)}
                    </p>
                    <p>
                      {latestRun.model_name ||
                        latestRun.report?.model ||
                        label(`triggers.${latestRun.trigger}`)}{' '}
                      · {latestRun.reason_code || latestRun.reason || '—'}
                    </p>
                    <p>
                      {label('latestComparable')}:{' '}
                      {latestFull
                        ? `${latestFull.report?.model || latestFull.model_name} · ${latestFull.report?.executed_cases}/${latestFull.report?.expected_cases} · ${date(latestFull.finished_at)}`
                        : label('noComparable')}
                      {latestFull &&
                        (latestFull.source_revision !==
                          runs.data?.source_revision ||
                          latestFull.source_revision === 'unverified') && (
                          <strong className="ml-2">
                            {label('historicalSource')}
                          </strong>
                        )}
                    </p>
                  </div>
                ) : (
                  runs.data && (
                    <p className="mt-4 text-text-secondary">
                      {label('noRuns')}
                    </p>
                  )
                )}
                {allCampaigns[0] && (
                  <p className="mt-3 text-sm text-text-secondary">
                    {label('nightlyCampaign')}: {allCampaigns[0].status} ·{' '}
                    {
                      allCampaigns[0].runs.filter(
                        (run) => !['PENDING', 'RUNNING'].includes(run.status),
                      ).length
                    }
                    /{allCampaigns[0].runs.length} {label('models')}
                  </p>
                )}
              </div>
              <div className="rounded-lg border border-border-button bg-bg-card p-5">
                <h2 className="font-medium text-text-primary">
                  {label('operationsTitle')}
                </h2>
                {operations.error && (
                  <p role="alert" className="mt-3 text-state-error">
                    {label('loadError')}
                  </p>
                )}
                {op && (
                  <div className="mt-4 space-y-2 text-sm">
                    <p>
                      {label('failureRate')}:{' '}
                      {op.terminal_jobs
                        ? `${op.failed}/${op.terminal_jobs} (${(op.failure_rate! * 100).toFixed(1)}%)`
                        : '—'}
                    </p>
                    <p>
                      {label('affectedDocuments')}: {op.failed_documents}/
                      {op.terminal_documents} · {label('affectedTenants')}:{' '}
                      {op.affected_tenants}
                    </p>
                    <p>
                      {label('pending')}: {op.pending + op.retrying} ·{' '}
                      {label('running')}: {op.running}
                    </p>
                  </div>
                )}
              </div>
            </div>
            <div className="rounded-lg border border-border-button bg-bg-card p-5">
              <h2 className="font-medium text-text-primary">
                {label('manualRun')}
              </h2>
              <div className="mt-4 flex flex-wrap items-end gap-3">
                <label className="text-sm text-text-secondary">
                  {label('models')}
                  <select
                    aria-label={label('models')}
                    value={manualModel}
                    onChange={(event) => setManualModel(event.target.value)}
                    className="mt-1 block rounded-md border border-border-button bg-bg-base p-2 text-text-primary"
                  >
                    <option value="">{label('defaultModel')}</option>
                    {models.data?.models.map((item) => (
                      <option key={item.digest} value={item.name}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-sm text-text-secondary">
                  {label('runScope')}
                  <select
                    aria-label={label('runScope')}
                    value={manualScope}
                    onChange={(event) =>
                      setManualScope(event.target.value as 'FULL' | 'CASE')
                    }
                    className="mt-1 block rounded-md border border-border-button bg-bg-base p-2 text-text-primary"
                  >
                    <option value="FULL">{label('fullSuite')}</option>
                    <option value="CASE">{label('oneCase')}</option>
                  </select>
                </label>
                {manualScope === 'CASE' && (
                  <label className="text-sm text-text-secondary">
                    {label('cases')}
                    <select
                      aria-label={label('cases')}
                      value={manualCase}
                      onChange={(event) => setManualCase(event.target.value)}
                      className="mt-1 block rounded-md border border-border-button bg-bg-base p-2 text-text-primary"
                    >
                      <option value="">{label('selectCase')}</option>
                      {models.data?.case_ids.map((id) => (
                        <option key={id} value={id}>
                          {id}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <Button
                  onClick={() => start.mutate()}
                  disabled={
                    !runs.data?.configured ||
                    start.isPending ||
                    (manualScope === 'CASE' && !manualCase)
                  }
                >
                  {label('runNow')}
                </Button>
              </div>
              {models.error && (
                <p role="alert" className="mt-2 text-state-error">
                  {label('modelCatalogError')}
                </p>
              )}
              {start.error && (
                <p role="alert" className="mt-2 text-state-error">
                  {label('runStartError')}
                </p>
              )}
              <p className="mt-3 text-xs text-text-secondary">
                {label('diagnosticNote')}
              </p>
            </div>
          </div>
        )}

        {tab === 'comparisons' && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="font-medium text-text-primary">
                {label('nightlyCampaign')}
              </h2>
              <select
                aria-label={label('nightlyCampaign')}
                value={chosenCampaign?.id || ''}
                onChange={(event) => change('campaign', event.target.value)}
                className="rounded-md border border-border-button bg-bg-card p-2 text-text-primary"
              >
                {allCampaigns.map((item) => (
                  <option key={item.id} value={item.id}>
                    {date(item.requested_at)} · {item.status}
                  </option>
                ))}
              </select>
            </div>
            {!chosenCampaign && campaigns.data && (
              <p className="text-text-secondary">{label('noCampaigns')}</p>
            )}
            {chosenCampaign && (
              <div className="rounded-lg border border-border-button bg-bg-card p-5">
                {technical && !technicalComparable && (
                  <p role="status" className="mb-3 text-state-warning">
                    {label('comparisonNotQualified')}
                  </p>
                )}
                <p className="mb-3 text-sm text-text-secondary">
                  {label('source')}: {chosenCampaign.source_revision} ·{' '}
                  {label('baseline')}: {chosenCampaign.baseline_status || '—'} ·{' '}
                  {label('completed')}:{' '}
                  {
                    chosenCampaign.runs.filter(
                      (run) => !['PENDING', 'RUNNING'].includes(run.status),
                    ).length
                  }
                  /{chosenCampaign.runs.length}
                  {(chosenCampaign.source_revision !==
                    runs.data?.source_revision ||
                    chosenCampaign.source_revision === 'unverified') && (
                    <strong className="ml-2">
                      {label('historicalSource')}
                    </strong>
                  )}
                </p>
                {chosenCampaign.reason_code && (
                  <p className="mb-3 text-state-warning">
                    {chosenCampaign.reason_code}
                  </p>
                )}
                {chosenCampaign.baseline_report?.cases
                  ?.filter((item) => item.status === 'FAIL')
                  .map((item) => (
                    <p
                      key={item.case_id}
                      className="mb-2 text-sm text-state-error"
                    >
                      {item.case_id} · {label('baseline')} ·{' '}
                      {item.diagnostics
                        ?.map((diagnostic) => diagnostic.code)
                        .join(', ')}
                    </p>
                  ))}
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-border-button text-text-secondary">
                        <th className="py-2">{label('rank')}</th>
                        <th>{label('models')}</th>
                        <th>{label('status')}</th>
                        {technical && <th>{label('executionEvidence')}</th>}
                        <th>{label('cases')}</th>
                        <th>{label('p0Pass')}</th>
                        {!technical && <th>{label('score')}</th>}
                        <th>{label('duration')}</th>
                        <th>{label('tokens')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {chosenCampaign.runs.map((run) => {
                        const complete =
                          run.scope === 'FULL' &&
                          run.report?.expected_cases != null &&
                          run.report.executed_cases ===
                            run.report.expected_cases &&
                          ['PASS', 'FAIL'].includes(run.status);
                        return (
                          <tr
                            key={run.id}
                            className="border-b border-border-button text-text-primary"
                          >
                            <td>{rankByRun.get(run.id) ?? '—'}</td>
                            <td className="py-3">
                              <button
                                className="text-left text-accent-primary"
                                onClick={() => setSelectedRun(run.id)}
                              >
                                {run.model_name || '—'}
                              </button>
                              <div className="text-xs text-text-secondary">
                                {run.model_digest?.slice(0, 12)}
                              </div>
                              {(chosenCampaign.models?.models.find(
                                (item) => item.digest === run.model_digest,
                              )?.aliases.length || 0) > 1 && (
                                <div className="text-xs text-text-secondary">
                                  {label('aliases')}:{' '}
                                  {chosenCampaign.models?.models
                                    .find(
                                      (item) =>
                                        item.digest === run.model_digest,
                                    )
                                    ?.aliases.join(', ')}
                                </div>
                              )}
                            </td>
                            <td className={statusColor(run.status)}>
                              {label(`statuses.${run.status}`)}
                            </td>
                            {technical && (
                              <td>
                                {run.report?.evidence_status || 'INCOMPLETE'}
                              </td>
                            )}
                            <td>
                              {run.report?.executed_cases ?? 0}/
                              {run.report?.expected_cases ?? '—'}
                            </td>
                            <td>
                              {complete && run.report?.p0_case_pass_rate != null
                                ? `${(run.report.p0_case_pass_rate * 100).toFixed(0)}%`
                                : '—'}
                            </td>
                            {!technical && (
                              <td>
                                {complete && run.report?.weighted_score != null
                                  ? `${run.report.weighted_score.toFixed(2)}/4`
                                  : '—'}
                              </td>
                            )}
                            <td>
                              {run.report?.duration_ms != null
                                ? `${number(Math.round(run.report.duration_ms / 1000))} s`
                                : '—'}
                            </td>
                            <td>
                              {run.report?.total_tokens != null
                                ? number(run.report.total_tokens)
                                : '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            <p className="text-xs text-text-secondary">
              {label('comparisonNote')}
            </p>
          </div>
        )}

        {tab === 'history' && (
          <div className="space-y-4">
            <h2 className="font-medium text-text-primary">
              {label('history')}
            </h2>
            <div className="flex flex-wrap gap-3">
              <select
                aria-label={label('triggerFilter')}
                value={params.get('trigger') || ''}
                onChange={(event) => change('trigger', event.target.value)}
                className="rounded-md border border-border-button bg-bg-card p-2 text-text-primary"
              >
                <option value="">{label('allTriggers')}</option>
                {(['NIGHTLY', 'MONTHLY', 'MANUAL'] as const).map((value) => (
                  <option key={value} value={value}>
                    {label(`triggers.${value}`)}
                  </option>
                ))}
              </select>
              <select
                aria-label={label('statusFilter')}
                value={params.get('status') || ''}
                onChange={(event) => change('status', event.target.value)}
                className="rounded-md border border-border-button bg-bg-card p-2 text-text-primary"
              >
                <option value="">{label('allStatuses')}</option>
                {(
                  [
                    'PASS',
                    'FAIL',
                    'INCOMPLETE',
                    'DIAGNOSTIC',
                    'PENDING',
                    'RUNNING',
                  ] as const
                ).map((value) => (
                  <option key={value} value={value}>
                    {label(`statuses.${value}`)}
                  </option>
                ))}
              </select>
              <select
                aria-label={label('modelFilter')}
                value={modelFilter}
                onChange={(event) => change('model', event.target.value)}
                className="rounded-md border border-border-button bg-bg-card p-2 text-text-primary"
              >
                <option value="">{label('allModels')}</option>
                {historicalModels.map(([digest, name]) => (
                  <option key={digest} value={digest}>
                    {name} · {digest.slice(0, 12)}
                  </option>
                ))}
              </select>
            </div>
            {modelFilter && (
              <p className="text-xs text-text-secondary">
                {label('historyBoundaryNote')}
              </p>
            )}
            {runs.data?.truncated && (
              <p className="text-xs text-state-warning">
                {label('historyLimited')}: {runs.data.runs.length}/
                {runs.data.total}
              </p>
            )}
            <div className="rounded-lg border border-border-button bg-bg-card px-5">
              {visibleHistory.length
                ? visibleHistory.map((run, index) =>
                    runButton(
                      run,
                      Boolean(
                        modelFilter &&
                        visibleHistory[index + 1] &&
                        comparisonFingerprint(run) !==
                          comparisonFingerprint(visibleHistory[index + 1]),
                      ),
                    ),
                  )
                : runs.data && (
                    <p className="py-5 text-text-secondary">
                      {label('noRuns')}
                    </p>
                  )}
            </div>
          </div>
        )}

        {tab === 'operations' && !technical && (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="font-medium text-text-primary">
                {label('operationsTitle')}
              </h2>
              {[1, 7, 30].map((value) => (
                <Button
                  key={value}
                  size="sm"
                  variant={days === value ? 'secondary' : 'ghost'}
                  aria-pressed={days === value}
                  onClick={() => change('days', String(value))}
                >
                  {label(`days.${value}`)}
                </Button>
              ))}
            </div>
            {operations.error && (
              <p role="alert" className="text-state-error">
                {label('loadError')}
              </p>
            )}
            {op && (
              <>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {[
                    [label('completed'), number(op.completed)],
                    [label('failed'), number(op.failed)],
                    [
                      label('failureRate'),
                      op.terminal_jobs
                        ? `${op.failed}/${op.terminal_jobs} · ${(op.failure_rate! * 100).toFixed(1)}%`
                        : '—',
                    ],
                    [
                      label('affectedDocuments'),
                      `${op.failed_documents}/${op.terminal_documents}`,
                    ],
                  ].map(([key, value]) => (
                    <div
                      key={key}
                      className="rounded-lg border border-border-button bg-bg-card p-4"
                    >
                      <div className="text-xs text-text-secondary">{key}</div>
                      <div className="mt-2 text-xl font-semibold text-text-primary">
                        {value}
                      </div>
                    </div>
                  ))}
                </div>
                <p className="text-sm text-text-secondary">
                  {label('affectedTenants')}: {op.affected_tenants} ·{' '}
                  {label('pending')}: {op.pending} · {label('retrying')}:{' '}
                  {op.retrying} · {label('running')}: {op.running}
                </p>
                <div className="rounded-lg border border-border-button bg-bg-card p-5 text-sm text-text-secondary">
                  <p>
                    {label('latencyP95')}:{' '}
                    {op.latency_p95_ms == null
                      ? '—'
                      : `${number(Math.round(op.latency_p95_ms))} ms`}{' '}
                    · {label('measured')}: {op.measured_latency_jobs}/
                    {op.sampled_jobs}
                  </p>
                  <p className="mt-2">
                    {label('modelLatency')}:{' '}
                    {op.model_latency_p95_ms == null
                      ? '—'
                      : `${number(Math.round(op.model_latency_p95_ms))} ms`}{' '}
                    · {label('measured')}: {op.measured_model_latency_jobs}/
                    {op.sampled_completed_jobs}
                  </p>
                  <p className="mt-2">
                    {label('tokens')}:{' '}
                    {op.measured_token_jobs ? number(op.total_tokens) : '—'} ·{' '}
                    {label('measured')}: {op.measured_token_jobs}/
                    {op.sampled_completed_jobs}
                  </p>
                  {op.truncated && (
                    <p role="note" className="mt-3 text-state-warning">
                      {label('truncated')}
                    </p>
                  )}
                </div>
                <div className="rounded-lg border border-border-button bg-bg-card p-5">
                  <h3 className="font-medium text-text-primary">
                    {label('tasks')}
                  </h3>
                  {!op.tasks.length && (
                    <p className="mt-3 text-text-secondary">{label('empty')}</p>
                  )}
                  {op.tasks.map((task) => (
                    <div
                      key={task.task_type}
                      className="flex flex-wrap justify-between gap-2 border-b border-border-button py-3 text-sm"
                    >
                      <span className="text-text-primary">
                        {label(`categories.${task.category}`)} ·{' '}
                        {t(
                          `admin.businessDocumentsQualityPage.taskTypes.${task.task_type}`,
                          { defaultValue: task.task_type },
                        )}
                      </span>
                      <span className="text-text-secondary">
                        {label('completed')}: {task.completed} ·{' '}
                        {label('failed')}: {task.dead} · {label('pending')}:{' '}
                        {task.pending + task.retry}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="rounded-lg border border-border-button bg-bg-card p-5">
                  <h3 className="font-medium text-text-primary">
                    {label('recentFailures')} · {jobsPage.data?.total ?? '—'}/
                    {op.failed}
                  </h3>
                  <div className="mt-3 flex flex-wrap gap-3">
                    <select
                      aria-label={label('categoryFilter')}
                      value={category}
                      onChange={(event) =>
                        changeJobFilter('category', event.target.value)
                      }
                      className="rounded-md border border-border-button bg-bg-base p-2 text-text-primary"
                    >
                      <option value="">{label('allCategories')}</option>
                      {['DOCUMENT_AI', 'SQL_AGENT', 'EXPORT', 'OTHER'].map(
                        (value) => (
                          <option key={value} value={value}>
                            {label(`categories.${value}`)}
                          </option>
                        ),
                      )}
                    </select>
                    <select
                      aria-label={label('taskTypeFilter')}
                      value={taskType}
                      onChange={(event) =>
                        changeJobFilter('task', event.target.value)
                      }
                      className="rounded-md border border-border-button bg-bg-base p-2 text-text-primary"
                    >
                      <option value="">{label('allTaskTypes')}</option>
                      {op.tasks.map((item) => (
                        <option key={item.task_type} value={item.task_type}>
                          {t(
                            `admin.businessDocumentsQualityPage.taskTypes.${item.task_type}`,
                            { defaultValue: item.task_type },
                          )}
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label={label('errorFilter')}
                      value={errorCode}
                      onChange={(event) =>
                        changeJobFilter('error', event.target.value)
                      }
                      className="rounded-md border border-border-button bg-bg-base p-2 text-text-primary"
                    >
                      <option value="">{label('allErrors')}</option>
                      {[
                        ...new Set([
                          ...(jobsPage.data?.error_codes || []),
                          ...(errorCode ? [errorCode] : []),
                        ]),
                      ].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </div>
                  {jobsPage.isLoading && (
                    <p className="mt-3 text-text-secondary">
                      {label('loading')}
                    </p>
                  )}
                  {jobsPage.error && (
                    <p role="alert" className="mt-3 text-state-error">
                      {label('loadError')}
                    </p>
                  )}
                  {visibleJobs.map((job) => (
                    <div
                      key={job.id}
                      className="grid gap-1 border-b border-border-button py-3 text-sm sm:grid-cols-[minmax(0,1fr)_8rem_10rem]"
                    >
                      <span
                        className="min-w-0 truncate text-text-primary"
                        title={job.id}
                      >
                        {job.task_type} · {job.document_id} · {job.id}
                      </span>
                      <span className={statusColor(job.status)}>
                        {job.status} {job.error_code && `· ${job.error_code}`} ·{' '}
                        {job.attempt}/{job.max_attempts}
                      </span>
                      <span className="text-text-secondary">
                        {date(job.finished_at)}
                      </span>
                    </div>
                  ))}
                  {jobsPage.data && !visibleJobs.length && (
                    <p className="mt-3 text-text-secondary">
                      {label('noFailedJobs')}
                    </p>
                  )}
                  {jobsPage.data && (
                    <div className="mt-4 flex items-center gap-3 text-sm text-text-secondary">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={offset === 0}
                        onClick={() =>
                          change('offset', String(Math.max(0, offset - 50)))
                        }
                      >
                        {label('previousPage')}
                      </Button>
                      <span>
                        {jobsPage.data.total
                          ? `${offset + 1}–${Math.min(offset + 50, jobsPage.data.total)}/${jobsPage.data.total}`
                          : '0/0'}
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={offset + 50 >= jobsPage.data.total}
                        onClick={() => change('offset', String(offset + 50))}
                      >
                        {label('nextPage')}
                      </Button>
                    </div>
                  )}
                </div>
                <p className="text-xs text-text-secondary">
                  {label('operationalOnly')}
                </p>
              </>
            )}
          </div>
        )}

        {selectedRun && (
          <div
            ref={detailRef}
            className="mt-6 rounded-lg border border-border-button bg-bg-card p-5"
            aria-label={label('caseDetails')}
          >
            <div className="flex items-start justify-between gap-3">
              <h2 className="min-w-0 break-words font-medium text-text-primary">
                {label('caseDetails')} · {selectedRun}
              </h2>
              <Button
                size="sm"
                variant="ghost"
                className="shrink-0"
                onClick={() => setSelectedRun(null)}
              >
                {label('close')}
              </Button>
            </div>
            {detail.isLoading && (
              <p className="mt-3 text-text-secondary">{label('loading')}</p>
            )}
            {detail.error && (
              <p role="alert" className="mt-3 text-state-error">
                {label('runsLoadError')}
              </p>
            )}
            {selectedDetail && (
              <div className="mt-3 space-y-3 text-sm">
                <p className="break-all text-text-secondary">
                  {selectedDetail.model_name ||
                    selectedDetail.report?.model ||
                    '—'}{' '}
                  · {selectedDetail.model_digest || '—'} ·{' '}
                  {selectedDetail.source_revision} ·{' '}
                  {date(selectedDetail.finished_at)}
                </p>
                {technical && (
                  <p>
                    {label('qualityStatus')}:{' '}
                    {selectedDetail.report?.quality_status || 'UNKNOWN'} ·{' '}
                    {label('executionEvidence')}:{' '}
                    {selectedDetail.report?.evidence_status || 'INCOMPLETE'}
                  </p>
                )}
                {technical && selectedDetail.report?.alignment_quality && (
                  <SemanticQuality
                    quality={selectedDetail.report.alignment_quality}
                    label={label}
                    number={number}
                  />
                )}
                {technical && selectedDetail.report?.nightly_experiments && (
                  <NightlyExperiments
                    experiments={selectedDetail.report.nightly_experiments}
                    label={label}
                  />
                )}
                {(selectedDetail.source_revision !==
                  runs.data?.source_revision ||
                  selectedDetail.source_revision === 'unverified' ||
                  selectedDetail.report?.source_dirty) && (
                  <p>{label('historicalSource')}</p>
                )}
                {selectedDetail.reason_code && (
                  <p className="text-state-warning">
                    {selectedDetail.reason_code}
                  </p>
                )}
                {!selectedDetail.report?.cases?.length && (
                  <p className="text-text-secondary">
                    {label('detailsUnavailable')}
                  </p>
                )}
                {selectedDetail.report?.gate_checks
                  ?.filter((check) => !check.passed)
                  .map((check) => (
                    <p key={check.metric} className="text-state-error">
                      {check.metric}: {check.actual} · {label('threshold')}:{' '}
                      {check.threshold}
                    </p>
                  ))}
                {selectedDetail.report?.cases?.map((item) => (
                  <div
                    key={item.case_id}
                    className="border-t border-border-button pt-3"
                  >
                    <p className={statusColor(item.status)}>
                      {item.case_id} · {item.priority || '—'} · {item.status}
                    </p>
                    {item.diagnostics?.map((diagnostic, index) => (
                      <p
                        key={`${diagnostic.code}-${index}`}
                        className="mt-1 text-text-secondary"
                      >
                        {t(
                          `admin.businessDocumentsQualityPage.diagnostics.${diagnostic.code}`,
                          { defaultValue: diagnostic.code },
                        )}
                        {[
                          diagnostic.fact_id,
                          diagnostic.job_code,
                          diagnostic.error_type,
                          diagnostic.requirement_id,
                          diagnostic.check_id,
                          diagnostic.section_id,
                        ]
                          .filter(Boolean)
                          .map((value) => `: ${value}`)
                          .join('')}
                      </p>
                    ))}
                    {item.metrics &&
                      Object.entries(item.metrics).map(([metric, actual]) => (
                        <p key={metric} className="mt-1 text-text-secondary">
                          {metric}: {actual}
                        </p>
                      ))}
                    {item.failure_count > 0 && !item.diagnostics?.length && (
                      <p className="text-text-secondary">
                        {label('detailsUnavailable')}
                      </p>
                    )}
                    {item.gate_checks
                      ?.filter((check) => !check.passed)
                      .map((check) => (
                        <p key={check.metric} className="text-text-secondary">
                          {check.metric}: {check.actual} · {label('threshold')}:{' '}
                          {check.threshold}
                        </p>
                      ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
