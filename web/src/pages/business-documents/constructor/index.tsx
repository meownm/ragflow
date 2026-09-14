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
import {
  useFetchTenantInfo,
  useFetchUserInfo,
} from '@/hooks/use-user-setting-request';
import { Routes } from '@/routes';
import { getBusinessDocumentCapabilities } from '@/services/business-document-service';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  Download,
  FileJson2,
  FlaskConical,
  LoaderCircle,
  Plus,
  RotateCcw,
  Upload,
} from 'lucide-react';
import { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import {
  createDocumentConstructorStorageKey,
  loadStoredTemplate,
  saveStoredTemplate,
  type DocumentConstructorStorageScope,
} from './draft-storage';
import { ExecutionRegistryDialog } from './execution-registry-dialog';
import {
  addChildSection,
  addRootSection,
  deleteSectionTree,
  indentSection,
  maxSectionDepthForHeading,
  moveSection,
  numberSections,
  outdentSection,
  validateTemplate,
  type ConstructorSection,
  type DocumentTemplateDraft,
} from './model';
import { QuerySpecificationDialog } from './query-specification-dialog';
import { SchemaWorkspaceDialog } from './schema-workspace-dialog';
import { SectionInspector } from './section-inspector';
import { createSqlQueryTemplate } from './sql-query-template';
import { StructureEditor } from './structure-editor';
import { exportTemplate, importTemplate } from './template-codec';
import { TemplatePreview } from './template-preview';
import { TemplateSettings } from './template-settings';

type SaveState = 'saving' | 'saved' | 'error';

function browserStorage() {
  try {
    return typeof window === 'undefined' ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}

function downloadedFilename(documentType: string, version: string) {
  const type = documentType.replace(/[^a-z0-9_-]+/gi, '-') || 'template';
  const normalizedVersion = version.replace(/[^a-z0-9._-]+/gi, '-') || 'draft';
  return `${type}-${normalizedVersion}.json`;
}

function descendantsFor(sections: ConstructorSection[], uid: string) {
  const index = sections.findIndex((section) => section.uid === uid);
  if (index < 0) return 0;
  const depth = sections[index].depth;
  let cursor = index + 1;
  while (cursor < sections.length && sections[cursor].depth > depth)
    cursor += 1;
  return cursor - index - 1;
}

export default function DocumentConstructorPage() {
  const importInputRef = useRef<HTMLInputElement>(null);
  const { data: userInfo, loading: userInfoLoading } = useFetchUserInfo();
  const { data: tenantInfo, loading: tenantInfoLoading } = useFetchTenantInfo();
  const actorId = userInfo.id?.trim();
  const accessQuery = useQuery({
    queryKey: ['business-document-capabilities', actorId],
    queryFn: getBusinessDocumentCapabilities,
    enabled: Boolean(actorId) && !userInfoLoading,
    retry: false,
  });
  const storageScope = useMemo<DocumentConstructorStorageScope | null>(() => {
    const userId = userInfo?.id?.trim();
    const tenantId = tenantInfo?.tenant_id?.trim();
    return userId && tenantId ? { userId, tenantId } : null;
  }, [tenantInfo?.tenant_id, userInfo?.id]);
  const storageKey = useMemo(
    () =>
      storageScope ? createDocumentConstructorStorageKey(storageScope) : null,
    [storageScope],
  );
  const [loadedStorageKey, setLoadedStorageKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<DocumentTemplateDraft>(() =>
    createSqlQueryTemplate(),
  );
  const [selectedUid, setSelectedUid] = useState<string | null>(
    draft.sections[0]?.uid ?? null,
  );
  const [mode, setMode] = useState<'structure' | 'preview'>('structure');
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [notice, setNotice] = useState<{
    kind: 'success' | 'warning' | 'error';
    message: string;
  } | null>(null);

  const numberedSections = useMemo(
    () => numberSections(draft.sections),
    [draft.sections],
  );
  const issues = useMemo(() => validateTemplate(draft), [draft]);
  const selectedSection =
    draft.sections.find((section) => section.uid === selectedUid) ?? null;
  const selectedNumberedSection = numberedSections.find(
    (section) => section.uid === selectedUid,
  );
  const selectedIssues = issues.filter(
    (issue) => issue.sectionUid === selectedUid,
  );

  useEffect(() => {
    if (
      accessQuery.data?.capabilities?.create !== true ||
      !storageScope ||
      !storageKey
    ) {
      return;
    }
    const storage = browserStorage();
    const storedDraft = loadStoredTemplate(storage, storageKey, storageScope);
    setDraft(storedDraft);
    setSelectedUid(storedDraft.sections[0]?.uid ?? null);
    setMode('structure');
    setNotice(null);
    setSaveState(storage ? 'saved' : 'error');
    setLoadedStorageKey(storageKey);
  }, [accessQuery.data?.capabilities?.create, storageKey, storageScope]);

  useEffect(() => {
    if (
      selectedUid &&
      !draft.sections.some((section) => section.uid === selectedUid)
    ) {
      setSelectedUid(draft.sections[0]?.uid ?? null);
    }
  }, [draft.sections, selectedUid]);

  useEffect(() => {
    if (
      !storageScope ||
      !storageKey ||
      loadedStorageKey !== storageKey ||
      accessQuery.data?.capabilities?.create !== true
    ) {
      return;
    }
    const storage = browserStorage();
    if (!storage) {
      setSaveState('error');
      return;
    }
    setSaveState('saving');
    const timeout = window.setTimeout(() => {
      try {
        saveStoredTemplate(storage, storageKey, storageScope, draft);
        setSaveState('saved');
      } catch {
        setSaveState('error');
      }
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [
    accessQuery.data?.capabilities?.create,
    draft,
    loadedStorageKey,
    storageKey,
    storageScope,
  ]);

  const storageIdentityPending = userInfoLoading || tenantInfoLoading;
  const canUseConstructor = accessQuery.data?.capabilities?.create === true;
  const isAdmin = accessQuery.data?.access_role === 'ADMIN';
  const draftIsReady = storageKey !== null && loadedStorageKey === storageKey;
  const gateIsLoading =
    accessQuery.isPending ||
    storageIdentityPending ||
    (canUseConstructor && !draftIsReady);

  if (gateIsLoading || !canUseConstructor || !storageScope) {
    const message = gateIsLoading
      ? 'Проверяем доступ и открываем ваш локальный черновик…'
      : accessQuery.isError
        ? 'Не удалось проверить право на использование конструктора.'
        : !canUseConstructor
          ? 'Конструктор доступен только пользователям с правом создания документов.'
          : 'Не удалось определить владельца локального черновика.';
    return (
      <main
        className="flex h-full min-h-0 items-center justify-center bg-bg-base p-6 text-text-primary"
        data-testid="document-constructor-page"
      >
        <div className="max-w-md text-center">
          {gateIsLoading ? (
            <LoaderCircle className="mx-auto size-6 animate-spin text-accent-primary" />
          ) : (
            <AlertTriangle className="mx-auto size-6 text-state-error" />
          )}
          <p
            className="mt-3 text-sm font-medium"
            data-testid={
              gateIsLoading ? undefined : 'document-constructor-access-denied'
            }
          >
            {message}
          </p>
          {!gateIsLoading && (
            <Button asChild className="mt-4" variant="outline">
              <Link to={Routes.BusinessDocuments}>Вернуться к документам</Link>
            </Button>
          )}
        </div>
      </main>
    );
  }

  const updateDraft = (patch: Partial<DocumentTemplateDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setNotice(null);
  };

  const setSections = (sections: ConstructorSection[]) => {
    updateDraft({ sections });
  };

  const updateSelectedSection = (patch: Partial<ConstructorSection>) => {
    if (!selectedUid) return;
    setSections(
      draft.sections.map((section) =>
        section.uid === selectedUid ? { ...section, ...patch } : section,
      ),
    );
  };

  const addRoot = () => {
    const next = addRootSection(draft.sections);
    setSections(next);
    setSelectedUid(next[next.length - 1].uid);
    setMode('structure');
  };

  const addChild = (uid: string) => {
    const existing = new Set(draft.sections.map((section) => section.uid));
    const next = addChildSection(
      draft.sections,
      uid,
      maxSectionDepthForHeading(draft.headingBaseLevel),
    );
    if (next === draft.sections) {
      setNotice({
        kind: 'warning',
        message: `Достигнут предел вложенности для H${draft.headingBaseLevel}.`,
      });
      return;
    }
    setSections(next);
    setSelectedUid(
      next.find((section) => !existing.has(section.uid))?.uid ?? uid,
    );
    setMode('structure');
  };

  const indent = (uid: string) => {
    const next = indentSection(
      draft.sections,
      uid,
      maxSectionDepthForHeading(draft.headingBaseLevel),
    );
    if (next === draft.sections) {
      setNotice({
        kind: 'warning',
        message:
          'Раздел нельзя вложить: нет предыдущего раздела того же уровня или достигнут предел глубины.',
      });
      return;
    }
    setSections(next);
  };

  const deleteSelected = () => {
    if (!selectedUid) return;
    const currentIndex = draft.sections.findIndex(
      (section) => section.uid === selectedUid,
    );
    const next = deleteSectionTree(draft.sections, selectedUid);
    setSections(next);
    setSelectedUid(
      next[Math.min(Math.max(0, currentIndex - 1), next.length - 1)]?.uid ??
        null,
    );
  };

  const resetDraft = () => {
    const next = createSqlQueryTemplate();
    setDraft(next);
    setSelectedUid(next.sections[0]?.uid ?? null);
    setMode('structure');
    setNotice({
      kind: 'success',
      message: 'Создан новый проект по пошаговому SQL-шаблону.',
    });
  };

  const downloadJson = () => {
    const validationIssues = validateTemplate(draft);
    if (validationIssues.length) {
      setNotice({
        kind: 'error',
        message: `Исправьте шаблон перед экспортом: ${validationIssues[0].message}`,
      });
      const targetUid = validationIssues.find(
        (issue) => issue.sectionUid,
      )?.sectionUid;
      if (targetUid) {
        setSelectedUid(targetUid);
        setMode('structure');
      }
      return;
    }
    const payload = JSON.stringify(exportTemplate(draft), null, 2);
    const url = URL.createObjectURL(
      new Blob([payload], { type: 'application/json;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = downloadedFilename(draft.documentType, draft.version);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    setNotice({
      kind: 'success',
      message: 'JSON проекта конструктора подготовлен.',
    });
  };

  const importJson = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
      const imported = importTemplate(JSON.parse(await file.text()));
      setDraft(imported);
      setSelectedUid(imported.sections[0]?.uid ?? null);
      setMode('structure');
      setNotice({
        kind: 'success',
        message: `Шаблон «${imported.name}» импортирован.`,
      });
    } catch (error) {
      setNotice({
        kind: 'error',
        message:
          error instanceof Error
            ? `Не удалось импортировать: ${error.message}`
            : 'Не удалось импортировать JSON.',
      });
    }
  };

  return (
    <main
      className="flex h-full min-h-0 flex-col bg-bg-base text-text-primary"
      data-testid="document-constructor-page"
    >
      <header className="shrink-0 border-b border-border-button bg-bg-base px-4 py-4 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">
                  Конструктор документов
                </h1>
                <Badge className="gap-1 border-accent-primary/30 bg-accent-primary/10 text-accent-primary">
                  <FlaskConical className="size-3" />
                  Эксперимент
                </Badge>
              </div>
              <p className="mt-1 text-xs text-text-secondary sm:text-sm">
                Локальный проект структуры и требований; публикация отключена
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="me-1 hidden items-center gap-1.5 text-xs text-text-secondary sm:flex">
              {saveState === 'saving' && (
                <LoaderCircle className="size-3.5 animate-spin" />
              )}
              {saveState === 'saved' && <Check className="size-3.5" />}
              {saveState === 'error' && (
                <AlertTriangle className="size-3.5 text-state-error" />
              )}
              {saveState === 'saving'
                ? 'Сохраняем'
                : saveState === 'saved'
                  ? 'Сохранено локально'
                  : 'Не удалось сохранить'}
            </span>
            {isAdmin && <ExecutionRegistryDialog />}
            <SchemaWorkspaceDialog scope={storageScope} />
            <QuerySpecificationDialog scope={storageScope} />
            <input
              ref={importInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={importJson}
              aria-label="Импортировать JSON проекта конструктора"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={() => importInputRef.current?.click()}
            >
              <Upload className="size-4" />
              <span className="hidden sm:inline">Импорт</span>
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" variant="outline">
                  <RotateCcw className="size-4" />
                  <span className="hidden sm:inline">Новый</span>
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    Создать проект из SQL-шаблона?
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    Текущий локальный черновик будет заменён. Сначала
                    экспортируйте JSON проекта, если хотите сохранить его
                    отдельно. Новый проект начнётся с пошаговой структуры для
                    проектирования SQL-запроса.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Отмена</AlertDialogCancel>
                  <AlertDialogAction onClick={resetDraft}>
                    Создать SQL-проект
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <Button
              size="sm"
              onClick={downloadJson}
              data-testid="document-constructor-export"
            >
              <Download className="size-4" />
              JSON
            </Button>
          </div>
        </div>

        {(notice || issues.length > 0) && (
          <div
            className={`mt-3 flex items-center justify-between gap-4 border-s-2 py-1 ps-3 text-xs ${
              notice?.kind === 'error'
                ? 'border-state-error text-state-error'
                : notice?.kind === 'success'
                  ? 'border-state-success text-state-success'
                  : 'border-state-warning text-state-warning'
            }`}
            role={notice?.kind === 'error' ? 'alert' : 'status'}
          >
            <span>
              {notice?.message ??
                `В шаблоне ${issues.length} ${
                  issues.length === 1 ? 'ошибка' : 'ошибки'
                } — экспорт временно недоступен.`}
            </span>
            {notice && (
              <button
                type="button"
                className="shrink-0 text-text-secondary hover:text-text-primary"
                onClick={() => setNotice(null)}
              >
                Скрыть
              </button>
            )}
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto xl:grid xl:grid-cols-[280px_minmax(420px,1fr)_380px] xl:overflow-hidden">
        <TemplateSettings draft={draft} onChange={updateDraft} />
        <StructureEditor
          sections={draft.sections}
          selectedUid={selectedUid}
          issues={issues}
          mode={mode}
          onModeChange={setMode}
          onSelect={setSelectedUid}
          onAddRoot={addRoot}
          onAddChild={addChild}
          onIndent={indent}
          onOutdent={(uid) => setSections(outdentSection(draft.sections, uid))}
          onMove={(uid, direction) =>
            setSections(moveSection(draft.sections, uid, direction))
          }
          renderPreview={() => <TemplatePreview draft={draft} />}
        />
        <SectionInspector
          section={selectedSection}
          resolvedId={selectedNumberedSection?.resolvedId}
          descendantCount={
            selectedUid ? descendantsFor(draft.sections, selectedUid) : 0
          }
          issues={selectedIssues}
          onChange={updateSelectedSection}
          onDelete={deleteSelected}
        />
      </div>

      <footer className="hidden h-8 shrink-0 items-center justify-between border-t border-border-button bg-bg-card/40 px-5 text-[11px] text-text-secondary xl:flex">
        <span className="flex items-center gap-1.5">
          <FileJson2 className="size-3.5" />
          Черновик хранится в этом браузере отдельно для пользователя и тенанта
        </span>
        <button
          type="button"
          className="flex items-center gap-1 hover:text-text-primary"
          onClick={addRoot}
        >
          <Plus className="size-3.5" />
          Добавить корневой раздел
        </button>
      </footer>
    </main>
  );
}
