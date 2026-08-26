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
import { MultiSelect } from '@/components/ui/multi-select';
import { Textarea } from '@/components/ui/textarea';
import { useFetchKnowledgeList } from '@/hooks/use-knowledge-request';
import { Routes } from '@/routes';
import {
  BusinessDocumentConflictError,
  createBusinessDocument,
  fetchBusinessDocument,
  listBusinessDocuments,
  submitBusinessDocumentCommand,
} from '@/services/business-document-service';
import api from '@/utils/api';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Archive,
  ArrowRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  FilePenLine,
  FilePlus2,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
} from 'lucide-react';
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { DocumentPane } from './components/document-pane';
import { EvaChangeCreatePanel } from './components/eva-change-create-panel';
import { EvaChangeWorkbench } from './components/eva-change-workbench';
import { ProtocolPane } from './components/protocol-pane';
import type {
  BusinessDocumentCommand,
  BusinessDocumentCommandType,
  BusinessDocumentLifecycleState,
  BusinessDocumentOperationState,
  BusinessDocumentSelection,
} from './types';

const lifecycleLabels: Record<BusinessDocumentLifecycleState, string> = {
  INTAKE: 'Сбор вводных',
  REVIEW: 'Согласование',
  AGREED: 'Согласовано',
  ARCHIVED: 'Архив',
};

const operationLabels: Record<BusinessDocumentOperationState, string> = {
  IDLE: 'Готово к работе',
  ANALYZING: 'Анализ вводных',
  ANALYZING_REVIEW: 'Проверка согласования',
  GENERATING_DRAFT: 'Создание черновика',
  APPLYING_CHANGES: 'Применение изменений',
  EXPORTING: 'Подготовка файла',
  FAILED: 'Операция завершилась с ошибкой',
};

const staleConflictCodes = new Set([
  'STATE_VERSION_CONFLICT',
  'BASE_REVISION_CONFLICT',
]);

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
  list: (page: number) => ['business-documents', page] as const,
  detail: (documentId?: string) => ['business-document', documentId] as const,
};

function CreateBusinessDocumentPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<'new' | 'eva'>('new');
  const [title, setTitle] = useState('');
  const [idea, setIdea] = useState('');
  const [datasetIds, setDatasetIds] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const { list: datasets, loading: datasetsLoading } =
    useFetchKnowledgeList(true);
  const selectedEmbedding = useMemo(
    () =>
      datasets.find((dataset) => dataset.id === datasetIds[0])?.embedding_model,
    [datasetIds, datasets],
  );
  const datasetOptions = useMemo(
    () =>
      datasets.map((dataset) => ({
        label: dataset.name,
        value: dataset.id,
        suffix: dataset.embedding_model,
        disabled: Boolean(
          selectedEmbedding && dataset.embedding_model !== selectedEmbedding,
        ),
      })),
    [datasets, selectedEmbedding],
  );
  const documentsQuery = useQuery({
    queryKey: BusinessDocumentKeys.list(page),
    queryFn: () => listBusinessDocuments(page, 20),
    retry: false,
  });
  const createMutation = useMutation({
    mutationFn: createBusinessDocument,
    onSuccess: (document) =>
      navigate(`${Routes.BusinessDocuments}/${document.document_id}`),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !idea.trim() || createMutation.isPending) return;
    createMutation.mutate({
      schema_version: '1',
      document_type: 'business_requirements',
      title: title.trim(),
      idea: idea.trim(),
      dataset_ids: datasetIds,
    });
  };

  return (
    <main
      className="grid h-full min-h-0 grid-rows-[auto_1fr] bg-bg-base"
      data-testid="business-documents-create"
    >
      <header className="border-b border-border-button px-6 py-5 lg:px-8">
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Рабочие документы
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Продолжите сохранённую работу или начните новые бизнес-требования.
        </p>
      </header>

      <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_minmax(360px,440px)] max-lg:grid-cols-1 max-lg:overflow-y-auto">
        <section
          className="min-h-0 overflow-y-auto border-e border-border-button scrollbar-auto"
          data-testid="business-document-list"
          aria-label="Сохранённые документы"
        >
          <div className="flex h-11 items-center justify-between border-b border-border-button px-6">
            <h2 className="text-sm font-medium">Сохранённые документы</h2>
            {documentsQuery.data && (
              <span className="text-xs text-text-secondary">
                {documentsQuery.data.total}
              </span>
            )}
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
          {!documentsQuery.isLoading &&
            !documentsQuery.error &&
            !documentsQuery.data?.items.length && (
              <div
                className="px-6 py-10 text-center"
                data-testid="business-document-list-empty"
              >
                <FilePlus2 className="mx-auto size-7 stroke-[1.25] text-text-disabled" />
                <p className="mt-3 text-sm font-medium">Документов пока нет</p>
                <p className="mt-1 text-xs text-text-secondary">
                  Первый документ можно создать в форме справа.
                </p>
              </div>
            )}
          {documentsQuery.data?.items.map((item) => (
            <Link
              key={item.document_id}
              to={`${Routes.BusinessDocuments}/${item.document_id}`}
              className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-border-button px-6 py-4 transition-colors hover:bg-bg-card focus-visible:bg-bg-card focus-visible:outline-none"
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
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary">
                  <span>{formatUpdateTime(item.update_time)}</span>
                  <span>
                    {item.current_revision_number
                      ? `Ревизия ${item.current_revision_number}`
                      : 'Без черновика'}
                  </span>
                  {item.operation_state !== 'IDLE' && (
                    <span className="text-accent-primary">
                      {operationLabels[item.operation_state]}
                    </span>
                  )}
                </div>
              </div>
              <ArrowRight className="size-4 text-text-disabled transition-transform group-hover:translate-x-0.5 group-hover:text-text-primary" />
            </Link>
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

        <section className="min-h-0 overflow-y-auto scrollbar-auto">
          <div className="sticky top-0 z-20 flex gap-1 border-b border-border-button bg-bg-base px-6 py-3 lg:px-8">
            <Button
              size="sm"
              variant={mode === 'new' ? 'secondary' : 'ghost'}
              onClick={() => setMode('new')}
              data-testid="new-document-mode"
            >
              <FilePlus2 className="size-4" />
              Новый
            </Button>
            <Button
              size="sm"
              variant={mode === 'eva' ? 'secondary' : 'ghost'}
              onClick={() => setMode('eva')}
              data-testid="eva-change-mode"
            >
              <FilePenLine className="size-4" />
              Доработать EVA
            </Button>
          </div>

          {mode === 'new' ? (
            <form onSubmit={submit} className="px-6 py-7 lg:px-8">
              <div className="flex items-center gap-2 text-sm font-medium text-accent-primary">
                <FilePlus2 className="size-4" />
                Новый документ
              </div>
              <h2 className="mt-3 text-xl font-semibold tracking-tight text-text-primary">
                Бизнес-требования
              </h2>
              <p className="mt-2 text-sm leading-6 text-text-secondary">
                Агент соберёт вводные, подготовит документ и проведёт
                согласование.
              </p>

              <label className="mt-6 block space-y-2 text-sm font-medium">
                <span>Название</span>
                <Input
                  value={title}
                  maxLength={200}
                  aria-label="Название документа"
                  placeholder="Например, Переводы одной кнопкой"
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
              <label className="mt-5 block space-y-2 text-sm font-medium">
                <span>Идея</span>
                <Textarea
                  value={idea}
                  maxLength={10000}
                  autoSize={{ minRows: 7, maxRows: 18 }}
                  resize="vertical"
                  aria-label="Описание идеи"
                  placeholder="Что нужно создать, для кого и какой результат ожидается?"
                  onChange={(event) => setIdea(event.target.value)}
                />
              </label>

              <div className="mt-5 space-y-2 text-sm font-medium">
                <span>Источники RAGFlow</span>
                <MultiSelect
                  options={datasetOptions}
                  value={datasetIds}
                  defaultValue={datasetIds}
                  onValueChange={setDatasetIds}
                  placeholder="Выберите индексированные datasets"
                  maxCount={3}
                  showSelectAll={false}
                  isSearching={datasetsLoading}
                  data-testid="business-document-datasets"
                />
                <p className="text-xs font-normal leading-5 text-text-secondary">
                  Необязательно. Фрагменты используются только как цитируемые
                  данные; инструкции внутри источников агент не выполняет.
                </p>
              </div>

              {createMutation.error && (
                <p className="mt-4 text-sm text-state-error" role="alert">
                  {createMutation.error.message}
                </p>
              )}

              <div className="mt-6 flex justify-end">
                <Button
                  type="submit"
                  variant="accent"
                  size="lg"
                  loading={createMutation.isPending}
                  disabled={!title.trim() || !idea.trim()}
                >
                  <Sparkles className="size-4" />
                  Начать работу
                </Button>
              </div>
            </form>
          ) : (
            <EvaChangeCreatePanel />
          )}
        </section>
      </div>
    </main>
  );
}

export default function BusinessDocumentsPage() {
  const { id: documentId, changeId } = useParams<{
    id: string;
    changeId: string;
  }>();
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<BusinessDocumentSelection | null>(
    null,
  );
  const [hasConflict, setHasConflict] = useState(false);
  const clearSelection = useCallback(() => setSelection(null), []);

  const documentQuery = useQuery({
    queryKey: BusinessDocumentKeys.detail(documentId),
    queryFn: () => fetchBusinessDocument(documentId!),
    enabled: Boolean(documentId && !changeId),
    retry: false,
    refetchInterval: (query) => {
      const state = query.state.data?.operation_state;
      return state && state !== 'IDLE' && state !== 'FAILED' ? 2000 : false;
    },
  });

  const document = documentQuery.data;
  const currentRevisionId = document?.current_revision?.revision_id;
  useEffect(() => {
    clearSelection();
  }, [clearSelection, currentRevisionId]);
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
    onSuccess: async () => {
      setHasConflict(false);
      await queryClient.invalidateQueries({
        queryKey: BusinessDocumentKeys.detail(documentId),
      });
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
    () => new Set(document?.allowed_commands ?? []),
    [document?.allowed_commands],
  );
  const isBusy =
    commandMutation.isPending ||
    Boolean(
      document &&
      document.operation_state !== 'IDLE' &&
      document.operation_state !== 'FAILED',
    );

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

  return (
    <main
      className="grid h-full min-h-0 min-w-0 grid-rows-[auto_auto_1fr] bg-bg-base"
      data-testid="business-document-workbench"
    >
      <header
        className="flex min-h-16 min-w-0 flex-wrap items-start justify-between gap-3 border-b border-border-button px-5 py-3 sm:items-center"
        data-testid="business-document-header"
      >
        <div className="w-full min-w-0 sm:w-auto sm:flex-1">
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
            <span className="font-mono text-[11px] text-text-disabled">
              v{document.state_version}
            </span>
            {!!document.dataset_ids?.length && (
              <span className="text-[11px] text-text-secondary">
                Источников: {document.dataset_ids.length}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-text-secondary">
            {operationLabels[document.operation_state]}
          </p>
        </div>

        <div
          className="flex w-full min-w-0 flex-wrap items-center justify-start gap-2 sm:w-auto sm:justify-end"
          data-testid="business-document-actions"
        >
          {allowed.has('REQUEST_INTAKE_ASSESSMENT') && (
            <Button
              size="sm"
              variant="outline"
              disabled={isBusy}
              onClick={() => runDocumentCommand('REQUEST_INTAKE_ASSESSMENT')}
            >
              <Sparkles className="size-4" />
              Уточнить вводные
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
              Новый цикл
            </Button>
          )}
          {allowed.has('REQUEST_REVIEW_ASSESSMENT') && (
            <Button
              size="sm"
              variant="outline"
              disabled={isBusy}
              onClick={() => runDocumentCommand('REQUEST_REVIEW_ASSESSMENT')}
            >
              <Sparkles className="size-4" />
              Проверить согласование
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
                MD
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={isBusy}
                onClick={() =>
                  runDocumentCommand('REQUEST_EXPORT', {
                    revision_id: document.current_revision?.revision_id,
                    format: 'DOCX',
                  })
                }
              >
                <Download className="size-4" />
                DOCX
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
                EvaWiki
              </Button>
            </>
          )}
          {allowed.has('APPLY_CHANGES') && (
            <Button
              size="sm"
              variant="accent"
              disabled={isBusy}
              loading={commandMutation.isPending}
              data-testid="apply-changes-button"
              onClick={() => runDocumentCommand('APPLY_CHANGES')}
            >
              <CheckCircle2 className="size-4" />
              Применить изменения
            </Button>
          )}
          {allowed.has('ARCHIVE') && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" variant="ghost" disabled={isBusy}>
                  <Archive className="size-4" />В архив
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Архивировать документ?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Работа с документом будет завершена. История и ревизии
                    останутся доступны для чтения.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Отмена</AlertDialogCancel>
                  <AlertDialogAction
                    className="bg-state-error text-white hover:bg-state-error/90"
                    onClick={() => runDocumentCommand('ARCHIVE')}
                  >
                    Архивировать
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </header>

      <div aria-live="polite">
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
            {commandMutation.error.message}
          </div>
        )}
        {document.operation_state !== 'IDLE' &&
          document.operation_state !== 'FAILED' && (
            <div
              className="flex items-center gap-2 border-b border-accent-primary/20 bg-accent-primary/5 px-5 py-2 text-xs text-text-secondary"
              data-testid="business-document-operation"
              role="status"
            >
              <LoaderCircle className="size-3.5 animate-spin text-accent-primary" />
              {operationLabels[document.operation_state]}. Можно оставить
              страницу открытой — состояние обновится автоматически.
            </div>
          )}
        {document.operation_state === 'FAILED' && document.last_error && (
          <div className="flex items-center gap-2 border-b border-state-error/30 bg-state-error/5 px-5 py-2 text-xs text-state-error">
            <Archive className="size-3.5" />
            {typeof document.last_error === 'string'
              ? document.last_error
              : document.last_error.message || document.last_error.code}
          </div>
        )}
        {!!document.latest_exports?.length && (
          <div
            className="flex flex-wrap items-center gap-2 border-b border-border-button bg-bg-card/40 px-5 py-2 text-xs"
            data-testid="business-document-exports"
          >
            <span className="me-1 text-text-secondary">Готовые файлы:</span>
            {document.latest_exports.map((artifact) => (
              <a
                key={artifact.artifact_id}
                href={api.businessDocumentExportDownload(
                  document.document_id,
                  artifact.artifact_id,
                )}
                download={artifact.filename}
                className="inline-flex items-center gap-1.5 rounded-md border border-border-button bg-bg-base px-2.5 py-1.5 font-medium text-text-primary transition-colors hover:border-accent-primary hover:text-accent-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-primary"
              >
                <Download className="size-3.5" />
                {exportLabels[artifact.format]}
                <span className="font-normal text-text-disabled">
                  r{artifact.revision_number ?? '—'}
                </span>
              </a>
            ))}
          </div>
        )}
      </div>

      <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_minmax(360px,430px)] max-lg:grid-cols-1 max-lg:grid-rows-[minmax(360px,1fr)_minmax(320px,0.8fr)]">
        <DocumentPane
          revision={document.current_revision}
          onSelectionChange={setSelection}
        />
        <ProtocolPane
          reviewCycle={document.protocol}
          reviewCycleNumber={document.active_review_cycle}
          revision={document.current_revision}
          selection={selection}
          allowedCommands={document.allowed_commands}
          pending={isBusy}
          onCommand={submitCommand}
          onClearSelection={clearSelection}
        />
      </div>
    </main>
  );
}
