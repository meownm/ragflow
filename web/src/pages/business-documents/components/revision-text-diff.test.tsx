import { render, screen } from '@testing-library/react';
import { RevisionTextDiff } from './revision-text-diff';

test('highlights separate Russian edits while preserving unchanged words and whitespace', () => {
  const before = 'Старый срок 10 дней.\nОтветственный: Иван.';
  const after = 'Новый срок 20 дней.\nОтветственный: Анна.';
  const { container } = render(
    <RevisionTextDiff before={before} after={after} />,
  );
  const views = container.querySelectorAll('pre');
  expect(views[0].textContent).toBe(before);
  expect(views[1].textContent).toBe(after);
  expect(
    [...container.querySelectorAll('del')].map((node) => node.textContent),
  ).toEqual(['Старый', '10', 'Иван']);
  expect(
    [...container.querySelectorAll('ins')].map((node) => node.textContent),
  ).toEqual(['Новый', '20', 'Анна']);
});

test.each([
  ['', 'Добавление'],
  ['Удаление', ''],
  ['Без изменений', 'Без изменений'],
  ['😀 <script>bad()</script>', '✅ <img onerror=bad()>'],
])('preserves both sides safely: %s -> %s', (before, after) => {
  const { container } = render(
    <RevisionTextDiff before={before} after={after} />,
  );
  const views = container.querySelectorAll('pre');
  expect(views[0].textContent).toBe(before || 'Требования отсутствуют');
  expect(views[1].textContent).toBe(after || 'Требования отсутствуют');
  expect(container.querySelector('script,img')).toBeNull();
});

test('bounds large replacements and keeps all source text', () => {
  const before = 'старое '.repeat(1000);
  const after = 'новое '.repeat(1000);
  const { container } = render(
    <RevisionTextDiff before={before} after={after} />,
  );
  const views = container.querySelectorAll('pre');
  expect(views[0].textContent).toBe(before);
  expect(views[1].textContent).toBe(after);
  expect(
    screen.getByTestId('revision-text-diff').querySelector('ins'),
  ).toBeTruthy();
});
