import { BusinessDocumentRequestError } from '@/services/business-document-service';
import { fireEvent, render, screen } from '@testing-library/react';
import { EvaAccessNotice } from './eva-access';

type EvaAccess = ReturnType<typeof import('./eva-access').useEvaAccess>;

jest.mock('@/hooks/common-hooks', () => ({
  useTranslate: () => ({ t: (key: string) => key }),
}));

function access(state: string) {
  return {
    data: { state, connector_id: 'eva-1' },
    isFetching: false,
    refetch: jest.fn(),
  } as unknown as EvaAccess;
}

test.each([
  [7, 'evaTokenExpiresWeek'],
  [1, 'evaTokenExpiresDay'],
])(
  'shows token renewal at %s days despite available EVA access',
  (days, text) => {
    const available = access('AVAILABLE');
    available.data!.expires_at = Date.now() + Number(days) * 86_400_000;
    render(
      <EvaAccessNotice
        access={available}
        returnTo="/business-documents/doc-1"
      />,
    );
    expect(screen.getByTestId('eva-token-expiry-warning')).toHaveTextContent(
      String(text),
    );
    expect(screen.getByTestId('eva-token-settings-link')).toHaveTextContent(
      'Обновить токен',
    );
  },
);
test('shows only the compact indicator until it is opened in the list', () => {
  render(
    <EvaAccessNotice
      access={access('MISSING')}
      compact
      returnTo="/business-documents"
    />,
  );
  expect(screen.getByTestId('eva-access-indicator')).toHaveTextContent(
    'EVA: нужен токен',
  );
  expect(
    screen.queryByTestId('eva-token-settings-link'),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByTestId('eva-access-indicator'));
  expect(screen.getByTestId('eva-token-settings-link')).toHaveAttribute(
    'href',
    '/user-setting/profile?eva=eva-1&returnTo=%2Fbusiness-documents#eva-token',
  );
  expect(screen.queryByTestId('eva-access-notice')).not.toBeInTheDocument();
});

test('shows the daily warning after the weekly warning was dismissed', () => {
  const available = access('AVAILABLE');
  available.data!.expires_at = Date.now() + 6 * 86_400_000;
  const view = render(
    <EvaAccessNotice access={available} returnTo="/business-documents/doc-1" />,
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Закрыть предупреждение EVA' }),
  );
  expect(screen.queryByTestId('eva-access-notice')).not.toBeInTheDocument();
  view.rerender(
    <EvaAccessNotice
      access={{ ...available, dataUpdatedAt: Date.now() }}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.queryByTestId('eva-access-notice')).not.toBeInTheDocument();
  const next = access('AVAILABLE');
  next.data!.expires_at = Date.now() + 3_600_000;
  view.rerender(
    <EvaAccessNotice access={next} returnTo="/business-documents/doc-1" />,
  );
  expect(screen.getByTestId('eva-token-expiry-warning')).toHaveTextContent(
    'evaTokenExpiresDay',
  );
});
test('shows token replacement on rejection and preserves the requirement in its tab', () => {
  render(
    <EvaAccessNotice
      access={access('AVAILABLE')}
      error={
        new BusinessDocumentRequestError('rejected', 'EVA_USER_TOKEN_REJECTED')
      }
      returnTo="/business-documents/doc-1"
    />,
  );
  const link = screen.getByTestId('eva-token-settings-link');
  expect(link).toHaveTextContent('Обновить токен');
  expect(link).toHaveAttribute('target', '_blank');
  expect(screen.getAllByTestId('eva-access-notice')).toHaveLength(1);
  expect(screen.queryByTestId('eva-access-indicator')).not.toBeInTheDocument();
});
test.each(['FORBIDDEN', 'UNAVAILABLE'])(
  'does not prescribe token replacement for %s',
  (state) => {
    render(
      <EvaAccessNotice
        access={access(state)}
        returnTo="/business-documents/doc-1"
      />,
    );
    expect(
      screen.queryByTestId('eva-token-settings-link'),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /провер/i })).toBeEnabled();
  },
);
test('removes the warning after access is recovered', () => {
  const rendered = render(
    <EvaAccessNotice
      access={access('MISSING')}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.getByTestId('eva-access-notice')).toBeVisible();
  rendered.rerender(
    <EvaAccessNotice
      access={access('AVAILABLE')}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.queryByTestId('eva-access-notice')).not.toBeInTheDocument();
});

test('dismisses the current warning across checks and shows a changed access state', () => {
  const rejected = access('REJECTED');
  const rendered = render(
    <EvaAccessNotice access={rejected} returnTo="/business-documents/doc-1" />,
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Закрыть предупреждение EVA' }),
  );
  expect(screen.queryByTestId('eva-access-notice')).not.toBeInTheDocument();
  expect(rejected.refetch).not.toHaveBeenCalled();

  rendered.rerender(
    <EvaAccessNotice
      access={{ ...access('REJECTED'), dataUpdatedAt: Date.now() }}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.queryByTestId('eva-access-notice')).not.toBeInTheDocument();

  rendered.rerender(
    <EvaAccessNotice
      access={access('FORBIDDEN')}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.getByTestId('eva-access-notice')).toHaveTextContent(
    'EVA: нет доступа',
  );
});

test('shows a new warning after recovery and a repeated rejection', () => {
  const rendered = render(
    <EvaAccessNotice
      access={access('REJECTED')}
      returnTo="/business-documents/doc-1"
    />,
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Закрыть предупреждение EVA' }),
  );
  rendered.rerender(
    <EvaAccessNotice
      access={access('AVAILABLE')}
      returnTo="/business-documents/doc-1"
    />,
  );
  rendered.rerender(
    <EvaAccessNotice
      access={access('REJECTED')}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.getByTestId('eva-access-notice')).toHaveTextContent(
    'EVA: обновите токен',
  );
});

test('clears a previous token rejection after a newer successful personal access check', () => {
  const error = new BusinessDocumentRequestError(
    'rejected',
    'EVA_USER_TOKEN_REJECTED',
  );
  const rendered = render(
    <EvaAccessNotice
      access={access('AVAILABLE')}
      error={error}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.getByTestId('eva-access-notice')).toBeVisible();
  const recovered = {
    ...access('AVAILABLE'),
    dataUpdatedAt: error.receivedAt + 1,
  } as EvaAccess;
  rendered.rerender(
    <EvaAccessNotice
      access={recovered}
      error={error}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.queryByTestId('eva-access-notice')).not.toBeInTheDocument();
});

test('a space read check does not clear a known write permission denial', () => {
  const error = new BusinessDocumentRequestError(
    'forbidden',
    'EVA_PERMISSION_DENIED',
  );
  const checked = {
    ...access('AVAILABLE'),
    dataUpdatedAt: error.receivedAt + 1,
  } as EvaAccess;
  render(
    <EvaAccessNotice
      access={checked}
      error={error}
      returnTo="/business-documents/doc-1"
    />,
  );
  expect(screen.getByTestId('eva-access-notice')).toHaveTextContent(
    'EVA: нет доступа',
  );
});
