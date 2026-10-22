import { DiagramCodeBlock } from '@/components/diagram-code-block';
import type { Element as HastElement, Root, RootContent } from 'hast';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import { rehypeDocumentBreaks } from '../business-documents/components/document-breaks';
import { rehypeDocumentHtml } from '../business-documents/components/document-html';
import {
  editorDiagrams,
  editorSourceText,
  stripEditorDiagrams,
} from './editor-diagrams';
import { selectionContext, type Context, type Specification } from './model';

// Text positions belong to the original source, including Markdown delimiters.
// They let a visually selected quote retain the exact saved source spelling.
function rehypeSourcePositions({ source = '' } = {}) {
  return (tree: Root) => {
    function visit(children: RootContent[], parent = '') {
      for (let index = 0; index < children.length; index++) {
        let node = children[index];
        const previous = children[index - 1];
        // rehype-raw merges the synthetic LF after a hard break with the next
        // text node. Its start is lost; the break's exact end anchors that text.
        if (
          node.type === 'text' &&
          !node.position?.start &&
          node.position?.end &&
          node.value.startsWith('\n') &&
          previous?.type === 'element' &&
          previous.tagName === 'br' &&
          previous.position?.end
        ) {
          node = {
            ...node,
            value: node.value.slice(1),
            position: {
              start: previous.position.end,
              end: node.position.end,
            },
          };
          children.splice(index, 1, { type: 'text', value: '\n' }, node);
          index++;
        }
        if (node.type === 'element') {
          if (
            node.tagName === 'code' &&
            node.position?.start &&
            node.position?.end &&
            node.children.length === 1 &&
            node.children[0].type === 'text' &&
            !node.children[0].position
          ) {
            const child = node.children[0];
            const text = child.value.replace(/\r\n?/g, '\n').replace(/\n$/, '');
            const start = node.position.start.offset ?? 0;
            const raw = source.slice(start, node.position.end.offset);
            const normalized = raw.replace(/\r\n?/g, '\n');
            const rawOffset = (offset: number) => {
              let at = 0;
              for (let index = 0; index < offset; index++, at++)
                if (raw[at] === '\r' && raw[at + 1] === '\n') at++;
              return start + at;
            };
            const at = normalized.indexOf(text);
            if (text && at >= 0 && normalized.indexOf(text, at + 1) < 0) {
              node.children = [
                {
                  ...child,
                  value: text,
                  position: {
                    start: { line: 1, column: 1, offset: rawOffset(at) },
                    end: {
                      line: 1,
                      column: 1,
                      offset: rawOffset(at + text.length),
                    },
                  },
                },
                ...(child.value.endsWith('\n')
                  ? [{ type: 'text' as const, value: '\n' }]
                  : []),
              ];
            }
          }
          visit(node.children, node.tagName);
        } else if (
          node.type === 'text' &&
          node.position?.start &&
          node.position?.end &&
          (node.value.trim() ||
            !['table', 'thead', 'tbody', 'tfoot', 'tr', 'ul', 'ol'].includes(
              parent,
            ))
        ) {
          children[index] = {
            type: 'element',
            tagName: 'span',
            properties: {
              'data-source-start': node.position.start.offset,
              'data-source-end': node.position.end.offset,
            },
            children: [node],
          } as HastElement;
        }
      }
    }
    visit(tree.children);
  };
}

export function editorHtml(element: HTMLElement, anchorPrefix = 'tz-render-') {
  const clone = element.cloneNode(true) as HTMLElement;
  stripEditorDiagrams(clone);
  // Renderer-only wrappers and attributes must never enter the saved document.
  clone.querySelectorAll('span[data-source-start]').forEach((span) => {
    span.replaceWith(...span.childNodes);
  });
  clone.querySelectorAll('*').forEach((node) => {
    for (const attribute of [
      'contenteditable',
      'data-source-start',
      'data-source-end',
    ])
      node.removeAttribute(attribute);
    if (node.id.startsWith(anchorPrefix))
      node.id = node.id.slice(anchorPrefix.length);
    const href = node.getAttribute('href');
    if (href?.startsWith('#' + anchorPrefix))
      node.setAttribute('href', '#' + href.slice(anchorPrefix.length + 1));
  });
  // A single plain paragraph stays plain: existing simple-text contracts remain
  // readable and no edit rewrites another section or an untouched Markdown file.
  const text = clone.textContent ?? '';
  const literal =
    /[^\p{L}\p{N} .,;:!?()-]/u.test(text) ||
    /^\s*(?:[-+]\s|\d+[.)]\s)/.test(text);
  if (!clone.querySelector('*'))
    return literal ? '<p>' + clone.innerHTML + '</p>' : text;
  if (
    clone.children.length === 1 &&
    clone.firstElementChild?.tagName === 'P' &&
    !clone.firstElementChild.children.length &&
    !literal
  )
    return clone.firstElementChild.textContent ?? '';
  return clone.innerHTML;
}

function sourceTextOffset(span: HTMLElement, source: string, offset: number) {
  const start = Number(span.dataset.sourceStart),
    end = Number(span.dataset.sourceEnd);
  if (!Number.isInteger(start) || !Number.isInteger(end)) return null;
  const raw = source.slice(start, end),
    displayed = span.textContent ?? '';
  if (raw === displayed) return start + offset;
  // Decode only spelling differences represented by this exact source node.
  // A failed alignment blocks the selection; it never expands it to a paragraph.
  const decoder = window.document.createElement('textarea');
  let decoded = '';
  const positions = [start];
  for (let index = 0; index < raw.length; ) {
    const rest = raw.slice(index);
    const entity = rest.match(
      /^&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[a-zA-Z][a-zA-Z0-9]+);/,
    );
    const escape = rest.match(/^\\[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/);
    let token = raw[index],
      consumed = 1;
    if (entity) {
      decoder.innerHTML = entity[0];
      token = decoder.value;
      consumed = entity[0].length;
    } else if (escape) {
      token = rest[1];
      consumed = 2;
    } else if (rest.startsWith('\r')) {
      token = '\n';
      consumed = rest.startsWith('\r\n') ? 2 : 1;
    }
    decoded += token;
    for (let unit = 0; unit < token.length; unit++)
      positions.push(
        start + index + (unit === token.length - 1 ? consumed : 0),
      );
    index += consumed;
  }
  return decoded === displayed ? (positions[offset] ?? null) : null;
}

export function renderedSelection(
  editor: HTMLElement,
  document: Specification,
  section: string,
  source: string,
  selectedRange?: Range,
): Context | null {
  const selection = window.getSelection();
  const range =
    selectedRange ?? (selection?.rangeCount ? selection.getRangeAt(0) : null);
  if (
    !range ||
    range.collapsed ||
    !editor.contains(range.startContainer) ||
    !editor.contains(range.endContainer)
  )
    return null;
  const activeRange = range;
  function offset(node: Node, position: number, end: boolean): number | null {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const child = node.childNodes[end ? position - 1 : position];
      if (!child) return null;
      node = child;
      while (node.childNodes.length)
        node = end ? node.lastChild! : node.firstChild!;
      position = end ? (node.textContent?.length ?? 0) : 0;
    }
    const span = node.parentElement?.closest<HTMLElement>(
      'span[data-source-start]',
    );
    if (!span || !editor.contains(span)) return null;
    const prefix = activeRange.cloneRange();
    prefix.selectNodeContents(span);
    prefix.setEnd(node, position);
    return sourceTextOffset(span, source, prefix.toString().length);
  }
  // DOM positions are authoritative. A unique plain-text search can still point
  // to another occurrence whose formatting differs from the selected one.
  const start = offset(range.startContainer, range.startOffset, false);
  const end = offset(range.endContainer, range.endOffset, true);
  return start !== null && end !== null && end > start
    ? selectionContext(document, section, source, start, end)
    : null;
}

function textPoint(
  root: HTMLElement,
  offset: number,
  end = false,
): [Node, number] | null {
  const walker = window.document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let remaining = offset;
  while (walker.nextNode()) {
    const node = walker.currentNode,
      length = node.textContent?.length ?? 0;
    if (remaining < length || (end && remaining === length))
      return [node, remaining];
    remaining -= length;
  }
  return null;
}

/** A formatted editor over the existing Markdown/HTML storage contract. */
export function SectionEditor({
  value,
  section,
  document,
  disabled,
  onChange,
  onSelection,
  onInvalidSelection,
}: {
  value: string;
  section: string;
  document: Specification;
  disabled: boolean;
  onChange: (value: string) => void;
  onSelection: (value: Context | null) => void;
  onInvalidSelection?: (invalid: boolean) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const rendered = useRef(value);
  const changed = useRef(false);
  const [diagrams, setDiagrams] = useState<ReturnType<typeof editorDiagrams>>(
    [],
  );
  const anchorPrefix = `tz-render-${document.id}-${section}-`;
  // React owns the boundary, the browser owns editable text nodes. Rebuild only
  // on an external snapshot, preserving the caret and native undo history.
  const markup = useCallback(
    (text: string) => {
      return renderToStaticMarkup(
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[
            rehypeRaw,
            [rehypeDocumentHtml, { anchorPrefix }],
            [rehypeSourcePositions, { source: text }],
            rehypeDocumentBreaks,
          ]}
        >
          {text}
        </ReactMarkdown>,
      );
    },
    [anchorPrefix],
  );
  useLayoutEffect(() => {
    if (rendered.current === value && changed.current) return;
    rendered.current = value;
    changed.current = false;
    if (host.current) {
      host.current.innerHTML = markup(value);
      setDiagrams(editorDiagrams(host.current));
    }
  }, [value, markup]);

  function input() {
    if (disabled || !host.current) return;
    changed.current = true;
    const html = editorHtml(host.current, anchorPrefix);
    rendered.current = html;
    onChange(html);
    setDiagrams(editorDiagrams(host.current));
    onSelection(null);
    onInvalidSelection?.(false);
  }
  function select() {
    if (!host.current) return;
    const native = window.getSelection();
    let selected: Context | null = null;
    if (native?.rangeCount && !native.isCollapsed) {
      const range = native.getRangeAt(0);
      if (
        changed.current &&
        host.current.contains(range.commonAncestorContainer)
      ) {
        // Map the live browser DOM through a fresh detached rendering. Never
        // reuse pre-edit Markdown offsets against newly serialized HTML.
        const fresh = window.document.createElement('div');
        fresh.innerHTML = markup(value);
        if (fresh.textContent === editorSourceText(host.current)) {
          const prefix = range.cloneRange();
          prefix.selectNodeContents(host.current);
          prefix.setEnd(range.startContainer, range.startOffset);
          const start = textPoint(fresh, editorSourceText(prefix).length);
          prefix.setEnd(range.endContainer, range.endOffset);
          const end = textPoint(fresh, editorSourceText(prefix).length, true);
          if (start && end) {
            const mapped = window.document.createRange();
            mapped.setStart(...start);
            mapped.setEnd(...end);
            selected = renderedSelection(
              fresh,
              document,
              section,
              value,
              mapped,
            );
          }
        }
      } else
        selected = renderedSelection(host.current, document, section, value);
    }
    onSelection(selected);
    onInvalidSelection?.(!!native && !native.isCollapsed && !selected);
  }
  return (
    <>
      <div
        ref={host}
        className="tz-section-editor"
        role="textbox"
        aria-label={`Раздел ${section}`}
        aria-multiline="true"
        aria-readonly={disabled}
        contentEditable={!disabled}
        suppressContentEditableWarning
        onInput={input}
        onMouseUp={select}
        onKeyUp={select}
        onPaste={(event) => {
          // Paste plain text; never admit executable clipboard HTML into the DOM.
          event.preventDefault();
          if (!disabled)
            window.document.execCommand(
              'insertText',
              false,
              event.clipboardData.getData('text/plain'),
            );
        }}
        onDrop={(event) => event.preventDefault()}
      />
      {diagrams.map(({ host: preview, language, source }, index) =>
        createPortal(
          <DiagramCodeBlock language={language} source={source} />,
          preview,
          String(index),
        ),
      )}
    </>
  );
}
