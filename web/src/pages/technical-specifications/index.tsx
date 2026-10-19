import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { DocumentMarkdown } from '@/pages/business-documents/components/document-markdown';
import {
  createSourceWorkspace,
  getSourceWorkspace,
  listSourceWorkspaces,
  sourceRequestError,
} from '@/services/source-workbench-service';
import {
  createSpecification,
  getSpecification,
  getSpecificationDraft,
  importEvaSpecification,
  listSpecifications,
  saveSpecification,
  saveSpecificationDraft,
  specificationAction,
  specificationHistory,
  specificationRevision,
} from '@/services/technical-specification-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { useBlocker, useSearchParams } from 'react-router';
import {
  clearCommentDraft,
  editCommentDraft,
  openCommentDraft,
  readCommentDrafts,
  writeCommentDrafts,
  type CommentDrafts,
} from './comment-drafts';
import {
  documentDirty,
  readDocumentDraft,
  reconcileDocument,
  writeDocumentDraft,
  type DocumentDraft,
} from './document-draft';
import { draftConflicts, sameDraft } from './draft-sync';
import { EvaImport } from './eva-import';
import {
  contextLabel,
  draftKey,
  headings,
  messageId,
  readDraft,
  selectionContext,
  staleContext,
  wholeContext,
  writeDraft,
  type Context,
  type Draft,
  type Revision,
  type SavedDraft,
  type Specification,
} from './model';
import { EvaChoices, ProposalPreview } from './proposal-preview';
import { RevisionPreview } from './revision-preview';

const selectClass =
  'max-w-full rounded-md border border-border-button bg-bg-base px-3 py-2 text-sm';

export const SpecificationKeys = {
  projects: (actor: string) => ['spec-projects', actor] as const,
  project: (actor: string, workspace: string) =>
    ['spec-project', actor, workspace] as const,
  list: (actor: string, workspace: string) =>
    ['spec-list', actor, workspace] as const,
  document: (actor: string, workspace: string, id: string) =>
    ['spec', actor, workspace, id] as const,
};

export default function TechnicalSpecifications() {
  const { data: user } = useFetchUserInfo();
  const [params, setParams] = useSearchParams();
  const workspace = params.get('workspace') ?? '';
  const documentId = params.get('document') ?? '';
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  function navigate(next: Record<string, string>) {
    setParams(next);
  }
  const client = useQueryClient();
  const projects = useQuery({
    queryKey: SpecificationKeys.projects(user.id),
    queryFn: listSourceWorkspaces,
    enabled: Boolean(user.id),
  });
  const project = useQuery({
    queryKey: SpecificationKeys.project(user.id, workspace),
    queryFn: () => getSourceWorkspace(workspace),
    enabled: Boolean(user.id && workspace),
  });
  const documents = useQuery({
    queryKey: SpecificationKeys.list(user.id, workspace),
    queryFn: () => listSpecifications(workspace),
    enabled: Boolean(user.id && workspace),
  });
  const current = useQuery({
    queryKey: SpecificationKeys.document(user.id, workspace, documentId),
    queryFn: () => getSpecification(workspace, documentId),
    enabled: Boolean(user.id && workspace && documentId),
  });
  const canEdit =
    project.data?.role === 'owner' || project.data?.role === 'editor';

  async function create(projectOnly: boolean) {
    setBusy(true);
    setError('');
    try {
      if (projectOnly) {
        const value = await createSourceWorkspace({
          title: name,
          dataset_ids: [],
        });
        await client.invalidateQueries({
          queryKey: SpecificationKeys.projects(user.id),
        });
        navigate({ workspace: value.id });
      } else {
        const value = await createSpecification(workspace, name);
        await client.invalidateQueries({
          queryKey: SpecificationKeys.list(user.id, workspace),
        });
        navigate({ workspace, document: value.id });
      }
      setName('');
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось создать'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex h-full min-h-0 min-w-0 flex-1 flex-col gap-5 overflow-auto p-5 text-text-primary">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border-button pb-4">
        <button
          type="button"
          className="text-xl font-medium"
          onClick={() => navigate(workspace ? { workspace } : {})}
        >
          Технические задания
        </button>
        <label className="flex flex-wrap items-center gap-2 text-sm">
          Проект
          <select
            className={selectClass}
            aria-label="Проект"
            value={workspace}
            onChange={(event) =>
              navigate(
                event.target.value ? { workspace: event.target.value } : {},
              )
            }
          >
            <option value="">Выберите проект</option>
            {projects.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
      </header>
      {(error ||
        projects.error ||
        project.error ||
        documents.error ||
        current.error) && (
        <p role="alert" className="text-state-error">
          {error ||
            sourceRequestError(
              projects.error ||
                project.error ||
                documents.error ||
                current.error,
              'Не удалось загрузить ТЗ',
            )}
        </p>
      )}
      {!documentId && (
        <section className="mx-auto w-full max-w-4xl space-y-5">
          <h1 className="text-lg font-medium">
            {workspace ? project.data?.title : 'Выберите или создайте проект'}
          </h1>
          <form
            className="flex flex-wrap gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void create(!workspace);
            }}
          >
            <Input
              aria-label={workspace ? 'Название ТЗ' : 'Название проекта'}
              placeholder={workspace ? 'Название ТЗ' : 'Название проекта'}
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="min-w-48 flex-1"
              maxLength={255}
            />
            <Button
              disabled={busy || !name.trim() || Boolean(workspace && !canEdit)}
            >
              {workspace ? 'Создать ТЗ' : 'Создать проект'}
            </Button>
          </form>
          {workspace && canEdit && (
            <EvaImport
              key={workspace}
              workspace={workspace}
              busy={busy}
              onImport={async (page, copy) => {
                setBusy(true);
                setError('');
                try {
                  const value = await importEvaSpecification(
                    workspace,
                    page.web_url,
                    copy,
                    page,
                  );
                  await client.invalidateQueries({
                    queryKey: SpecificationKeys.list(user.id, workspace),
                  });
                  navigate({
                    workspace: value.workspace_id,
                    document: value.id,
                  });
                } catch (cause) {
                  setError(
                    sourceRequestError(
                      cause,
                      'Не удалось импортировать из Eva',
                    ),
                  );
                } finally {
                  setBusy(false);
                }
              }}
            />
          )}
          {documents.isFetching && <p role="status">Загрузка…</p>}
          {documents.data?.map((item) => (
            <button
              type="button"
              key={item.id}
              className="flex w-full items-center justify-between gap-4 border-b border-border-button py-4 text-left hover:text-accent-primary"
              onClick={() => navigate({ workspace, document: item.id })}
            >
              <span>{item.title}</span>
              <span className="text-sm text-text-secondary">
                Редакция {item.revision}
              </span>
            </button>
          ))}
          {workspace && documents.data?.length === 0 && (
            <p className="text-text-secondary">В проекте пока нет ТЗ.</p>
          )}
        </section>
      )}
      {documentId && current.isFetching && !current.data && (
        <p role="status">Загрузка ТЗ…</p>
      )}
      {current.data && documentId && user.id && (
        <Editor
          key={`${user.id}:${workspace}:${documentId}`}
          initial={current.data}
          actor={user.id}
          canEdit={canEdit}
          onSaved={(value) => {
            const previous = client.getQueryData<Specification>(
              SpecificationKeys.document(user.id, workspace, documentId),
            );
            if (previous && previous.version >= value.version) return;
            client.setQueryData<Specification>(
              SpecificationKeys.document(user.id, workspace, documentId),
              (old) => (!old || value.version >= old.version ? value : old),
            );
            void client.invalidateQueries({
              queryKey: SpecificationKeys.list(user.id, workspace),
            });
          }}
        />
      )}
    </main>
  );
}

function Editor({
  initial,
  actor,
  canEdit,
  onSaved,
}: {
  initial: Specification;
  actor: string;
  canEdit: boolean;
  onSaved: (value: Specification) => void;
}) {
  const [doc, setDoc] = useState(initial);
  const projectionSaved = useRef(onSaved);
  projectionSaved.current = onSaved;
  const manualKey =
    draftKey(actor, initial.workspace_id, initial.id) + ':document';
  const [editing, setEditing] = useState(() =>
    readDocumentDraft(manualKey, initial),
  );
  const { title, sections: edited } = editing;
  const [documentSaveError, setDocumentSaveError] = useState('');
  const liveEditing = useRef(editing);
  const manualSaved = useRef(true);
  const [mode, setMode] = useState<'document' | 'chat'>('document');
  const [panel, setPanel] = useState<'comments' | 'history' | 'eva' | null>(
    null,
  );
  const panelRef = useRef<HTMLElement | null>(null);
  const openedPanel = useRef<typeof panel>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [draftStatus, setDraftStatus] = useState('');
  const [draftConflict, setDraftConflict] = useState<SavedDraft | null>(null);
  const [draftSyncFailed, setDraftSyncFailed] = useState(false);
  const [draftSyncAttempt, setDraftSyncAttempt] = useState(0);
  const syncPaused = useRef(false);
  const key = draftKey(actor, initial.workspace_id, initial.id);
  const mounted = useRef(true);
  const [draftBackup, setDraftBackup] = useState(() =>
    readDraft(key + ':previous'),
  );
  const initialDraft = useRef(readDraft(key));
  const [chatDraft, setChatDraft] = useState<Draft>(
    initialDraft.current?.draft ?? {
      message_id: messageId(),
      text: '',
      context: wholeContext(initial),
    },
  );
  const liveDraft = useRef(chatDraft);
  const localChat = useRef(initialDraft.current?.draft ?? null);
  const serverChat = useRef<Draft | null>(null);
  const draftVersion = useRef(initialDraft.current?.version ?? 0);
  const saveQueue = useRef(Promise.resolve());
  const [draftLoaded, setDraftLoaded] = useState(false);
  const touched = useRef(false);
  const [candidateContext, setCandidateContext] = useState<Context | null>(
    null,
  );
  const [commentDrafts, setCommentDrafts] = useState(() =>
    readCommentDrafts(key + ':comments', initial),
  );
  const liveComments = useRef(commentDrafts);
  const savedComments = useRef(commentDrafts);
  const activeComment = commentDrafts.entries[commentDrafts.active];
  const {
    text: comment,
    context: commentContext,
    reply_to: replyTo,
  } = activeComment;
  const [commentSaveError, setCommentSaveError] = useState('');
  const [showResolved, setShowResolved] = useState(false);
  const replyParent = doc.comments.find((item) => item.id === replyTo);
  const [history, setHistory] = useState<Revision[]>([]);
  const [oldRevision, setOldRevision] = useState<Revision | null>(null);
  useLayoutEffect(() => {
    const changed = openedPanel.current !== panel;
    openedPanel.current = panel;
    if (panel && window.innerWidth < 1280) {
      if (changed) panelRef.current?.focus({ preventScroll: true });
      if (changed || panel === 'history')
        panelRef.current?.scrollIntoView?.({ block: 'start' });
    }
  }, [panel, history.length, oldRevision?.number]);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const revisionRequest = useRef(0);
  useEffect(
    () => () => {
      revisionRequest.current++;
    },
    [],
  );
  const dirty =
    documentDirty(editing) ||
    editing.conflicts.length > 0 ||
    editing.base.revision !== doc.revision;
  const stale = staleContext(chatDraft.context, doc.sections);
  const ready = doc.proposals.filter(
    (item) => item.status !== 'applied' && item.base_revision === doc.revision,
  );

  const persistEditing = useCallback(
    (next: DocumentDraft) => {
      liveEditing.current = next;
      setEditing(next);
      manualSaved.current = writeDocumentDraft(manualKey, next);
      setDocumentSaveError(
        manualSaved.current
          ? ''
          : 'Не удалось сохранить ручные правки на устройстве. Сохраните ТЗ перед переходом',
      );
    },
    [manualKey],
  );

  useEffect(() => {
    setDoc((current) =>
      initial.version > current.version ? initial : current,
    );
  }, [initial]);

  useEffect(() => {
    const next = reconcileDocument(liveEditing.current, doc);
    if (next !== liveEditing.current) persistEditing(next);
  }, [doc, persistEditing]);

  const canLeave = useCallback(() => {
    let manualSafe = manualSaved.current || !documentDirty(liveEditing.current);
    if (!manualSafe) {
      manualSafe = writeDocumentDraft(manualKey, liveEditing.current);
      manualSaved.current = manualSafe;
      setDocumentSaveError(
        manualSafe
          ? ''
          : 'Сохраните ТЗ перед переходом: копия ручных правок на устройстве недоступна',
      );
    }
    let chatSafe =
      !touched.current ||
      (localChat.current !== null &&
        sameDraft(localChat.current, liveDraft.current)) ||
      (serverChat.current !== null &&
        sameDraft(serverChat.current, liveDraft.current));
    if (!chatSafe) {
      chatSafe = writeDraft(key, {
        version: draftVersion.current,
        draft: liveDraft.current,
      });
      if (chatSafe) localChat.current = liveDraft.current;
      setDraftStatus(
        chatSafe
          ? 'Черновик сохранён на этом устройстве'
          : 'Черновик чата не сохранён. Дождитесь сохранения на сервере перед переходом',
      );
    }
    let commentsSafe =
      !Object.values(liveComments.current.entries).some((entry) =>
        entry.text.trim(),
      ) ||
      JSON.stringify(savedComments.current) ===
        JSON.stringify(liveComments.current);
    if (!commentsSafe) {
      commentsSafe = writeCommentDrafts(
        key + ':comments',
        liveComments.current,
      );
      if (commentsSafe) savedComments.current = liveComments.current;
      setCommentSaveError(
        commentsSafe
          ? ''
          : 'Черновик комментария не сохранён. Отправьте комментарий или восстановите хранилище перед переходом',
      );
    }
    const safe = manualSafe && chatSafe && commentsSafe;
    if (!safe)
      setError(
        'Переход остановлен: есть несохранённые правки или черновики. Дождитесь сохранения или восстановите хранилище устройства.',
      );
    return safe;
  }, [key, manualKey]);
  const blocker = useBlocker(useCallback(() => !canLeave(), [canLeave]));
  useEffect(() => {
    if (blocker.state === 'blocked') blocker.reset();
  }, [blocker]);

  useEffect(() => {
    function beforeUnload(event: BeforeUnloadEvent) {
      if (!canLeave()) {
        event.preventDefault();
        event.returnValue = '';
      }
    }
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      window.removeEventListener('beforeunload', beforeUnload);
    };
  }, [canLeave]);

  function changeDraft(next: Draft) {
    next = { ...next, message_id: messageId() };
    touched.current = true;
    liveDraft.current = next;
    setChatDraft(next);
    const saved = writeDraft(key, {
      version: draftVersion.current,
      draft: next,
    });
    if (saved) localChat.current = next;
    setDraftStatus(
      saved
        ? 'Черновик сохранён на этом устройстве'
        : 'Не удалось сохранить черновик на устройстве. Не закрывайте страницу',
    );
  }

  function changeComments(next: CommentDrafts) {
    liveComments.current = next;
    setCommentDrafts(next);
    const saved = writeCommentDrafts(key + ':comments', next);
    if (saved) savedComments.current = next;
    setCommentSaveError(
      saved ? '' : 'Черновик комментария не сохранён. Не закрывайте страницу',
    );
  }

  function beginComment(context: Context, replyTo: string | null) {
    changeComments(openCommentDraft(liveComments.current, context, replyTo));
  }

  function chooseDraft(useServer: boolean) {
    if (!draftConflict) return;
    const previous = useServer
      ? { version: draftVersion.current, draft: liveDraft.current }
      : draftConflict;
    if (!writeDraft(key + ':previous', previous)) {
      setDraftStatus(
        'Не удалось сохранить второй вариант на устройстве. Выбор не применён',
      );
      return;
    }
    setDraftBackup(previous);
    draftVersion.current = draftConflict.version;
    if (useServer) {
      liveDraft.current = draftConflict.draft;
      setChatDraft(draftConflict.draft);
    }
    const saved = writeDraft(key, {
      version: draftVersion.current,
      draft: liveDraft.current,
    });
    if (saved) localChat.current = liveDraft.current;
    setDraftStatus(
      saved
        ? 'Выбранный черновик сохранён на устройстве'
        : 'Не удалось сохранить на устройстве. Не закрывайте страницу',
    );
    syncPaused.current = false;
    setDraftConflict(null);
    setDraftLoaded(true);
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    void getSpecificationDraft(initial.workspace_id, initial.id)
      .then((remote) => {
        if (!active) return;
        setDraftSyncFailed(false);
        syncPaused.current = false;
        serverChat.current = remote?.draft ?? null;
        if (remote && !initialDraft.current && !touched.current) {
          draftVersion.current = remote.version;
          liveDraft.current = remote.draft;
          setChatDraft(remote.draft);
          if (writeDraft(key, remote)) localChat.current = remote.draft;
          setDraftStatus('Черновик восстановлен');
        } else if (remote && sameDraft(remote.draft, liveDraft.current)) {
          draftVersion.current = remote.version;
        } else if (
          remote &&
          draftConflicts(
            { version: draftVersion.current, draft: liveDraft.current },
            remote,
          )
        ) {
          syncPaused.current = true;
          setDraftConflict(remote);
        } else if (!remote) {
          draftVersion.current = 0;
        }
        setDraftLoaded(true);
      })
      .catch((cause) => {
        if (active) {
          setDraftSyncFailed(true);
          const localSaved = writeDraft(key, {
            version: draftVersion.current,
            draft: liveDraft.current,
          });
          setDraftStatus(
            `${sourceRequestError(cause, 'Серверный черновик недоступен')}. ${localSaved ? 'Копия сохранена на устройстве.' : 'Копия на устройстве не сохранена. Не закрывайте страницу.'}`,
          );
        }
      });
    return () => {
      active = false;
    };
  }, [initial.workspace_id, initial.id, key, draftSyncAttempt]);

  useEffect(() => {
    if (!draftLoaded || draftConflict) return;
    let active = true;
    const timer = setTimeout(() => {
      const snapshot = chatDraft;
      saveQueue.current = saveQueue.current.then(async () => {
        if (syncPaused.current) return;
        try {
          const result = await saveSpecificationDraft(
            initial.workspace_id,
            initial.id,
            { version: draftVersion.current, draft: snapshot },
          );
          serverChat.current = snapshot;
          draftVersion.current = result.version;
          const localSaved = writeDraft(key, {
            version: result.version,
            draft: liveDraft.current,
          });
          if (localSaved) localChat.current = liveDraft.current;
          if (active) setDraftStatus('Черновик сохранён');
        } catch (cause) {
          // Do not blindly replay against an unknown newer server draft.
          syncPaused.current = true;
          const localSaved = writeDraft(key, {
            version: draftVersion.current,
            draft: liveDraft.current,
          });
          if (mounted.current) {
            setDraftLoaded(false);
            setDraftSyncFailed(true);
            setDraftStatus(
              `${sourceRequestError(cause, 'Не удалось сохранить на сервере')}. ${localSaved ? 'Копия сохранена на устройстве.' : 'Копия на устройстве не сохранена. Не закрывайте страницу.'}`,
            );
          }
        }
      });
    }, 600);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [
    chatDraft,
    draftLoaded,
    draftConflict,
    initial.workspace_id,
    initial.id,
    key,
  ]);

  async function perform(operation: () => Promise<Specification>) {
    setBusy(true);
    setError('');
    setStatus('');
    try {
      const result = await operation();
      setDoc((current) =>
        result.version >= current.version ? result : current,
      );
      setStatus('Сохранено');
      onSaved(result);
      return result;
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось выполнить действие'));
      try {
        const latest = await getSpecification(doc.workspace_id, doc.id);
        setDoc((current) =>
          latest.version >= current.version ? latest : current,
        );
        onSaved(latest);
      } catch {
        /* Retain the current document and every local draft. */
      }
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function action(name: string, data: object) {
    const result = await perform(() =>
      specificationAction(doc.workspace_id, doc.id, name, {
        version: doc.version,
        ...data,
      }),
    );
    if (name === 'apply' && result) {
      const requested = (data as { proposal_id?: string }).proposal_id;
      setStatus(
        result.proposals.find((item) => item.id === requested)?.status ===
          'needs_resolution'
          ? 'Нужно устранить противоречия перед применением'
          : 'Изменения применены',
      );
    }
    return result;
  }

  const pendingChat = doc.messages.some((item) => item.status === 'processing');
  useEffect(() => {
    if (!busy && !pendingChat) return;
    let active = true;
    const timer = setInterval(() => {
      void getSpecification(doc.workspace_id, doc.id)
        .then((value) => {
          if (!active) return;
          setDoc((current) =>
            value.version > current.version ? value : current,
          );
          projectionSaved.current(value);
        })
        .catch(() => {
          /* The accepted message stays visible during connection loss. */
        });
    }, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [busy, pendingChat, doc.workspace_id, doc.id]);

  async function send() {
    const submitted = chatDraft;
    const result = await action('chat', { draft: submitted });
    if (
      result &&
      JSON.stringify(liveDraft.current) === JSON.stringify(submitted)
    )
      changeDraft({ ...submitted, text: '' });
  }

  async function showHistory() {
    setPanel(panel === 'history' ? null : 'history');
    setError('');
    try {
      setHistory(await specificationHistory(doc.workspace_id, doc.id));
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось загрузить историю'));
    }
  }

  async function openRevision(number: number) {
    const attempt = ++revisionRequest.current;
    setRevisionLoading(true);
    setOldRevision(null);
    setError('');
    try {
      const value = await specificationRevision(
        doc.workspace_id,
        doc.id,
        number,
      );
      if (revisionRequest.current === attempt) setOldRevision(value);
    } catch (cause) {
      if (revisionRequest.current === attempt)
        setError(sourceRequestError(cause, 'Не удалось открыть редакцию'));
    } finally {
      if (revisionRequest.current === attempt) setRevisionLoading(false);
    }
  }

  return (
    <section className="min-w-0 space-y-4" aria-label="Рабочее пространство ТЗ">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="break-words text-xl font-medium">{doc.title}</h1>
          <p className="text-sm text-text-secondary">Редакция {doc.revision}</p>
        </div>
        <nav className="flex flex-wrap gap-2" aria-label="Режим ТЗ">
          <Button
            variant={mode === 'document' ? 'default' : 'outline'}
            onClick={() => setMode('document')}
          >
            Документ
          </Button>
          <Button
            variant={mode === 'chat' ? 'default' : 'outline'}
            onClick={() => setMode('chat')}
          >
            Чат
          </Button>
          <Button
            variant="outline"
            onClick={() => setPanel(panel === 'comments' ? null : 'comments')}
          >
            Комментарии · {doc.comments.filter((item) => !item.resolved).length}
          </Button>
          <Button variant="outline" onClick={() => void showHistory()}>
            История
          </Button>
          <Button
            variant="outline"
            onClick={() => setPanel(panel === 'eva' ? null : 'eva')}
          >
            Eva
          </Button>
        </nav>
      </header>
      {error && (
        <p role="alert" className="whitespace-pre-wrap text-state-error">
          {error}
        </p>
      )}
      {status && (
        <p role="status" className="text-sm text-text-secondary">
          {status}
        </p>
      )}
      {documentSaveError && (
        <p role="alert" className="text-state-error">
          {documentSaveError}
        </p>
      )}
      {editing.base.revision > doc.revision && (
        <p role="status">
          Загружается актуальная редакция. Ваши ручные правки сохранены.
        </p>
      )}
      {editing.conflicts.length > 0 && (
        <section
          aria-label="Конфликт ручных правок"
          className="space-y-3 border-y border-border-button py-4"
        >
          <p>
            Документ изменился. Независимые правки объединены; выберите текст в
            спорных разделах.
          </p>
          {editing.conflicts.map((field) => (
            <details key={field} open>
              <summary>
                {field === 'title' ? 'Название ТЗ' : `Раздел ${field}`}
              </summary>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <p>Ваш текст</p>
                  <p className="whitespace-pre-wrap break-words">
                    {field === 'title' ? title : edited[field]}
                  </p>
                </div>
                <div>
                  <p>На сервере</p>
                  <p className="whitespace-pre-wrap break-words">
                    {field === 'title' ? doc.title : doc.sections[field]}
                  </p>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() =>
                    persistEditing({
                      ...editing,
                      conflicts: editing.conflicts.filter(
                        (item) => item !== field,
                      ),
                    })
                  }
                >
                  Оставить мой текст
                </Button>
                <Button
                  variant="outline"
                  onClick={() =>
                    persistEditing({
                      ...editing,
                      ...(field === 'title'
                        ? { title: doc.title }
                        : {
                            sections: {
                              ...edited,
                              [field]: doc.sections[field],
                            },
                          }),
                      conflicts: editing.conflicts.filter(
                        (item) => item !== field,
                      ),
                    })
                  }
                >
                  Взять серверный текст
                </Button>
              </div>
            </details>
          ))}
        </section>
      )}
      <div
        className={`grid min-w-0 gap-8 ${panel ? 'xl:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]' : ''}`}
      >
        <div className="min-w-0">
          {mode === 'document' ? (
            <div className="mx-auto max-w-4xl space-y-7">
              <label className="block space-y-2">
                Название ТЗ
                <Input
                  value={title}
                  aria-label="Название документа"
                  onChange={(event) =>
                    persistEditing({ ...editing, title: event.target.value })
                  }
                  disabled={!canEdit || busy}
                  maxLength={255}
                />
              </label>
              {headings.map((heading, index) => {
                const section = String(index + 1);
                return (
                  <section
                    key={section}
                    className="space-y-3 border-t border-border-button pt-5"
                  >
                    <header className="flex flex-wrap items-center justify-between gap-2">
                      <h2 className="font-medium">
                        {section}. {heading}
                      </h2>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            changeDraft({
                              ...chatDraft,
                              context:
                                candidateContext?.anchor?.section === section
                                  ? candidateContext
                                  : wholeContext(doc, section),
                            });
                            setMode('chat');
                          }}
                        >
                          В чат
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            beginComment(
                              candidateContext?.anchor?.section === section
                                ? candidateContext
                                : wholeContext(doc, section),
                              null,
                            );
                            setPanel('comments');
                          }}
                        >
                          Комментарий
                        </Button>
                      </div>
                    </header>
                    <Textarea
                      aria-label={`Раздел ${section}`}
                      value={edited[section]}
                      disabled={!canEdit || busy}
                      className="min-h-36 font-mono"
                      onChange={(event) =>
                        persistEditing({
                          ...editing,
                          sections: {
                            ...edited,
                            [section]: event.target.value,
                          },
                        })
                      }
                      onSelect={(event) => {
                        const target = event.currentTarget;
                        setCandidateContext(
                          selectionContext(
                            doc,
                            section,
                            target.value,
                            target.selectionStart,
                            target.selectionEnd,
                          ),
                        );
                      }}
                    />
                  </section>
                );
              })}
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  disabled={
                    !canEdit ||
                    !dirty ||
                    busy ||
                    editing.conflicts.length > 0 ||
                    editing.base.revision !== doc.revision
                  }
                  onClick={() =>
                    void perform(() =>
                      saveSpecification(doc.workspace_id, {
                        ...doc,
                        title,
                        sections: edited,
                      }),
                    )
                  }
                >
                  Сохранить ТЗ
                </Button>
                {dirty && (
                  <span className="text-sm text-text-secondary">
                    Есть несохранённые правки
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-4xl space-y-5">
              {doc.messages.length === 0 && (
                <p className="text-text-secondary">
                  Задайте вопрос или попросите подготовить правки ТЗ.
                </p>
              )}
              {doc.messages.map((item, index) => (
                <article
                  key={index}
                  className="space-y-2 border-b border-border-button pb-4"
                >
                  <p className="text-sm font-medium">
                    {item.role === 'user'
                      ? item.author_id === actor
                        ? 'Вы'
                        : (doc.people[item.author_id ?? ''] ?? 'Участник')
                      : 'Помощник'}
                    {item.context && ` · ${contextLabel(item.context)}`}
                  </p>
                  {item.context?.anchor && (
                    <blockquote className="border-l-2 border-border-button pl-3 text-sm text-text-secondary">
                      {item.context.anchor.quote}
                    </blockquote>
                  )}
                  <DocumentMarkdown>{item.text}</DocumentMarkdown>
                  {item.source_revision &&
                    item.source_revision !== doc.revision && (
                      <p className="text-sm text-text-secondary">
                        Относится к редакции {item.source_revision}. Предложения
                        к прежней редакции не применяются.
                      </p>
                    )}
                  {item.status === 'processing' && (
                    <p role="status" className="text-sm text-text-secondary">
                      Запрос сохранён · помощник готовит ответ
                    </p>
                  )}
                  {item.status === 'failed' && (
                    <p role="alert" className="text-sm text-state-error">
                      {item.error?.message ?? 'Не удалось получить ответ'}
                    </p>
                  )}
                  {item.author_id === actor &&
                    item.context &&
                    (item.status === 'failed' ||
                      item.status === 'processing') && (
                      <Button
                        variant="outline"
                        disabled={busy || dirty || !canEdit}
                        onClick={() =>
                          void action('chat', {
                            draft: {
                              message_id: item.id,
                              text: item.text,
                              context: item.context,
                            },
                          })
                        }
                      >
                        {item.status === 'failed'
                          ? 'Повторить сообщение'
                          : 'Проверить запрос'}
                      </Button>
                    )}
                </article>
              ))}
              {ready.map((proposal) => (
                <section
                  key={proposal.id}
                  className="space-y-4 border-y border-border-button py-5"
                  aria-label="Предложенные изменения"
                >
                  <h2 className="font-medium">
                    Правки к редакции {proposal.base_revision}
                  </h2>
                  {!!proposal.issues?.length && (
                    <ul
                      role="alert"
                      className="list-disc space-y-1 pl-5 text-state-error"
                    >
                      {proposal.issues.map((issue, index) => (
                        <li key={index}>{issue}</li>
                      ))}
                    </ul>
                  )}
                  {proposal.status === 'needs_resolution' ? (
                    <div className="space-y-4">
                      <p>
                        {proposal.source === 'eva' ? (
                          <>
                            Изменены одни и те же требования. Выберите значения
                            для связанных разделов; независимые правки обеих
                            сторон сохраняются.
                          </>
                        ) : (
                          <>
                            Опишите исправление в чате. Помощнику доступны
                            предложение и найденные противоречия.
                          </>
                        )}
                      </p>
                      {!proposal.conflicts?.length && (
                        <ProposalPreview
                          local={doc.sections}
                          candidate={proposal.sections}
                          includeUnchanged
                        />
                      )}
                      {proposal.conflicts?.map((conflict) => (
                        <details key={conflict.section}>
                          <summary>Раздел {conflict.section}</summary>
                          <div className="grid gap-4 md:grid-cols-2">
                            <div>
                              <p>Ваш текст</p>
                              <DocumentMarkdown>
                                {conflict.local}
                              </DocumentMarkdown>
                            </div>
                            <div>
                              <p>Из Eva</p>
                              <DocumentMarkdown>
                                {conflict.remote}
                              </DocumentMarkdown>
                            </div>
                          </div>
                        </details>
                      ))}
                      {proposal.source === 'eva' ? (
                        <EvaChoices
                          disabled={busy || dirty || !canEdit}
                          onChoose={(choice) =>
                            void action('resolve-eva', {
                              proposal_id: proposal.id,
                              choice,
                            })
                          }
                        />
                      ) : (
                        <Button
                          variant="outline"
                          onClick={() =>
                            changeDraft({
                              ...chatDraft,
                              context: wholeContext(doc),
                            })
                          }
                        >
                          Обсудить всё ТЗ
                        </Button>
                      )}
                    </div>
                  ) : (
                    <>
                      <ProposalPreview
                        local={doc.sections}
                        candidate={proposal.sections}
                      />
                      <Button
                        disabled={!canEdit || busy || dirty}
                        onClick={() =>
                          void action('apply', { proposal_id: proposal.id })
                        }
                      >
                        Применить изменения
                      </Button>
                      {proposal.source === 'eva' && (
                        <EvaChoices
                          disabled={busy || dirty || !canEdit}
                          onChoose={(choice) =>
                            void action('resolve-eva', {
                              proposal_id: proposal.id,
                              choice,
                            })
                          }
                        />
                      )}
                    </>
                  )}
                </section>
              ))}
              <label className="flex flex-wrap items-center gap-2 text-sm">
                Контекст
                <select
                  aria-label="Контекст чата"
                  value={chatDraft.context.scope}
                  className={selectClass}
                  onChange={(event) =>
                    changeDraft({
                      ...chatDraft,
                      context: wholeContext(doc, event.target.value),
                    })
                  }
                >
                  <option value="all">Всё ТЗ</option>
                  {headings.map((_, index) => (
                    <option key={index} value={String(index + 1)}>
                      Раздел {index + 1}
                    </option>
                  ))}
                  {chatDraft.context.scope === 'selection' && (
                    <option value="selection">Выделенный фрагмент</option>
                  )}
                </select>
              </label>
              {chatDraft.context.anchor && (
                <blockquote className="whitespace-pre-wrap border-l-2 border-border-button pl-3 text-sm">
                  {chatDraft.context.anchor.quote}
                  <p className="mt-2 text-text-secondary">
                    Исходная редакция {chatDraft.context.revision}
                  </p>
                </blockquote>
              )}
              {stale && (
                <div role="alert" className="space-y-2 text-sm">
                  <p>
                    Исходный фрагмент изменён или удалён. Выберите контекст
                    явно.
                  </p>
                  <Button variant="outline" onClick={() => setMode('document')}>
                    Открыть документ
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() =>
                      changeDraft({
                        ...chatDraft,
                        context: wholeContext(
                          doc,
                          chatDraft.context.anchor?.section ?? 'all',
                        ),
                      })
                    }
                  >
                    Использовать весь раздел
                  </Button>
                </div>
              )}
              {draftConflict && (
                <div
                  role="alert"
                  className="space-y-3 rounded-lg border border-border-button p-3 text-sm"
                >
                  <p>
                    На сервере другой черновик. Выберите вариант для
                    продолжения.
                  </p>
                  <details>
                    <summary className="cursor-pointer">
                      Серверный: {contextLabel(draftConflict.draft.context)},
                      редакция {draftConflict.draft.context.revision}
                    </summary>
                    {draftConflict.draft.context.anchor && (
                      <blockquote className="my-2 whitespace-pre-wrap border-l-2 pl-3">
                        {draftConflict.draft.context.anchor.quote}
                      </blockquote>
                    )}
                    <p className="mt-2 whitespace-pre-wrap">
                      {draftConflict.draft.text || 'Пустой черновик'}
                    </p>
                  </details>
                  <p className="text-text-secondary">
                    Второй вариант сохранится на этом устройстве.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      onClick={() => chooseDraft(false)}
                    >
                      Продолжить с моим
                    </Button>
                    <Button variant="outline" onClick={() => chooseDraft(true)}>
                      Продолжить с серверным
                    </Button>
                  </div>
                </div>
              )}
              <Textarea
                aria-label="Сообщение помощнику"
                value={chatDraft.text}
                onChange={(event) =>
                  changeDraft({ ...chatDraft, text: event.target.value })
                }
                maxLength={10000}
                className="min-h-28"
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p role="status" className="text-sm text-text-secondary">
                  {draftStatus}
                </p>
                <Button
                  disabled={
                    !canEdit ||
                    busy ||
                    stale ||
                    dirty ||
                    !!draftConflict ||
                    !chatDraft.text.trim()
                  }
                  onClick={() => void send()}
                >
                  {busy ? 'Обработка…' : 'Отправить'}
                </Button>
              </div>
              {draftSyncFailed && (
                <Button
                  variant="outline"
                  onClick={() => setDraftSyncAttempt((value) => value + 1)}
                >
                  Повторить синхронизацию черновика
                </Button>
              )}
              {draftBackup && (
                <details className="text-sm">
                  <summary className="cursor-pointer">
                    Предыдущий вариант черновика
                  </summary>
                  <p className="my-2 text-text-secondary">
                    {contextLabel(draftBackup.draft.context)}, редакция{' '}
                    {draftBackup.draft.context.revision}
                  </p>
                  {draftBackup.draft.context.anchor && (
                    <blockquote className="my-2 whitespace-pre-wrap border-l-2 pl-3">
                      {draftBackup.draft.context.anchor.quote}
                    </blockquote>
                  )}
                  <p className="mb-2 whitespace-pre-wrap">
                    {draftBackup.draft.text || 'Пустой черновик'}
                  </p>
                  <Button
                    variant="outline"
                    disabled={!!draftConflict}
                    onClick={() => changeDraft(draftBackup.draft)}
                  >
                    Использовать этот вариант
                  </Button>
                </details>
              )}
              {dirty && (
                <p className="text-sm text-text-secondary">
                  Сохраните правки документа перед отправкой или применением.
                </p>
              )}
            </div>
          )}
        </div>
        {panel === 'comments' && (
          <aside
            className="min-w-0 space-y-4 border-t border-border-button pt-4 xl:border-l xl:border-t-0 xl:pl-5"
            aria-label="Комментарии"
            ref={panelRef}
            tabIndex={-1}
          >
            <h2 className="font-medium">Комментарии</h2>
            <label className="block space-y-2 text-sm">
              Комментарии к
              <select
                className={selectClass}
                aria-label="Контекст комментариев"
                value={commentContext.scope}
                onChange={(event) =>
                  beginComment(wholeContext(doc, event.target.value), null)
                }
              >
                <option value="all">Всё ТЗ</option>
                {headings.map((_, index) => (
                  <option key={index} value={String(index + 1)}>
                    Раздел {index + 1}
                  </option>
                ))}
                {commentContext.scope === 'selection' && (
                  <option value="selection">Выделенный фрагмент</option>
                )}
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={showResolved}
                onChange={(event) => setShowResolved(event.target.checked)}
              />
              Показывать решённые
            </label>
            {doc.comments
              .filter(
                (item) =>
                  (showResolved || !item.resolved) &&
                  (commentContext.scope === 'all' ||
                    (item.context.anchor?.section ?? item.context.scope) ===
                      (commentContext.anchor?.section ?? commentContext.scope)),
              )
              .map((item) => (
                <article
                  key={item.id}
                  className="space-y-3 border-b border-border-button pb-4"
                >
                  <p className="text-sm font-medium">
                    {item.author_id === actor
                      ? 'Вы'
                      : (doc.people?.[item.author_id] ?? 'Удалённый участник')}
                  </p>
                  <p className="text-sm text-text-secondary">
                    {contextLabel(item.context)} ·{' '}
                    {item.resolved ? 'Решён' : 'Открыт'}
                  </p>
                  {item.context.anchor && (
                    <blockquote className="border-l-2 border-border-button pl-2 text-sm">
                      {item.context.anchor.quote}
                    </blockquote>
                  )}
                  <p className="whitespace-pre-wrap break-words">{item.text}</p>
                  {item.replies.map((reply) => (
                    <p
                      className="ml-3 whitespace-pre-wrap break-words border-l border-border-button pl-3"
                      key={reply.id}
                    >
                      <span className="block text-xs text-text-secondary">
                        {reply.author_id === actor
                          ? 'Вы'
                          : (doc.people[reply.author_id] ?? 'Участник')}
                      </span>
                      {reply.text}
                    </p>
                  ))}
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!canEdit || busy}
                      onClick={() => {
                        beginComment(item.context, item.id);
                      }}
                    >
                      Ответить
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!canEdit || busy || dirty}
                      onClick={() =>
                        void action('comments', {
                          action: 'resolve',
                          comment_id: item.id,
                          resolved: !item.resolved,
                        })
                      }
                    >
                      {item.resolved ? 'Открыть снова' : 'Решён'}
                    </Button>
                  </div>
                </article>
              ))}
            <p className="text-sm">
              {replyTo ? 'Ответ на комментарий' : 'Новый комментарий'} ·{' '}
              {contextLabel(commentContext)}
            </p>
            {replyTo && (
              <Button
                variant="ghost"
                onClick={() => beginComment(wholeContext(doc), null)}
              >
                Отменить ответ
              </Button>
            )}
            {replyParent && (
              <blockquote className="space-y-2 border-l-2 border-border-button pl-3 text-sm">
                <p>
                  {replyParent.author_id === actor
                    ? 'Вы'
                    : (doc.people?.[replyParent.author_id] ??
                      'Удалённый участник')}{' '}
                  · {contextLabel(replyParent.context)}
                </p>
                <p className="whitespace-pre-wrap break-words">
                  {replyParent.text}
                </p>
              </blockquote>
            )}
            {commentSaveError && (
              <p role="alert" className="text-sm text-state-error">
                {commentSaveError}
              </p>
            )}
            <Textarea
              aria-label="Текст комментария"
              value={comment}
              onChange={(event) =>
                changeComments(
                  editCommentDraft(liveComments.current, event.target.value),
                )
              }
              maxLength={10000}
            />
            <Button
              disabled={!canEdit || busy || dirty || !comment.trim()}
              onClick={async () => {
                const submitted = liveComments.current;
                const draft = submitted.entries[submitted.active];
                const result = await action(
                  'comments',
                  replyTo
                    ? {
                        action: 'reply',
                        comment_id: replyTo,
                        text: draft.text,
                        message_id: draft.message_id,
                      }
                    : {
                        text: draft.text,
                        context: draft.context,
                        message_id: draft.message_id,
                      },
                );
                if (result) {
                  changeComments(
                    clearCommentDraft(
                      liveComments.current,
                      submitted.active,
                      draft.message_id,
                    ),
                  );
                }
              }}
            >
              Добавить комментарий
            </Button>
          </aside>
        )}
        {panel === 'eva' && (
          <aside
            className="min-w-0 space-y-4 border-t border-border-button pt-4 xl:border-l xl:border-t-0 xl:pl-5"
            aria-label="Обмен с Eva"
            ref={panelRef}
            tabIndex={-1}
          >
            <h2 className="font-medium">Eva</h2>
            {doc.eva ? (
              <>
                <p className="break-words">
                  {doc.eva.document_name ?? doc.title}
                </p>
                <a
                  href={doc.eva.page_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="break-all underline"
                >
                  Открыть страницу Eva
                </a>
                <p className="text-sm text-text-secondary">
                  Получена версия {doc.eva.remote_version}
                </p>
                <Button
                  variant="outline"
                  disabled={busy || dirty || !canEdit}
                  onClick={async () => {
                    const result = await action('pull', {});
                    if (result) {
                      setMode('chat');
                      setStatus(
                        result.version === doc.version
                          ? 'Новых изменений в Eva нет'
                          : 'Изменения готовы к просмотру',
                      );
                    }
                  }}
                >
                  Получить изменения
                </Button>
              </>
            ) : (
              <p className="text-sm text-text-secondary">
                ТЗ не связано со страницей Eva.
              </p>
            )}
          </aside>
        )}
        {panel === 'history' && (
          <aside
            className="min-w-0 space-y-4 border-t border-border-button pt-4 xl:border-l xl:border-t-0 xl:pl-5"
            aria-label="История редакций"
            ref={panelRef}
            tabIndex={-1}
          >
            <h2 className="font-medium">История редакций</h2>
            {history.map((item) => (
              <Button
                key={item.number}
                variant="ghost"
                onClick={() => void openRevision(item.number)}
              >
                Редакция {item.number} ·{' '}
                {new Date(item.created_at).toLocaleString('ru')}
              </Button>
            ))}
            {revisionLoading && <p role="status">Открытие редакции…</p>}
            {oldRevision?.sections && (
              <section className="space-y-4">
                <h3>Редакция {oldRevision.number}</h3>
                <RevisionPreview current={doc} previous={oldRevision} />
                <Button
                  disabled={
                    !canEdit ||
                    busy ||
                    dirty ||
                    oldRevision.number === doc.revision
                  }
                  onClick={() =>
                    void action('restore', { revision: oldRevision.number })
                  }
                >
                  Восстановить как новую редакцию
                </Button>
              </section>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}
