import type {
  CatalogAnswer,
  CatalogEntity,
  Freshness,
} from '@/pages/openmetadata/types';

export type BusinessDocumentLifecycleState =
  | 'INTAKE'
  | 'REVIEW'
  | 'AGREED'
  | 'ARCHIVED';

export interface SqlSchemaResolutionResponse {
  schema_version: '1';
  status: 'READY' | 'NEEDS_CLARIFICATION' | 'DEGRADED';
  resolutions: Array<{
    term: string;
    lookup: SqlSchemaLookupState;
    catalog_answer: CatalogAnswer;
    interpretation: {
      term: string;
      kind: 'entity' | 'field' | 'unknown';
      normalized_term: string;
      recommended_entity_id: string | null;
      recommended_column_ids: string[];
      confidence: number | null;
      reason: string;
      clarification_question: string | null;
    };
    needs_clarification: boolean;
  }>;
  llm: {
    status: 'APPLIED' | 'FALLBACK' | 'SKIPPED';
    prompt: {
      name: string;
      version: string;
      content_hash: string;
    } | null;
    warning: string | null;
  };
}

export interface SqlSchemaLookupState {
  status: 'OK' | 'ERROR';
  error_code: string | null;
  retryable: boolean;
  message: string | null;
}

export interface SqlSchemaEntityDetailsResponse {
  schema_version: '1';
  status: 'READY' | 'DEGRADED';
  entities: Array<{
    entity_id: string;
    lookup: SqlSchemaLookupState;
    entity: CatalogEntity | null;
    freshness: Freshness | null;
    retrieval: string;
    sources: Array<{ label: string; url?: string; dataset_id?: string }>;
    warnings: string[];
  }>;
}

export interface SqlQueryCompileRequest {
  schema_version: '1';
  schema_snapshot: Record<string, unknown>;
  accepted_requirements: string;
  accepted_schema: Array<{
    entity_id: string;
    version: number;
    schema_fingerprint: string;
  }>;
  specification: {
    dialect: 'postgres';
    from: { entity_id: string; alias: string };
    select: Array<{
      id: string;
      kind:
        | 'column'
        | 'sum'
        | 'avg'
        | 'min'
        | 'max'
        | 'count'
        | 'count_distinct'
        | 'date_bucket';
      column_id: string;
      alias: string;
      grain: 'day' | 'week' | 'month' | 'quarter' | 'year' | null;
    }>;
    joins: Array<{
      id: string;
      join_type: 'INNER' | 'LEFT';
      entity_id: string;
      alias: string;
      left_column_id: string;
      right_column_id: string;
      description: string;
      decision: 'user' | 'automatic_exact' | null;
      confirmed: boolean;
    }>;
    filters: Array<{
      id: string;
      column_id: string;
      operator:
        | 'eq'
        | 'ne'
        | 'gt'
        | 'gte'
        | 'lt'
        | 'lte'
        | 'like'
        | 'ilike'
        | 'in'
        | 'not_in'
        | 'is_null'
        | 'is_not_null';
      parameter: string | null;
      description: string;
      decision: 'user' | 'automatic_exact' | null;
      confirmed: boolean;
    }>;
    order_by: Array<{
      select_item_id: string;
      direction: 'ASC' | 'DESC';
    }>;
    parameters: Array<{
      name: string;
      type:
        | 'text'
        | 'integer'
        | 'decimal'
        | 'boolean'
        | 'date'
        | 'datetime'
        | 'text_list'
        | 'integer_list';
      value: unknown;
    }>;
    limit_parameter: string;
  };
}

export interface SqlQueryCompileResponse {
  schema_version: '1';
  status: 'READY' | 'NEEDS_CLARIFICATION';
  snapshot_fingerprint: string;
  blocking_issues: Array<{ code: string; path: string; message: string }>;
  sql: string | null;
  parameters: Record<string, unknown>;
  guard:
    | { status: 'NOT_RUN' }
    | {
        status: 'PASS';
        dialect: 'postgres';
        statement_count: 1;
        read_only: true;
        tables: string[];
        parameters: string[];
      };
}

export type SqlQueryPlanRequest = Pick<
  SqlQueryCompileRequest,
  | 'schema_version'
  | 'schema_snapshot'
  | 'accepted_requirements'
  | 'accepted_schema'
> & {
  locale: 'ru' | 'en';
};

export interface SqlQueryPlanProposal {
  base_entity_id: string;
  aliases: Record<string, string>;
  select: SqlQueryCompileRequest['specification']['select'];
  joins: Array<{
    id: string;
    join_type: 'INNER' | 'LEFT';
    entity_id: string;
    alias: string;
    left_column_id: string;
    right_column_id: string;
    description: string;
    confirmed: boolean;
  }>;
  filters: Array<{
    id: string;
    column_id: string;
    operator: SqlQueryCompileRequest['specification']['filters'][number]['operator'];
    parameter_name: string;
    parameter_type: SqlQueryCompileRequest['specification']['parameters'][number]['type'];
    parameter_value: string;
    description: string;
    confirmed: boolean;
  }>;
  order_by: SqlQueryCompileRequest['specification']['order_by'];
  row_limit: number;
}

export interface SqlQueryPlanResponse {
  schema_version: '1';
  status: 'PROPOSED' | 'NEEDS_CLARIFICATION' | 'FALLBACK';
  proposal: SqlQueryPlanProposal | null;
  clarification_questions: string[];
  warning: string | null;
  diagnostic: string | null;
  llm: {
    status: 'APPLIED' | 'FALLBACK';
    prompt: {
      name: string;
      version: string;
      content_hash: string;
    } | null;
    warning: string | null;
  };
}

export interface SqlExecutionConnector {
  id: string;
  name: string;
  source: 'postgresql';
  database: string;
  available: boolean;
}

export interface SqlExecutionProfilePolicy {
  id: string;
  name: string;
  dialect: 'postgres';
  statement_timeout_ms: number;
  max_rows: number;
  max_result_bytes: number;
  version: number;
  policy_fingerprint: string;
  target_database: string;
}

export interface SqlExecutionProfile extends SqlExecutionProfilePolicy {
  allowed_schemas: string[];
  enabled: boolean;
  available: boolean;
  policy_valid: boolean;
  connector_available: boolean;
  connector_identity_matches: boolean;
  connector: {
    id: string;
    name: string;
    source: string;
    database: string;
  };
  created_by: string;
  updated_by: string;
}

export interface SqlExecutionProfileInput {
  schema_version: '1';
  name: string;
  connector_id: string;
  dialect: 'postgres';
  allowed_schemas: string[];
  statement_timeout_ms: number;
  max_rows: number;
  max_result_bytes: number;
  enabled: boolean;
}

export type SqlExecutionProfileUpdateInput = SqlExecutionProfileInput & {
  expected_version: number;
};

export interface SqlExecutionRegistryDisableInput {
  schema_version: '1';
  enabled: false;
  expected_version: number;
}

export interface SqlCatalogBinding {
  id: string;
  catalog_service: string;
  catalog_database: string;
  catalog_schema: string;
  execution_profile_id: string;
  enabled: boolean;
  version: number;
  created_by: string;
  updated_by: string;
}

export interface SqlCatalogBindingInput {
  schema_version: '1';
  catalog_service: string;
  catalog_database: string;
  catalog_schema: string;
  execution_profile_id: string;
  enabled: boolean;
}

export type SqlCatalogBindingUpdateInput = SqlCatalogBindingInput & {
  expected_version: number;
};

export interface SqlExecutionRegistryList<T> {
  schema_version: '1';
  items: T[];
}

export type SqlExecutionBindingRequest = Pick<
  SqlQueryCompileRequest,
  | 'schema_version'
  | 'schema_snapshot'
  | 'accepted_requirements'
  | 'accepted_schema'
> & {
  selected_profile_id: string | null;
};

export interface SqlExecutionCatalogScope {
  service: string;
  database: string;
  schema: string;
  table_ids: string[];
}

export interface SqlExecutionBindingResponse {
  schema_version: '1';
  status: 'BOUND' | 'NEEDS_SELECTION' | 'UNAVAILABLE';
  reason:
    | 'CATALOG_IDENTITY_MISSING'
    | 'CATALOG_BINDING_MISSING'
    | 'CROSS_PROFILE_QUERY_UNSUPPORTED'
    | 'MULTIPLE_EXECUTION_PROFILES'
    | null;
  snapshot_fingerprint: string;
  catalog_scopes: SqlExecutionCatalogScope[];
  unresolved_catalog_scopes: SqlExecutionCatalogScope[];
  candidates: SqlExecutionProfilePolicy[];
  selection: {
    decision: 'user' | 'automatic_exact';
    profile: SqlExecutionProfilePolicy;
    bindings: Array<{ binding_id: string; version: number }>;
  } | null;
}

export type BusinessDocumentOperationState =
  | 'IDLE'
  | 'ANALYZING'
  | 'ANALYZING_REVIEW'
  | 'GENERATING_DRAFT'
  | 'APPLYING_CHANGES'
  | 'EXPORTING'
  | 'FAILED';

export type BusinessDocumentCommandType =
  | 'REQUEST_INTAKE_ASSESSMENT'
  | 'REQUEST_REVIEW_ASSESSMENT'
  | 'ANSWER_QUESTION'
  | 'REQUEST_DRAFT'
  | 'DECIDE_PROPOSAL'
  | 'ADD_COMMENT'
  | 'APPLY_CHANGES'
  | 'START_REVIEW'
  | 'REQUEST_EXPORT'
  | 'ARCHIVE';

export type BusinessDocumentBlock =
  | { type: 'paragraph'; text: string }
  | { type: 'list'; items: string[] }
  | {
      type: 'table';
      headers: string[];
      rows: Array<Array<string | number | boolean | null>>;
    }
  | { type: 'plantuml'; source: string }
  | { type: 'image'; alt: string; url: string }
  | { type: 'reference'; label: string; url: string };

export interface BusinessDocumentSection {
  id: string;
  title: string;
  blocks: BusinessDocumentBlock[];
  evidence_refs?: string[];
}

export interface BusinessDocumentAst {
  schema_version: '1';
  document_type: 'business_requirements';
  template_version: string;
  sections: BusinessDocumentSection[];
}

export interface BusinessDocumentRevision {
  revision_id: string;
  revision_number: number;
  author_id?: string | null;
  author_name?: string | null;
  author_login?: string | null;
  document_ast: BusinessDocumentAst;
  section_texts: Record<string, string>;
  body_markdown: string;
  content_hash: string;
  source_event_ids?: string[];
  created_at?: number | null;
  change_basis?: BusinessDocumentRevisionBasis[];
}

export interface BusinessDocumentRevisionBasis {
  event_id: string;
  type: 'INITIAL_DRAFT' | 'QUESTION' | 'PROPOSAL' | 'COMMENT' | 'EVA_SYNC';
  title: string;
  summary: string;
  details?: string | null;
  section_id?: string | null;
  actor_id?: string;
  actor_type?: 'USER' | 'AI' | 'SYSTEM';
  actor_name?: string | null;
  actor_login?: string | null;
  initiated_by_actor_id?: string | null;
  initiated_by_actor_name?: string | null;
  initiated_by_actor_login?: string | null;
  created_at?: number | null;
}

export type BusinessDocumentEvaCapability =
  | 'OPEN'
  | 'PULL_FROM_EVA'
  | 'CREATE_EVA_CHANGE';

export interface BusinessDocumentEvaBinding {
  page_url: string;
  status: 'LINK_ONLY' | 'CONNECTED';
  capabilities: BusinessDocumentEvaCapability[];
  connector_id?: string | null;
  project_id?: string | null;
  document_id?: string | null;
  document_code?: string | null;
  document_name?: string | null;
  remote_version?: string | null;
  remote_content_hash?: string | null;
  last_pulled_content_hash?: string | null;
  last_pulled_at?: number | null;
  last_pull_event_id?: string | null;
  last_pull_review_cycle?: number | null;
}

export interface BusinessDocumentSelection {
  revision_id: string;
  section_id: string;
  selected_text: string;
  prefix: string;
  suffix: string;
  start_offset: number;
  end_offset: number;
}

export interface BusinessDocumentExportArtifact {
  artifact_id: string;
  revision_id: string;
  revision_number: number | null;
  format: 'MARKDOWN' | 'DOCX' | 'EVA_WIKI';
  filename: string;
  mime_type: string;
  size: number;
  content_hash: string;
  create_time: number | null;
}

export interface BusinessDocumentQuestionOption {
  option_id: string;
  label: string;
  description?: string;
}

export interface BusinessDocumentQuestion {
  question_id: string;
  sequence_number?: number;
  target_section_id?: string;
  text: string;
  options: BusinessDocumentQuestionOption[];
  allow_custom_answer: boolean;
  status: 'OPEN' | 'ANSWERED' | 'CANCELLED';
  answer?: {
    answer_id?: string;
    selected_option_id?: string | null;
    custom_answer?: string | null;
    actor_id?: string;
  };
}

export interface BusinessDocumentProposal {
  proposal_id: string;
  sequence_number?: number;
  target_section_id?: string;
  text: string;
  rationale?: string;
  decision: 'PENDING' | 'ACCEPTED' | 'REJECTED';
}

export interface BusinessDocumentCommentAnchor {
  revision_id: string;
  section_id: string;
  selected_text: string;
  prefix: string;
  suffix: string;
  start_offset: number;
  end_offset: number;
}

export interface BusinessDocumentCommentDisposition {
  comment_event_id: string;
  disposition: 'CONFIRMED_CHANGE' | 'NEEDS_QUESTION' | 'NO_CHANGE';
  question_id?: string;
  question_semantic_tag?: string;
}

export interface BusinessDocumentComment {
  comment_id: string;
  text: string;
  anchor?: BusinessDocumentCommentAnchor | null;
  anchor_status: 'GENERAL' | 'ANCHORED' | 'ORPHANED';
  revision_id?: string;
  section_id?: string | null;
  disposition?: BusinessDocumentCommentDisposition | null;
}

export interface BusinessDocumentReviewCycle {
  questions: BusinessDocumentQuestion[];
  proposals: BusinessDocumentProposal[];
  comments: BusinessDocumentComment[];
}

export interface BusinessDocumentJobSummary {
  job_id: string;
  job_type: string;
  status: 'PENDING' | 'RUNNING' | 'RETRY' | 'COMPLETED' | 'DEAD';
  progress?: number;
  progress_stage?: string;
  progress_message?: string | null;
  attempt: number;
  max_attempts: number;
  available_at?: number | null;
  lease_expires_at?: number | null;
  error?:
    | { code?: string; message?: string; details?: unknown }
    | string
    | null;
  create_time?: number | null;
  update_time?: number | null;
}

export interface BusinessDocumentProjection {
  document_id: string;
  catalog_entry_id?: string | null;
  owner_id?: string;
  owner_name?: string | null;
  access_role?: BusinessDocumentRole;
  permissions?: BusinessDocumentPermissions;
  title: string;
  document_type?: string;
  idea?: string;
  dataset_ids?: string[];
  state_version: number;
  lifecycle_state: BusinessDocumentLifecycleState;
  operation_state: BusinessDocumentOperationState;
  current_revision: BusinessDocumentRevision | null;
  active_review_cycle: number;
  protocol: BusinessDocumentReviewCycle;
  allowed_commands: BusinessDocumentCommandType[];
  last_error?: { code?: string; message?: string } | string | null;
  latest_job?: BusinessDocumentJobSummary | null;
  latest_exports?: BusinessDocumentExportArtifact[];
  eva_binding?: BusinessDocumentEvaBinding | null;
}

export interface BusinessDocumentSummary {
  document_id: string;
  catalog_entry_id?: string | null;
  owner_id?: string;
  owner_name?: string | null;
  access_role?: BusinessDocumentRole;
  permissions?: BusinessDocumentPermissions;
  title: string;
  lifecycle_state: BusinessDocumentLifecycleState;
  operation_state: BusinessDocumentOperationState;
  state_version: number;
  current_revision_number: number | null;
  eva_page_url?: string | null;
  latest_job?: BusinessDocumentJobSummary | null;
  update_time: number | null;
}

export interface BusinessDocumentPermissions {
  read: boolean;
  edit: boolean;
  delete: boolean;
  assign?: boolean;
}

export type BusinessDocumentRole =
  | 'AUTHOR_CREATOR'
  | 'AUTHOR_EDITOR'
  | 'MODERATOR_CREATOR'
  | 'EXTENDED_MODERATOR'
  | 'ADMIN';

export interface BusinessDocumentCapabilities {
  read: boolean;
  create: boolean;
  edit_own: boolean;
  edit_all: boolean;
  delete: boolean;
  assign: boolean;
}

export interface BusinessDocumentAssignableUser {
  user_id: string;
  nickname: string;
  email: string;
  role: BusinessDocumentRole;
}

export interface DeleteBusinessDocumentResult {
  document_id: string;
  deleted: true;
  deleted_artifacts: number;
  storage_cleanup_failures: number;
}

export interface BusinessDocumentList {
  items: BusinessDocumentSummary[];
  total: number;
  page: number;
  page_size: number;
  scope?: 'mine' | 'all';
  access_role?: BusinessDocumentRole;
  capabilities?: BusinessDocumentCapabilities;
}

interface CreateBusinessDocumentRequestBase {
  document_type: 'business_requirements';
  idea: string;
  dataset_ids?: string[];
  eva_page_url?: string;
  eva_decision?: { mode: 'SKIP' } | { mode: 'BIND' };
}

export type CreateBusinessDocumentRequest =
  | (CreateBusinessDocumentRequestBase & {
      schema_version: '1' | '2';
      title: string;
    })
  | (CreateBusinessDocumentRequestBase & {
      schema_version: '3';
      catalog_entry_id: string;
    });

export interface BusinessDocumentCatalogEntry {
  id: string;
  title: string;
  title_en?: string | null;
  description?: string | null;
  capability_level: 'L5';
  capability_type?: string | null;
  hierarchy: Record<string, string>;
}

export interface BusinessDocumentCatalog {
  items: BusinessDocumentCatalogEntry[];
  total: number;
}

export interface EvaPageBreadcrumb {
  id: string;
  name: string;
  web_url: string;
}

export interface EvaTitleMatch {
  connector_id: string;
  connector_name: string;
  eva_origin: string;
  id: string;
  name: string;
  code: string;
  project_id: string;
  web_url: string;
  breadcrumbs: EvaPageBreadcrumb[];
  hierarchy: string;
  binding_available: boolean;
  linked_document?: { document_id: string; title?: string | null };
}

export interface BusinessDocumentEvaPullResult {
  document: BusinessDocumentProjection;
  sync: {
    changed: boolean;
    direction: 'FROM_EVA';
    event_id?: string;
    remote_version?: string | null;
  };
}

export interface BusinessDocumentCommand {
  schema_version: '1';
  command_id: string;
  idempotency_key: string;
  expected_state_version: number;
  type: BusinessDocumentCommandType;
  payload: Record<string, unknown>;
}

export interface BusinessDocumentCommandResult {
  accepted: boolean;
  document_id: string;
  state_version: number;
  lifecycle_state: BusinessDocumentLifecycleState;
  operation_state: BusinessDocumentOperationState;
  job_id?: string | null;
  event_id?: string;
  allowed_commands?: BusinessDocumentCommandType[];
  idempotent_replay?: boolean;
}

export type EvaDocumentChangeState =
  | 'EDITING'
  | 'GENERATING_DRAFT'
  | 'APPROVED'
  | 'PREPARING_EVA_DRAFT'
  | 'EVA_DRAFT_READY'
  | 'PUBLISHING'
  | 'PUBLISHED';

export type EvaDocumentChangeAction =
  | 'GENERATE_DRAFT'
  | 'APPROVE'
  | 'PREPARE_EVA_DRAFT'
  | 'PUBLISH_EVA';

export interface EvaDocumentSource {
  connector_id: string;
  connector_name: string;
  id: string;
  name: string;
  code: string;
  project_id: string;
  version: string;
  modified_at: string;
  web_url: string;
  excerpt: string;
}

export interface EvaDocumentSourceSearchResult {
  items: EvaDocumentSource[];
  connectors: Array<{ connector_id: string; connector_name: string }>;
}

export interface EvaDocumentDiffLine {
  type: 'context' | 'added' | 'removed';
  content: string;
}

export interface EvaDocumentSectionDiff {
  key: string;
  title: string;
  lines: EvaDocumentDiffLine[];
}

export interface EvaDocumentDiff {
  changed: boolean;
  added_lines: number;
  removed_lines: number;
  changed_sections: number;
  sections: EvaDocumentSectionDiff[];
}

export interface EvaDocumentChangeEvent {
  event_id: string;
  sequence: number;
  event_type: string;
  actor_id: string;
  payload: Record<string, unknown>;
  create_time: number | null;
}

export interface EvaDocumentChange {
  change_id: string;
  state_version: number;
  workflow_state: EvaDocumentChangeState;
  change_summary: string;
  source: {
    connector_id: string;
    project_id: string;
    document_id: string;
    document_code?: string | null;
    document_name: string;
    web_url?: string | null;
    base_version: string;
    base_content_hash: string;
  };
  base_markdown: string;
  draft_markdown: string;
  draft_content_hash: string;
  diff: EvaDocumentDiff;
  allowed_actions: EvaDocumentChangeAction[];
  approved_at?: number | null;
  eva_draft_at?: number | null;
  published_at?: number | null;
  published_version?: string | null;
  last_error?: { code?: string; message?: string; details?: unknown } | null;
  operation_retry_after_ms?: number | null;
  events: EvaDocumentChangeEvent[];
}

export interface EvaDocumentChangeSummary {
  change_id: string;
  document_name: string;
  document_code?: string | null;
  change_summary: string;
  workflow_state: EvaDocumentChangeState;
  state_version: number;
  update_time: number | null;
  web_url?: string | null;
}

export interface EvaDocumentChangeList {
  items: EvaDocumentChangeSummary[];
  total: number;
  page: number;
  page_size: number;
}
