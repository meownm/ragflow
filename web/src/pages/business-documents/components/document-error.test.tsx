import { render, screen } from '@testing-library/react';
import { BusinessDocumentError } from './document-error';

test('shows all saved SOAR omissions without hiding their definitions', () => {
  const metrics = [
    'Время появления тикета: от создания инцидента SOAR до появления связанного тикета в ЕЛКа.',
    'Время обработки тикета: от появления алерта в очереди ЕЛКа до назначения другой команде (не ОФМ).',
    'Время работы с инцидентом: от появления алерта до закрытия тикета.',
    'Время реакции ОФМ: до первого действия ОФМ.',
    'Время реакции ОПК: от назначения исполнителя до первого действия.',
    'Время исполнения решения: от решения ОФМ до результата блокировки/разблокировки.',
    'Доля возвратов за данными: тикеты с запросом / все тикеты.',
    'Полнота истории: тикеты с результатом / все завершённые тикеты.',
  ];
  render(
    <BusinessDocumentError
      error={{
        code: 'REQUIREMENT_COVERAGE_FAILED',
        message:
          'Generated changes omit, contradict or misplace confirmed requirements',
        details: {
          requirements: metrics.map((text) => ({ text, status: 'MISSING' })),
        },
      }}
    />,
  );
  expect(
    screen.getByText('План изменений не сохранил подтверждённые требования.'),
  ).toBeVisible();
  expect(screen.getAllByRole('listitem')).toHaveLength(8);
  for (const text of metrics) expect(screen.getByText(text)).toBeVisible();
});

test('shows source text, destination and the current semantic reason', () => {
  render(
    <BusinessDocumentError
      error={{
        code: 'REQUIREMENT_COVERAGE_FAILED',
        details: {
          requirements: [
            {
              text: 'Доля возвратов: тикеты с запросом / все тикеты.',
              section_id: '5.4',
              reason: 'В знаменателе потеряны все тикеты.',
            },
          ],
        },
      }}
    />,
  );
  expect(screen.getByText('Раздел 5.4')).toBeVisible();
  expect(
    screen.getByText('Причина: В знаменателе потеряны все тикеты.'),
  ).toBeVisible();
});

test('explains the reporting and monitoring duplication guard', () => {
  render(
    <BusinessDocumentError
      error={{
        code: 'DUPLICATE_REQUIREMENT_CONTENT',
        details: {
          duplicate_section_pairs: [['5.4', '5.5'], null, ['5.4'], [1, {}]],
        },
      }}
    />,
  );
  expect(
    screen.getByText('План изменений повторяет определения в разных разделах.'),
  ).toBeVisible();
  expect(
    screen.getByText('Повторяющиеся определения: разделы 5.4 и 5.5.'),
  ).toBeVisible();
});

test.each([
  [{ status: 'CONTRADICTED' }, 'Противоречит подтверждённому требованию'],
  [
    {
      status: 'MISSING',
      reason: 'WRONG_SECTION',
      required_section_ids: ['5.4'],
    },
    'Размещено не в том разделе · Раздел 5.4',
  ],
])('explains saved contradiction and placement verdicts', (issue, expected) => {
  render(
    <BusinessDocumentError
      error={{
        details: { requirements: [{ text: 'Требование автора', ...issue }] },
      }}
    />,
  );
  expect(screen.getByText(expected)).toBeVisible();
});

test.each([
  null,
  [],
  { requirements: null },
  { requirements: [null, 1, {}, { text: [] }, { reason: {} }] },
])('keeps a readable fallback for malformed details %p', (details) => {
  render(
    <BusinessDocumentError error={{ message: 'Ошибка обработки', details }} />,
  );
  expect(screen.getByText('Ошибка обработки')).toBeVisible();
  expect(screen.queryByRole('list')).not.toBeInTheDocument();
});

test('renders error text as text and retains plain string errors', () => {
  const { rerender, container } = render(
    <BusinessDocumentError
      error={{
        details: {
          requirements: [
            {
              text: '<script>alert(1)</script>',
              reason: '<img onerror="alert(1)">',
            },
          ],
        },
      }}
    />,
  );
  expect(screen.getByText('<script>alert(1)</script>')).toBeVisible();
  expect(container.querySelector('script, img')).toBeNull();
  rerender(<BusinessDocumentError error="Сеть недоступна" />);
  expect(screen.getByText('Сеть недоступна')).toBeVisible();
});
