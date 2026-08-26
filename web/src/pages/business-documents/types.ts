export type BusinessDocumentLifecycleState =
  | 'INTAKE'
  | 'REVIEW'
  | 'AGREED'
  | 'ARCHIVED';

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
  | { type: 'plantuml' | 'bpmn'; source: string }
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
  document_ast: BusinessDocumentAst;
  section_texts: Record<string, string>;
  body_markdown: string;
  content_hash: string;
  source_event_ids?: string[];
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

export interface BusinessDocumentProjection {
  document_id: string;
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
  latest_exports?: BusinessDocumentExportArtifact[];
}

export interface BusinessDocumentSummary {
  document_id: string;
  title: string;
  lifecycle_state: BusinessDocumentLifecycleState;
  operation_state: BusinessDocumentOperationState;
  state_version: number;
  current_revision_number: number | null;
  update_time: number | null;
}

export interface BusinessDocumentList {
  items: BusinessDocumentSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface CreateBusinessDocumentRequest {
  schema_version: '1';
  document_type: 'business_requirements';
  title: string;
  idea: string;
  dataset_ids?: string[];
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
