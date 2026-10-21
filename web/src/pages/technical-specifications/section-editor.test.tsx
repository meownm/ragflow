import { fireEvent, render, screen } from '@testing-library/react';
import type { Specification } from './model';
import { SectionEditor, renderedSelection } from './section-editor';

const document = {
  id: 'doc',
  workspace_id: 'project',
  revision: 1,
  version: 1,
  title: 'ТЗ',
  sections: { '1': 'Текст' },
  messages: [],
  comments: [],
  proposals: [],
  people: {},
  eva: null,
} as Specification;

function editor(value: string, disabled = false) {
  const onChange = jest.fn(),
    onSelection = jest.fn();
  const props = {
    value,
    section: '1',
    document,
    disabled,
    onChange,
    onSelection,
  };
  const view = render(<SectionEditor {...props} />);
  return {
    ...view,
    props,
    onChange,
    onSelection,
    area: screen.getByRole('textbox', { name: 'Раздел 1' }),
  };
}

test('renders Markdown tables and lists without rewriting untouched source', () => {
  const { area, onChange } = editor(
    '### Сценарий\n\n**Важно**\n\n- Проверить права\n\n| Поле | Тип |\n| --- | --- |\n| id | UUID |',
  );
  expect(area.querySelector('h3')).toHaveTextContent('Сценарий');
  expect(area.querySelector('strong')).toHaveTextContent('Важно');
  expect(area.querySelector('li')).toHaveTextContent('Проверить права');
  expect(area.querySelector('td')).toHaveTextContent('id');
  expect(onChange).not.toHaveBeenCalled();
});

test('a real edit retains formatting and excludes renderer-only quote markers', () => {
  const { area, onChange } = editor('**Важно**: исходный текст');
  area.querySelector('strong')!.textContent = 'Обязательно';
  fireEvent.input(area);
  expect(onChange).toHaveBeenCalledWith(
    '<p><strong>Обязательно</strong>: исходный текст</p>',
  );
});

test('blur preserves live DOM and selections keep using the current source map', () => {
  const { area, onChange, onSelection, props, rerender } = editor(
    '**Важно**: исходный текст',
  );
  const span = area.querySelector('strong span')!;
  span.textContent = 'Обязательно';
  fireEvent.input(area);
  rerender(<SectionEditor {...props} value={onChange.mock.calls[0][0]} />);
  fireEvent.blur(area);
  expect(area.querySelector('strong span')).toBe(span);
  const range = window.document.createRange();
  range.selectNodeContents(span);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  fireEvent.mouseUp(area);
  expect(onSelection.mock.calls.at(-1)?.[0]?.anchor?.quote).toBe('Обязательно');
  selection.removeAllRanges();
});

test('selection across formatting and repeated text retains exact saved source offsets', () => {
  const value = 'Повтор. **Повтор** и конец.';
  const { area } = editor(value);
  const strong = area.querySelector('strong span')!;
  const range = window.document.createRange();
  range.selectNodeContents(strong);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  const result = renderedSelection(area, document, '1', value);
  expect(result?.scope).toBe('selection');
  expect(result?.anchor?.quote).toBe('Повтор');
  expect(result?.anchor?.prefix).toBe('Повтор. **');
  expect(result?.anchor?.suffix).toBe('** и конец.');
  selection.removeAllRanges();
});

test('typing keeps the caret DOM stable until a real external snapshot arrives', () => {
  const { area, props, rerender } = editor('Исходный текст');
  area.textContent = 'Новый ввод';
  fireEvent.input(area);
  const textNode = area.firstChild;
  rerender(<SectionEditor {...props} value="Новый ввод" />);
  expect(area.firstChild).toBe(textNode);
  rerender(<SectionEditor {...props} value="Внешняя редакция" />);
  expect(area).toHaveTextContent('Внешняя редакция');
});

test('existing sanitization drops executable HTML and read-only mode never emits changes', () => {
  const { area, onChange } = editor(
    '<p>Текст</p><script>alert(1)</script><img src="javascript:alert(1)" onerror="alert(1)">',
    true,
  );
  expect(area.querySelector('script')).toBeNull();
  expect(area.querySelector('[onerror]')).toBeNull();
  expect(area).toHaveAttribute('contenteditable', 'false');
  fireEvent.input(area);
  expect(onChange).not.toHaveBeenCalled();
});

test.each([
  'Use &lt;name&gt;',
  String.raw`\*required\*`,
  String.raw`\# literal`,
  String.raw`\+ item`,
  String.raw`1\. item`,
])('literal source spelling survives a real edit and blur: %s', (value) => {
  const { area, onChange, props, rerender } = editor(value);
  const visible = area.textContent!;
  area.append('!');
  fireEvent.input(area);
  const saved = onChange.mock.calls[0][0];
  rerender(<SectionEditor {...props} value={saved} />);
  fireEvent.blur(area);
  expect(area.textContent).toBe(visible + '!');
  expect(area.querySelector('name,em,strong')).toBeNull();
});

test('a live selection after formatting starts at the next text node, not a closing tag', () => {
  const { area, props, rerender, onChange, onSelection } = editor('**A**B');
  const text = area.querySelector('p')!.lastChild!.firstChild!;
  text.textContent = 'B!';
  fireEvent.input(area);
  rerender(<SectionEditor {...props} value={onChange.mock.calls[0][0]} />);
  const range = window.document.createRange();
  range.setStart(text, 0);
  range.setEnd(text, 1);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  fireEvent.mouseUp(area);
  expect(onSelection.mock.calls.at(-1)?.[0]?.anchor?.quote).toBe('B');
  selection.removeAllRanges();
});

test('source mapping cannot substitute a different occurrence with different formatting', () => {
  const value = '**Hel**lo Hello';
  const { area } = editor(value);
  const spans = area.querySelectorAll('span[data-source-start]');
  const range = window.document.createRange();
  range.setStart(spans[0].firstChild!, 0);
  range.setEnd(spans[1].firstChild!, 2);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  const result = renderedSelection(area, document, '1', value);
  expect(result?.anchor?.prefix).toBe('**');
  expect(result?.anchor?.quote).toBe('Hel**lo');
  selection.removeAllRanges();
});

test('an entity before repeated text cannot widen the selected quote', () => {
  const value = 'A &amp; repeat repeat';
  const { area } = editor(value);
  const text = area.querySelector('span')!.firstChild!;
  const range = window.document.createRange();
  range.setStart(text, 11);
  range.setEnd(text, 17);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  const result = renderedSelection(area, document, '1', value);
  expect(result?.anchor?.quote).toBe('repeat');
  expect(result?.anchor?.prefix).toBe('A &amp; repeat ');
  selection.removeAllRanges();
});

test('a code block quote retains the code source rather than selecting the section', () => {
  const value = '```json\n{"timeout": 30}\n```';
  const { area } = editor(value);
  const span = area.querySelector('code span')!;
  expect(span).not.toBeNull();
  const range = window.document.createRange();
  range.selectNodeContents(span);
  const result = renderedSelection(area, document, '1', value, range);
  expect(result?.anchor?.quote).toBe('{"timeout": 30}');
  expect(result?.anchor?.prefix).toBe('```json\n');
});
