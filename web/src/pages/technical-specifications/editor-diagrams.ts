import { getDiagramKind } from '@/components/diagram-code-block';

export function editorDiagrams(editor: HTMLElement) {
  const diagrams: { host: HTMLElement; language: string; source: string }[] =
    [];
  editor.querySelectorAll<HTMLElement>('pre > code').forEach((code) => {
    const language = code.className.match(/\blanguage-([\w+-]+)/)?.[1];
    if (!language || !getDiagramKind(language)) return;
    const pre = code.parentElement!;
    let wrapper = pre.closest<HTMLElement>('[data-tz-diagram]');
    if (!wrapper) {
      wrapper = window.document.createElement('div');
      wrapper.dataset.tzDiagram = '';
      const preview = window.document.createElement('div');
      preview.dataset.tzDiagramPreview = '';
      preview.contentEditable = 'false';
      const details = window.document.createElement('details');
      const summary = window.document.createElement('summary');
      summary.textContent = 'Код диаграммы';
      summary.contentEditable = 'false';
      pre.replaceWith(wrapper);
      details.append(summary, pre);
      wrapper.append(preview, details);
    }
    diagrams.push({
      host: wrapper.querySelector<HTMLElement>('[data-tz-diagram-preview]')!,
      language,
      source: (code.textContent ?? '').replace(/\n$/, ''),
    });
  });
  return diagrams;
}

/** Operates on clones only; generated SVG and controls never become source. */
export function stripEditorDiagrams(root: HTMLElement) {
  root
    .querySelectorAll(
      '[data-tz-diagram-preview], [data-tz-diagram] > details > summary',
    )
    .forEach((node) => node.remove());
  root.querySelectorAll('[data-tz-diagram]').forEach((wrapper) => {
    const pre = wrapper.querySelector('pre');
    if (pre) wrapper.replaceWith(pre);
    else wrapper.remove();
  });
}

export function editorSourceText(rangeOrElement: Range | HTMLElement) {
  const clone = window.document.createElement('div');
  clone.append(
    rangeOrElement instanceof Range
      ? rangeOrElement.cloneContents()
      : rangeOrElement.cloneNode(true),
  );
  stripEditorDiagrams(clone);
  return clone.textContent ?? '';
}
