import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  documentTypes,
  getWorkspaceOrganization,
  listWorkspaceMembers,
  setWorkspaceMember,
  setWorkspaceOrganization,
  updateWorkspaceDocument,
  type WorkspaceDocument,
  type WorkspaceMember,
  type WorkspaceOrganization,
} from '@/services/linked-document-service';
import type { SourceWorkspace } from '@/services/source-workbench-service';
import { sourceRequestError } from '@/services/source-workbench-service';
import { useEffect, useRef, useState } from 'react';
import { Choice } from './document-controls';

export const policies = {
  working: 'Рабочий документ',
  approval: 'Изменения по согласованию',
  locked: 'Зафиксирован',
};
export type RunDocumentAction = (
  action: () => Promise<WorkspaceDocument | void>,
) => Promise<void>;

export function WorkspaceMembers({
  workspaceId,
  canManage,
  onWorkspaceChange,
}: {
  workspaceId: string;
  canManage: boolean;
  onWorkspaceChange?(workspace: SourceWorkspace): void;
}) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<WorkspaceMember['role']>('reader');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [opened, setOpened] = useState(false);
  const [organization, setOrganization] =
    useState<WorkspaceOrganization | null>(null);
  const [tenantId, setTenantId] = useState('');
  const generation = useRef(0);
  useEffect(() => {
    setBusy(false);
    setError('');
    setOrganization(null);
    setMembers([]);
    return () => {
      generation.current += 1;
    };
  }, [workspaceId]);
  useEffect(() => {
    if (!opened) return;
    let cancelled = false;
    listWorkspaceMembers(workspaceId)
      .then((items) => {
        if (!cancelled) setMembers(items);
      })
      .catch((cause) => {
        if (!cancelled)
          setError(sourceRequestError(cause, 'Не удалось получить участников'));
      });
    if (canManage)
      getWorkspaceOrganization(workspaceId)
        .then((value) => {
          if (!cancelled) {
            setOrganization(value);
            setTenantId(value.tenant_id || value.items[0]?.id || '');
          }
        })
        .catch((cause) => {
          if (!cancelled)
            setError(
              sourceRequestError(cause, 'Не удалось получить организацию'),
            );
        });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, opened, canManage]);
  const roles = {
    owner: 'Владелец',
    editor: 'Редактор',
    reviewer: 'Согласующий',
    reader: 'Читатель',
  };
  return (
    <details
      onToggle={(event) => setOpened(event.currentTarget.open)}
      className="border-t border-border-button pt-3 text-sm"
    >
      <summary className="cursor-pointer">Участники пространства</summary>
      {canManage && organization && !organization.tenant_id && (
        <form
          className="my-3 space-y-2"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            const requestGeneration = generation.current;
            try {
              const result = await setWorkspaceOrganization(
                workspaceId,
                tenantId,
                organization.version,
              );
              if (requestGeneration === generation.current) {
                setOrganization(result);
                onWorkspaceChange?.(result.workspace);
              }
            } catch (cause) {
              if (requestGeneration === generation.current)
                setError(
                  sourceRequestError(cause, 'Не удалось назначить организацию'),
                );
            } finally {
              if (requestGeneration === generation.current) setBusy(false);
            }
          }}
        >
          <Choice
            label="Организация пространства"
            value={tenantId}
            options={Object.fromEntries(
              organization.items.map((item) => [item.id, item.name]),
            )}
            onChange={setTenantId}
          />
          <Button variant="outline" type="submit" disabled={busy || !tenantId}>
            Назначить организацию
          </Button>
          <p className="text-xs text-text-secondary">
            Организация нужна для совместной работы. После назначения заменить
            её нельзя.
          </p>
        </form>
      )}
      <ul className="my-3 space-y-2">
        {members.map((member) => (
          <li key={member.user_id}>
            {member.name || member.email || member.user_id} ·{' '}
            {roles[member.role]}
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="text-state-error">
          {error}
        </p>
      )}
      {canManage && (
        <form
          className="space-y-3"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            const requestGeneration = generation.current;
            try {
              const result = await setWorkspaceMember(workspaceId, {
                email,
                role,
              });
              if (requestGeneration === generation.current) {
                setMembers(result);
                setEmail('');
              }
            } catch (cause) {
              if (requestGeneration === generation.current)
                setError(
                  sourceRequestError(cause, 'Не удалось сохранить участника'),
                );
            } finally {
              if (requestGeneration === generation.current) setBusy(false);
            }
          }}
        >
          <Input
            type="email"
            aria-label="Почта участника"
            placeholder="Электронная почта участника"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
          <Choice
            label="Полномочия участника"
            value={role}
            options={{
              reader: roles.reader,
              editor: roles.editor,
              reviewer: roles.reviewer,
            }}
            onChange={(value) => setRole(value as WorkspaceMember['role'])}
          />
          <Button type="submit" variant="outline" disabled={busy || !email}>
            Сохранить участника
          </Button>
          <p className="text-xs text-text-secondary">
            Доступ к пространству не открывает закрытые источники.
          </p>
        </form>
      )}
    </details>
  );
}

export function DocumentSettings({
  workspaceId,
  document,
  disabled,
  run,
}: {
  workspaceId: string;
  document: WorkspaceDocument;
  disabled: boolean;
  run: RunDocumentAction;
}) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [error, setError] = useState('');
  const [opened, setOpened] = useState(false);
  const [title, setTitle] = useState(document.title);
  useEffect(() => setTitle(document.title), [document.title]);
  useEffect(() => {
    if (!opened) return;
    let cancelled = false;
    listWorkspaceMembers(workspaceId)
      .then((items) => {
        if (!cancelled) setMembers(items);
      })
      .catch((cause) => {
        if (!cancelled)
          setError(sourceRequestError(cause, 'Не удалось получить участников'));
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, opened]);
  const update = (
    values: Partial<
      Pick<
        WorkspaceDocument,
        | 'title'
        | 'document_type'
        | 'change_policy'
        | 'responsible_id'
        | 'approver_id'
        | 'required_for_readiness'
      >
    >,
  ) =>
    void run(() =>
      updateWorkspaceDocument(workspaceId, document.id, {
        ...values,
        expected_version: document.version,
      }),
    );
  const options = (roles: string[]) =>
    Object.fromEntries(
      members
        .filter((member) => roles.includes(member.role))
        .map((member) => [
          member.user_id,
          member.name || member.email || member.user_id,
        ]),
    );
  return (
    <details
      className="border-t border-border-button pt-3"
      onToggle={(event) => setOpened(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-sm">Свойства документа</summary>
      <div className="mt-3 space-y-3">
        {document.source_kind === 'local' && (
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={!!document.required_for_readiness}
              disabled={disabled || !document.capabilities.manage}
              onChange={(event) =>
                update({ required_for_readiness: event.target.checked })
              }
            />
            Входит в обязательный комплект
          </label>
        )}
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            update({ title });
          }}
        >
          <Input
            aria-label="Название документа"
            value={title}
            maxLength={255}
            onChange={(event) => setTitle(event.target.value)}
            disabled={disabled || !document.capabilities.edit}
          />
          <Button
            variant="outline"
            type="submit"
            disabled={
              disabled ||
              !document.capabilities.edit ||
              !title.trim() ||
              title === document.title
            }
          >
            Переименовать
          </Button>
        </form>
        {error && (
          <p role="alert" className="text-state-error">
            {error}
          </p>
        )}
        <Choice
          label="Тип документа"
          value={document.document_type}
          options={documentTypes}
          disabled={disabled || !document.capabilities.edit}
          onChange={(value) =>
            update({
              document_type: value as WorkspaceDocument['document_type'],
            })
          }
        />
        <Choice
          label="Порядок изменения"
          value={document.change_policy}
          options={policies}
          disabled={
            disabled ||
            !document.capabilities.manage ||
            document.source_kind !== 'local'
          }
          onChange={(value) =>
            update({
              change_policy: value as WorkspaceDocument['change_policy'],
            })
          }
        />
        <Choice
          label="Ответственный за документ"
          value={document.responsible_id}
          options={options(['owner', 'editor'])}
          disabled={
            disabled || !document.capabilities.manage || !members.length
          }
          onChange={(value) => update({ responsible_id: value })}
        />
        <Choice
          label="Согласующий документа"
          value={document.approver_id}
          options={options(['owner', 'reviewer'])}
          disabled={
            disabled || !document.capabilities.manage || !members.length
          }
          onChange={(value) => update({ approver_id: value })}
        />
      </div>
    </details>
  );
}
