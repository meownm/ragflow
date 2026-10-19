import { render } from '@testing-library/react';
import { DocumentMarkdown } from './document-markdown';

test.each([1, 2, 3, 4, 5, 6])('EVA content retains heading H%s', (level) => {
  const { container } = render(
    <DocumentMarkdown>{`${'#'.repeat(level)} Заголовок`}</DocumentMarkdown>,
  );
  expect(container.querySelector(`h${level}`)).toHaveTextContent('Заголовок');
});

test.each(['left', 'center', 'right'] as const)(
  'EVA table retains %s alignment in header and data cells',
  (alignment) => {
    const separator = { left: ':---', center: ':---:', right: '---:' }[
      alignment
    ];
    const { container } = render(
      <DocumentMarkdown>{`| А |\n| ${separator} |\n| Б |`}</DocumentMarkdown>,
    );
    expect(container.querySelector('th')).toHaveStyle({ textAlign: alignment });
    expect(container.querySelector('td')).toHaveStyle({ textAlign: alignment });
  },
);

test('EVA task list keeps checked and unchecked states', () => {
  const { container } = render(
    <DocumentMarkdown>{'- [ ] Сделать\n- [x] Готово'}</DocumentMarkdown>,
  );
  const boxes = container.querySelectorAll('input[type="checkbox"]');
  expect(boxes).toHaveLength(2);
  expect(boxes[0]).not.toBeChecked();
  expect(boxes[1]).toBeChecked();
  expect(boxes[0]).toBeDisabled();
  expect(boxes[1]).toBeDisabled();
});

test.each([0, 1, 3])('EVA ordered list retains start %s', (start) => {
  const { container } = render(
    <DocumentMarkdown>{`${start}. Один\n${start + 1}. Два`}</DocumentMarkdown>,
  );
  expect(container.querySelector('ol')?.start).toBe(start);
  expect(container.querySelectorAll('ol > li')).toHaveLength(2);
});

test.each([
  ['a', 'lower-alpha'],
  ['A', 'upper-alpha'],
  ['i', 'lower-roman'],
  ['I', 'upper-roman'],
])(
  'native list type %s overrides the default decimal CSS',
  (type, listStyleType) => {
    const { container } = render(
      <DocumentMarkdown>{`<ol type="${type}"><li>Item</li></ol>`}</DocumentMarkdown>,
    );
    expect(container.querySelector('ol')).toHaveStyle({ listStyleType });
  },
);

test('EVA code keeps language, spaces, blank lines and literal triple fence', () => {
  const source = '\nfirst  \n```\nlast\n\n';
  const { container } = render(
    <DocumentMarkdown>{`\`\`\`\`json\n${source}\`\`\`\``}</DocumentMarkdown>,
  );
  expect(container.querySelector('pre code')).toHaveClass('language-json');
  expect(container.querySelector('pre code')?.textContent).toBe(source);
});

test.each(['\n', '\r\n'])(
  'EVA prose preserves line separators %j',
  (newline) => {
    const { container } = render(
      <DocumentMarkdown>{`Первая${newline}Вторая${newline}${newline}Третья`}</DocumentMarkdown>,
    );
    expect(container.querySelectorAll('p')).toHaveLength(2);
    expect(container.querySelectorAll('br')).toHaveLength(1);
  },
);

test.each([
  '> | А |\n> | --- |\n> | Б |',
  '- Один\n\n  | А |\n  | --- |\n  | Б |',
])('EVA nested GFM table retains its structure: %s', (markdown) => {
  const { container } = render(<DocumentMarkdown>{markdown}</DocumentMarkdown>);
  expect(container.querySelector('blockquote table, li table')).toBeTruthy();
  expect(container.querySelector('th')).toHaveTextContent('А');
  expect(container.querySelector('td')).toHaveTextContent('Б');
});

test.each(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'code'])(
  'retains safe %s styling, direction and title from Eva HTML',
  (tag) => {
    const { container } = render(
      <DocumentMarkdown>{`<${tag} style="color:red;text-align:right" dir="rtl" title="Описание">Текст</${tag}>`}</DocumentMarkdown>,
    );
    const element = container.querySelector(`${tag}[title="Описание"]`);
    expect(element).toHaveStyle({ color: 'red', textAlign: 'right' });
    expect(element).toHaveAttribute('dir', 'rtl');
    expect(element).toHaveTextContent('Текст');
  },
);

test('retains native merged cells, numbering, image size and inline styles', () => {
  const { container } = render(
    <DocumentMarkdown>
      {
        '<p><u>Подчёркнуто</u> <sub>низ</sub> <sup>верх</sup> <mark>Важно</mark></p>\n\n<table border="1" width="300"><tr><td colspan="2" rowspan="3" width="200">Ячейка</td></tr></table>\n\n<ol type="I" reversed start="3"><li value="7">Семь</li></ol>\n\n<p><img src="https://example.test/image.png" alt="Схема" width="320" height="200"></p>'
      }
    </DocumentMarkdown>,
  );
  expect(container.querySelector('u')).toHaveTextContent('Подчёркнуто');
  expect(container.querySelector('sub')).toHaveTextContent('низ');
  expect(container.querySelector('sup')).toHaveTextContent('верх');
  expect(container.querySelector('mark')).toHaveTextContent('Важно');
  expect(container.querySelector('td')).toHaveAttribute('colspan', '2');
  expect(container.querySelector('td')).toHaveAttribute('rowspan', '3');
  expect(container.querySelector('td')).toHaveAttribute('width', '200');
  expect(container.querySelector('table')).toHaveAttribute('border', '1');
  expect(container.querySelector('ol')).toHaveAttribute('type', 'I');
  expect(container.querySelector('ol')).toHaveAttribute('reversed');
  expect(container.querySelector('li')).toHaveAttribute('value', '7');
  expect(container.querySelector('img')).toHaveAttribute('width', '320');
  expect(container.querySelector('img')).toHaveAttribute('height', '200');
});

test.each([
  'background-image:url(https://evil.test/x)',
  'background-color:URL(javascript:alert(1))',
  'font-family:expression(alert(1))',
  String.raw`background-color:u\72l(https://evil.test/x)`,
  'position:fixed;inset:0',
])('rejects unsafe CSS while keeping safe color: %s', (style) => {
  const { container } = render(
    <DocumentMarkdown>{`<p style="color:red;${style}" onclick="evil()">Safe</p>`}</DocumentMarkdown>,
  );
  expect(container.querySelector('p')).toHaveAttribute('style', 'color: red;');
  expect(container.querySelector('[onclick]')).toBeNull();
});

test.each([
  'javascript:alert(1)',
  'java&#x09;script:alert(1)',
  'data:text/html,evil',
])('rejects active raw HTML and unsafe resources: %s', (url) => {
  const { container } = render(
    <DocumentMarkdown>{`<p><a href="${url}" id="clobber">Label</a><img src="${url}" alt="Alt" onerror="evil()"></p><svg><script>evil()</script></svg><iframe>evil</iframe><form><input type="text"></form>`}</DocumentMarkdown>,
  );
  expect(
    container.querySelector(
      '[href], [src], [id="clobber"], [onerror], script, svg, iframe, form, input',
    ),
  ).toBeNull();
  expect(container).toHaveTextContent('LabelAlt');
});

test('document anchors retain their targets in an isolated DOM namespace', () => {
  const { container } = render(
    <DocumentMarkdown>
      {'<h2 id="location">Target</h2><p><a href="#location">Jump</a></p>'}
    </DocumentMarkdown>,
  );
  const target = container.querySelector('h2')?.id;
  expect(target).toMatch(/^business-document-.+-location$/);
  expect(container.querySelector('a')).toHaveAttribute('href', `#${target}`);
  expect(container.querySelector('a')).not.toHaveAttribute('target');
  expect(container.querySelector('[id="location"]')).toBeNull();
});

test('adjacent document views have independent heading-anchor namespaces', () => {
  const markdown =
    '## 1. Scope\n\n<a id="section" data-document-heading-anchor=""></a>\n\n[Jump](#section)';
  const { container } = render(
    <>
      <DocumentMarkdown>{markdown}</DocumentMarkdown>
      <DocumentMarkdown>{markdown}</DocumentMarkdown>
    </>,
  );
  const headings = container.querySelectorAll('h2');
  const links = container.querySelectorAll('a');
  expect(headings).toHaveLength(2);
  expect(links).toHaveLength(2);
  expect(headings[0].id).not.toBe(headings[1].id);
  expect(links[0]).toHaveAttribute('href', `#${headings[0].id}`);
  expect(links[1]).toHaveAttribute('href', `#${headings[1].id}`);
});
