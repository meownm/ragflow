import type {
  SqlAgentProject,
  SqlQueryCompileRequest,
  SqlQueryCompileResponse,
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

function queryProposal(project: SqlAgentProject): SqlQueryPlanProposal | null {
  const artifact = record(project.artifacts?.query);
  if (!artifact || !Array.isArray(artifact.select)) return null;
  return artifact as unknown as SqlQueryPlanProposal;
}

function parseParameterValue(type: string, value: unknown) {
  const source = text(value).trim();
  if (type === 'integer') return Number(source);
  if (type === 'decimal') return source;
  if (type === 'boolean') return source === 'true';
  if (type === 'integer_list') {
    return source
      .split(/[\n,]+/)
      .map((item) => Number(item.trim()))
      .filter(Number.isFinite);
  }
  if (type === 'text_list') {
    return source
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return source;
}

export function buildCompileRequestFromProject(
  project: SqlAgentProject,
): SqlQueryCompileRequest {
  const schema = record(project.artifacts?.schema);
  const proposal = queryProposal(project);
  const acceptedRequirements = acceptedRequirementsText(project);
  if (
    !schema ||
    !record(schema.schema_snapshot) ||
    !Array.isArray(schema.accepted_schema) ||
    !proposal ||
    !acceptedRequirements
  ) {
    throw new Error('Проект ещё не содержит всех подтверждённых артефактов.');
  }
  const parameters: SqlQueryCompileRequest['specification']['parameters'] =
    proposal.filters
      .filter(
        (filter) =>
          filter.operator !== 'is_null' && filter.operator !== 'is_not_null',
      )
      .map((filter) => ({
        name: filter.parameter_name,
        type: filter.parameter_type,
        value: parseParameterValue(
          filter.parameter_type,
          filter.parameter_value,
        ),
      }));
  parameters.push({
    name: 'row_limit',
    type: 'integer',
    value: proposal.row_limit,
  });
  return {
    schema_version: '1',
    schema_snapshot: schema.schema_snapshot as JsonRecord,
    accepted_requirements: acceptedRequirements,
    accepted_schema:
      schema.accepted_schema as SqlQueryCompileRequest['accepted_schema'],
    specification: {
      dialect: 'postgres',
      from: {
        entity_id: proposal.base_entity_id,
        alias: proposal.aliases[proposal.base_entity_id],
      },
      select: proposal.select,
      joins: proposal.joins.map((join) => ({
        ...join,
        decision: 'user',
        confirmed: true,
      })),
      filters: proposal.filters.map((filter) => ({
        id: filter.id,
        column_id: filter.column_id,
        operator: filter.operator,
        parameter:
          filter.operator === 'is_null' || filter.operator === 'is_not_null'
            ? null
            : filter.parameter_name,
        description: filter.description,
        decision: 'user',
        confirmed: true,
      })),
      order_by: proposal.order_by,
      parameters,
      limit_parameter: 'row_limit',
    },
  };
}

export function queryProposalFromPending(project: SqlAgentProject) {
  const proposal = record(agentResult(project)?.proposal);
  return proposal && Array.isArray(proposal.select)
    ? (proposal as unknown as SqlQueryPlanProposal)
    : null;
}

function markdownList(values: string[], empty = 'Не задано') {
  return values.length ? values.map((value) => `- ${value}`).join('\n') : empty;
}

export function buildSqlDocumentMarkdown(
  project: SqlAgentProject,
  compiled: SqlQueryCompileResponse,
) {
  const requirements = record(project.artifacts?.requirements);
  const schema = record(project.artifacts?.schema);
  const proposal = queryProposal(project);
  const requirementItems = Array.isArray(requirements?.requirements)
    ? requirements.requirements.filter(record)
    : [];
  const snapshot = record(schema?.schema_snapshot);
  const tables = Array.isArray(snapshot?.requirements)
    ? snapshot.requirements
        .filter(record)
        .map((item) => record(item.selected_table))
        .filter((item): item is JsonRecord => item !== null)
    : [];
  const selectItems = proposal?.select ?? [];
  const joins = proposal?.joins ?? [];
  const filters = proposal?.filters ?? [];
  return `# ${project.title}

## 1. Исходные требования

${project.source_request || 'Не заданы'}

### Подтверждённые требования

${markdownList(requirementItems.map((item) => text(item.statement)).filter(Boolean))}

## 2. Требования к выводу данных

### Таблицы и поля

${markdownList(
  tables.map((table) => {
    const columns = Array.isArray(table.columns)
      ? table.columns
          .filter(record)
          .filter((column) => column.selected === true)
          .map((column) => text(column.name))
          .filter(Boolean)
      : [];
    return `${text(table.fqn) || text(table.name)}: ${columns.join(', ') || 'поля не указаны'}`;
  }),
)}

### SELECT без фильтров

${markdownList(selectItems.map((item) => `${item.kind}: ${item.column_id} AS ${item.alias}`))}

### Соединение таблиц

${markdownList(
  joins.map(
    (join) =>
      `${join.join_type} ${join.entity_id}: ${join.left_column_id} = ${join.right_column_id} — ${join.description}`,
  ),
)}

## 3. Требования к фильтрации

${markdownList(
  filters.map(
    (filter) =>
      `${filter.description} — ${filter.column_id} ${filter.operator} :${filter.parameter_name}`,
  ),
)}

## 4. Ограничения на вывод

- Лимит строк: ${proposal?.row_limit ?? 'не задан'}
- Сортировка: ${
    proposal?.order_by
      .map((item) => `${item.select_item_id} ${item.direction}`)
      .join(', ') || 'не задана'
  }

## 5. SQL-запрос

\`\`\`sql
${compiled.sql || '-- SQL не сформирован'}
\`\`\`

### Параметры

\`\`\`json
${JSON.stringify(compiled.parameters, null, 2)}
\`\`\`

## 6. Постобработка результатов на Python

Не настроена. Возможность зарезервирована для следующей версии конструктора.
`;
}
