import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';
import { CheckCircle2, Clock3, RotateCcw, TriangleAlert } from 'lucide-react';
import type {
  BusinessDocumentJobSummary,
  BusinessDocumentOperationState,
} from '../types';
import {
  BusinessDocumentError,
  hasBusinessDocumentError,
} from './document-error';

const stageLabels: Record<string, string> = {
  QUEUED: 'Ожидает запуска',
  STARTING: 'Запускаем обработку',
  RETRIEVING: 'Подбираем связанные материалы',
  RETRIEVED: 'Материалы подготовлены',
  GENERATING: 'Формируем результат',
  VALIDATING: 'Проверяем результат',
  EXPORTING: 'Формируем файл',
  PERSISTING: 'Сохраняем результат',
  RETRY_WAIT: 'Ожидает повторного запуска',
  COMPLETED: 'Обработка завершена',
  FAILED: 'Не удалось завершить обработку',
};

type ProgressTone = 'active' | 'retry' | 'failed' | 'complete';

const toneClasses: Record<
  ProgressTone,
  { text: string; surface: string; bar: string }
> = {
  active: {
    text: 'text-accent-primary',
    surface: 'border-accent-primary/20 bg-accent-primary/5',
    bar: '[&>div]:bg-accent-primary',
  },
  retry: {
    text: 'text-state-warning',
    surface: 'border-state-warning/30 bg-state-warning/5',
    bar: '[&>div]:bg-state-warning',
  },
  failed: {
    text: 'text-state-error',
    surface: 'border-state-error/30 bg-state-error/5',
    bar: '[&>div]:bg-state-error',
  },
  complete: {
    text: 'text-state-success',
    surface: 'border-state-success/30 bg-state-success/5',
    bar: '[&>div]:bg-state-success',
  },
};

function progressTone(
  job: BusinessDocumentJobSummary | null | undefined,
  operationState: BusinessDocumentOperationState,
): ProgressTone {
  if (job?.status === 'DEAD' || operationState === 'FAILED') return 'failed';
  if (job?.status === 'RETRY') return 'retry';
  if (job?.status === 'COMPLETED') return 'complete';
  return 'active';
}

function progressIcon(tone: ProgressTone) {
  if (tone === 'failed') return TriangleAlert;
  if (tone === 'retry') return RotateCcw;
  if (tone === 'complete') return CheckCircle2;
  return Clock3;
}

function elapsedTime(createdAt: number | null | undefined) {
  if (!createdAt) return '';
  const milliseconds =
    createdAt < 1_000_000_000_000 ? createdAt * 1000 : createdAt;
  const seconds = Math.max(0, Math.floor((Date.now() - milliseconds) / 1000));
  if (seconds < 60) return `${seconds} сек`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60
    ? `${minutes} мин`
    : `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
}

function progressStep(job: BusinessDocumentJobSummary | null | undefined) {
  if (job?.status === 'COMPLETED') return 4;
  if (job?.progress_stage === 'PERSISTING') return 4;
  if (job?.progress_stage === 'VALIDATING') return 3;
  if (['GENERATING', 'EXPORTING'].includes(job?.progress_stage ?? '')) return 2;
  return 1;
}

export function isBusinessDocumentOperationActive(
  operationState: BusinessDocumentOperationState,
) {
  return operationState !== 'IDLE' && operationState !== 'FAILED';
}

export function BusinessDocumentProgress({
  job,
  operationState,
  operationLabel,
  compact = false,
}: {
  job?: BusinessDocumentJobSummary | null;
  operationState: BusinessDocumentOperationState;
  operationLabel: string;
  compact?: boolean;
}) {
  const tone = progressTone(job, operationState);
  const classes = toneClasses[tone];
  const Icon = progressIcon(tone);
  const step = progressStep(job);
  const percent = step * 25;
  const stage =
    job?.progress_message ||
    (job?.progress_stage ? stageLabels[job.progress_stage] : '') ||
    operationLabel;
  const elapsed = elapsedTime(job?.create_time);
  const attempt = job?.attempt
    ? `Попытка ${job.attempt} из ${job.max_attempts}`
    : '';

  if (compact) {
    return (
      <div
        className="flex min-w-[180px] flex-1 items-center gap-2"
        data-testid="business-document-list-progress"
      >
        <Icon className={cn('size-3 shrink-0', classes.text)} />
        <span className={cn('min-w-0 truncate font-medium', classes.text)}>
          {stage}
        </span>
        <Progress
          value={percent}
          aria-label={`Этап ${step} из 4`}
          className={cn(
            'h-1 w-16 shrink-0 bg-border-button sm:w-24',
            classes.bar,
          )}
        />
        <span className="shrink-0 tabular-nums text-text-primary">
          Этап {step}/4
        </span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-2 border-b px-4 py-3 text-xs',
        classes.surface,
      )}
      data-testid="business-document-operation"
      role="status"
    >
      <Icon
        className={cn(
          'size-3.5 shrink-0',
          classes.text,
          tone === 'active' && 'animate-pulse',
        )}
      />
      <div className="min-w-0">
        <p className="font-medium text-text-primary">{operationLabel}</p>
        <p className="mt-1 break-words text-text-secondary">{stage}</p>
      </div>
      <span className="shrink-0 tabular-nums font-medium text-text-primary">
        Этап {step}/4
      </span>
      <Progress
        value={percent}
        aria-label={`Этап ${step} из 4`}
        className={cn('col-span-3 h-1 w-full bg-border-button', classes.bar)}
      />
      {(elapsed || attempt) && (
        <span className="col-span-3 tabular-nums text-[11px] text-text-secondary">
          {[elapsed, attempt].filter(Boolean).join(' · ')}
        </span>
      )}
      {hasBusinessDocumentError(job?.error) && (
        <div className="col-span-3 min-w-0 text-[11px] text-state-warning">
          <span>Предыдущая ошибка: </span>
          <BusinessDocumentError error={job?.error} />
        </div>
      )}
    </div>
  );
}
