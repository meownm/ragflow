import type { Root, RootContent } from 'hast';

const inline = new Set([
  'span',
  'a',
  'strong',
  'b',
  'em',
  'i',
  'del',
  's',
  'u',
  'mark',
  'sub',
  'sup',
]);
const textContainers = new Set([
  ...inline,
  'p',
  'div',
  'blockquote',
  'dt',
  'dd',
  'summary',
  'aside',
  'details',
  'li',
  'td',
  'th',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
]);

/** Add visible line breaks without discarding the parser's source positions. */
export function rehypeDocumentBreaks() {
  return (tree: Root) => {
    function visit(
      children: RootContent[],
      parent = '',
      afterBreak = false,
    ): boolean {
      const output: RootContent[] = [];
      for (const node of children) {
        if (node.type === 'element') {
          if (
            !['pre', 'code'].includes(node.tagName) &&
            !/(?:^|;)\s*white-space\s*:/i.test(
              String(node.properties.style ?? ''),
            )
          ) {
            const trailingBreak = visit(
              node.children,
              node.tagName,
              inline.has(node.tagName) && afterBreak,
            );
            afterBreak =
              node.tagName === 'br' ||
              (inline.has(node.tagName) && trailingBreak);
          } else afterBreak = false;
          output.push(node);
        } else if (node.type === 'text' && textContainers.has(parent)) {
          // Keep LF in textContent: selections still map to the original source.
          const parts = node.value.split(/(\r\n|\r|\n)/);
          for (const part of parts) {
            if (!part) continue;
            if (/^[\r\n]+$/.test(part)) {
              if (!afterBreak && node.value.trim()) {
                output.push({
                  type: 'element',
                  tagName: 'br',
                  properties: {},
                  children: [],
                });
              }
              afterBreak = false;
            } else if (part.trim()) afterBreak = false;
            output.push({ type: 'text', value: part });
          }
        } else output.push(node);
      }
      children.splice(0, children.length, ...output);
      return afterBreak;
    }
    visit(tree.children);
  };
}
