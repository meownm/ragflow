import { render } from '@testing-library/react';
import { DocumentMarkdown } from './document-markdown';

jest.mock('@/components/diagram-code-block', () => ({
  getDiagramKind: (language: string) =>
    ['mermaid', 'plantuml', 'puml'].includes(language),
  DiagramCodeBlock: ({
    language,
    source,
  }: {
    language: string;
    source: string;
  }) => (
    <figure aria-label={language}>
      <svg>
        <text>{source}</text>
      </svg>
    </figure>
  ),
}));

test.each(['mermaid', 'plantuml', 'puml'])(
  'renders fenced and HTML %s as diagrams',
  (language) => {
    const { container } = render(
      <DocumentMarkdown>
        {'```' +
          language +
          '\nA-->B\n```\n\n<pre><code class="language-' +
          language +
          '">A--&gt;B</code></pre>'}
      </DocumentMarkdown>,
    );
    expect(container.querySelectorAll('figure svg')).toHaveLength(2);
    expect(container.querySelectorAll('pre')).toHaveLength(0);
    expect(container.querySelector('svg')).toHaveTextContent('A-->B');
  },
);

test.each([
  'One\nTwo',
  'One  \nTwo',
  'One\\\nTwo',
  '<p>One\nTwo</p>',
  '<p>One<br>\nTwo</p>',
  '<div>One\nTwo</div>',
  '<blockquote>One\nTwo</blockquote>',
])('retains one visible break in Markdown and HTML: %j', (value) => {
  const { container } = render(<DocumentMarkdown>{value}</DocumentMarkdown>);
  expect(container.querySelectorAll('br')).toHaveLength(1);
});

test('line-break handling leaves code and structural table whitespace untouched', () => {
  const { container } = render(
    <DocumentMarkdown>
      {'```text\nOne\nTwo\n```\n\n<table>\n<tr><td>A\nB</td></tr>\n</table>'}
    </DocumentMarkdown>,
  );
  expect(container.querySelector('pre code')?.textContent).toBe('One\nTwo\n');
  expect(container.querySelector('pre br, table > br, tr > br')).toBeNull();
  expect(container.querySelectorAll('td br')).toHaveLength(1);
});

test('explicit HTML white-space styling owns its line breaks', () => {
  const { container } = render(
    <DocumentMarkdown>
      {'<p style="white-space:pre-wrap">One\nTwo</p>'}
    </DocumentMarkdown>,
  );
  expect(container.querySelector('p')).toHaveStyle({ whiteSpace: 'pre-wrap' });
  expect(container.querySelector('p br')).toBeNull();
});
