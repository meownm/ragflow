import { useEffect, useState } from 'react';

interface PlantUmlDiagramProps {
  source: string;
}

export function PlantUmlDiagram({ source }: PlantUmlDiagramProps) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;

    setImageUrl(null);
    setError(false);

    void fetch('/plantuml/svg', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      body: source,
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`PlantUML renderer returned ${response.status}`);
        }
        return response.blob();
      })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [source]);

  if (error) {
    return (
      <div className="my-5 rounded border border-state-error/40 bg-state-error/5 p-4">
        <p className="mb-2 text-sm text-state-error">
          Не удалось отрисовать диаграмму PlantUML.
        </p>
        <pre className="overflow-x-auto text-xs">
          <code>{source}</code>
        </pre>
      </div>
    );
  }

  if (!imageUrl) {
    return (
      <div
        className="my-5 rounded border border-border-button bg-bg-card px-4 py-6 text-center text-sm text-text-secondary"
        aria-busy="true"
      >
        Формируется диаграмма…
      </div>
    );
  }

  return (
    <figure className="my-5 overflow-x-auto rounded border border-border-button bg-white p-4">
      <img
        src={imageUrl}
        alt="Диаграмма PlantUML"
        className="mx-auto h-auto max-w-full"
      />
    </figure>
  );
}
