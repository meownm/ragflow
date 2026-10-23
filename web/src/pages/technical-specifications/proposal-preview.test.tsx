import { render, screen, within } from '@testing-library/react';
import { ProposalPreview } from './proposal-preview';

test('highlights individual proposed edits and preserves both complete versions', () => {
  const before = 'Срок 10 дней.\nОтветственный: Иван.';
  const after = 'Срок 20 дней.\nОтветственный: Анна.';
  const { container } = render(
    <ProposalPreview
      local={{ '1': before, '2': 'Без изменений' }}
      candidate={{ '1': after, '2': 'Без изменений' }}
    />,
  );
  const diff = screen.getByTestId('revision-text-diff');
  expect(within(diff).getByText('Сейчас')).toBeInTheDocument();
  expect(within(diff).getByText('После применения')).toBeInTheDocument();
  expect(
    [...diff.querySelectorAll('pre')].map((node) => node.textContent),
  ).toEqual([before, after]);
  expect(
    [...diff.querySelectorAll('del')].map((node) => node.textContent),
  ).toEqual(['10', 'Иван']);
  expect(
    [...diff.querySelectorAll('ins')].map((node) => node.textContent),
  ).toEqual(['20', 'Анна']);
  expect(container.querySelectorAll(':scope > details')).toHaveLength(1);
});

test('keeps section scope and shows complete additions and deletions', () => {
  const { rerender } = render(
    <ProposalPreview
      local={{ '1': '', '2': 'Удалить' }}
      candidate={{ '1': 'Добавить', '2': '' }}
      section="1"
    />,
  );
  expect(
    within(screen.getByTestId('revision-text-diff')).getByText('Раздел пуст'),
  ).toBeInTheDocument();
  expect(
    screen.getByTestId('revision-text-diff').querySelector('ins'),
  ).toHaveTextContent('Добавить');
  expect(
    within(screen.getByTestId('revision-text-diff')).queryByText('Удалить'),
  ).toBeNull();
  rerender(
    <ProposalPreview
      local={{ '1': '', '2': 'Удалить' }}
      candidate={{ '1': 'Добавить', '2': '' }}
      section="2"
    />,
  );
  expect(
    screen.getByTestId('revision-text-diff').querySelector('del'),
  ).toHaveTextContent('Удалить');
  expect(
    within(screen.getByTestId('revision-text-diff')).queryByText('Добавить'),
  ).toBeNull();
});

test('retains formatted tables alongside the highlighted source comparison', () => {
  render(
    <ProposalPreview
      local={{ '1': '' }}
      candidate={{
        '1': '| Параметр | Значение |\n| --- | --- |\n| Срок | 20 дней |',
      }}
    />,
  );
  expect(screen.getByText('Текст с форматированием')).toBeInTheDocument();
  expect(screen.getByRole('table', { hidden: true })).toHaveTextContent(
    '20 дней',
  );
});
