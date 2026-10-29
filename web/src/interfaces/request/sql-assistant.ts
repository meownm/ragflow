export interface AssistantCommand {
  client_request_id: string;
  expected_revision: number;
  expected_project_version: number;
  type: string;
  payload: Record<string, unknown>;
}

export interface CreateAssistantSession {
  question: string;
  client_request_id: string;
  references?: Array<{ kind: 'TABLE'; id: string }>;
}
