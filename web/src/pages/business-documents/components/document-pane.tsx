import { DiagramCodeBlock } from '@/components/diagram-code-block';
import {
  AlertTriangle,
  ChevronRight,
  FileText,
  MousePointer2,
} from 'lucide-react';
import type { Nodes, Root } from 'mdast';
import { isValidElement, useEffect, useId, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type {
  BusinessDocumentChangePreview,
  BusinessDocumentRevision,
  BusinessDocumentSelection,
} from '../types';
import { DocumentSectionChange } from './change-preview-panel';
import { RevisionTextDiff } from './revision-text-diff';

interface DocumentPaneProps {
  revision: BusinessDocumentRevision | null;
  historyMode?: boolean;
  previousRevision?: BusinessDocumentRevision;
  onSelectionChange: (selection: BusinessDocumentSelection | null) => void;
  highlightedSectionIds?: string[];
  savedSectionIds?: string[];
  focusRequest?: { sectionId: string; sequence: number } | null;
  changePreview?: BusinessDocumentChangePreview | null;
  preliminary?: boolean;
  onFocusSource?: (
    kind: 'question' | 'proposal' | 'comment',
    id: string,
  ) => void;
}

const CONTEXT_WINDOW = 64;
const SECTION_SELECTOR = '[data-section-id][data-section-text]';

function isHighSurrogate(value: number) {
  return value >= 0xd800 && value <= 0xdbff;
}

function isLowSurrogate(value: number) {
  return value >= 0xdc00 && value <= 0xdfff;
}

function isUtf16Boundary(text: string, offset: number) {
  return !(
    offset > 0 &&
    offset < text.length &&
    isHighSurrogate(text.charCodeAt(offset - 1)) &&
    isLowSurrogate(text.charCodeAt(offset))
  );
}

function contextForSelection(
  text: string,
  startOffset: number,
  endOffset: number,
) {
  let prefixStart = Math.max(0, startOffset - CONTEXT_WINDOW);
  if (!isUtf16Boundary(text, prefixStart)) prefixStart += 1;

  let suffixEnd = Math.min(text.length, endOffset + CONTEXT_WINDOW);
  if (!isUtf16Boundary(text, suffixEnd)) suffixEnd -= 1;

  return {
    prefix: text.slice(prefixStart, startOffset),
    suffix: text.slice(endOffset, suffixEnd),
  };
}

function sectionForNode(node: Node | null) {
  const element =
    node instanceof Element ? node : (node?.parentElement ?? null);
  return element?.closest<HTMLElement>(SECTION_SELECTOR) ?? null;
}

interface NormalizedText {
  text: string;
  starts: number[];
  ends: number[];
}

// Preserve parser positions and display soft line breaks without splitting text nodes.
function remarkSelectionSource() {
  return (tree: Root) => {
    const visit = (node: Nodes) => {
      if (node.type === 'text' && node.position) {
        node.data = {
          ...node.data,
          hName: 'span',
          hProperties: {
            ...node.data?.hProperties,
            className: 'whitespace-pre-line',
            'data-source-start': node.position.start.offset,
            'data-source-end': node.position.end.offset,
          },
        };
      }
      if ('children' in node) node.children.forEach(visit);
    };
    visit(tree);
  };
}

function decodeSource(value: string, literal: boolean): NormalizedText {
  let text = '';
  const starts: number[] = [];
  const ends: number[] = [];
  let decoder: HTMLTextAreaElement | undefined;
  // Decode only Markdown escapes and character references, never raw HTML.
  const units = literal
    ? /[\s\S]/gu
    : /\\[!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~]|&(?:#[xX][\da-fA-F]+|#\d+|[a-zA-Z][a-zA-Z\d]+);|[\s\S]/gu;
  for (const match of value.matchAll(units)) {
    const raw = match[0];
    let decoded = raw;
    if (!literal && raw.startsWith('\\')) decoded = raw.slice(1);
    if (!literal && raw.startsWith('&') && raw.endsWith(';')) {
      decoder ??= document.createElement('textarea');
      decoder.innerHTML = raw;
      decoded = decoder.value;
    }
    text += decoded;
    for (let index = 0; index < decoded.length; index += 1) {
      starts.push(match.index! + (raw === decoded ? index : 0));
      ends.push(match.index! + (raw === decoded ? index + 1 : raw.length));
    }
  }
  return { text, starts, ends };
}

function projectRenderedSection(
  section: HTMLElement,
  sectionText: string,
): NormalizedText | null {
  const body = section.querySelector<HTMLElement>('[data-section-body]');
  if (!body) return null;

  let text = '';
  const starts: number[] = [];
  const ends: number[] = [];
  let sourceCursor = 0;
  let pendingWhitespace = false;
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const value = node.textContent ?? '';
    if (!value.trim()) {
      pendingWhitespace ||= value.length > 0;
      node = walker.nextNode();
      continue;
    }
    const element = node.parentElement;
    if (element?.closest('[data-diagram-kind]')) {
      // Rendered diagram labels/status are not canonical prose anchors.
      text += '\0';
      starts.push(sourceCursor);
      ends.push(sourceCursor);
      node = walker.nextNode();
      continue;
    }
    const source = element?.closest<HTMLElement>('[data-source-start]');
    if (!source) return null;
    const rangeStart = Number(source.dataset.sourceStart);
    const rangeEnd = Number(source.dataset.sourceEnd);
    const decoded = decodeSource(
      sectionText.slice(rangeStart, rangeEnd),
      source?.dataset.sourceLiteral === 'true',
    );
    let decodedCursor = 0;
    let renderedCursor = 0;
    for (const match of value.matchAll(/\S+/gu)) {
      const token = match[0];
      const tokenStart = decoded.text.indexOf(token, decodedCursor);
      if (tokenStart < 0) return null;
      const sourceStart = rangeStart + decoded.starts[tokenStart];
      if (sourceStart < sourceCursor) return null;
      if (
        text &&
        (pendingWhitespace ||
          /\s/u.test(value.slice(renderedCursor, match.index)) ||
          /\s/u.test(sectionText.slice(sourceCursor, sourceStart)))
      ) {
        text += ' ';
        starts.push(sourceCursor);
        ends.push(sourceStart);
      }
      for (let index = 0; index < token.length; index += 1) {
        text += token[index];
        starts.push(rangeStart + decoded.starts[tokenStart + index]);
        ends.push(rangeStart + decoded.ends[tokenStart + index]);
      }
      decodedCursor = tokenStart + token.length;
      sourceCursor = rangeStart + decoded.ends[decodedCursor - 1];
      renderedCursor = match.index! + token.length;
      pendingWhitespace = false;
    }
    pendingWhitespace ||= /\s/u.test(value.slice(renderedCursor));
    node = walker.nextNode();
  }
  return { text, starts, ends };
}

function findUniqueOffset(
  section: HTMLElement,
  sectionText: string,
  selectedText: string,
) {
  const canonical = projectRenderedSection(section, sectionText);
  if (!canonical) return { error: 'NOT_EXACT' as const };
  const selected = selectedText.replace(/\s+/gu, ' ').trim();
  const normalizedStart = canonical.text.indexOf(selected);
  if (!selected || normalizedStart < 0) return { error: 'NOT_EXACT' as const };
  if (canonical.text.indexOf(selected, normalizedStart + 1) >= 0) {
    return { error: 'AMBIGUOUS' as const };
  }
  const normalizedEnd = normalizedStart + selected.length - 1;
  return {
    startOffset: canonical.starts[normalizedStart],
    endOffset: canonical.ends[normalizedEnd],
  };
}

export function DocumentPane({
  revision,
  onSelectionChange,
  highlightedSectionIds = [],
  savedSectionIds = [],
  focusRequest,
  changePreview,
  preliminary = false,
  onFocusSource,
  historyMode = false,
  previousRevision,
}: DocumentPaneProps) {
  const paneRef = useRef<HTMLDivElement>(null);
  const contentId = useId();
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(
    () => new Set(),
  );
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [focusedSectionId, setFocusedSectionId] = useState<string | null>(null);
  const changes = new Map(
    changePreview?.sections.map((change) => [change.section_id, change]),
  );
  const sections = [...(revision?.document_ast.sections ?? [])];
  const sectionIds = new Set(sections.map((section) => section.id));
  const addedSections = (changePreview?.sections ?? []).filter(
    (change) => !sectionIds.has(change.section_id),
  );
  if (addedSections.length) {
    sections.push(
      ...addedSections.map((change) => ({
        id: change.section_id,
        title: change.title,
        blocks: [],
      })),
    );
    sections.sort((a, b) => a.id.localeCompare(b.id, 'en', { numeric: true }));
  }

  useEffect(() => setSelectionError(null), [revision?.revision_id]);
  useEffect(() => {
    if (!focusRequest) return;
    const section = [
      ...(paneRef.current?.querySelectorAll<HTMLElement>(SECTION_SELECTOR) ??
        []),
    ].find(
      (candidate) => candidate.dataset.sectionId === focusRequest.sectionId,
    );
    if (!section) return;
    setCollapsedSections((previous) => {
      if (!previous.has(focusRequest.sectionId)) return previous;
      const next = new Set(previous);
      next.delete(focusRequest.sectionId);
      return next;
    });
    const frame = window.requestAnimationFrame(() =>
      section.scrollIntoView?.({ behavior: 'smooth', block: 'center' }),
    );
    setFocusedSectionId(focusRequest.sectionId);
    const timer = window.setTimeout(() => setFocusedSectionId(null), 2500);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [focusRequest]);

  const rejectSelection = (message: string) => {
    onSelectionChange(null);
    setSelectionError(message);
  };

  const captureSelection = () => {
    const browserSelection = window.getSelection();
    const range = browserSelection?.rangeCount
      ? browserSelection.getRangeAt(0)
      : null;
    const selectedText = browserSelection?.toString() ?? '';

    if (!range || !selectedText.trim()) {
      onSelectionChange(null);
      setSelectionError(null);
      return;
    }

    const pane = paneRef.current;
    const startSection = sectionForNode(range.startContainer);
    const endSection = sectionForNode(range.endContainer);
    if (
      !pane?.contains(range.commonAncestorContainer) ||
      !startSection ||
      !endSection ||
      startSection !== endSection
    ) {
      rejectSelection('Выделите фрагмент внутри одного раздела.');
      return;
    }

    const sectionId = startSection.dataset.sectionId;
    const sectionText = startSection.dataset.sectionText;
    if (!sectionId || sectionText === undefined) {
      rejectSelection('Не удалось определить раздел для комментария.');
      return;
    }
    if (startSection.dataset.sectionPreview === 'true') {
      rejectSelection(
        'Комментарий к этому фрагменту можно привязать после применения изменений.',
      );
      return;
    }

    const match = findUniqueOffset(startSection, sectionText, selectedText);
    if ('error' in match) {
      rejectSelection(
        match.error === 'AMBIGUOUS'
          ? 'Фрагмент повторяется в разделе. Выделите более длинный текст.'
          : 'Выделение не совпадает с каноническим текстом раздела.',
      );
      return;
    }

    const endOffset = match.endOffset;
    if (
      !isUtf16Boundary(sectionText, match.startOffset) ||
      !isUtf16Boundary(sectionText, endOffset)
    ) {
      rejectSelection(
        'Выделение разрывает составной символ. Выделите его целиком.',
      );
      return;
    }
    const context = contextForSelection(
      sectionText,
      match.startOffset,
      endOffset,
    );
    const canonicalSelectedText = sectionText.slice(
      match.startOffset,
      endOffset,
    );
    onSelectionChange({
      revision_id: revision!.revision_id,
      section_id: sectionId,
      selected_text: canonicalSelectedText,
      prefix: context.prefix,
      suffix: context.suffix,
      start_offset: match.startOffset,
      end_offset: endOffset,
    });
    setSelectionError(null);
  };

  return (
    <section
      className="flex min-h-0 flex-1 flex-col bg-bg-base"
      data-testid="business-document-pane"
      aria-label="Документ"
    >
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-border-button px-5">
        <div className="flex items-center gap-2 text-sm font-medium text-text-primary">
          <FileText className="size-4 text-text-secondary" />
          {historyMode ? 'Просмотр истории' : 'Документ'}
        </div>
        {revision && (
          <span className="font-mono text-xs text-text-secondary">
            Редакция {revision.revision_number}
          </span>
        )}
      </div>

      {!revision ? (
        <div
          className="flex min-h-0 flex-1 items-center justify-center px-8"
          data-testid="business-document-empty"
        >
          <div className="max-w-sm text-center">
            <FileText className="mx-auto size-8 stroke-[1.25] text-text-disabled" />
            <h2 className="mt-4 text-base font-medium text-text-primary">
              Черновик ещё не создан
            </h2>
            <p className="mt-2 text-sm leading-6 text-text-secondary">
              Ответьте на вопросы в обсуждении. Когда вводных будет достаточно,
              агент подготовит первую редакцию.
            </p>
          </div>
        </div>
      ) : (
        <div className="rf-document-body">
          <nav
            className="rf-document-outline"
            aria-label="Содержание бизнес-требований"
          >
            <p>Содержание</p>
            {sections.map((section) => (
              <a
                href={`#${contentId}-section-${section.id}`}
                key={section.id}
                data-nested={section.id.includes('.')}
                aria-current={
                  (focusedSectionId || sections[0]?.id) === section.id
                }
                onClick={(event) => {
                  event.preventDefault();
                  setCollapsedSections((previous) => {
                    const next = new Set(previous);
                    next.delete(section.id);
                    return next;
                  });
                  setFocusedSectionId(section.id);
                  requestAnimationFrame(() => {
                    const target = [
                      ...(paneRef.current?.querySelectorAll<HTMLElement>(
                        SECTION_SELECTOR,
                      ) ?? []),
                    ].find((node) => node.dataset.sectionId === section.id);
                    target?.scrollIntoView?.({
                      behavior: 'smooth',
                      block: 'start',
                    });
                  });
                }}
              >
                {section.id}. {section.title}
              </a>
            ))}
          </nav>
          <div
            ref={paneRef}
            onMouseUp={historyMode ? undefined : captureSelection}
            className="min-h-0 flex-1 overflow-y-auto px-6 pt-4 pb-8 scrollbar-auto md:px-10 lg:px-14"
            data-testid="business-document-markdown"
          >
            <article className="mx-auto max-w-[860px] text-[15px] leading-7 text-text-primary">
              {sections.map((section) => {
                const change = changes.get(section.id);
                const savedText = revision.section_texts[section.id];
                const sectionText = savedText ?? '';
                const previewText =
                  change && !preliminary ? change.after : sectionText;
                const displayedSectionText = previewText.trim()
                  ? previewText
                  : 'Требований нет';
                const headingLevel = Math.min(
                  6,
                  2 + section.id.split('.').length - 1,
                );
                const Heading = `h${headingLevel}` as
                  | 'h2'
                  | 'h3'
                  | 'h4'
                  | 'h5'
                  | 'h6';
                const collapsed = collapsedSections.has(section.id);
                const sectionContentId = `${contentId}-${section.id}`;
                return (
                  <section
                    key={section.id}
                    id={`${contentId}-section-${section.id}`}
                    data-section-id={section.id}
                    data-section-text={sectionText}
                    data-section-preview={Boolean(
                      change && (!preliminary || savedText === undefined),
                    )}
                    data-background-state={
                      highlightedSectionIds.includes(section.id)
                        ? 'possible'
                        : savedSectionIds.includes(section.id)
                          ? 'saved'
                          : undefined
                    }
                    data-testid="business-document-section"
                    className={
                      focusedSectionId === section.id
                        ? 'rounded-md bg-accent-primary/10 ring-2 ring-accent-primary/50 ring-offset-0'
                        : change || highlightedSectionIds.includes(section.id)
                          ? 'rounded-md bg-state-warning/5 ring-1 ring-state-warning/30 ring-offset-0'
                          : savedSectionIds.includes(section.id)
                            ? 'rounded-md bg-state-success/5 ring-1 ring-state-success/30 ring-offset-0'
                            : undefined
                    }
                  >
                    <Heading
                      className={
                        headingLevel === 2
                          ? 'mb-3 mt-9 border-b border-border-button pb-2 text-xl font-semibold tracking-tight first:mt-0'
                          : 'mb-2 mt-6 text-base font-semibold'
                      }
                    >
                      <button
                        type="button"
                        aria-expanded={!collapsed}
                        aria-controls={sectionContentId}
                        onMouseUp={(event) => event.stopPropagation()}
                        onClick={() =>
                          setCollapsedSections((previous) => {
                            const next = new Set(previous);
                            if (next.has(section.id)) next.delete(section.id);
                            else next.add(section.id);
                            return next;
                          })
                        }
                        className="flex w-full items-start gap-2 rounded text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary"
                      >
                        <ChevronRight
                          aria-hidden="true"
                          className={`mt-1 size-4 shrink-0 transition-transform ${collapsed ? '' : 'rotate-90'}`}
                        />
                        <span>
                          {section.id}. {section.title}
                        </span>
                      </button>
                    </Heading>
                    {!change && highlightedSectionIds.includes(section.id) && (
                      <p className="mb-3 text-xs text-state-warning">
                        Возможно изменение · можно комментировать сохранённый
                        текст
                      </p>
                    )}
                    {!change &&
                      !highlightedSectionIds.includes(section.id) &&
                      savedSectionIds.includes(section.id) && (
                        <p className="mb-3 text-xs text-text-primary">
                          Изменения сохранены в этой редакции
                        </p>
                      )}
                    {change && (
                      <p className="mb-3 text-xs font-medium text-accent-primary">
                        {preliminary
                          ? 'Предварительные изменения · проверка продолжается'
                          : 'Изменения проверены · ещё не применены'}
                      </p>
                    )}
                    <div id={sectionContentId} hidden={collapsed}>
                      <div data-section-body>
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm, remarkSelectionSource]}
                          components={{
                            p: ({ children }) => (
                              <p className="mb-4">{children}</p>
                            ),
                            ul: ({ children }) => (
                              <ul className="mb-4 list-disc space-y-1 ps-6">
                                {children}
                              </ul>
                            ),
                            ol: ({ children }) => (
                              <ol className="mb-4 list-decimal space-y-1 ps-6">
                                {children}
                              </ol>
                            ),
                            blockquote: ({ children }) => (
                              <blockquote className="my-5 border-s-2 border-accent-primary ps-4 text-text-secondary">
                                {children}
                              </blockquote>
                            ),
                            table: ({ children }) => (
                              <div className="mb-5 overflow-x-auto">
                                <table className="w-full border-collapse text-sm">
                                  {children}
                                </table>
                              </div>
                            ),
                            th: ({ children }) => (
                              <th className="border-b border-border-default bg-bg-card px-3 py-2 text-start font-medium">
                                {children}
                              </th>
                            ),
                            td: ({ children }) => (
                              <td className="border-b border-border-button px-3 py-2 align-top">
                                {children}
                              </td>
                            ),
                            pre: ({ children }) => {
                              const code = isValidElement<{
                                className?: string;
                                children?: unknown;
                              }>(children)
                                ? children
                                : null;
                              if (
                                code?.props.className
                                  ?.split(/\s+/)
                                  .includes('language-plantuml')
                              ) {
                                return (
                                  <DiagramCodeBlock
                                    language="plantuml"
                                    source={String(
                                      code.props.children ?? '',
                                    ).replace(/\n$/, '')}
                                  />
                                );
                              }
                              return (
                                <pre className="mb-4 overflow-x-auto rounded bg-bg-card p-3 text-sm">
                                  {children}
                                </pre>
                              );
                            },
                            code: ({ children, node }) => (
                              <code
                                data-source-start={node?.position?.start.offset}
                                data-source-end={node?.position?.end.offset}
                                data-source-literal="true"
                                className="rounded bg-bg-card px-1.5 py-0.5 font-mono text-[0.9em]"
                              >
                                {children}
                              </code>
                            ),
                            a: ({ children, href }) => (
                              <a
                                href={href}
                                target="_blank"
                                rel="noreferrer noopener"
                                className="text-accent-primary underline-offset-4 hover:underline"
                              >
                                {children}
                              </a>
                            ),
                          }}
                        >
                          {displayedSectionText}
                        </ReactMarkdown>
                      </div>
                      {previousRevision &&
                        previousRevision.section_texts[section.id] !==
                          sectionText && (
                          <details
                            open
                            className="mb-6 rounded border border-border-button bg-bg-card p-3"
                          >
                            <summary className="mb-3 cursor-pointer text-xs font-medium">
                              Изменения относительно редакции{' '}
                              {previousRevision.revision_number}
                            </summary>
                            <RevisionTextDiff
                              before={
                                previousRevision.section_texts[section.id] ?? ''
                              }
                              after={sectionText}
                            />
                          </details>
                        )}
                      {change && (
                        <DocumentSectionChange
                          change={change}
                          onFocusSource={onFocusSource}
                        />
                      )}
                    </div>
                  </section>
                );
              })}
            </article>
          </div>
        </div>
      )}

      {revision && (
        <div
          className={`flex shrink-0 items-center gap-2 border-t px-5 py-2 text-xs ${
            selectionError
              ? 'border-state-warning/40 bg-state-warning/5 text-state-warning'
              : 'border-border-button text-text-secondary'
          }`}
          role={selectionError ? 'alert' : undefined}
          data-testid={
            selectionError
              ? 'business-document-selection-error'
              : 'business-document-selection-help'
          }
        >
          {selectionError ? (
            <AlertTriangle className="size-3.5 shrink-0" />
          ) : (
            <MousePointer2 className="size-3.5" />
          )}
          {selectionError ??
            'Выделите фрагмент, чтобы привязать к нему комментарий'}
        </div>
      )}
    </section>
  );
}
