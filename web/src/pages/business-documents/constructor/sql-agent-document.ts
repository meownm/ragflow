import type {
  SqlAgentProject,
  SqlQueryPlanProposal,
  SqlSchemaResolutionResponse,
} from '@/pages/business-documents/types';
import { selectedSchemaTables } from './query-specification';
import {
  buildSchemaSnapshot,
  createSchemaResolution,
  type SchemaInterpretation,
  type SchemaWorkspaceState,
} from './schema-workspace';

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function text(value: unknown) {
  return typeof value === 'string' ? value : '';
}

function agentResult(project: SqlAgentProject) {
  return record(project.pending_proposal?.payload.agent_result);
}

export function requirementsProposal(project: SqlAgentProject) {
  const proposal = record(agentResult(project)?.proposal);
  const requirements = Array.isArray(proposal?.requirements)
    ? proposal.requirements.filter(record)
    : [];
  const questions = Array.isArray(proposal?.questions)
    ? proposal.questions.filter(record)
    : [];
  return { requirements, questions };
}

export function acceptedRequirementsText(project: SqlAgentProject) {
  const artifact = record(project.artifacts?.requirements);
  const requirements = Array.isArray(artifact?.requirements)
    ? artifact.requirements.filter(record)
    : [];
  return requirements
    .map((item) => text(item.statement).trim())
    .filter(Boolean)
    .map((statement) => `- ${statement}`)
    .join('\n');
}

function schemaInterpretation(
  raw: JsonRecord,
  response: SqlSchemaResolutionResponse,
): SchemaInterpretation {
  const prompt = response.llm.prompt;
  return {
    kind: raw.kind === 'entity' || raw.kind === 'field' ? raw.kind : 'unknown',
    normalizedTerm: text(raw.normalized_term),
    recommendedEntityId: text(raw.recommended_entity_id) || null,
    recommendedColumnIds: Array.isArray(raw.recommended_column_ids)
      ? raw.recommended_column_ids.map(text).filter(Boolean)
      : [],
    confidence:
      typeof raw.confidence === 'number' && Number.isFinite(raw.confidence)
        ? raw.confidence
        : null,
    reason: text(raw.reason),
    clarificationQuestion: text(raw.clarification_question) || null,
    llmStatus: response.llm.status,
    promptName: prompt?.name ?? null,
    promptVersion: prompt?.version ?? null,
    promptHash: prompt?.content_hash ?? null,
    llmWarning: response.llm.warning,
  };
}

export function schemaWorkspaceFromProposal(
  project: SqlAgentProject,
): SchemaWorkspaceState {
  const result = agentResult(project) as SqlSchemaResolutionResponse | null;
  const requirements = acceptedRequirementsText(project);
  if (!result || !Array.isArray(result.resolutions)) {
    return { input: '', requirements, resolutions: [] };
  }
  const resolutions = result.resolutions.map((item) => {
    const interpretation = schemaInterpretation(
      item.interpretation as unknown as JsonRecord,
      result,
    );
    const resolution = createSchemaResolution(
      item.term,
      item.catalog_answer,
      undefined,
      interpretation,
    );
    const recommended = interpretation.recommendedEntityId;
    if (
      recommended &&
      !resolution.selectedEntityId &&
      resolution.candidates.some((candidate) => candidate.id === recommended)
    ) {
      return {
        ...resolution,
        status: 'confirmed' as const,
        decision: 'user' as const,
        selectedEntityId: recommended,
      };
    }
    return resolution;
  });
  return {
    input: result.resolutions.map((item) => item.term).join('\n'),
    requirements,
    resolutions,
  };
}

export function schemaArtifactFromWorkspace(workspace: SchemaWorkspaceState) {
  const schemaSnapshot = buildSchemaSnapshot(
    workspace.resolutions,
    workspace.requirements,
  );
  const selected = selectedSchemaTables(workspace);
  if (schemaSnapshot.status !== 'READY' || !selected.length) {
    throw new Error('Выберите таблицы и поля для всех сущностей.');
  }
  return {
    schema_snapshot: schemaSnapshot as unknown as JsonRecord,
    accepted_schema: selected.map(({ table }) => ({
      entity_id: table.id,
      version: table.version!,
      schema_fingerprint: table.schemaFingerprint!,
    })),
  };
}

export function queryProposalFromPending(project: SqlAgentProject) {
  const proposal = record(agentResult(project)?.proposal);
  return proposal && Array.isArray(proposal.select)
    ? (proposal as unknown as SqlQueryPlanProposal)
    : null;
}
