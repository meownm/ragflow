import { exportBusinessDocumentSqlProject } from '@/services/business-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { useLayoutEffect } from 'react';
import { SqlExportButton } from './sql-export-button';

jest.mock('@/services/business-document-service', () => ({
  exportBusinessDocumentSqlProject: jest.fn(),
}));
const exporter = jest.mocked(exportBusinessDocumentSqlProject);
const command = {
  schema_version: '1',
  kind: 'QUERY',
  format: 'json',
  compilation_id: 'compilation-old',
  query_artifact_id: 'query-old',
} as const;
const props = {
  projectId: 'project',
  stateVersion: 1,
  command,
  label: 'Скачать SQL',
};
const copy = jest.fn();
let click: jest.SpyInstance;

function file(sql = 'SELECT original AS value') {
  return {
    blob: { text: async () => JSON.stringify({ sql }) } as Blob,
    filename: 'sql-query-old.json',
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: copy },
  });
  URL.createObjectURL = jest.fn(() => 'blob:export');
  URL.revokeObjectURL = jest.fn();
  click = jest
    .spyOn(HTMLAnchorElement.prototype, 'click')
    .mockImplementation(() => {});
});
afterEach(() => click.mockRestore());

test('invalidates a removed export before the next project commit can observe it', async () => {
  let complete!: (value: ReturnType<typeof file>) => void;
  let signal: AbortSignal | undefined;
  const canceledAtCommit: boolean[] = [];
  exporter.mockImplementationOnce((_project, _command, requestSignal) => {
    signal = requestSignal;
    return new Promise((resolve) => {
      complete = resolve;
    });
  });
  function Host({ visible }: { visible: boolean }) {
    useLayoutEffect(() => {
      if (!visible) canceledAtCommit.push(Boolean(signal?.aborted));
    }, [visible]);
    return visible ? <SqlExportButton {...props} /> : <h3>Другой проект</h3>;
  }
  const view = render(<Host visible />);
  fireEvent.click(screen.getByRole('button'));
  view.rerender(<Host visible={false} />);
  expect(canceledAtCommit).toEqual([true]);
  await act(async () => complete(file()));
  expect(click).not.toHaveBeenCalled();
});

test('copies only the SQL returned by a fresh authorized export', async () => {
  exporter.mockResolvedValue(file());
  render(<SqlExportButton {...props} copySql label="Копировать" />);
  fireEvent.click(screen.getByRole('button'));
  await screen.findByText('Скопировано');
  expect(exporter).toHaveBeenCalledWith(
    'project',
    command,
    expect.any(AbortSignal),
  );
  expect(copy).toHaveBeenCalledWith('SELECT original AS value');
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});

test('downloads the returned blob without parsing result numbers', async () => {
  const response = file();
  exporter.mockResolvedValue(response);
  render(<SqlExportButton {...props} />);
  fireEvent.click(screen.getByRole('button'));
  await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  expect(URL.createObjectURL).toHaveBeenCalledWith(response.blob);
  await waitFor(() =>
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:export'),
  );
  expect(copy).not.toHaveBeenCalled();
});

test('a revoked source shows an error and produces no download or copy', async () => {
  exporter.mockRejectedValue(new Error('Доступ к таблице отозван'));
  render(<SqlExportButton {...props} copySql />);
  fireEvent.click(screen.getByRole('button'));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Доступ к таблице отозван',
  );
  expect(copy).not.toHaveBeenCalled();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});

test.each(['source', 'project', 'state', 'disabled', 'unmount', 'pagehide'])(
  'ignores a late download after %s changes',
  async (change) => {
    let release!: (value: ReturnType<typeof file>) => void;
    exporter.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const view = render(<SqlExportButton {...props} />);
    fireEvent.click(screen.getByRole('button'));
    const signal = exporter.mock.calls[0][2]!;
    if (change === 'unmount') view.unmount();
    else if (change === 'pagehide') fireEvent(window, new Event('pagehide'));
    else
      view.rerender(
        <SqlExportButton
          {...props}
          projectId={change === 'project' ? 'other-project' : props.projectId}
          stateVersion={change === 'state' ? 2 : 1}
          disabled={change === 'disabled'}
          command={
            change === 'source'
              ? { ...command, query_artifact_id: 'new-query' }
              : command
          }
        />,
      );
    await act(async () => release(file()));
    expect(signal.aborted).toBe(true);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(copy).not.toHaveBeenCalled();
  },
);

test('restoring a page cancels the old request and permits a fresh authorized attempt', async () => {
  exporter
    .mockReturnValueOnce(new Promise(() => {}))
    .mockResolvedValueOnce(file());
  render(<SqlExportButton {...props} />);
  fireEvent.click(screen.getByRole('button'));
  fireEvent(window, new Event('pagehide'));
  fireEvent(window, new Event('pageshow'));
  fireEvent.click(screen.getByRole('button'));
  await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  expect(exporter).toHaveBeenCalledTimes(2);
});

test('expired export clears the owning result without producing a download', async () => {
  const expired = jest.fn();
  exporter.mockRejectedValueOnce({ code: 'SQL_RESULT_EXPIRED' });
  render(
    <SqlExportButton
      {...props}
      command={{
        schema_version: '1',
        kind: 'RESULT',
        format: 'json',
        run_id: 'run',
      }}
      onExpired={expired}
    />,
  );
  fireEvent.click(screen.getByRole('button'));
  await waitFor(() => expect(expired).toHaveBeenCalledTimes(1));
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(copy).not.toHaveBeenCalled();
});

test('a late expiry from another project cannot clear the current result', async () => {
  let reject!: (failure: unknown) => void;
  exporter.mockReturnValueOnce(
    new Promise((_resolve, fail) => {
      reject = fail;
    }),
  );
  const old = jest.fn();
  const current = jest.fn();
  const view = render(<SqlExportButton {...props} onExpired={old} />);
  fireEvent.click(screen.getByRole('button'));
  view.rerender(
    <SqlExportButton {...props} projectId="another" onExpired={current} />,
  );
  await act(async () => reject({ code: 'SQL_RESULT_EXPIRED' }));
  expect(old).not.toHaveBeenCalled();
  expect(current).not.toHaveBeenCalled();
});
