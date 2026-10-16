import type { BusinessDocumentOperationError } from '../types';

type OperationError =
  | BusinessDocumentOperationError
  | string
  | null
  | undefined;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requirementIssues(error: OperationError) {
  if (typeof error !== 'object' || !error || !isRecord(error.details))
    return [];
  const requirements = error.details.requirements;
  if (!Array.isArray(requirements)) return [];
  return requirements.filter(isRecord).flatMap((issue) => {
    const text = typeof issue.text === 'string' ? issue.text.trim() : '';
    const reason = typeof issue.reason === 'string' ? issue.reason.trim() : '';
    if (!text && !reason) return [];
    const sections = Array.isArray(issue.required_section_ids)
      ? issue.required_section_ids.filter(
          (id): id is string => typeof id === 'string',
        )
      : typeof issue.section_id === 'string'
        ? [issue.section_id]
        : [];
    const status =
      reason === 'WRONG_SECTION'
        ? 'Размещено не в том разделе'
        : issue.status === 'MISSING'
          ? 'Не добавлено'
          : issue.status === 'CONTRADICTED'
            ? 'Противоречит подтверждённому требованию'
            : '';
    return [
      {
        text,
        reason: reason === 'WRONG_SECTION' ? '' : reason,
        sections,
        status,
      },
    ];
  });
}

export function BusinessDocumentError({ error }: { error: OperationError }) {
  const message =
    typeof error === 'string'
      ? error
      : error?.code === 'REQUIREMENT_COVERAGE_FAILED'
        ? 'План изменений не сохранил подтверждённые требования.'
        : error?.code === 'DUPLICATE_REQUIREMENT_CONTENT'
          ? 'План изменений повторяет определения в разных разделах.'
          : error?.message || error?.code || '';
  const issues = requirementIssues(error);
  const details =
    typeof error === 'object' && error && isRecord(error.details)
      ? error.details
      : undefined;
  const duplicatePairs = Array.isArray(details?.duplicate_section_pairs)
    ? details.duplicate_section_pairs.filter(
        (pair): pair is string[] =>
          Array.isArray(pair) &&
          pair.length === 2 &&
          pair.every((id) => typeof id === 'string' && id.trim()),
      )
    : [];
  return (
    <div className="min-w-0 whitespace-pre-wrap break-words">
      <p>{message}</p>
      {duplicatePairs.map((pair, index) => (
        <p key={index}>
          Повторяющиеся определения: разделы {pair.join(' и ')}.
        </p>
      ))}
      {issues.length > 0 && (
        <ol className="mt-2 max-h-64 list-decimal space-y-2 overflow-y-auto pe-2 ps-5">
          {issues.map((issue, index) => (
            <li key={index}>
              {(issue.status || issue.sections.length > 0) && (
                <p className="font-medium">
                  {[
                    issue.status,
                    issue.sections.length > 0 &&
                      `Раздел ${issue.sections.join(', ')}`,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
              )}
              {issue.text && <p>{issue.text}</p>}
              {issue.reason && <p>Причина: {issue.reason}</p>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
