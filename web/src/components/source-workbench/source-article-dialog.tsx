import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  previewSourceWorkspaceArticle,
  sourceRequestError,
  type SourceArticle,
  type SourceCandidate,
  type SourceReference,
} from '@/services/source-workbench-service';
import { useEffect, useState } from 'react';
import { SourceArticleContent } from './source-article-content';

export function SourceArticleDialog({
  workspaceId,
  source,
  onClose,
}: {
  workspaceId: string;
  source: SourceCandidate | SourceReference | null;
  onClose(): void;
}) {
  const [article, setArticle] = useState<SourceArticle | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!source) return;
    const controller = new AbortController();
    setArticle(null);
    setError('');
    setLoading(true);
    previewSourceWorkspaceArticle(workspaceId, source, controller.signal)
      .then(setArticle)
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(sourceRequestError(cause, 'Не удалось открыть статью'));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [workspaceId, source]);

  const fallbackTitle =
    source && 'title' in source ? source.title : source?.document_id;
  return (
    <Dialog
      open={Boolean(source)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="grid max-h-[92vh] max-w-[min(1120px,calc(100vw-2rem))] grid-rows-[auto_minmax(0,1fr)] overflow-hidden">
        <DialogHeader className="min-w-0 text-start">
          <DialogTitle className="break-words pe-8">
            {article?.title || fallbackTitle}
          </DialogTitle>
          <DialogDescription>
            Полный проиндексированный текст статьи
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto pe-2">
          {loading && (
            <p role="status" className="text-sm text-text-secondary">
              Загрузка статьи…
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-state-error">
              {error}
            </p>
          )}
          {article && (
            <SourceArticleContent
              text={article.text}
              label={`Содержимое статьи ${article.title}`}
              allowHtml
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
