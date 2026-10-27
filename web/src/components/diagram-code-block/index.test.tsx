import '@/locales/config';
import { render, screen, waitFor } from '@testing-library/react';
import i18n from 'i18next';
import mermaid from 'mermaid';
import { DiagramCodeBlock, getDiagramKind } from '.';

jest.mock('mermaid', () => ({
  __esModule: true,
  default: {
    initialize: jest.fn(),
    render: jest.fn(),
  },
}));

const mockedMermaidRender = mermaid.render as jest.Mock;

describe('DiagramCodeBlock', () => {
  beforeEach(async () => {
    jest.clearAllMocks();
    await i18n.changeLanguage('ru');
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it.each([
    ['mermaid', 'mermaid'],
    ['meraid', 'mermaid'],
    ['plantuml', 'plantuml'],
    ['puml', 'plantuml'],
    ['startuml', 'plantuml'],
  ] as const)('maps %s to %s', (language, expected) => {
    expect(getDiagramKind(language)).toBe(expected);
  });

  it('renders and sanitizes Mermaid SVG', async () => {
    mockedMermaidRender.mockResolvedValue({
      svg: '<svg><text>Flow</text><image href="https://example.com/leak.png"/><script>alert(1)</script></svg>',
      diagramType: 'flowchart-v2',
      bindFunctions: undefined,
    });

    const { container } = render(
      <DiagramCodeBlock language="mermaid" source="flowchart LR; A --> B" />,
    );

    await waitFor(() => {
      expect(screen.getByText('Flow')).toBeInTheDocument();
    });
    expect(container.querySelector('script')).not.toBeInTheDocument();
    expect(container.querySelector('image')).not.toBeInTheDocument();
    expect(mockedMermaidRender).toHaveBeenCalledWith(
      expect.stringMatching(/^ragflow-mermaid-/),
      'flowchart LR; A --> B',
    );
    expect(mermaid.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ htmlLabels: false }),
    );
  });

  it('posts PlantUML source to the local renderer and sanitizes its SVG', async () => {
    const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        '<svg><text>Sequence</text><image href="https://example.com/leak.png"/><script>bad()</script></svg>',
        {
          status: 200,
          headers: { 'Content-Type': 'image/svg+xml' },
        },
      ),
    );
    const source = '@startuml\nAlice -> Bob: hello\n@enduml';

    const { container } = render(
      <DiagramCodeBlock language="plantuml" source={source} />,
    );

    await waitFor(() => {
      expect(screen.getByText('Sequence')).toBeInTheDocument();
    });
    expect(container.querySelector('script')).not.toBeInTheDocument();
    expect(container.querySelector('image')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/plantuml/svg/',
      expect.objectContaining({
        method: 'POST',
        body: source,
      }),
    );
  });

  it('shows Mermaid source when rendering fails', async () => {
    const source = 'not valid Mermaid source';
    mockedMermaidRender.mockRejectedValue(new Error('Mermaid render failed'));

    render(<DiagramCodeBlock language="mermaid" source={source} />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Mermaid render failed',
    );
    expect(screen.getByText(source)).toBeInTheDocument();
  });

  it.each([400, 502])(
    'shows PlantUML source on HTTP %s without a syntax error',
    async (status) => {
      jest
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(new Response('render failed', { status }));
      const source = '@startuml\ninvalid source\n@enduml';

      render(<DiagramCodeBlock language="plantuml" source={source} />);

      expect(await screen.findByRole('alert')).toHaveTextContent(
        `Не удалось построить схему PlantUML (HTTP ${status})`,
      );
      expect(screen.getByText(/invalid source/)).toBeInTheDocument();
    },
  );

  it('shows the one-based syntax error line and preserves the original source', async () => {
    const source = '@startuml\nstart\ninvalid source\n@enduml';
    jest.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<svg><script>bad()</script></svg>', {
        status: 400,
        headers: {
          'Content-Type': 'image/svg+xml',
          'X-PlantUML-Diagram-Error': 'Syntax Error?',
          'X-PlantUML-Diagram-Error-Line': '2',
        },
      }),
    );

    const { container } = render(
      <DiagramCodeBlock language="plantuml" source={source} />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Ошибка синтаксиса PlantUML в строке 3',
    );
    expect(container.querySelector('pre')?.textContent).toBe(source);
    expect(container.querySelector('svg')).not.toBeInTheDocument();
  });

  it.each([null, '-1', 'NaN', '99999'])(
    'shows syntax errors without an invalid line number (%s)',
    async (line) => {
      const headers: Record<string, string> = {
        'X-PlantUML-Diagram-Error': 'Syntax Error?',
      };
      if (line !== null) headers['X-PlantUML-Diagram-Error-Line'] = line;
      jest
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(
          new Response('render failed', { status: 400, headers }),
        );
      render(
        <DiagramCodeBlock language="plantuml" source="@startuml\n@enduml" />,
      );
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Ошибка синтаксиса PlantUML. Исправьте код схемы ниже.',
      );
    },
  );

  it('does not add an empty source block when no diagram code is available', async () => {
    mockedMermaidRender.mockRejectedValue(new Error('Mermaid render failed'));

    const { container } = render(
      <DiagramCodeBlock language="mermaid" source=" " />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Mermaid render failed',
    );
    expect(container.querySelector('pre')).not.toBeInTheDocument();
  });
});
