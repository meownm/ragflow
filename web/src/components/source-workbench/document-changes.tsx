import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  decideDocumentChange,
  getWorkspaceDocument,
  prepareDocumentChanges,
  verifyDocumentSelection,
  type ChangeSet,
  type WorkspaceDocument,
} from '@/services/linked-document-service';
import { useEffect, useMemo, useState } from 'react';
import { documentInputClass, sectionTitle } from './document-controls';
import type { RunDocumentAction } from './document-settings';

const outcomes = {
  changes_required: 'Предложены правки',
  no_impact: 'Изменения не требуются',
  conflict: 'Противоречие источников',
  insufficient_data: 'Недостаточно данных',
};

function operationGroups(change: ChangeSet) {
  const groups: { sections: string[]; sources: Set<string> }[] = [];
  for (const operation of change.plan.operations) {
    const matching = groups.filter((group) =>
      operation.source_ids.some((id) => group.sources.has(id)),
    );
    const merged = {
      sections: [
        ...matching.flatMap((group) => group.sections),
        operation.section_id,
      ],
      sources: new Set([
        ...matching.flatMap((group) => [...group.sources]),
        ...operation.source_ids,
      ]),
    };
    for (const group of matching) groups.splice(groups.indexOf(group), 1);
    groups.push(merged);
  }
  return groups;
}

export function DocumentChanges({
  workspaceId,
  document,
  proposal,
  busy,
  dirty,
  pending,
  locked,
  run,
  onSelectChange,
  onPreview,
}: {
  workspaceId: string;
  document: WorkspaceDocument;
  proposal?: ChangeSet;
  busy: boolean;
  dirty: boolean;
  pending: boolean;
  locked: boolean;
  run: RunDocumentAction;
  onSelectChange(id: string): void;
  onPreview(id: string): void;
}) {
  const [prompt, setPrompt] = useState(
    'Приведи документ в соответствие с подключёнными основаниями. Сохрани незатронутые требования и ручной текст.',
  );
  const [comment, setComment] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const proposalId = proposal?.id;
  const operationIds = useMemo(
    () =>
      proposal?.plan.operations.map((operation) => operation.section_id) || [],
    [proposal?.plan.operations],
  );
  useEffect(() => {
    setSelected(operationIds);
  }, [proposalId, operationIds]);
  const current =
    proposal?.status === 'ready' &&
    proposal.base_revision_id === document.current_revision_id &&
    proposal.document_version === document.version;
  const partial =
    proposal?.origin !== 'manual' &&
    !!proposal?.plan.operations.length &&
    selected.length !== proposal.plan.operations.length;
  const disabled =
    busy || dirty || pending || !current || !document.capabilities.approve;
  const groups = useMemo(
    () => (proposal ? operationGroups(proposal) : []),
    [proposal],
  );

  return (
    <div className="space-y-5">
      <label className="block text-sm">
        Задача подготовки
        <textarea
          className={`${documentInputClass} mt-2 min-h-24`}
          aria-label="Задача подготовки"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </label>
      <Button
        disabled={
          busy ||
          dirty ||
          pending ||
          locked ||
          !document.dependencies.length ||
          document.provenance_state === 'unresolved'
        }
        onClick={() =>
          void run(async () => {
            await prepareDocumentChanges(
              workspaceId,
              document.id,
              document.version,
              prompt,
            );
            onSelectChange('');
            return getWorkspaceDocument(workspaceId, document.id);
          })
        }
      >
        Подготовить предложения
      </Button>
      {!document.dependencies.length && (
        <p className="text-sm text-text-secondary">
          Подключите основания во вкладке «Источники и связи».
        </p>
      )}
      <ul className="space-y-2">
        {document.changes.map((change) => (
          <li key={change.id}>
            <button
              className="text-start text-sm text-accent-primary"
              onClick={() => onSelectChange(change.id)}
            >
              {change.origin === 'manual'
                ? 'Ручная правка'
                : outcomes[change.plan.outcome]}{' '}
              ·{' '}
              {change.status === 'applied'
                ? 'Применено'
                : change.status === 'rejected'
                  ? 'Отклонено'
                  : change.status === 'blocked'
                    ? 'Требуется решение'
                    : 'Ожидает подтверждения'}
            </button>
          </li>
        ))}
      </ul>
      {proposal && (
        <div className="space-y-3 border-t border-border-button pt-4">
          <h3 className="font-medium">
            {proposal.origin === 'manual'
              ? 'Согласование ручной правки'
              : outcomes[proposal.plan.outcome]}
          </h3>
          <p className="text-sm">{proposal.plan.summary}</p>
          {proposal.origin === 'manual' && proposal.author_name && (
            <p className="text-sm text-text-secondary">
              Автор: {proposal.author_name}
              {proposal.created_at
                ? ` · ${new Date(proposal.created_at).toLocaleString()}`
                : ''}
            </p>
          )}
          {proposal.origin === 'manual' && (
            <p className="text-sm text-text-secondary">
              Проверьте полный текст ручной правки и связанные требования.
              Согласование текста не закрывает проверку влияния оснований.
            </p>
          )}
          {proposal.decisions?.map((decision) => (
            <p
              key={`${decision.actor_id}-${decision.created_at}`}
              className="text-sm text-text-secondary"
            >
              {decision.actor_name}:{' '}
              {decision.action === 'accept' ? 'Подтверждено' : 'Отклонено'} ·{' '}
              {new Date(decision.created_at).toLocaleString()}
              {decision.comment ? ` · ${decision.comment}` : ''}
            </p>
          ))}
          {proposal.plan.source_results.map((result) => (
            <div key={result.dependency_id} className="text-sm">
              <p className="font-medium">
                {
                  proposal.inputs.find(
                    (input) => input.dependency_id === result.dependency_id,
                  )?.title
                }
              </p>
              <p>{result.explanation}</p>
            </div>
          ))}
          {proposal.verification.issues.map((issue, index) => (
            <p key={index} className="text-sm text-state-error">
              {issue}
            </p>
          ))}
          {proposal.status === 'ready' && !current && (
            <p role="alert" className="text-sm text-state-error">
              Предложение устарело: документ или его настройки изменились.
              Подготовьте новое.
            </p>
          )}
          {(proposal.origin === 'manual' ||
            proposal.plan.operations.length > 0) &&
            (current ? (
              <Button variant="outline" onClick={() => onPreview(proposal.id)}>
                Посмотреть правки в документе
              </Button>
            ) : (
              <p className="text-sm text-text-secondary">
                Предварительный просмотр доступен только для актуального
                предложения, готового к подтверждению.
              </p>
            ))}
          {current && proposal.origin !== 'manual' && groups.length > 1 && (
            <fieldset className="space-y-2 text-sm">
              <legend className="font-medium">Независимые группы правок</legend>
              <p className="text-xs text-text-secondary">
                Для выбранной части система отдельно проверит независимость.
                Отложенные основания останутся открытыми.
              </p>
              {groups.map((group) => (
                <label className="flex gap-2" key={group.sections[0]}>
                  <input
                    type="checkbox"
                    checked={group.sections.every((id) =>
                      selected.includes(id),
                    )}
                    disabled={busy || pending}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked
                          ? [...new Set([...selected, ...group.sections])]
                          : selected.filter(
                              (id) => !group.sections.includes(id),
                            ),
                      )
                    }
                  />
                  {group.sections
                    .map((id) => {
                      const operation = proposal.plan.operations.find(
                        (item) => item.section_id === id,
                      );
                      const section = document.sections.find(
                        (item) => item.id === id,
                      );
                      return sectionTitle({
                        id,
                        text:
                          operation?.text ||
                          section?.text ||
                          'Удаление раздела',
                      });
                    })
                    .join(', ')}
                </label>
              ))}
            </fieldset>
          )}
          {proposal.status === 'ready' && (
            <>
              <Input
                aria-label="Комментарий к решению"
                placeholder="Комментарий к решению"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={
                    disabled ||
                    (!!proposal.plan.operations.length && !selected.length)
                  }
                  onClick={() =>
                    void run(async () => {
                      if (partial) {
                        await verifyDocumentSelection(
                          workspaceId,
                          document.id,
                          proposal.id,
                          selected,
                        );
                        onSelectChange('');
                        return getWorkspaceDocument(workspaceId, document.id);
                      }
                      return decideDocumentChange(
                        workspaceId,
                        document.id,
                        proposal.id,
                        'accept',
                        comment,
                      );
                    })
                  }
                >
                  {partial
                    ? 'Проверить выбранные правки'
                    : proposal.plan.outcome === 'no_impact'
                      ? 'Подтвердить отсутствие влияния'
                      : 'Подтвердить и применить'}
                </Button>
                <Button
                  variant="outline"
                  disabled={busy || dirty || !document.capabilities.approve}
                  onClick={() =>
                    void run(() =>
                      decideDocumentChange(
                        workspaceId,
                        document.id,
                        proposal.id,
                        'reject',
                        comment,
                      ),
                    )
                  }
                >
                  Отклонить предложение
                </Button>
              </div>
              <p className="text-xs text-text-secondary">
                Отклонение предложения не подтверждает отсутствие влияния
                источника.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
