import { render, screen, waitFor } from '@testing-library/react';
import { PlantUmlDiagram } from './plantuml-diagram';

const fetchMock = jest.fn();
const createObjectUrlMock = jest.fn(() => 'blob:plantuml-diagram');
const revokeObjectUrlMock = jest.fn();

beforeEach(() => {
  fetchMock.mockReset();
  createObjectUrlMock.mockClear();
  revokeObjectUrlMock.mockClear();
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    writable: true,
    value: fetchMock,
  });
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: createObjectUrlMock,
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    writable: true,
    value: revokeObjectUrlMock,
  });
});

it('renders PlantUML source through the local SVG endpoint', async () => {
  fetchMock.mockResolvedValue({
    ok: true,
    blob: async () => new Blob(['<svg />'], { type: 'image/svg+xml' }),
  });

  const source = '@startuml\nstart\nstop\n@enduml';
  const { unmount } = render(<PlantUmlDiagram source={source} />);

  expect(screen.getByText('Формируется диаграмма…')).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByRole('img', { name: 'Диаграмма PlantUML' }),
    ).toHaveAttribute('src', 'blob:plantuml-diagram'),
  );
  expect(fetchMock).toHaveBeenCalledWith('/plantuml/svg', {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: source,
    signal: expect.any(AbortSignal),
  });

  unmount();
  expect(revokeObjectUrlMock).toHaveBeenCalledWith('blob:plantuml-diagram');
});

it('shows the source when PlantUML rendering fails', async () => {
  fetchMock.mockResolvedValue({ ok: false, status: 503 });

  render(<PlantUmlDiagram source="@startuml\n@enduml" />);

  expect(
    await screen.findByText('Не удалось отрисовать диаграмму PlantUML.'),
  ).toBeInTheDocument();
  expect(screen.getByText(/@startuml/)).toBeInTheDocument();
});
