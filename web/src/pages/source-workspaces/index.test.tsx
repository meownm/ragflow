import {
  createSourceWorkspace,
  listSourceDatasets,
  listSourceWorkspaces,
} from '@/services/source-workbench-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SourceWorkspacesPage from './index';

jest.mock('@/services/source-workbench-service', () => ({
  createSourceWorkspace: jest.fn(),
  listSourceDatasets: jest.fn(),
  listSourceWorkspaces: jest.fn(),
  sourceRequestError: (error: Error) => error.message,
}));
jest.mock('@/components/source-workbench/source-picker', () => ({
  SourcePicker: () => null,
}));
jest.mock('@/components/source-workbench/source-chat', () => ({
  SourceChat: () => null,
}));
jest.mock('@/components/source-workbench/source-processor', () => ({
  SourceProcessor: () => null,
}));

test('search users can choose assigned sources with matching tenant and embedding model', async () => {
  jest.mocked(listSourceWorkspaces).mockResolvedValue([]);
  jest.mocked(listSourceDatasets).mockResolvedValue([
    { id: 'a', name: 'Первая', tenant_id: 'owner-1', embd_id: 'model-1' },
    { id: 'b', name: 'Вторая', tenant_id: 'owner-1', embd_id: 'model-1' },
    {
      id: 'c',
      name: 'Другой владелец',
      tenant_id: 'owner-2',
      embd_id: 'model-1',
    },
    {
      id: 'd',
      name: 'Другая модель',
      tenant_id: 'owner-1',
      embd_id: 'model-2',
    },
  ]);
  jest.mocked(createSourceWorkspace).mockResolvedValue({
    id: 'workspace-1',
    title: 'Новая подборка',
    dataset_ids: ['a', 'b'],
    selected_documents: [],
    search_queries: [],
    version: 1,
    created_at: '',
    updated_at: '',
  });
  render(<SourceWorkspacesPage />);

  const first = await screen.findByRole('checkbox', { name: 'Первая' });
  fireEvent.click(first);
  expect(screen.getByRole('checkbox', { name: 'Вторая' })).not.toBeDisabled();
  expect(
    screen.getByRole('checkbox', { name: /^Другой владелец/ }),
  ).toBeDisabled();
  expect(
    screen.getByRole('checkbox', { name: /^Другая модель/ }),
  ).toBeDisabled();

  fireEvent.click(screen.getByRole('checkbox', { name: 'Вторая' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Название подборки' }), {
    target: { value: 'Новая подборка' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Создать' }));
  await waitFor(() =>
    expect(createSourceWorkspace).toHaveBeenCalledWith({
      title: 'Новая подборка',
      dataset_ids: ['a', 'b'],
    }),
  );
});
