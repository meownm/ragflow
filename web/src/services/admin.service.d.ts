declare namespace AdminService {
  export type LoginData = {
    access_token: string;
    avatar: unknown;
    color_schema: 'Bright' | 'Dark';
    create_date: string;
    create_time: number;
    email: string;
    id: string;
    is_active: '0' | '1';
    is_anonymous: '0' | '1';
    is_authenticated: '0' | '1';
    is_superuser: boolean;
    language: string;
    last_login_time: string;
    login_channel: unknown;
    nickname: string;
    password: string;
    status: '0' | '1';
    timezone: string;
    update_date: [string];
    update_time: [number];
  };

  export type ListUsersItem = {
    id: string;
    create_date: string;
    email: string;
    is_active: '0' | '1';
    is_superuser: boolean;
    role: string;
    nickname: string;
    business_document_role:
      | 'AUTHOR_CREATOR'
      | 'AUTHOR_EDITOR'
      | 'MODERATOR_CREATOR'
      | 'EXTENDED_MODERATOR';
  };

  export type UserDetail = {
    avatar?: string;
    create_date: string;
    email: string;
    is_active: '0' | '1';
    is_anonymous: '0' | '1';
    is_superuser: boolean;
    language: string;
    last_login_time: string;
    login_channel: unknown;
    status: '0' | '1';
    update_date: string;
    role: string;
  };

  export type ListUserDatasetItem = {
    avatar?: string;
    chunk_num: number;
    create_date: string;
    doc_num: number;
    language: string;
    name: string;
    permission: string;
    status: '0' | '1';
    token_num: number;
    update_date: string;
  };

  export type ListUserAgentItem = {
    avatar?: string;
    canvas_category: 'agent';
    permission: 'string';
    title: string;
  };

  export type TaskExecutorHeartbeatItem = {
    name: string;
    boot_at: string;
    now: string;
    ip_address: string;
    current: Record<string, object>;
    done: number;
    failed: number;
    lag: number;
    pending: number;
    pid: number;
  };

  export type TaskExecutorInfo = Record<string, TaskExecutorHeartbeatItem[]>;

  export type ListServicesItem = {
    extra: Record<string, unknown>;
    host: string;
    id: number;
    name: string;
    port: number;
    service_type: string;
    status: 'alive' | 'timeout' | 'fail';
  };

  export type ServiceDetail =
    | {
        service_name: string;
        status: 'alive' | 'timeout';
        message: string | Record<string, any> | Record<string, any>[];
      }
    | {
        service_name: 'task_executor';
        status: 'alive' | 'timeout';
        message: AdminService.TaskExecutorInfo;
      };

  export type PermissionData = {
    enable: boolean;
    read: boolean;
    write: boolean;
    share: boolean;
  };

  export type ListRoleItem = {
    id: string;
    role_name: string;
    description: string;
    create_date: string;
    update_date: string;
  };

  export type ListRoleItemWithPermission = ListRoleItem & {
    permissions: Record<string, PermissionData>;
  };

  export type RoleDetailWithPermission = {
    role: {
      id: string;
      name: string;
      description: string;
    };
    permissions: Record<string, PermissionData>;
  };

  export type RoleDetail = {
    id: string;
    name: string;
    description: string;
    create_date: string;
    update_date: string;
  };

  export type AssignRolePermissionsInput = Record<
    string,
    Partial<PermissionData>
  >;
  export type RevokeRolePermissionInput = AssignRolePermissionsInput;

  export type UserDetailWithPermission = {
    user: {
      id: string;
      username: string;
      role: string;
    };
    role_permissions: Record<string, PermissionData>;
  };

  export type ResourceType = {
    resource_types: string[];
  };

  export type ListWhitelistItem = {
    id: number;
    email: string;
    create_date: string;
    create_time: number;
    update_date: string;
    update_time: number;
  };

  // Sandbox settings types
  export type SandboxProvider = {
    id: string;
    name: string;
    description: string;
    tags: string[];
  };

  export type SandboxConfigFieldBase = {
    required?: boolean;
    label?: string;
    placeholder?: string;
    description?: string;
    multiline?: boolean;
    readonly?: boolean;
    scope?: 'runtime' | 'deployment';
  };

  export type SandboxConfigStringField = SandboxConfigFieldBase & {
    type: 'string';
    default?: string;
    secret?: boolean;
  };

  export type SandboxConfigIntegerField = SandboxConfigFieldBase & {
    type: 'integer';
    default?: number;
    min?: number;
    max?: number;
  };

  export type SandboxConfigBooleanField = SandboxConfigFieldBase & {
    type: 'boolean';
    default?: boolean;
  };

  export type SandboxConfigJsonField = SandboxConfigFieldBase & {
    type: 'json';
    default?: unknown;
  };

  export type SandboxConfigField =
    | SandboxConfigStringField
    | SandboxConfigIntegerField
    | SandboxConfigBooleanField
    | SandboxConfigJsonField;

  export type SandboxConfig = {
    provider_type: string;
    config: Record<string, unknown>;
  };

  export type NavigationVisibility = {
    visible_sections: import('@/constants/navigation').NavigationSection[];
  };

  export type AccessGroup = {
    id: string;
    name: string;
    description: string;
    user_ids: string[];
    dataset_ids: string[];
    sections: import('@/constants/navigation').NavigationSection[];
  };

  export type AccessGroupInput = Omit<AccessGroup, 'id'>;

  export type AccessGroupOptions = {
    users: Array<
      Pick<ListUsersItem, 'id' | 'email' | 'nickname' | 'is_superuser'>
    >;
    datasets: Array<{ id: string; name: string; tenant_id: string }>;
    sections: import('@/constants/navigation').NavigationSection[];
  };

  export type BusinessDocumentsEvaConnection = {
    api_base_url: string;
    web_base_url: string;
    project_id: string;
    verify_ssl: boolean;
    include_archived: boolean;
    token_configured: boolean;
  };

  export type BusinessDocumentsSettings = {
    eva_connection: BusinessDocumentsEvaConnection;
  };

  export type BusinessDocumentsCatalogStatus = {
    source_id: string;
    source_version: string;
    source_sha256: string;
    filename: string;
    storage: 'bundled' | 'uploaded';
    catalog_items: number;
    active_items: number;
    total_items: number;
    imported_at: string | null;
    imported_by: string | null;
  };

  export type BusinessDocumentsCatalogImport =
    BusinessDocumentsCatalogStatus & {
      created_items: number;
      updated_items: number;
      reactivated_items: number;
      deactivated_items: number;
    };

  export type BusinessDocumentsEvaConnectionInput = Omit<
    BusinessDocumentsEvaConnection,
    'token_configured'
  > & {
    eva_api_token?: string;
    clear_token?: boolean;
  };

  export type AuditEventSource =
    | 'application'
    | 'business_documents'
    | 'document_constructor'
    | 'ingestion'
    | 'connectors';

  export type DocumentQualityDashboard = {
    updated_at: number;
    days: 1 | 7 | 30;
    sampled_jobs: number;
    sampled_completed_jobs: number;
    truncated: boolean;
    terminal_jobs: number;
    completed: number;
    failed: number;
    pending: number;
    retrying: number;
    running: number;
    failure_rate: number | null;
    failed_documents: number;
    terminal_documents: number;
    affected_tenants: number;
    latency_p95_ms: number | null;
    measured_latency_jobs: number;
    model_latency_p95_ms: number | null;
    measured_model_latency_jobs: number;
    total_tokens: number;
    measured_token_jobs: number;
    tasks: { task_type: string; category: string; completed: number; dead: number; pending: number; retry: number; running: number }[];
    models: { provider: string; model: string; count: number }[];
    errors: { task_type: string; error_code: string; count: number }[];
  };

  export type DocumentQualityJob = {
    id: string;
    document_id: string;
    task_type: string;
    category: string;
    status: 'DEAD';
    finished_at: number;
    error_code: string;
    attempt: number;
    max_attempts: number;
  };
  export type DocumentQualityJobs = {
    days: 1 | 7 | 30;
    updated_at: number;
    total: number;
    offset: number;
    limit: number;
    error_codes: string[];
    jobs: DocumentQualityJob[];
  };

  export type DocumentQualityDiagnostic = { code: string; fact_id?: string };
  export type DocumentQualityGateCheck = { metric: string; actual: number; threshold: number; passed: boolean };
  export type DocumentQualityReport = {
    status: string;
    source_dirty?: boolean | null;
    suite_id?: string;
    suite_version?: string;
    suite_sha256?: string;
    prompt_hashes?: Record<string, string>;
    parameter_profiles?: Record<string, unknown>[];
    rubric_version?: string;
    template_version?: string;
    expected_cases?: number;
    executed_cases?: number;
    provider?: string | null;
    model?: string | null;
    model_digest?: string | null;
    duration_ms?: number | null;
    total_tokens?: number | null;
    weighted_score?: number | null;
    grounded_reference_precision?: number | null;
    p0_case_pass_rate?: number | null;
    all_case_pass_rate?: number | null;
    criterion_scores?: Record<string, number>;
    metrics?: Record<string, number>;
    gate_checks?: DocumentQualityGateCheck[];
    cases?: { case_id: string; priority?: string; status: string; failure_count: number; diagnostics?: DocumentQualityDiagnostic[]; metrics?: Record<string, number>; gate_checks?: DocumentQualityGateCheck[] }[];
  };

  export type DocumentQualityRun = {
    id: string;
    trigger: 'NIGHTLY' | 'MONTHLY' | 'MANUAL';
    status: 'PENDING' | 'RUNNING' | 'PASS' | 'FAIL' | 'INCOMPLETE' | 'DIAGNOSTIC';
    requested_at: number;
    started_at: number | null;
    finished_at: number | null;
    source_revision: string | null;
    campaign_id: string | null;
    model_name: string | null;
    model_digest: string | null;
    scope: 'FULL' | 'CASE';
    case_id: string | null;
    reason_code: string | null;
    reason: string | null;
    report: DocumentQualityReport | null;
  };

  export type DocumentQualityRuns = {
    configured: boolean;
    source_revision: string;
    total: number;
    truncated: boolean;
    runs: DocumentQualityRun[];
  };

  export type DocumentQualityCampaign = {
    id: string;
    status: 'PENDING' | 'RUNNING' | 'COMPLETE' | 'PARTIAL' | 'INCOMPLETE';
    source_revision: string | null;
    requested_at: number;
    started_at: number | null;
    finished_at: number | null;
    baseline_status: string | null;
    baseline_report: DocumentQualityReport | null;
    reason_code: string | null;
    models: { models: { name: string; digest: string; aliases: string[] }[]; errors: string[] } | null;
    runs: DocumentQualityRun[];
  };

  export type DocumentQualityCampaigns = { campaigns: DocumentQualityCampaign[] };
  export type DocumentQualityModels = {
    configured: boolean;
    case_ids: string[];
    models: { name: string; digest: string; aliases: string[] }[];
    errors: string[];
  };

  export type AuditEventOutcome =
    | 'success'
    | 'failure'
    | 'pending'
    | 'cancelled';

  export type AuditEvent = {
    id: string;
    occurred_at: number;
    source: AuditEventSource;
    action: string;
    outcome: AuditEventOutcome;
    summary: string;
    actor: {
      id?: string;
      type: string;
      email?: string;
      nickname?: string;
    };
    object: {
      type: string;
      id: string;
      label: string;
    };
    correlation_id?: string | null;
    causation_id?: string | null;
    request_id?: string | null;
    trace_id?: string | null;
    span_id?: string | null;
    interaction_id?: string | null;
    job_id?: string | null;
    session_id?: string | null;
    error_id?: string | null;
    error?: {
      code?: string;
      message: string;
    } | null;
    details: Record<string, unknown>;
  };

  export type AuditEventQuery = {
    page?: number;
    page_size?: number;
    source?: AuditEventSource | '';
    outcome?: AuditEventOutcome | '';
    query?: string;
    actor?: string;
    correlation_id?: string;
  };

  export type AuditEventPage = {
    items: AuditEvent[];
    page: number;
    page_size: number;
    total: number;
    retention_days: number;
    unavailable_sources: AuditEventSource[];
    stats: {
      failures: number;
      sources: number;
    };
    observability: {
      enabled: boolean;
      grafana_url: string;
      loki_datasource_uid: string;
      tempo_datasource_uid: string;
    };
  };
}
