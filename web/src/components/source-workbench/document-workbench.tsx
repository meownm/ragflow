import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  approveWorkspaceDocument,
  cancelDocumentJob,
  createWorkspaceDocument,
  documentTypes,
  establishDocumentBaseline,
  getDocumentRevision,
  getWorkspaceDocument,
  importWorkspaceSource,
  listWorkspaceDocuments,
  refreshWorkspaceDocument,
  updateWorkspaceDocument,
  type ChangeSet,
  type DocumentSummary,
  type DocumentType,
  type Revision,
  type Section,
  type WorkspaceDocument,
} from '@/services/linked-document-service';
import {
  sourceRequestError,
  type SourceWorkspace,
} from '@/services/source-workbench-service';
import { useCallback, useEffect, useRef, useState } from 'react';
import { DocumentChanges } from './document-changes';
import {
  Choice,
  sectionTitle,
  documentInputClass as selectClass,
} from './document-controls';
import { DocumentDependencies } from './document-dependencies';
import { WorkspaceMembers, policies } from './document-settings';
import { SourceArticleContent } from './source-article-content';

const stages: Record<string, string> = {
  queued: 'В очереди',
  preparing: 'Проверка оснований',
  planning: 'Подготовка правок',
  verifying: 'Смысловая проверка',
  done: 'Завершено',
  failed: 'Ошибка',
  cancelled: 'Отменено',
};
function ProposalText({
  section,
  change,
}: {
  section: Section;
  change?: ChangeSet;
}) {
  const operation = change?.plan.operations.find(
    (item) => item.section_id === section.id && item.op !== 'add',
  );
  return (
    <div id={`document-section-${section.id}`} className="scroll-mt-24">
      {operation ? (
        <div className="my-4 border-s-2 border-accent-primary ps-3">
          <p className="text-sm font-medium">
            {operation.op === 'delete'
              ? 'Предлагается удалить раздел'
              : 'Предлагается изменить раздел'}
          </p>
          {operation.op === 'replace' && (
            <SourceArticleContent
              text={operation.text || ''}
              label={`Предложение: ${sectionTitle(section)}`}
            />
          )}
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer text-text-secondary">
              Прежний текст
            </summary>
            <SourceArticleContent
              text={section.text}
              label={`Прежний текст: ${sectionTitle(section)}`}
            />
          </details>
          {operation.source_ids.length > 0 && (
            <p className="mt-2 text-xs text-text-secondary">
              Основания:{' '}
              {operation.source_ids
                .map(
                  (id) =>
                    change?.inputs.find((input) => input.dependency_id === id)
                      ?.title || id,
                )
                .join(', ')}
            </p>
          )}
        </div>
      ) : (
        <SourceArticleContent
          text={section.text}
          label={sectionTitle(section)}
        />
      )}
    </div>
  );
}

function AddedSections({
  afterId,
  change,
}: {
  afterId: string | null;
  change?: ChangeSet;
}) {
  return (
    <>
      {change?.plan.operations
        .filter(
          (item) => item.op === 'add' && (item.after_id || null) === afterId,
        )
        .map((item) => (
          <div
            key={item.section_id}
            className="my-4 border-s-2 border-accent-primary ps-3"
          >
            <p className="text-sm font-medium">Новый раздел</p>
            <SourceArticleContent text={item.text || ''} label="Новый раздел" />
          </div>
        ))}
    </>
  );
}

export function DocumentWorkbench({
  workspace,
  onBusyChange,
  visible = true,
  onWorkspaceChange,
}: {
  workspace: SourceWorkspace;
  onBusyChange?(busy: boolean): void;
  visible?: boolean;
  onWorkspaceChange?(workspace: SourceWorkspace): void;
}) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [active, setActive] = useState<WorkspaceDocument | null>(null);
  const [editor, setEditor] = useState('');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [title, setTitle] = useState('');
  const [documentType, setDocumentType] = useState<DocumentType>(
    'technical_specification',
  );
  const [tab, setTab] = useState<'text' | 'sources' | 'changes' | 'history'>(
    'text',
  );
  const [selectedChange, setSelectedChange] = useState('');
  const [history, setHistory] = useState<Revision | null>(null);
  const generation = useRef(0);
  const refreshOnReturn = useRef(false);
  const dirty = !!active && editor !== active.content;
  const locked =
    active?.source_kind !== 'local' ||
    active?.change_policy === 'locked' ||
    !active?.capabilities.edit;
  const pending =
    active?.jobs.some((job) => ['queued', 'running'].includes(job.status)) ||
    false;
  const activeId = active?.id;
  const activeVersion = active?.version;
  const canEditWorkspace =
    !workspace.role || ['owner', 'editor'].includes(workspace.role);
  const proposal =
    active?.changes.find((item) => item.id === selectedChange) ||
    active?.changes.find((item) => ['ready', 'blocked'].includes(item.status));
  const currentProposal =
    proposal?.status === 'ready' &&
    proposal.base_revision_id === active?.current_revision_id &&
    proposal.document_version === active?.version;
  const preview = currentProposal && tab === 'text' ? proposal : undefined;

  const loadList = useCallback(async () => {
    const list = await listWorkspaceDocuments(workspace.id);
    setDocuments(list);
  }, [workspace.id]);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    listWorkspaceDocuments(workspace.id)
      .then((items) => {
        if (!cancelled) setDocuments(items);
      })
      .catch((cause) => {
        if (!cancelled)
          setError(sourceRequestError(cause, 'Не удалось загрузить документы'));
      });
    return () => {
      cancelled = true;
      generation.current += 1;
    };
  }, [workspace.id, visible]);

  useEffect(() => {
    onBusyChange?.(busy || dirty);
    return () => onBusyChange?.(false);
  }, [busy, dirty, onBusyChange]);

  useEffect(() => {
    if (!visible) {
      refreshOnReturn.current = true;
      return;
    }
    if (!activeId || dirty || busy) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const requestGeneration = generation.current;
    const refresh = async () => {
      try {
        const item = await getWorkspaceDocument(workspace.id, activeId);
        if (!cancelled && requestGeneration === generation.current) {
          if (item.version >= (activeVersion || 0)) {
            setActive(item);
            setEditor(item.content);
          }
        }
      } catch (cause) {
        if (!cancelled && requestGeneration === generation.current)
          setError(sourceRequestError(cause, 'Не удалось обновить документ'));
      } finally {
        if (!cancelled && pending) timer = setTimeout(refresh, 2000);
      }
    };
    if (refreshOnReturn.current) {
      refreshOnReturn.current = false;
      void refresh();
    } else if (pending) timer = setTimeout(refresh, 2000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [workspace.id, activeId, activeVersion, pending, dirty, busy, visible]);

  const show = (document: WorkspaceDocument) => {
    setActive(document);
    setEditor(document.content);
    setEditing(false);
    setHistory(null);
  };

  const perform = async (
    operation: () => Promise<WorkspaceDocument | void>,
  ) => {
    if (busy) return;
    const requestGeneration = ++generation.current;
    setBusy(true);
    setError('');
    try {
      const result = await operation();
      if (requestGeneration === generation.current) {
        if (result) show(result);
        await loadList();
      }
    } catch (cause) {
      if (requestGeneration === generation.current)
        setError(sourceRequestError(cause, 'Действие не выполнено'));
    } finally {
      if (requestGeneration === generation.current) setBusy(false);
    }
  };

  const open = (id: string) => {
    if (dirty) {
      setError(
        'Сохраните или отмените ручные правки перед переключением документа.',
      );
      return;
    }
    setSelectedChange('');
    void perform(() => getWorkspaceDocument(workspace.id, id));
  };

  const importSelection = () =>
    perform(async () => {
      let last: WorkspaceDocument | undefined;
      for (const reference of workspace.selected_documents)
        last = await importWorkspaceSource(workspace.id, reference);
      return last;
    });

  return (
    <section
      aria-label="Связанные документы"
      className="mt-5 border-t border-border-button pt-5"
    >
      {error && (
        <p role="alert" className="mb-4 text-state-error">
          {error}
        </p>
      )}
      <div className="grid min-w-0 gap-6 xl:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="space-y-5">
          <div>
            <h2 className="font-semibold">Документы пространства</h2>
            <p className="mt-1 text-xs text-text-secondary">
              Источники, рабочие документы и их редакции.
            </p>
          </div>
          <ul className="space-y-1">
            {documents.map((document) => (
              <li key={document.id}>
                <button
                  className={`w-full rounded p-3 text-start text-sm ${active?.id === document.id ? 'bg-bg-card ring-1 ring-border-button' : 'hover:bg-bg-card'}`}
                  disabled={busy}
                  aria-current={active?.id === document.id ? 'page' : undefined}
                  onClick={() => open(document.id)}
                >
                  <span className="block font-medium">{document.title}</span>
                  <span className="text-xs text-text-secondary">
                    {documentTypes[document.document_type]} ·{' '}
                    {document.source_kind === 'indexed'
                      ? 'Источник'
                      : document.approved_revision_id ===
                          document.current_revision_id
                        ? 'Утверждён'
                        : 'Рабочая редакция'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {!documents.length && (
            <p className="text-sm text-text-secondary">
              Создайте документ или подключите выбранные статьи.
            </p>
          )}
          <form
            className="space-y-3 border-t border-border-button pt-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (dirty) return;
              void perform(async () => {
                const result = await createWorkspaceDocument(workspace.id, {
                  title,
                  document_type: documentType,
                  content: '',
                });
                setTitle('');
                return result;
              });
            }}
          >
            <Input
              aria-label="Название нового документа"
              placeholder="Название документа"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={255}
              disabled={busy}
            />
            <Choice
              label="Тип нового документа"
              value={documentType}
              options={documentTypes}
              onChange={(value) => setDocumentType(value as DocumentType)}
              disabled={busy}
            />
            <Button
              type="submit"
              disabled={busy || dirty || !title.trim() || !canEditWorkspace}
            >
              Создать документ
            </Button>
          </form>
          <Button
            type="button"
            variant="outline"
            className="h-auto min-h-8 w-full whitespace-normal py-2"
            disabled={
              busy ||
              dirty ||
              !workspace.selected_documents.length ||
              !canEditWorkspace
            }
            onClick={() => void importSelection()}
          >
            Подключить выбранные статьи ({workspace.selected_documents.length})
          </Button>
          <p className="text-xs text-text-secondary">
            Статьи выбираются во вкладке поиска. Их текст сохраняется как снимок
            источника.
          </p>
          <WorkspaceMembers
            workspaceId={workspace.id}
            canManage={!workspace.role || workspace.role === 'owner'}
            onWorkspaceChange={onWorkspaceChange}
          />
        </aside>
        {active ? (
          <div className="min-w-0">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-button pb-4">
              <div>
                <h2 className="text-xl font-semibold">{active.title}</h2>
                <p className="mt-1 text-sm text-text-secondary">
                  {documentTypes[active.document_type]} · редакция{' '}
                  {active.revisions[0]?.number} ·{' '}
                  {policies[active.change_policy]}
                </p>
                <p className="mt-1 text-xs text-text-secondary">
                  {active.approved_revision_id === active.current_revision_id
                    ? 'Текущая редакция утверждена'
                    : 'Текущая редакция не утверждена'}
                  {active.impacts.some((impact) => impact.state === 'pending')
                    ? ' · Есть непроверенные изменения оснований'
                    : ''}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  disabled={busy || dirty || !active.capabilities.edit}
                  onClick={() =>
                    void perform(() =>
                      refreshWorkspaceDocument(workspace.id, active.id),
                    )
                  }
                >
                  Проверить источники
                </Button>
                <Button
                  variant="outline"
                  disabled={
                    busy ||
                    dirty ||
                    !active.capabilities.approve ||
                    active.approved_revision_id === active.current_revision_id
                  }
                  onClick={() =>
                    void perform(() =>
                      approveWorkspaceDocument(
                        workspace.id,
                        active.id,
                        active.version,
                      ),
                    )
                  }
                >
                  Утвердить редакцию
                </Button>
              </div>
            </div>
            {active.provenance_state === 'unresolved' && (
              <div className="my-4 rounded border border-border-button p-4 text-sm">
                <p>
                  Исторические тексты источников этого черновика не сохранены.
                  Можно явно зафиксировать сегодняшние источники как новую базу.
                  Это не восстановит прежнюю историю.
                </p>
                <Button
                  className="mt-3"
                  disabled={busy || dirty || !active.capabilities.manage}
                  onClick={() =>
                    void perform(() =>
                      establishDocumentBaseline(
                        workspace.id,
                        active.id,
                        active.version,
                      ),
                    )
                  }
                >
                  Подтвердить текущую базу источников
                </Button>
              </div>
            )}
            <nav aria-label="Документ" className="my-4 flex flex-wrap gap-4">
              {(
                [
                  ['text', 'Текст'],
                  ['sources', 'Источники и связи'],
                  ['changes', 'Изменения'],
                  ['history', 'История'],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  className={`border-b-2 py-2 text-sm ${tab === key ? 'border-accent-primary text-accent-primary' : 'border-transparent'}`}
                  aria-current={tab === key ? 'page' : undefined}
                  onClick={() => setTab(key)}
                >
                  {label}
                </button>
              ))}
            </nav>
            {active.jobs.map((job) => (
              <div
                key={job.id}
                className="mb-3 text-sm"
                role={job.status === 'failed' ? 'alert' : 'status'}
              >
                {stages[job.stage] || job.stage}
                {job.error && `: ${job.error.message}`}
                {['queued', 'running'].includes(job.status) && (
                  <Button
                    className="ms-3"
                    variant="outline"
                    disabled={busy || !active.capabilities.edit}
                    onClick={() =>
                      void perform(async () => {
                        const result = await cancelDocumentJob(
                          workspace.id,
                          active.id,
                          job.id,
                        );
                        if (dirty) {
                          setActive((previous) =>
                            previous
                              ? {
                                  ...previous,
                                  jobs: previous.jobs.map((item) =>
                                    item.id === result.id ? result : item,
                                  ),
                                }
                              : previous,
                          );
                          return;
                        }
                        return getWorkspaceDocument(workspace.id, active.id);
                      })
                    }
                  >
                    Отменить задание
                  </Button>
                )}
              </div>
            ))}
            {tab === 'text' && (
              <>
                <div className="flex flex-wrap gap-2">
                  {!editing && (
                    <Button
                      variant="outline"
                      disabled={busy || locked}
                      onClick={() => setEditing(true)}
                    >
                      Редактировать текст
                    </Button>
                  )}
                  {editing && (
                    <>
                      <Button
                        disabled={busy || !dirty}
                        onClick={() =>
                          void perform(() =>
                            updateWorkspaceDocument(workspace.id, active.id, {
                              content: editor,
                              expected_version: active.version,
                            }),
                          )
                        }
                      >
                        {active.change_policy === 'approval'
                          ? 'Отправить на согласование'
                          : 'Сохранить новую редакцию'}
                      </Button>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                          setEditor(active.content);
                          setEditing(false);
                        }}
                      >
                        Отменить ручные правки
                      </Button>
                    </>
                  )}
                </div>
                {editing ? (
                  <label className="mt-4 block text-sm">
                    Содержание документа
                    <textarea
                      aria-label="Содержание документа"
                      className={`${selectClass} mt-2 min-h-[28rem] font-mono leading-6`}
                      value={editor}
                      onChange={(event) => setEditor(event.target.value)}
                      disabled={busy}
                    />
                  </label>
                ) : (
                  <>
                    {preview && (
                      <p className="mt-4 text-sm text-accent-primary">
                        Показан проект изменений. Текст документа изменится
                        после подтверждения во вкладке «Изменения».
                      </p>
                    )}
                    {preview?.origin === 'manual' ? (
                      <>
                        {preview.candidate_sections?.map((section) => {
                          const before = active.sections.find(
                            (item) => item.id === section.id,
                          );
                          return before ? (
                            <ProposalText
                              key={section.id}
                              section={before}
                              change={preview}
                            />
                          ) : (
                            <div
                              key={section.id}
                              className="my-4 border-s-2 border-accent-primary ps-3"
                            >
                              <p className="text-sm font-medium">
                                Новый раздел
                              </p>
                              <SourceArticleContent
                                text={section.text}
                                label="Новый раздел"
                              />
                            </div>
                          );
                        })}
                        {active.sections
                          .filter(
                            (section) =>
                              !preview.candidate_sections?.some(
                                (item) => item.id === section.id,
                              ),
                          )
                          .map((section) => (
                            <ProposalText
                              key={section.id}
                              section={section}
                              change={preview}
                            />
                          ))}
                      </>
                    ) : (
                      <>
                        <AddedSections afterId={null} change={preview} />
                        {active.sections.map((section) => (
                          <div key={section.id}>
                            <ProposalText section={section} change={preview} />
                            <AddedSections
                              afterId={section.id}
                              change={preview}
                            />
                          </div>
                        ))}
                      </>
                    )}
                  </>
                )}
              </>
            )}
            {tab === 'sources' && (
              <DocumentDependencies
                key={active.id}
                workspace={workspace}
                active={active}
                documents={documents}
                busy={busy}
                dirty={dirty}
                perform={perform}
                open={open}
              />
            )}
            <div hidden={tab !== 'changes'}>
              <DocumentChanges
                key={active.id}
                workspaceId={workspace.id}
                document={active}
                proposal={proposal}
                busy={busy}
                dirty={dirty}
                pending={pending}
                locked={locked}
                run={perform}
                onSelectChange={setSelectedChange}
                onPreview={(id) => {
                  setSelectedChange(id);
                  setTab('text');
                }}
              />
            </div>
            {tab === 'history' && (
              <div className="space-y-4">
                <ul className="flex flex-wrap gap-2">
                  {active.revisions.map((revision) => (
                    <li key={revision.id}>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() =>
                          void perform(async () => {
                            setHistory(
                              await getDocumentRevision(
                                workspace.id,
                                active.id,
                                revision.id,
                              ),
                            );
                          })
                        }
                      >
                        Редакция {revision.number} ·{' '}
                        {new Date(revision.created_at).toLocaleString()}
                      </Button>
                    </li>
                  ))}
                </ul>
                {history && (
                  <>
                    <h3 className="font-medium">
                      Редакция {history.number}: сравнение с текущим текстом
                    </h3>
                    {history.sections.map((section) => {
                      const current = active.sections.find(
                        (item) => item.id === section.id,
                      );
                      return current?.text === section.text ? (
                        <details key={section.id}>
                          <summary className="cursor-pointer text-sm text-text-secondary">
                            Без изменений: {sectionTitle(section)}
                          </summary>
                          <SourceArticleContent
                            text={section.text}
                            label="Неизменённый раздел"
                          />
                        </details>
                      ) : (
                        <div
                          key={section.id}
                          className="grid gap-3 md:grid-cols-2"
                        >
                          <SourceArticleContent
                            text={section.text}
                            label="Прежняя редакция"
                          />
                          {current ? (
                            <SourceArticleContent
                              text={current.text}
                              label="Текущая редакция"
                            />
                          ) : (
                            <p className="pt-4 text-sm">Раздел удалён</p>
                          )}
                        </div>
                      );
                    })}
                    {active.sections
                      .filter(
                        (section) =>
                          !history.sections.some(
                            (item) => item.id === section.id,
                          ),
                      )
                      .map((section) => (
                        <div key={section.id}>
                          <p className="text-sm">Добавленный раздел</p>
                          <SourceArticleContent
                            text={section.text}
                            label="Добавленный раздел"
                          />
                        </div>
                      ))}
                  </>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="flex min-h-64 items-center justify-center text-text-secondary">
            <p>Откройте документ или создайте новый.</p>
          </div>
        )}
      </div>
    </section>
  );
}
