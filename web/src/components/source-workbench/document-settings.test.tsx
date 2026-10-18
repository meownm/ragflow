import * as service from '@/services/linked-document-service';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { WorkspaceMembers } from './document-settings';

jest.mock('@/utils/next-request', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
}));
jest.mock('@/services/source-workbench-service', () => ({
  sourceRequestError: (error: Error) => error.message,
}));
jest.mock('@/services/linked-document-service', () => ({
  ...jest.requireActual('@/services/linked-document-service'),
  listWorkspaceMembers: jest.fn(),
  getWorkspaceOrganization: jest.fn(),
  setWorkspaceOrganization: jest.fn(),
  setWorkspaceMember: jest.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const result = {
  tenant_id: 'tenant',
  version: 2,
  items: [{ id: 'tenant', name: 'Организация' }],
  workspace: {
    id: 'first',
    title: 'Первое пространство',
    dataset_ids: [],
    selected_documents: [],
    search_queries: [],
    version: 2,
    created_at: '',
    updated_at: '',
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(service.listWorkspaceMembers).mockResolvedValue([]);
  jest
    .mocked(service.getWorkspaceOrganization)
    .mockResolvedValue({ ...result, tenant_id: null, version: 1 });
});

async function submitOrganization() {
  const details = screen
    .getByText('Участники пространства')
    .closest('details')!;
  details.open = true;
  fireEvent(details, new Event('toggle'));
  const button = await screen.findByRole('button', {
    name: 'Назначить организацию',
  });
  await act(async () => {
    fireEvent.click(button);
  });
}

test('organization assignment updates the current workspace', async () => {
  const onWorkspaceChange = jest.fn();
  jest.mocked(service.setWorkspaceOrganization).mockResolvedValue(result);
  render(
    <WorkspaceMembers
      workspaceId="first"
      canManage
      onWorkspaceChange={onWorkspaceChange}
    />,
  );
  await submitOrganization();
  expect(onWorkspaceChange).toHaveBeenCalledWith(result.workspace);
});

test('late organization assignment cannot navigate back after switching workspace', async () => {
  const response = deferred<typeof result>();
  const onWorkspaceChange = jest.fn();
  jest
    .mocked(service.setWorkspaceOrganization)
    .mockReturnValue(response.promise);
  const { rerender } = render(
    <WorkspaceMembers
      workspaceId="first"
      canManage
      onWorkspaceChange={onWorkspaceChange}
    />,
  );
  await submitOrganization();
  expect(service.setWorkspaceOrganization).toHaveBeenCalledWith(
    'first',
    'tenant',
    1,
  );
  rerender(
    <WorkspaceMembers
      workspaceId="second"
      canManage
      onWorkspaceChange={onWorkspaceChange}
    />,
  );
  await act(async () => {
    response.resolve(result);
  });
  expect(onWorkspaceChange).not.toHaveBeenCalled();
  expect(
    await screen.findByRole('button', { name: 'Назначить организацию' }),
  ).toBeEnabled();
});

test('late organization assignment is ignored after leaving the workbench', async () => {
  const response = deferred<typeof result>();
  const onWorkspaceChange = jest.fn();
  jest
    .mocked(service.setWorkspaceOrganization)
    .mockReturnValue(response.promise);
  const { unmount } = render(
    <WorkspaceMembers
      workspaceId="first"
      canManage
      onWorkspaceChange={onWorkspaceChange}
    />,
  );
  await submitOrganization();
  unmount();
  await act(async () => {
    response.resolve(result);
  });
  expect(onWorkspaceChange).not.toHaveBeenCalled();
});
