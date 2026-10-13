import { BusinessDocumentRequestError } from '@/services/business-document-service';
import { fireEvent, render, screen } from '@testing-library/react';
import { EvaAccessNotice, useEvaAccess } from './eva-access';

function access(state: string) {
  return {
    data: { state, connector_id: 'eva-1' },
    isFetching: false,
    refetch: jest.fn(),
  } as unknown as ReturnType<typeof useEvaAccess>;
}
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
  } as ReturnType<typeof useEvaAccess>;
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
  } as ReturnType<typeof useEvaAccess>;
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
