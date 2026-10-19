import type { Element, Root, RootContent } from 'hast';
import policy from '../../../../../api/apps/business_documents/html_policy.json';

const cssValue = new RegExp(policy.cssValuePattern);
const aliases: Record<string, string> = {
  className: 'class',
  dataDocumentWarnings: 'data-document-warnings',
  dataDocumentHeadingAnchor: 'data-document-heading-anchor',
};

function safeUrl(value: string, image: boolean) {
  const normalized = Array.from(value)
    .filter((char) => char.charCodeAt(0) > 32 && char.charCodeAt(0) !== 127)
    .join('');
  try {
    const url = new URL(normalized, 'https://document.invalid');
    return image
      ? /^https?:\/\//i.test(normalized) && !!url.hostname
      : ['http:', 'https:', 'mailto:'].includes(url.protocol);
  } catch {
    return false;
  }
}

function sanitizeProperties(node: Element, anchorPrefix: string) {
  const tagAttributes = policy.attributes as Record<string, string[]>;
  const allowed = [
    ...tagAttributes['*'],
    ...(tagAttributes[node.tagName] ?? []),
  ];
  for (const [property, value] of Object.entries(node.properties)) {
    const attribute = aliases[property] ?? property.toLowerCase();
    if (
      !allowed.includes(attribute) ||
      (['href', 'src'].includes(attribute) &&
        !safeUrl(String(value), attribute === 'src'))
    ) {
      delete node.properties[property];
    } else if (attribute === 'style') {
      node.properties[property] = String(value)
        .split(';')
        .flatMap((part) => {
          const colon = part.indexOf(':');
          const name = part.slice(0, colon).trim().toLowerCase();
          const content = part.slice(colon + 1).trim();
          return policy.cssProperties.includes(name) && cssValue.test(content)
            ? [`${name}:${content}`]
            : [];
        })
        .join(';');
    } else if (attribute === 'class') {
      node.properties[property] = (
        Array.isArray(value) ? value : [value]
      ).filter(
        (name) =>
          /^language-[\w+-]+$/.test(String(name)) ||
          (node.tagName === 'aside' && name === 'business-document-warnings'),
      ) as string[];
    } else if (attribute === 'id') {
      // Isolate document anchors from named properties on window/document.
      node.properties[property] = `${anchorPrefix}${value}`;
    }
  }
  if (String(node.properties.href ?? '').startsWith('#')) {
    node.properties.href = `#${anchorPrefix}${String(node.properties.href).slice(1)}`;
  }
  if (node.tagName === 'input') {
    node.properties.type = 'checkbox';
    node.properties.disabled = true;
  }
}

function clean(nodes: RootContent[], anchorPrefix: string): RootContent[] {
  for (let index = 0; index < nodes.length; index++) {
    const heading = nodes[index];
    if (heading.type !== 'element' || !/^h[1-6]$/.test(heading.tagName))
      continue;
    let next = index + 1;
    while (
      nodes[next]?.type === 'text' &&
      !String((nodes[next] as { value: string }).value).trim()
    )
      next++;
    const container = nodes[next];
    if (container?.type !== 'element') continue;
    const children =
      container.tagName === 'p'
        ? container.children.filter(
            (child) => child.type !== 'text' || child.value.trim(),
          )
        : [container];
    const anchor = children[0];
    if (
      children.length === 1 &&
      anchor.type === 'element' &&
      anchor.tagName === 'a' &&
      anchor.properties.dataDocumentHeadingAnchor !== undefined &&
      anchor.properties.id &&
      !anchor.children.length
    ) {
      heading.properties.id = anchor.properties.id;
      nodes.splice(next, 1);
    }
  }
  return nodes.flatMap((node) => {
    if (node.type === 'text') return [node];
    if (node.type !== 'element') return [];
    if (
      policy.dropContent.includes(node.tagName) ||
      (node.tagName === 'input' &&
        String(node.properties.type).toLowerCase() !== 'checkbox')
    ) {
      return [];
    }
    node.children = clean(node.children, anchorPrefix) as Element['children'];
    if (!policy.tags.includes(node.tagName)) return node.children;
    sanitizeProperties(node, anchorPrefix);
    if (node.tagName === 'img' && !node.properties.src) {
      return [{ type: 'text', value: String(node.properties.alt ?? '') }];
    }
    return [node];
  });
}

/** Only the shared document-formatting allowlist reaches React's DOM. */
export function rehypeDocumentHtml({
  anchorPrefix = 'business-document-',
} = {}) {
  return (tree: Root) => {
    tree.children = clean(tree.children, anchorPrefix);
  };
}
