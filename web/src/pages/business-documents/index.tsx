import { SelectWithSearch } from '@/components/originui/select-with-search';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Textarea } from '@/components/ui/textarea';
import { useSystemConfig } from '@/hooks/use-system-request';
import {
  useFetchTenantInfo,
  useFetchUserInfo,
} from '@/hooks/use-user-setting-request';
import { Routes } from '@/routes';
import {
  assignBusinessDocumentOwner,
  BusinessDocumentConflictError,
  checkBusinessDocumentEvaUpdate,
  createBusinessDocument,
  createEvaChangeFromBusinessDocument,
  deleteBusinessDocument,
  downloadBusinessDocumentExport,
  fetchBusinessDocument,
  fetchBusinessDocumentChangePreview,
  listBusinessDocumentAccessUsers,
  listBusinessDocumentCatalog,
  listBusinessDocumentRevisions,
  listBusinessDocuments,
  pullBusinessDocumentFromEva,
  rebindBusinessDocumentToEva,
  submitBusinessDocumentCommand,
  watchBusinessDocumentJobEvents,
} from '@/services/business-document-service';
import api from '@/utils/api';
import { downloadFileFromBlob } from '@/utils/file-util';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Archive,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpFromLine,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileClock,
  FilePlus2,
  LayoutTemplate,
  Link2,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import {
  CSSProperties,
  FormEvent,
  MouseEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { BackgroundWork } from './components/background-work';
import { ChangePreviewPanel } from './components/change-preview-panel';
import { BusinessDocumentError } from './components/document-error';
import { DocumentMarkdown } from './components/document-markdown';
import { DocumentPane } from './components/document-pane';
import {
  BusinessDocumentProgress,
  isBusinessDocumentOperationActive,
} from './components/document-progress';
import {
  EvaAccessNotice,
  isEvaAccessError,
  useEvaAccess,
} from './components/eva-access';
import { EvaChangeWorkbench } from './components/eva-change-workbench';
import { evidenceRefsChanged } from './components/evidence-refs-diff';
import { ProtocolPane } from './components/protocol-pane';
import { ResizeHandle } from './components/resize-handle';
import { RevisionHistoryPanel } from './components/revision-history-panel';
import { appendVoiceTranscript, VoiceInput } from './components/voice-input';
import type {
  BusinessDocumentChangePreview,
  BusinessDocumentCommand,
  BusinessDocumentCommandType,
  BusinessDocumentLifecycleState,
  BusinessDocumentOperationState,
  BusinessDocumentProjection,
  BusinessDocumentRevision,
  BusinessDocumentSectionStreamPreview,
  BusinessDocumentSelection,
  CreateBusinessDocumentRequest,
  EvaTitleMatch,
} from './types';

const lifecycleLabels: Record<BusinessDocumentLifecycleState, string> = {
  INTAKE: 'Сбор вводных',
  REVIEW: 'Внесение изменений',
  AGREED: 'Правки внесены',
  ARCHIVED: 'В архиве',
};

const operationLabels: Record<BusinessDocumentOperationState, string> = {
  IDLE: 'Готово к работе',
  ANALYZING: 'Анализ вводных',
  ANALYZING_REVIEW: 'Анализ замечаний',
  GENERATING_DRAFT: 'Создание черновика',
  APPLYING_CHANGES: 'Подготовка плана исправлений',
  EXPORTING: 'Подготовка файла',
  FAILED: 'Операция завершилась с ошибкой',
};

const staleConflictCodes = new Set([
  'STATE_VERSION_CONFLICT',
  'BASE_REVISION_CONFLICT',
  'CHANGE_PREVIEW_STALE',
]);

const protocolPaneWidthStorageKey =
  'ragflow.business-documents.protocol-pane-width';
const defaultProtocolPaneWidth = 460;
const minProtocolPaneWidth = 360;
function getMaxProtocolPaneWidth() {
  return typeof window === 'undefined'
    ? defaultProtocolPaneWidth
    : Math.max(minProtocolPaneWidth, Math.floor(window.innerWidth * 0.8));
}

function clampProtocolPaneWidth(width: number) {
  return Math.min(
    getMaxProtocolPaneWidth(),
    Math.max(minProtocolPaneWidth, width),
  );
}

function readProtocolPaneWidth() {
  try {
    const stored = Number(
      window.localStorage.getItem(protocolPaneWidthStorageKey),
    );
    return Number.isFinite(stored) && stored > 0
      ? clampProtocolPaneWidth(stored)
      : defaultProtocolPaneWidth;
  } catch {
    return defaultProtocolPaneWidth;
  }
}

function makeId(prefix: string) {
  const randomPart = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now()}-${randomPart}`;
}

function formatUpdateTime(value: number | null) {
  if (!value) return 'Нет изменений';
  const milliseconds = value < 1_000_000_000_000 ? value * 1000 : value;
  const date = new Date(milliseconds);
  return Number.isNaN(date.valueOf())
    ? 'Дата неизвестна'
    : date.toLocaleString('ru-RU', {
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      });
}

const exportLabels = {
  MARKDOWN: 'Markdown',
  DOCX: 'Word',
  EVA_WIKI: 'EvaWiki',
} as const;

const BusinessDocumentKeys = {
  lists: () => ['business-documents'] as const,
  list: (page: number, scope: 'mine' | 'all') =>
    [...BusinessDocumentKeys.lists(), scope, page] as const,
  catalog: () => ['business-document-catalog'] as const,
  detail: (documentId?: string) => ['business-document', documentId] as const,
  accessUsers: () => ['business-document-access-users'] as const,
  evaUpdate: (documentId?: string) =>
    ['business-document-eva-update', documentId] as const,
};

function BusinessDocumentCreationSidebar({
  visible,
  children,
}: {
  visible: boolean;
  children: ReactNode;
}) {
  if (!visible) return null;

  return (
    <section
      className="min-h-0 overflow-y-auto scrollbar-auto"
      data-testid="business-document-create-panel"
    >
      {children}
    </section>
  );
}

function CreateBusinessDocumentPage() {
  const navigate = useNavigate();
  const evaAccess = useEvaAccess();
  const { config } = useSystemConfig();
  const queryClient = useQueryClient();
  const [catalogEntryId, setCatalogEntryId] = useState('');
  const [idea, setIdea] = useState('');
  const [evaMatches, setEvaMatches] = useState<EvaTitleMatch[]>([]);
  const [page, setPage] = useState(1);
  const [scope, setScope] = useState<'mine' | 'all'>('mine');
  const documentsQuery = useQuery({
    queryKey: BusinessDocumentKeys.list(page, scope),
    queryFn: () => listBusinessDocuments(page, 20, scope),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) =>
        isBusinessDocumentOperationActive(item.operation_state),
      )
        ? 2000
        : false,
  });
  const catalogQuery = useQuery({
    queryKey: BusinessDocumentKeys.catalog(),
    queryFn: listBusinessDocumentCatalog,
    retry: false,
  });
  const selectedCatalogEntry = catalogQuery.data?.items.find(
    (item) => item.id === catalogEntryId,
  );
  const existingDocuments = selectedCatalogEntry?.existing_documents ?? [];
  const catalogGroups = [
    {
      label: 'Без документов',
      items: (catalogQuery.data?.items ?? []).filter(
        (item) => !item.existing_documents?.length,
      ),
    },
    {
      label: 'Документ уже есть',
      items: (catalogQuery.data?.items ?? []).filter(
        (item) => item.existing_documents?.length,
      ),
    },
  ];
  const canCreate = documentsQuery.data?.capabilities?.create === true;
  const createMutation = useMutation({
    mutationFn: createBusinessDocument,
    onSuccess: async (document) => {
      setEvaMatches([]);
      void queryClient.invalidateQueries({
        queryKey: BusinessDocumentKeys.catalog(),
      });
      if (document.current_revision) {
        navigate(`${Routes.BusinessDocuments}/${document.document_id}`);
        return;
      }
      const command: BusinessDocumentCommand = {
        schema_version: '1',
        command_id: makeId('cmd-initial-analysis'),
        idempotency_key: makeId('idem-initial-analysis'),
        expected_state_version: document.state_version,
        type: 'REQUEST_INTAKE_ASSESSMENT',
        payload: {},
      };

      try {
        await submitBusinessDocumentCommand(document.document_id, command);
      } finally {
        // The document already exists even if the follow-up request fails. Open
        // it so the author can retry the analysis without creating a duplicate.
        navigate(`${Routes.BusinessDocuments}/${document.document_id}`);
      }
    },
    onError: (error) => {
      if (
        error instanceof BusinessDocumentConflictError &&
        error.code === 'EVA_BINDING_DECISION_REQUIRED' &&
        error.details &&
        typeof error.details === 'object' &&
        'matches' in error.details &&
        Array.isArray(error.details.matches)
      ) {
        setEvaMatches(error.details.matches as EvaTitleMatch[]);
      }
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (documentId: string) => deleteBusinessDocument(documentId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: BusinessDocumentKeys.lists(),
        }),
        queryClient.invalidateQueries({
          queryKey: BusinessDocumentKeys.catalog(),
        }),
      ]);
    },
  });

  const createDocument = (
    evaDecision?: CreateBusinessDocumentRequest['eva_decision'],
    selectedEvaPageUrl?: string,
  ) => {
    if (!catalogEntryId || createMutation.isPending) return;
    createMutation.mutate({
      schema_version: '4',
      document_type: 'business_requirements',
      catalog_entry_id: catalogEntryId,
      ...(idea.trim() ? { idea: idea.trim() } : {}),
      dataset_ids: [],
      ...(selectedEvaPageUrl ? { eva_page_url: selectedEvaPageUrl } : {}),
      ...(evaDecision ? { eva_decision: evaDecision } : {}),
    });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    createDocument();
  };

  return (
    <main
      className="grid h-full min-h-0 grid-rows-[auto_1fr] bg-bg-base"
      data-testid="business-documents-create"
    >
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-border-button px-6 py-5 lg:px-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            Бизнес-требования
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            Продолжите сохранённую работу или начните новые бизнес-требования.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <EvaAccessNotice
            access={evaAccess}
            compact
            error={createMutation.error}
            returnTo={Routes.BusinessDocuments}
            onRetry={() => {
              if (isEvaAccessError(createMutation.error))
                createMutation.reset();
            }}
          />
          {canCreate &&
            config?.visibleSections.includes('document_constructor') && (
              <Button asChild variant="outline">
                <Link
                  to={Routes.DocumentConstructor}
                  data-testid="open-document-constructor"
                >
                  <LayoutTemplate className="size-4" />
                  Конструктор
                </Link>
              </Button>
            )}
        </div>
      </header>

      <div
        className={`grid min-h-0 ${
          canCreate
            ? 'grid-cols-[minmax(0,1fr)_minmax(360px,440px)] max-lg:grid-cols-1 max-lg:overflow-y-auto'
            : 'grid-cols-1'
        }`}
      >
        <section
          className={`min-h-0 overflow-y-auto scrollbar-auto ${
            canCreate ? 'border-e border-border-button' : ''
          }`}
          data-testid="business-document-list"
          aria-label="Сохранённые документы"
        >
          <div className="flex min-h-11 flex-wrap items-center justify-between gap-3 border-b border-border-button px-6 py-2">
            <div className="flex items-center gap-3">
              <h2 className="text-sm font-medium">Сохранённые документы</h2>
              {documentsQuery.data && (
                <span className="text-xs text-text-secondary">
                  {documentsQuery.data.total}
                </span>
              )}
            </div>
            <div
              className="flex items-center gap-1"
              role="group"
              aria-label="Фильтр документов"
            >
              <Button
                size="sm"
                variant={scope === 'mine' ? 'secondary' : 'ghost'}
                onClick={() => {
                  setScope('mine');
                  setPage(1);
                }}
                data-testid="business-documents-filter-mine"
              >
                Мои
              </Button>
              <Button
                size="sm"
                variant={scope === 'all' ? 'secondary' : 'ghost'}
                onClick={() => {
                  setScope('all');
                  setPage(1);
                }}
                data-testid="business-documents-filter-all"
              >
                Все
              </Button>
            </div>
          </div>

          {documentsQuery.isLoading && (
            <div
              className="flex items-center gap-2 px-6 py-8 text-sm text-text-secondary"
              data-testid="business-document-list-loading"
            >
              <LoaderCircle className="size-4 animate-spin text-accent-primary" />
              Загружаем документы
            </div>
          )}
          {documentsQuery.error && (
            <div
              className="border-s-2 border-state-error px-5 py-4"
              data-testid="business-document-list-error"
            >
              <p className="text-sm text-state-error">
                {documentsQuery.error.message}
              </p>
              <Button
                size="sm"
                variant="ghost"
                className="mt-2"
                onClick={() => documentsQuery.refetch()}
              >
                <RefreshCw className="size-3.5" />
                Повторить
              </Button>
            </div>
          )}
          {deleteMutation.error && (
            <div
              className="border-s-2 border-state-error px-5 py-4 text-sm text-state-error"
              data-testid="business-document-delete-error"
            >
              {deleteMutation.error.message}
            </div>
          )}
          {!documentsQuery.isLoading &&
            !documentsQuery.error &&
            !documentsQuery.data?.items.length && (
              <div
                className="px-6 py-10 text-center"
                data-testid="business-document-list-empty"
              >
                <FilePlus2 className="mx-auto size-7 stroke-[1.25] text-text-disabled" />
                <p className="mt-3 text-sm font-medium">
                  {scope === 'mine'
                    ? 'У вас пока нет назначенных документов'
                    : 'Документов пока нет'}
                </p>
                <p className="text-xs text-text-secondary">
                  {canCreate
                    ? 'Новый документ можно создать в форме справа.'
                    : 'Откройте фильтр «Все», чтобы просмотреть доступные документы.'}
                </p>
              </div>
            )}
          {documentsQuery.data?.items.map((item) => (
            <div
              key={item.document_id}
              className="group grid grid-cols-[minmax(0,1fr)_auto] items-center border-b border-border-button transition-colors hover:bg-bg-card"
            >
              <Link
                to={`${Routes.BusinessDocuments}/${item.document_id}`}
                className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-6 py-4 focus-visible:outline-none"
                data-testid="business-document-list-item"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate text-sm font-medium text-text-primary">
                      {item.title}
                    </h3>
                    <Badge
                      variant={
                        item.lifecycle_state === 'AGREED'
                          ? 'success'
                          : 'secondary'
                      }
                    >
                      {lifecycleLabels[item.lifecycle_state]}
                    </Badge>
                    {item.owner_name && (
                      <span className="text-xs text-text-secondary">
                        Владелец: {item.owner_name}
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary">
                    <span>{formatUpdateTime(item.update_time)}</span>
                    <span>
                      {item.current_revision_number
                        ? `Редакция ${item.current_revision_number}`
                        : 'Без черновика'}
                    </span>
                    {item.operation_state !== 'IDLE' && (
                      <BusinessDocumentProgress
                        compact
                        job={item.latest_job}
                        operationState={item.operation_state}
                        operationLabel={operationLabels[item.operation_state]}
                      />
                    )}
                  </div>
                </div>
                <ArrowRight className="size-4 text-text-disabled transition-transform group-hover:translate-x-0.5 group-hover:text-text-primary" />
              </Link>
              {item.permissions?.delete && (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      className="me-4 text-text-secondary hover:text-state-error"
                      aria-label={`Удалить документ «${item.title}»`}
                      disabled={deleteMutation.isPending}
                      data-testid="business-document-delete"
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Удалить документ?</AlertDialogTitle>
                      <AlertDialogDescription>
                        Документ «{item.title}», его история, задания и файлы
                        экспорта будут удалены без возможности восстановления.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Отмена</AlertDialogCancel>
                      <AlertDialogAction
                        className="bg-state-error text-white hover:bg-state-error/90"
                        onClick={() => deleteMutation.mutate(item.document_id)}
                      >
                        Удалить
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              )}
            </div>
          ))}

          {documentsQuery.data && documentsQuery.data.total > 20 && (
            <div className="flex items-center justify-between gap-3 px-6 py-4">
              <span className="text-xs text-text-secondary">
                Страница {documentsQuery.data.page}
              </span>
              <div className="flex gap-2">
                <Button
                  size="icon-sm"
                  variant="outline"
                  aria-label="Предыдущая страница"
                  disabled={page <= 1 || documentsQuery.isFetching}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  <ChevronLeft className="size-4" />
                </Button>
                <Button
                  size="icon-sm"
                  variant="outline"
                  aria-label="Следующая страница"
                  disabled={
                    page * documentsQuery.data.page_size >=
                      documentsQuery.data.total || documentsQuery.isFetching
                  }
                  onClick={() => setPage((current) => current + 1)}
                >
                  <ChevronRight className="size-4" />
                </Button>
              </div>
            </div>
          )}
        </section>

        <BusinessDocumentCreationSidebar visible={canCreate}>
          <form onSubmit={submit} className="px-6 py-7 lg:px-8">
            <div className="flex items-center gap-2 text-sm font-medium text-accent-primary">
              <FilePlus2 className="size-4" />
              Новый документ
            </div>
            <h2 className="mt-3 text-xl font-semibold tracking-tight text-text-primary">
              Бизнес-требования
            </h2>
            <p className="mt-2 text-sm leading-6 text-text-secondary">
              Выберите название, чтобы создать пустые обязательные разделы. Если
              укажете идею, агент начнёт сбор вводных.
            </p>

            <label className="mt-6 block space-y-2 text-sm font-medium">
              <span>Название</span>
              <select
                value={catalogEntryId}
                onChange={(event) => {
                  setCatalogEntryId(event.target.value);
                  setEvaMatches([]);
                }}
                disabled={catalogQuery.isLoading || catalogQuery.isError}
                className="h-9 w-full rounded-md border border-border-button bg-bg-base px-3 text-sm font-normal text-text-primary outline-none [color-scheme:light] focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 dark:[color-scheme:dark] [&_optgroup]:bg-bg-base [&_optgroup]:text-text-primary [&_option]:bg-bg-base [&_option]:text-text-primary"
                aria-label="Название документа"
                aria-describedby={
                  existingDocuments.length
                    ? 'existing-topic-documents'
                    : undefined
                }
                data-testid="business-document-catalog-select"
              >
                <option value="" disabled>
                  Выберите документ из справочника
                </option>
                {catalogGroups
                  .filter((group) => group.items.length)
                  .map((group) => (
                    <optgroup key={group.label} label={group.label}>
                      {group.items.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.title}
                          {item.existing_documents?.length
                            ? ' — документ уже есть'
                            : ''}
                        </option>
                      ))}
                    </optgroup>
                  ))}
              </select>
              {catalogQuery.isLoading && (
                <span className="block text-xs font-normal text-text-secondary">
                  Загружаем справочник документов…
                </span>
              )}
              {catalogQuery.isError && (
                <span className="block text-xs font-normal text-state-error">
                  Не удалось загрузить справочник документов.
                </span>
              )}
              {selectedCatalogEntry?.description && (
                <span className="block text-xs font-normal leading-5 text-text-secondary">
                  {selectedCatalogEntry.description}
                </span>
              )}
            </label>
            {existingDocuments.length > 0 && (
              <div
                id="existing-topic-documents"
                role="status"
                className="mt-3 rounded-md border border-border-button bg-bg-card p-3 text-sm"
                data-testid="business-document-existing-topic"
              >
                <p className="font-medium text-text-primary">
                  По этой теме документ уже есть
                </p>
                <p className="mt-1 text-xs leading-5 text-text-secondary">
                  Откройте существующий документ, чтобы продолжить работу, или
                  создайте ещё один.
                </p>
                <ul className="mt-2 space-y-2">
                  {existingDocuments.map((document) => (
                    <li key={document.document_id}>
                      <Link
                        to={`${Routes.BusinessDocuments}/${document.document_id}`}
                        className="font-medium text-text-primary underline underline-offset-4"
                      >
                        {document.title} ·{' '}
                        {lifecycleLabels[document.lifecycle_state]}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <label className="mt-5 block space-y-2 text-sm font-medium">
              <span>Идея (необязательно)</span>
              <div className="relative">
                <Textarea
                  value={idea}
                  maxLength={10000}
                  autoSize={{ minRows: 7, maxRows: 18 }}
                  resize="vertical"
                  aria-label="Описание идеи"
                  placeholder="Что нужно создать, для кого и какой результат ожидается?"
                  className="pe-11"
                  onChange={(event) => setIdea(event.target.value)}
                />
                <div className="absolute end-2 top-2">
                  <VoiceInput
                    label="Идея"
                    onTranscript={(transcript) =>
                      setIdea((value) =>
                        appendVoiceTranscript(value, transcript, 10000),
                      )
                    }
                    testId="voice-input-document-idea"
                  />
                </div>
              </div>
            </label>

            {evaMatches.length > 0 && (
              <div
                className="mt-5 rounded-lg border border-state-warning/40 bg-state-warning/10 p-4"
                data-testid="eva-title-match-banner"
              >
                <div className="flex items-start gap-3">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-state-warning" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-text-primary">
                      В EVA Wiki найдены страницы с таким названием
                    </p>
                    <p className="mt-1 text-xs leading-5 text-text-secondary">
                      Выберите свободную страницу: её содержимое откроется как
                      первая редакция для комментариев и анализа.
                    </p>
                    <div className="mt-3 space-y-2">
                      {evaMatches.map((match) => (
                        <div
                          key={match.id || match.web_url}
                          className="rounded-md border border-border-button bg-bg-base p-3"
                        >
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-sm font-medium text-text-primary">
                                {match.name}
                              </p>
                              <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-text-secondary">
                                {match.breadcrumbs.map((item, index) => (
                                  <span
                                    key={item.id}
                                    className="flex items-center gap-1"
                                  >
                                    {index > 0 && (
                                      <span aria-hidden="true">›</span>
                                    )}
                                    <a
                                      href={item.web_url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="hover:text-accent-primary hover:underline"
                                    >
                                      {item.name}
                                    </a>
                                  </span>
                                ))}
                                {match.breadcrumbs.length === 0 && (
                                  <a
                                    href={match.web_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="break-all hover:text-accent-primary hover:underline"
                                  >
                                    {match.hierarchy || match.web_url}
                                  </a>
                                )}
                              </div>
                              {!match.binding_available && (
                                <p className="mt-2 text-xs text-state-error">
                                  Уже привязана к документу «
                                  {match.linked_document?.title ||
                                    'другой документ'}
                                  »
                                </p>
                              )}
                            </div>
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              disabled={
                                !match.binding_available ||
                                createMutation.isPending
                              }
                              onClick={() =>
                                createDocument({ mode: 'BIND' }, match.web_url)
                              }
                            >
                              Открыть в редакторе
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="mt-3"
                      disabled={createMutation.isPending}
                      onClick={() => createDocument({ mode: 'SKIP' })}
                      data-testid="create-without-eva-binding"
                    >
                      Создать без привязки
                    </Button>
                  </div>
                </div>
              </div>
            )}

            {createMutation.error &&
              !isEvaAccessError(createMutation.error) &&
              !(
                createMutation.error instanceof BusinessDocumentConflictError &&
                createMutation.error.code === 'EVA_BINDING_DECISION_REQUIRED'
              ) && (
                <p className="mt-4 text-sm text-state-error" role="alert">
                  {createMutation.error.message}
                </p>
              )}

            <div className="mt-6 flex scroll-mt-20 flex-wrap justify-end gap-2">
              {evaAccess.data &&
                ['MISSING', 'REJECTED', 'FORBIDDEN', 'UNAVAILABLE'].includes(
                  evaAccess.data.state,
                ) && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!catalogEntryId || createMutation.isPending}
                    onClick={() => createDocument({ mode: 'SKIP' })}
                    data-testid="create-without-eva-access"
                  >
                    Начать без привязки к EVA
                  </Button>
                )}
              <Button
                type="submit"
                variant="accent"
                size="lg"
                loading={createMutation.isPending}
                disabled={!catalogEntryId}
              >
                <Sparkles className="size-4" />
                {existingDocuments.length
                  ? 'Создать ещё один документ'
                  : 'Начать работу'}
              </Button>
            </div>
          </form>
        </BusinessDocumentCreationSidebar>
      </div>
    </main>
  );
}

export default function BusinessDocumentsPage() {
  const { id: documentId, changeId } = useParams<{
    id: string;
    changeId: string;
  }>();
  const activeDocumentId = useRef(documentId);
  activeDocumentId.current = documentId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: userInfo } = useFetchUserInfo();
  const { data: tenantInfo } = useFetchTenantInfo();
  const [selection, setSelection] = useState<BusinessDocumentSelection | null>(
    null,
  );
  const [sectionFocus, setSectionFocus] = useState<{
    sectionId: string;
    sequence: number;
  } | null>(null);
  const [sourceFocus, setSourceFocus] = useState<{
    kind: 'question' | 'proposal' | 'comment';
    id: string;
    sequence: number;
  } | null>(null);
  const [hasConflict, setHasConflict] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedRevisionId, setSelectedRevisionId] = useState<string>();
  const [comparisonRevisionId, setComparisonRevisionId] = useState<string>();
  const [evaSyncNotice, setEvaSyncNotice] = useState<string>();
  const [ownerSelection, setOwnerSelection] = useState('');
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [streamPreview, setStreamPreview] = useState<{
    jobId: string;
    attempt: number;
    baseRevisionId?: string | null;
    sections: BusinessDocumentSectionStreamPreview[];
  } | null>(null);
  const [streamError, setStreamError] = useState<string>();
  const [dismissedOperationError, setDismissedOperationError] =
    useState<string>();
  const [protocolPaneWidth, setProtocolPaneWidth] = useState(
    readProtocolPaneWidth,
  );
  const [maxProtocolPaneWidth, setMaxProtocolPaneWidth] = useState(
    getMaxProtocolPaneWidth,
  );
  const clearSelection = useCallback(() => setSelection(null), []);
  const promptStorageIdentity = useMemo(() => {
    const userId = userInfo?.id?.trim();
    const tenantId = tenantInfo?.tenant_id?.trim();
    return userId && tenantId ? `${tenantId}.${userId}` : null;
  }, [tenantInfo?.tenant_id, userInfo?.id]);

  const resizeProtocolPane = useCallback((width: number) => {
    const nextWidth = clampProtocolPaneWidth(width);
    setProtocolPaneWidth(nextWidth);
    try {
      window.localStorage.setItem(
        protocolPaneWidthStorageKey,
        String(nextWidth),
      );
    } catch {
      // Width persistence is optional; resizing remains available in-session.
    }
  }, []);

  useEffect(() => {
    const reflowProtocolPane = () => {
      setMaxProtocolPaneWidth(getMaxProtocolPaneWidth());
      setProtocolPaneWidth((width) => clampProtocolPaneWidth(width));
    };
    window.addEventListener('resize', reflowProtocolPane);
    return () => window.removeEventListener('resize', reflowProtocolPane);
  }, []);

  const documentQuery = useQuery({
    queryKey: BusinessDocumentKeys.detail(documentId),
    queryFn: () => fetchBusinessDocument(documentId!),
    enabled: Boolean(documentId && !changeId),
    retry: false,
    refetchInterval: (query) => {
      const state = query.state.data?.operation_state;
      return (state && state !== 'IDLE' && state !== 'FAILED') ||
        query.state.data?.background_work?.some(
          (work) => work.status === 'QUEUED',
        )
        ? 2000
        : false;
    },
  });

  const document = documentQuery.data;
  const operationErrorKey = JSON.stringify([
    documentId,
    document?.latest_job?.job_id ?? document?.state_version,
    document?.latest_job?.attempt,
    document?.last_error,
  ]);
  const streamJobId =
    document?.latest_job?.job_type === 'PLAN_CHANGES' &&
    ['PENDING', 'RUNNING', 'RETRY'].includes(document.latest_job.status)
      ? document.latest_job.job_id
      : undefined;
  useEffect(() => {
    if (!documentId || !streamJobId || changeId) return;
    const controller = new AbortController();
    setStreamError(undefined);
    setStreamPreview({ jobId: streamJobId, attempt: 0, sections: [] });
    void watchBusinessDocumentJobEvents(
      documentId,
      streamJobId,
      controller.signal,
      (event) => {
        if (['retry', 'failed', 'not_applied'].includes(event.type)) {
          setStreamPreview({
            jobId: streamJobId,
            attempt: event.attempt,
            sections: [],
          });
        } else if (event.type === 'section_preview') {
          const section =
            event.payload as unknown as BusinessDocumentSectionStreamPreview;
          if (
            typeof section.section_id !== 'string' ||
            typeof section.before !== 'string' ||
            typeof section.after !== 'string' ||
            !Array.isArray(section.source_event_ids)
          )
            return;
          setStreamPreview((current) => {
            const sections =
              current?.jobId === streamJobId &&
              current.attempt === event.attempt
                ? current.sections
                : [];
            return {
              jobId: streamJobId,
              attempt: event.attempt,
              baseRevisionId: event.base_revision_id,
              sections: [
                ...sections.filter(
                  (item) => item.section_id !== section.section_id,
                ),
                section,
              ],
            };
          });
        }
        if (
          [
            'preview_ready',
            'failed',
            'applied',
            'discarded',
            'not_applied',
          ].includes(event.type)
        ) {
          if (event.type === 'applied') {
            if (activeDocumentId.current === documentId) {
              setHistoryOpen(false);
              setSelectedRevisionId(undefined);
              setComparisonRevisionId(undefined);
            }
            void queryClient.invalidateQueries({
              queryKey: ['business-document-revisions', documentId],
              refetchType: 'none',
            });
          }
          void queryClient.invalidateQueries({
            queryKey: BusinessDocumentKeys.detail(documentId),
          });
        }
      },
    ).catch((error: Error) => {
      if (!controller.signal.aborted) setStreamError(error.message);
    });
    return () => controller.abort();
  }, [documentId, streamJobId, changeId, queryClient]);
  const changePreviewQuery = useQuery({
    queryKey: [
      'business-document-change-preview',
      documentId,
      document?.change_preview?.job_id,
    ],
    queryFn: () =>
      fetchBusinessDocumentChangePreview(
        documentId!,
        document!.change_preview!.job_id,
      ),
    enabled: Boolean(
      documentId && document?.change_preview?.job_id && !changeId,
    ),
    retry: false,
  });
  const inlineChangePreview =
    useMemo<BusinessDocumentChangePreview | null>(() => {
      if (document?.change_preview) {
        const preview = changePreviewQuery.data;
        return preview?.job_id === document.change_preview.job_id &&
          preview.base_revision_id === document.current_revision?.revision_id
          ? preview
          : null;
      }
      if (
        !document ||
        !streamJobId ||
        streamPreview?.jobId !== streamJobId ||
        streamPreview.baseRevisionId !==
          document.current_revision?.revision_id ||
        !streamPreview.sections.length
      )
        return null;
      return {
        job_id: streamJobId,
        base_revision_id: document.current_revision?.revision_id ?? '',
        state_version: document.state_version,
        sections: streamPreview.sections,
        acknowledged_no_change_event_ids: [],
      };
    }, [document, changePreviewQuery.data, streamPreview, streamJobId]);
  useEffect(() => setSelection(null), [inlineChangePreview?.sections]);
  const focusSection = useCallback((sectionId: string) => {
    setSectionFocus((current) => ({
      sectionId,
      sequence: (current?.sequence ?? 0) + 1,
    }));
  }, []);
  const focusSource = useCallback(
    (kind: 'question' | 'proposal' | 'comment', id: string) => {
      setHistoryOpen(false);
      setSourceFocus((current) => ({
        kind,
        id,
        sequence: (current?.sequence ?? 0) + 1,
      }));
    },
    [],
  );
  const highlightedSectionIds = useMemo(() => {
    if (!document) return [];
    const proposals = new Set(
      document.background_work
        ?.filter((work) => work.status !== 'FAILED')
        .map((work) => work.proposal_id),
    );
    return [
      ...new Set(
        [
          ...(inlineChangePreview?.sections.map(
            (section) => section.section_id,
          ) ?? []),
          ...document.protocol.proposals
            .filter((proposal) => proposals.has(proposal.proposal_id))
            .map((proposal) => proposal.target_section_id),
        ].filter((sectionId): sectionId is string => Boolean(sectionId)),
      ),
    ];
  }, [inlineChangePreview, document]);
  useEffect(() => {
    setOwnerSelection(document?.owner_id ?? '');
  }, [document?.owner_id]);
  const accessUsersQuery = useQuery({
    queryKey: BusinessDocumentKeys.accessUsers(),
    queryFn: listBusinessDocumentAccessUsers,
    enabled: Boolean(document?.permissions?.assign),
    retry: false,
  });
  const assignOwnerMutation = useMutation({
    mutationFn: () => {
      if (!documentId || !document || !ownerSelection) {
        throw new Error('Выберите пользователя');
      }
      return assignBusinessDocumentOwner(
        documentId,
        ownerSelection,
        document.state_version,
      );
    },
    onSuccess: async (updatedDocument) => {
      queryClient.setQueryData(
        BusinessDocumentKeys.detail(documentId),
        updatedDocument,
      );
      await queryClient.invalidateQueries({
        queryKey: ['business-documents'],
      });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => {
      if (!documentId) throw new Error('Документ не загружен');
      return deleteBusinessDocument(documentId);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['business-documents'],
      });
      navigate(Routes.BusinessDocuments);
    },
  });
  const [initialEvaPageUrl, setInitialEvaPageUrl] = useState('');
  const evaAccess = useEvaAccess(Boolean(documentId && !changeId));
  const hasPersonalEvaToken = evaAccess.data?.state === 'AVAILABLE';
  const currentRevisionId = document?.current_revision?.revision_id;
  const evaUpdateQuery = useQuery({
    queryKey: BusinessDocumentKeys.evaUpdate(documentId),
    queryFn: () => checkBusinessDocumentEvaUpdate(documentId!),
    enabled: Boolean(
      documentId &&
      !changeId &&
      hasPersonalEvaToken &&
      document?.eva_binding?.status === 'CONNECTED' &&
      document.eva_binding.capabilities.includes('PULL_FROM_EVA') &&
      document.current_revision &&
      ['REVIEW', 'AGREED'].includes(document.lifecycle_state),
    ),
    retry: false,
    staleTime: 0,
  });
  useEffect(() => {
    clearSelection();
  }, [clearSelection, documentId]);
  const revisionsQuery = useQuery({
    queryKey: ['business-document-revisions', documentId],
    queryFn: () => listBusinessDocumentRevisions(documentId!),
    enabled: Boolean(documentId && !changeId && historyOpen),
    retry: false,
  });
  const commandMutation = useMutation({
    mutationFn: ({
      type,
      payload,
    }: {
      type: BusinessDocumentCommandType;
      payload: Record<string, unknown>;
    }) => {
      if (!documentId || !document) {
        throw new Error('Документ не загружен');
      }
      const command: BusinessDocumentCommand = {
        schema_version: '1',
        command_id: makeId('cmd'),
        idempotency_key: makeId('idem'),
        expected_state_version: document.state_version,
        type,
        payload,
      };
      return submitBusinessDocumentCommand(documentId, command);
    },
    onSuccess: async (result, { type }) => {
      const targetDocumentId = result.document_id;
      if (activeDocumentId.current === targetDocumentId) {
        setHasConflict(false);
      }
      const updatedDocument = result.document;
      if (updatedDocument) {
        await queryClient.cancelQueries({
          queryKey: BusinessDocumentKeys.detail(targetDocumentId),
          exact: true,
        });
        queryClient.setQueryData<BusinessDocumentProjection>(
          BusinessDocumentKeys.detail(targetDocumentId),
          (current) =>
            current && current.state_version > updatedDocument.state_version
              ? current
              : updatedDocument,
        );
      }
      if (
        type === 'APPLY_CHANGES' ||
        type === 'CONFIRM_PREPARED_CHANGES' ||
        type === 'RESTORE_REVISION'
      ) {
        if (activeDocumentId.current === targetDocumentId) {
          setHistoryOpen(false);
          setSelectedRevisionId(undefined);
          setComparisonRevisionId(undefined);
        }
        await queryClient.invalidateQueries({
          queryKey: ['business-document-revisions', targetDocumentId],
          refetchType: 'none',
        });
      }
      if (!updatedDocument) {
        await queryClient.invalidateQueries({
          queryKey: BusinessDocumentKeys.detail(targetDocumentId),
        });
      }
    },
    onError: (error) => {
      if (
        error instanceof BusinessDocumentConflictError &&
        staleConflictCodes.has(error.code)
      ) {
        setHasConflict(true);
        void documentQuery.refetch();
      } else {
        setHasConflict(false);
      }
    },
  });

  const submitCommand = useCallback(
    (
      type: BusinessDocumentCommandType,
      payload: Record<string, unknown>,
      onSuccess?: () => void,
    ) => commandMutation.mutate({ type, payload }, { onSuccess }),
    [commandMutation],
  );

  const allowed = useMemo(
    () =>
      new Set(
        document?.permissions?.edit === false
          ? []
          : (document?.allowed_commands ?? []),
      ),
    [document?.allowed_commands, document?.permissions?.edit],
  );
  const isBusy =
    commandMutation.isPending ||
    Boolean(
      document &&
      document.operation_state !== 'IDLE' &&
      document.operation_state !== 'FAILED',
    );
  const displayedRevision = useMemo<BusinessDocumentRevision | null>(() => {
    if (!document?.current_revision) return null;
    if (!historyOpen || !selectedRevisionId) return document.current_revision;
    return (
      revisionsQuery.data?.find(
        (revision) => revision.revision_id === selectedRevisionId,
      ) ?? document.current_revision
    );
  }, [
    document?.current_revision,
    historyOpen,
    revisionsQuery.data,
    selectedRevisionId,
  ]);
  const comparisonRevision = historyOpen
    ? (revisionsQuery.data?.find(
        (revision) => revision.revision_id === comparisonRevisionId,
      ) ??
      revisionsQuery.data?.find(
        (revision) =>
          revision.revision_number ===
          (displayedRevision?.revision_number ?? 0) - 1,
      ) ??
      displayedRevision ??
      undefined)
    : undefined;
  const documentWarnings = historyOpen
    ? (displayedRevision?.document_ast.warnings ?? [])
    : Array.from(
        new Map(
          [
            ...(document?.protocol.warnings ?? []),
            ...(inlineChangePreview?.warnings ??
              displayedRevision?.document_ast.warnings ??
              []),
          ].map((warning) => [JSON.stringify(warning), warning]),
        ).values(),
      );
  const documentHighlightedSectionIds =
    historyOpen && displayedRevision && comparisonRevision
      ? displayedRevision.document_ast.sections
          .filter(
            (section) =>
              displayedRevision.section_texts[section.id] !==
                comparisonRevision.section_texts[section.id] ||
              comparisonRevision.document_ast.sections.find(
                (item) => item.id === section.id,
              )?.title !== section.title ||
              evidenceRefsChanged(
                comparisonRevision.document_ast.sections.find(
                  (item) => item.id === section.id,
                )?.evidence_refs,
                section.evidence_refs,
              ),
          )
          .map((section) => section.id)
      : highlightedSectionIds;
  const ensureEvaCapability = useCallback(
    async (capability: 'PULL_FROM_EVA' | 'CREATE_EVA_CHANGE') => {
      if (!documentId || !document) throw new Error('Документ не загружен');
      if (document.eva_binding?.capabilities.includes(capability)) {
        return document;
      }
      const updatedDocument = await rebindBusinessDocumentToEva(
        documentId,
        document.state_version,
      );
      queryClient.setQueryData(
        BusinessDocumentKeys.detail(documentId),
        updatedDocument,
      );
      if (!updatedDocument.eva_binding?.capabilities.includes(capability)) {
        throw new Error('Связанная страница EVA недоступна для этой операции.');
      }
      return updatedDocument;
    },
    [document, documentId, queryClient],
  );
  const pullEvaMutation = useMutation({
    mutationFn: async () => {
      if (!documentId) throw new Error('Документ не загружен');
      const connectedDocument = await ensureEvaCapability('PULL_FROM_EVA');
      return pullBusinessDocumentFromEva(
        documentId,
        connectedDocument.state_version,
      );
    },
    onSuccess: async (result) => {
      queryClient.setQueryData(
        BusinessDocumentKeys.detail(documentId),
        result.document,
      );
      setEvaSyncNotice(
        result.sync.changed
          ? 'Новая версия EVA добавлена в цикл внесения изменений. Запустите анализ замечаний.'
          : 'В EVA нет новых изменений после последней синхронизации.',
      );
      await queryClient.invalidateQueries({
        queryKey: ['business-document-revisions', documentId],
      });
      await queryClient.invalidateQueries({
        queryKey: BusinessDocumentKeys.evaUpdate(documentId),
      });
    },
  });
  const rebindEvaMutation = useMutation({
    mutationFn: (pageUrl?: string) => {
      if (!documentId || !document) throw new Error('Документ не загружен');
      return pageUrl
        ? rebindBusinessDocumentToEva(
            documentId,
            document.state_version,
            pageUrl,
          )
        : rebindBusinessDocumentToEva(documentId, document.state_version);
    },
    onSuccess: (updatedDocument) => {
      queryClient.setQueryData(
        BusinessDocumentKeys.detail(documentId),
        updatedDocument,
      );
      setEvaSyncNotice('Страница EVA подключена. Синхронизация доступна.');
    },
  });
  const createEvaChangeMutation = useMutation({
    mutationFn: async () => {
      if (!documentId) throw new Error('Документ не загружен');
      const connectedDocument = await ensureEvaCapability('CREATE_EVA_CHANGE');
      return createEvaChangeFromBusinessDocument(
        documentId,
        connectedDocument.state_version,
      );
    },
    onSuccess: (change) =>
      navigate(`${Routes.BusinessDocuments}/eva/${change.change_id}`),
  });

  if (changeId) return <EvaChangeWorkbench changeId={changeId} />;

  if (!documentId) return <CreateBusinessDocumentPage />;

  if (documentQuery.isLoading) {
    return (
      <main
        className="flex h-full items-center justify-center bg-bg-base"
        data-testid="business-document-loading"
      >
        <div className="flex items-center gap-3 text-sm text-text-secondary">
          <LoaderCircle className="size-5 animate-spin text-accent-primary" />
          Загружаем рабочий документ
        </div>
      </main>
    );
  }

  if (documentQuery.error || !document) {
    return (
      <main
        className="flex h-full items-center justify-center bg-bg-base p-6"
        data-testid="business-document-error"
      >
        <div className="max-w-md border-s-2 border-state-error ps-5">
          <AlertTriangle className="size-5 text-state-error" />
          <h1 className="mt-3 text-lg font-semibold">
            Не удалось открыть документ
          </h1>
          <p className="mt-2 text-sm text-text-secondary">
            {documentQuery.error?.message || 'Документ не найден'}
          </p>
          <Button
            className="mt-4"
            variant="outline"
            onClick={() => documentQuery.refetch()}
          >
            <RefreshCw className="size-4" />
            Повторить
          </Button>
        </div>
      </main>
    );
  }

  const runDocumentCommand = (
    type: BusinessDocumentCommandType,
    payload?: Record<string, unknown>,
  ) => {
    const revisionId = document.current_revision?.revision_id;
    const payloadByCommand: Partial<
      Record<BusinessDocumentCommandType, Record<string, unknown>>
    > = {
      APPLY_CHANGES: { base_revision_id: revisionId },
    };
    submitCommand(type, payload ?? payloadByCommand[type] ?? {});
  };

  const visibleExports =
    document.latest_exports?.filter((artifact) => artifact.format !== 'DOCX') ??
    [];

  const downloadExport = async (
    event: MouseEvent<HTMLAnchorElement>,
    artifactId: string,
    filename: string,
  ) => {
    event.preventDefault();
    const blob = await downloadBusinessDocumentExport(
      document.document_id,
      artifactId,
    );
    downloadFileFromBlob(blob, filename);
  };

  return (
    <main
      className="grid h-full min-h-0 min-w-0 grid-rows-[auto_auto_1fr] bg-bg-base"
      data-testid="business-document-workbench"
    >
      <header
        className="flex min-h-14 min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border-button px-5 py-2"
        data-testid="business-document-header"
      >
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h1 className="min-w-0 break-words text-lg font-semibold tracking-tight sm:truncate">
              {document.title}
            </h1>
            <Badge
              variant={
                document.lifecycle_state === 'AGREED' ? 'success' : 'secondary'
              }
            >
              {lifecycleLabels[document.lifecycle_state]}
            </Badge>
          </div>
          {!isBusinessDocumentOperationActive(document.operation_state) && (
            <p className="text-xs text-text-secondary">
              {operationLabels[document.operation_state]}
            </p>
          )}
        </div>
        <div
          className="flex w-full min-w-0 flex-wrap items-center justify-start gap-2 sm:w-auto sm:justify-end"
          data-testid="business-document-actions"
        >
          <Popover open={detailsOpen} onOpenChange={setDetailsOpen}>
            <PopoverTrigger asChild>
              <Button
                size="sm"
                variant="ghost"
                data-testid="business-document-details-toggle"
              >
                <Link2 className="size-3.5 text-accent-primary" />
                EVA
                {evaUpdateQuery.data?.changed
                  ? ' · обновление'
                  : document.eva_binding
                    ? ' · связана'
                    : ' · не связана'}
                <ChevronDown className="size-3.5" />
              </Button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              className="max-h-[min(80vh,680px)] w-[min(480px,calc(100vw-24px))] overflow-y-auto p-4"
              data-testid="business-document-details"
            >
              <div className="space-y-3 text-xs">
                <div className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
                  <span>Владелец: {document.owner_name || 'не назначен'}</span>
                  {document.permissions?.assign && (
                    <>
                      <div className="min-w-56">
                        <SelectWithSearch
                          value={ownerSelection}
                          onChange={setOwnerSelection}
                          options={(accessUsersQuery.data?.items ?? []).map(
                            (user) => ({
                              value: user.user_id,
                              label: `${user.nickname} (${user.email})`,
                            }),
                          )}
                          placeholder="Выберите владельца"
                          emptyData="Нет доступных пользователей"
                          disabled={
                            accessUsersQuery.isLoading ||
                            assignOwnerMutation.isPending
                          }
                          testId="business-document-owner-select"
                          optionTestIdPrefix="business-document-owner-option-"
                        />
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={
                          !ownerSelection ||
                          ownerSelection === document.owner_id ||
                          assignOwnerMutation.isPending
                        }
                        loading={assignOwnerMutation.isPending}
                        onClick={() => assignOwnerMutation.mutate()}
                        data-testid="business-document-assign-owner"
                      >
                        Назначить
                      </Button>
                    </>
                  )}
                </div>
                {(accessUsersQuery.error || assignOwnerMutation.error) && (
                  <p className="mt-1 text-xs text-state-error" role="alert">
                    {
                      (accessUsersQuery.error || assignOwnerMutation.error)
                        ?.message
                    }
                  </p>
                )}
              </div>
              {!document.eva_binding &&
                document.permissions?.edit !== false &&
                document.lifecycle_state !== 'ARCHIVED' && (
                  <div className="space-y-2 border-b border-border-button px-5 py-3">
                    <EvaAccessNotice
                      access={evaAccess}
                      error={rebindEvaMutation.error}
                      returnTo={`${Routes.BusinessDocuments}/${documentId}`}
                      onRetry={() => rebindEvaMutation.reset()}
                    />
                    <form
                      className="flex flex-wrap items-center gap-2"
                      onSubmit={(event) => {
                        event.preventDefault();
                        if (
                          !isBusy &&
                          hasPersonalEvaToken &&
                          initialEvaPageUrl.trim()
                        ) {
                          rebindEvaMutation.mutate(initialEvaPageUrl.trim());
                        }
                      }}
                    >
                      <Input
                        aria-label="Ссылка на страницу EVA"
                        placeholder="Ссылка на учебную или рабочую страницу EVA"
                        value={initialEvaPageUrl}
                        onChange={(event) =>
                          setInitialEvaPageUrl(event.target.value)
                        }
                        maxLength={2048}
                        disabled={isBusy || rebindEvaMutation.isPending}
                      />
                      <Button
                        type="submit"
                        disabled={
                          isBusy ||
                          !hasPersonalEvaToken ||
                          !initialEvaPageUrl.trim() ||
                          rebindEvaMutation.isPending
                        }
                      >
                        Связать с EVA
                      </Button>
                    </form>
                    <p className="text-xs text-text-secondary">
                      Привязка сохраняет текущую редакцию. Чтение обновления и
                      публикация выполняются отдельно.
                    </p>
                    {rebindEvaMutation.error && (
                      <p className="text-xs text-state-error" role="alert">
                        {rebindEvaMutation.error.message}
                      </p>
                    )}
                  </div>
                )}
              {document.eva_binding && (
                <div
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border-button px-5 py-3 text-xs"
                  data-testid="business-document-eva-binding"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    <Link2 className="size-3.5 shrink-0 text-accent-primary" />
                    <span className="text-text-secondary">Связано с EVA:</span>
                    <a
                      href={document.eva_binding.page_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex min-w-0 items-center gap-1 font-medium text-text-primary hover:text-accent-primary"
                    >
                      <span className="truncate">
                        {document.eva_binding.document_name ||
                          document.eva_binding.document_code ||
                          document.eva_binding.page_url}
                      </span>
                      <ExternalLink className="size-3 shrink-0" />
                    </a>
                    {document.eva_binding.status === 'LINK_ONLY' && (
                      <>
                        <span className="text-text-disabled">
                          Только ссылка — доступный коннектор не найден
                        </span>
                        {document.permissions?.edit !== false && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={
                              isBusy ||
                              !hasPersonalEvaToken ||
                              rebindEvaMutation.isPending
                            }
                            loading={rebindEvaMutation.isPending}
                            onClick={() => rebindEvaMutation.mutate(undefined)}
                            data-testid="rebind-business-document-to-eva"
                          >
                            <RefreshCw className="size-3.5" />
                            Подключить заново
                          </Button>
                        )}
                      </>
                    )}
                  </div>
                  {document.permissions?.edit !== false &&
                    evaUpdateQuery.data?.changed &&
                    evaUpdateQuery.data.can_pull && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={
                          isBusy ||
                          !hasPersonalEvaToken ||
                          !document.current_revision ||
                          !['REVIEW', 'AGREED'].includes(
                            document.lifecycle_state,
                          ) ||
                          pullEvaMutation.isPending
                        }
                        loading={pullEvaMutation.isPending}
                        onClick={() => pullEvaMutation.mutate()}
                        data-testid="pull-business-document-from-eva"
                      >
                        <ArrowDownToLine className="size-3.5" />
                        Загрузить обновление из EVA
                      </Button>
                    )}
                  {document.permissions?.edit !== false && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={
                        isBusy ||
                        !hasPersonalEvaToken ||
                        !document.current_revision ||
                        !['REVIEW', 'AGREED'].includes(
                          document.lifecycle_state,
                        ) ||
                        createEvaChangeMutation.isPending
                      }
                      loading={createEvaChangeMutation.isPending}
                      onClick={() => createEvaChangeMutation.mutate()}
                      data-testid="push-business-document-to-eva"
                    >
                      <ArrowUpFromLine className="size-3.5" />
                      Сохранить текущий вариант в EVA
                    </Button>
                  )}
                  {(rebindEvaMutation.error ||
                    pullEvaMutation.error ||
                    createEvaChangeMutation.error) &&
                    !isEvaAccessError(
                      rebindEvaMutation.error ||
                        pullEvaMutation.error ||
                        createEvaChangeMutation.error,
                    ) && (
                      <span className="w-full text-state-error" role="alert">
                        {
                          (
                            rebindEvaMutation.error ||
                            pullEvaMutation.error ||
                            createEvaChangeMutation.error
                          )?.message
                        }
                      </span>
                    )}
                  {evaSyncNotice && (
                    <span className="w-full text-text-secondary">
                      {evaSyncNotice}
                    </span>
                  )}
                  {evaUpdateQuery.data?.changed && (
                    <span
                      className="w-full text-state-warning"
                      data-testid="eva-update-available"
                    >
                      В EVA есть более новая версия документа. Загрузите её,
                      чтобы добавить изменения в текущий цикл ревью.
                    </span>
                  )}
                  {evaUpdateQuery.error &&
                    !isEvaAccessError(evaUpdateQuery.error) && (
                      <span className="w-full text-text-disabled" role="status">
                        Не удалось проверить обновления EVA:{' '}
                        {evaUpdateQuery.error.message}
                      </span>
                    )}
                  <div className="w-full">
                    <EvaAccessNotice
                      access={evaAccess}
                      error={
                        rebindEvaMutation.error ||
                        pullEvaMutation.error ||
                        createEvaChangeMutation.error ||
                        evaUpdateQuery.error
                      }
                      returnTo={`${Routes.BusinessDocuments}/${documentId}`}
                      onRetry={() => {
                        rebindEvaMutation.reset();
                        pullEvaMutation.reset();
                        createEvaChangeMutation.reset();
                        if (hasPersonalEvaToken) void evaUpdateQuery.refetch();
                      }}
                    />
                  </div>
                </div>
              )}
            </PopoverContent>
          </Popover>
          {!!document.current_revision && (
            <Button
              size="sm"
              variant={historyOpen ? 'secondary' : 'ghost'}
              onClick={() => {
                setHistoryOpen((open) => !open);
                setSelectedRevisionId(undefined);
                setComparisonRevisionId(undefined);
              }}
              data-testid="business-document-history-toggle"
            >
              <FileClock className="size-4" />
              История документа
            </Button>
          )}
          {allowed.has('REQUEST_INTAKE_ASSESSMENT') && (
            <Button
              size="sm"
              variant="outline"
              disabled={isBusy}
              onClick={() => runDocumentCommand('REQUEST_INTAKE_ASSESSMENT')}
            >
              <Sparkles className="size-4" />
              Проанализировать вводные
            </Button>
          )}
          {allowed.has('REQUEST_DRAFT') && (
            <Button
              size="sm"
              variant="accent"
              disabled={isBusy}
              onClick={() => runDocumentCommand('REQUEST_DRAFT')}
            >
              <FilePlus2 className="size-4" />
              Создать черновик
            </Button>
          )}
          {allowed.has('START_REVIEW') && (
            <Button
              size="sm"
              variant="outline"
              disabled={isBusy}
              onClick={() => runDocumentCommand('START_REVIEW')}
            >
              <RotateCcw className="size-4" />
              Начать вносить изменения
            </Button>
          )}

          {allowed.has('REQUEST_EXPORT') && (
            <>
              <Button
                size="sm"
                variant="outline"
                disabled={isBusy}
                onClick={() =>
                  runDocumentCommand('REQUEST_EXPORT', {
                    revision_id: document.current_revision?.revision_id,
                    format: 'MARKDOWN',
                  })
                }
              >
                <Download className="size-4" />
                {historyOpen
                  ? `Markdown · текущая редакция ${document.current_revision?.revision_number}`
                  : 'Создать Markdown'}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={isBusy}
                onClick={() =>
                  runDocumentCommand('REQUEST_EXPORT', {
                    revision_id: document.current_revision?.revision_id,
                    format: 'EVA_WIKI',
                  })
                }
              >
                <Send className="size-4" />
                {historyOpen
                  ? `HTML для EvaWiki · текущая редакция ${document.current_revision?.revision_number}`
                  : 'Создать HTML для EvaWiki'}
              </Button>
            </>
          )}
          {(allowed.has('APPLY_CHANGES') ||
            (document.change_preview &&
              allowed.has('CONFIRM_PREPARED_CHANGES'))) && (
            <Button
              size="sm"
              disabled={isBusy}
              loading={commandMutation.isPending}
              data-testid="apply-changes-button"
              onClick={() =>
                document.change_preview &&
                allowed.has('CONFIRM_PREPARED_CHANGES')
                  ? runDocumentCommand('CONFIRM_PREPARED_CHANGES', {
                      job_id: document.change_preview.job_id,
                    })
                  : runDocumentCommand('APPLY_CHANGES')
              }
            >
              <CheckCircle2 className="size-4" />
              Применить исправления
            </Button>
          )}
          {document.permissions?.delete && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-state-error"
                  disabled={isBusy || deleteMutation.isPending}
                  data-testid="business-document-delete-detail"
                >
                  <Trash2 className="size-4" />
                  Удалить
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Удалить документ?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Документ «{document.title}», его история, задания и файлы
                    экспорта будут удалены без возможности восстановления.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Отмена</AlertDialogCancel>
                  <AlertDialogAction
                    className="bg-state-error text-white hover:bg-state-error/90"
                    onClick={() => deleteMutation.mutate()}
                  >
                    Удалить
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </header>

      <div aria-live="polite">
        {deleteMutation.error && (
          <div className="border-b border-state-error/40 bg-state-error/10 px-5 py-2 text-sm text-state-error">
            {deleteMutation.error.message}
          </div>
        )}
        {hasConflict && (
          <div
            className="flex flex-wrap items-center gap-3 border-b border-state-warning/40 bg-state-warning/5 px-5 py-2.5 text-sm"
            data-testid="business-document-conflict"
            role="alert"
          >
            <AlertTriangle className="size-4 shrink-0 text-state-warning" />
            <span className="min-w-0 flex-1">
              Документ изменился в другой вкладке. Состояние обновлено,
              проверьте решение ещё раз.
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setHasConflict(false);
                commandMutation.reset();
                void documentQuery.refetch();
              }}
            >
              Обновить
            </Button>
          </div>
        )}
        {commandMutation.error && !hasConflict && (
          <div
            className="flex items-center gap-3 border-b border-state-error/40 bg-state-error/5 px-5 py-2.5 text-sm text-state-error"
            data-testid="business-document-command-error"
            role="alert"
          >
            <AlertTriangle className="size-4 shrink-0" />
            <span className="min-w-0 flex-1 break-words">
              {commandMutation.error.message}
            </span>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label="Закрыть сообщение об ошибке"
              onClick={() => commandMutation.reset()}
            >
              <X className="size-4" />
            </Button>
          </div>
        )}
        {streamJobId && streamError && (
          <div
            className="border-b border-state-error/30 bg-state-error/5 px-5 py-2 text-xs text-state-error"
            role="alert"
          >
            Не удалось получить предварительные изменения: {streamError}
          </div>
        )}
        {document.change_preview && changePreviewQuery.error && (
          <div
            className="border-b border-state-error/30 px-5 py-2 text-xs text-state-error"
            role="alert"
          >
            Не удалось загрузить предпросмотр:{' '}
            {changePreviewQuery.error.message}
          </div>
        )}
        {document.operation_state === 'FAILED' &&
          document.last_error &&
          dismissedOperationError !== operationErrorKey && (
            <div
              className="flex items-start gap-2 border-b border-state-error/30 bg-state-error/5 px-5 py-2 text-xs text-state-error"
              data-testid="business-document-operation-error"
              role="alert"
            >
              <Archive className="mt-1.5 size-3.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <BusinessDocumentError error={document.last_error} />
              </div>
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                aria-label="Закрыть сообщение об ошибке"
                onClick={() => setDismissedOperationError(operationErrorKey)}
              >
                <X className="size-4" />
              </Button>
            </div>
          )}
        {!!visibleExports.length && (
          <div
            className="flex flex-wrap items-center gap-2 border-b border-border-button bg-bg-card/40 px-5 py-2 text-xs"
            data-testid="business-document-exports"
          >
            <span className="me-1 text-text-secondary">Готовые файлы:</span>
            {visibleExports.map((artifact) => (
              <a
                key={artifact.artifact_id}
                href={api.businessDocumentExportDownload(
                  document.document_id,
                  artifact.artifact_id,
                )}
                download={artifact.filename}
                onClick={(event) =>
                  void downloadExport(
                    event,
                    artifact.artifact_id,
                    artifact.filename,
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-md border border-border-button bg-bg-base px-2.5 py-1.5 font-medium text-text-primary transition-colors hover:border-accent-primary hover:text-accent-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-primary"
              >
                <Download className="size-3.5" />
                {exportLabels[artifact.format]}
                <span className="font-normal text-text-secondary">
                  r{artifact.revision_number ?? '—'}
                </span>
              </a>
            ))}
          </div>
        )}
      </div>

      <div
        className="grid min-h-0 grid-cols-[minmax(0,1fr)_8px_var(--business-document-protocol-width)] grid-rows-[auto_minmax(0,1fr)] max-lg:grid-cols-1 max-lg:grid-rows-[auto_minmax(360px,1fr)_minmax(520px,0.8fr)] max-lg:overflow-y-auto scrollbar-auto"
        data-testid="business-document-panes"
        style={
          {
            '--business-document-protocol-width': `${protocolPaneWidth}px`,
          } as CSSProperties
        }
      >
        <div className="row-span-2 flex min-h-0 flex-col max-lg:row-start-2 max-lg:row-span-1">
          {documentWarnings.length > 0 && (
            <details
              className="shrink-0 border-b border-state-warning/30 bg-state-warning/5 px-5 py-3 text-sm"
              data-testid="business-document-warnings"
              open
            >
              <summary className="cursor-pointer font-medium text-text-primary">
                {document.current_revision
                  ? 'Документ сохранён с замечаниями'
                  : 'Замечания к подготовке документа'}{' '}
                ({documentWarnings.length})
              </summary>
              <ul className="mt-2 max-h-40 list-disc space-y-1 overflow-y-auto ps-5 text-xs text-text-secondary">
                {documentWarnings.map((warning, index) => (
                  <li key={`${warning.code}-${warning.section_id}-${index}`}>
                    <DocumentMarkdown>
                      {`${warning.section_id ? `Раздел ${warning.section_id}: ` : ''}${warning.message}`}
                    </DocumentMarkdown>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {inlineChangePreview && (
            <ChangePreviewPanel
              preliminary={!document.change_preview}
              preview={inlineChangePreview}
              pending={isBusy}
              canDiscard={allowed.has('DISCARD_PREPARED_CHANGES')}
              onFocusSource={focusSource}
              onDiscard={() =>
                runDocumentCommand('DISCARD_PREPARED_CHANGES', {
                  job_id: inlineChangePreview.job_id,
                })
              }
            />
          )}
          <DocumentPane
            key={documentId}
            revision={displayedRevision}
            historyMode={historyOpen}
            previousRevision={comparisonRevision}
            changePreview={historyOpen ? null : inlineChangePreview}
            preliminary={!document.change_preview}
            onFocusSource={focusSource}
            highlightedSectionIds={documentHighlightedSectionIds}
            savedSectionIds={
              !historyOpen &&
              document.last_application &&
              document.last_application.revision_id === currentRevisionId
                ? document.last_application.section_ids
                : []
            }
            focusRequest={sectionFocus}
            onSelectionChange={(nextSelection) => {
              if (!historyOpen) setSelection(nextSelection);
            }}
          />
        </div>
        <ResizeHandle
          direction="width"
          label="Изменить ширину обсуждения"
          testId="business-document-protocol-resizer"
          value={protocolPaneWidth}
          min={minProtocolPaneWidth}
          max={maxProtocolPaneWidth}
          onChange={resizeProtocolPane}
          onReset={() => resizeProtocolPane(defaultProtocolPaneWidth)}
        />
        <div
          className="col-start-3 row-start-1 min-w-0 lg:max-h-64 lg:overflow-y-auto max-lg:col-start-1 scrollbar-auto"
          data-testid="business-document-activity"
        >
          {isBusinessDocumentOperationActive(document.operation_state) && (
            <BusinessDocumentProgress
              job={document.latest_job}
              operationState={document.operation_state}
              operationLabel={operationLabels[document.operation_state]}
            />
          )}
          <BackgroundWork document={document} onFocusSection={focusSection} />
        </div>
        <div className="col-start-3 row-start-2 grid min-h-0 max-lg:col-start-1 max-lg:row-start-3">
          {historyOpen ? (
            <RevisionHistoryPanel
              revisions={revisionsQuery.data ?? []}
              selectedRevisionId={
                selectedRevisionId ?? document.current_revision?.revision_id
              }
              currentRevisionId={document.current_revision?.revision_id}
              comparisonRevision={comparisonRevision}
              onCompare={(revision) =>
                setComparisonRevisionId(revision.revision_id)
              }
              loading={revisionsQuery.isLoading}
              error={revisionsQuery.error}
              canRestore={allowed.has('RESTORE_REVISION')}
              pending={isBusy}
              onRestore={(revision) =>
                runDocumentCommand('RESTORE_REVISION', {
                  revision_id: revision.revision_id,
                })
              }
              onSelect={(revision) => {
                setSelectedRevisionId(revision.revision_id);
                clearSelection();
              }}
              onClose={() => {
                setHistoryOpen(false);
                setSelectedRevisionId(undefined);
                setComparisonRevisionId(undefined);
              }}
            />
          ) : (
            <ProtocolPane
              documentId={document.document_id}
              storageIdentity={promptStorageIdentity}
              reviewCycle={document.protocol}
              previousCycles={document.protocol_history}
              onAnalyze={
                allowed.has('REQUEST_REVIEW_ASSESSMENT')
                  ? () => runDocumentCommand('REQUEST_REVIEW_ASSESSMENT')
                  : undefined
              }
              proposalDecisionsOpen={document.lifecycle_state === 'REVIEW'}
              revision={document.current_revision}
              selection={selection}
              allowedCommands={[...allowed]}
              pending={commandMutation.isPending}
              editable={
                Boolean(promptStorageIdentity) &&
                document.permissions?.edit !== false &&
                (allowed.has('ADD_COMMENT') || isBusy)
              }
              onCommand={submitCommand}
              onClearSelection={clearSelection}
              focusRequest={sourceFocus}
            />
          )}
        </div>
      </div>
    </main>
  );
}
