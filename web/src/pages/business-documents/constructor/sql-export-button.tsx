import { Button } from '@/components/ui/button';
import {
  exportBusinessDocumentSqlProject,
  type SqlExportRequest,
} from '@/services/business-document-service';
import { useLayoutEffect, useRef, useState } from 'react';
import { isExpiredResultError } from './sql-result-deadline';

export function SqlExportButton({
  projectId,
  stateVersion,
  command,
  label,
  copySql = false,
  disabled = false,
  onExpired,
}: {
  projectId: string;
  stateVersion: number;
  command: SqlExportRequest;
  label: string;
  copySql?: boolean;
  disabled?: boolean;
  onExpired?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const expirationHandler = useRef(onExpired);
  expirationHandler.current = onExpired;
  const signature = JSON.stringify([
    projectId,
    stateVersion,
    command,
    disabled,
  ]);
  useLayoutEffect(() => {
    const abort = () => controller.current?.abort();
    const restore = () => {
      abort();
      setBusy(false);
      setCopied(false);
      setError(null);
    };
    setBusy(false);
    setCopied(false);
    setError(null);
    window.addEventListener('pagehide', abort);
    window.addEventListener('pageshow', restore);
    return () => {
      abort();
      window.removeEventListener('pagehide', abort);
      window.removeEventListener('pageshow', restore);
    };
  }, [signature]);

  const execute = async () => {
    if (disabled || busy) return;
    const request = new AbortController();
    controller.current?.abort();
    controller.current = request;
    setBusy(true);
    setError(null);
    setCopied(false);
    const active = () =>
      !request.signal.aborted && controller.current === request;
    try {
      const { blob, filename } = await exportBusinessDocumentSqlProject(
        projectId,
        command,
        request.signal,
      );
      if (!active()) return;
      if (copySql) {
        const packet = JSON.parse(await blob.text());
        if (!active()) return;
        if (command.kind !== 'QUERY' || typeof packet.sql !== 'string')
          throw new Error('Проверенный SQL недоступен.');
        await navigator.clipboard.writeText(packet.sql);
        if (active()) setCopied(true);
      } else {
        const url = URL.createObjectURL(blob);
        try {
          const link = document.createElement('a');
          link.href = url;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          link.remove();
        } finally {
          window.setTimeout(() => URL.revokeObjectURL(url), 0);
        }
      }
    } catch (failure) {
      if (
        active() &&
        isExpiredResultError(failure) &&
        expirationHandler.current
      ) {
        expirationHandler.current();
        return;
      }
      if (active())
        setError(
          failure instanceof Error
            ? failure.message
            : 'Не удалось получить экспорт.',
        );
    } finally {
      if (active()) setBusy(false);
    }
  };

  return (
    <div className="inline-flex flex-col items-start gap-1">
      <Button
        size="sm"
        variant="ghost"
        disabled={disabled || busy}
        onClick={() => void execute()}
      >
        {busy ? 'Подготовка…' : copied ? 'Скопировано' : label}
      </Button>
      {error && (
        <p role="alert" className="max-w-80 text-xs text-state-error">
          {error}
        </p>
      )}
    </div>
  );
}
