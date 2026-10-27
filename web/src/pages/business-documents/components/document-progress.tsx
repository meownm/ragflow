import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';
import { CheckCircle2, Clock3, RotateCcw, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import type {
  BusinessDocumentJobSummary,
  BusinessDocumentOperationState,
  BusinessDocumentProjection,
} from '../types';
import { BackgroundWork } from './background-work';
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
  if (job?.status === 'COMPLETED') return 'complete';
  if (job?.status === 'RETRY' || (job?.attempt ?? 0) > 1) return 'retry';
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

const shortOperationLabels: Record<BusinessDocumentOperationState, string> = {
  IDLE: 'Готово к работе',
  ANALYZING: 'Анализируем вводные',
  ANALYZING_REVIEW: 'Анализируем замечания',
  GENERATING_DRAFT: 'Готовим черновик',
  APPLYING_CHANGES: 'Готовим исправления',
  EXPORTING: 'Готовим файл',
  FAILED: 'Не удалось завершить работу',
};

export function BusinessDocumentStatus({
  document,
  operationLabel,
  onFocusSection,
}: {
  document: BusinessDocumentProjection;
  operationLabel: string;
  onFocusSection: (sectionId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const job = document.latest_job;
  const active = isBusinessDocumentOperationActive(document.operation_state);
  const work = document.background_work ?? [];
  const queued = work.filter((item) => item.status === 'QUEUED').length;
  const running = work.some((item) => item.status === 'RUNNING');
  const failed =
    document.operation_state === 'FAILED' ||
    job?.status === 'DEAD' ||
    work.some((item) => item.status === 'FAILED');
  const result = document.last_analysis;
  const unapplied = result && !result.applied && !result.superseded;
  const tone = failed
    ? 'failed'
    : unapplied
      ? 'retry'
      : !active && result?.applied
        ? 'complete'
        : progressTone(job, document.operation_state);
  const Icon = progressIcon(tone);
  const label = failed
    ? shortOperationLabels.FAILED
    : active
      ? shortOperationLabels[document.operation_state]
      : running
        ? 'Работа идёт в фоне'
        : queued
          ? 'Ожидает запуска'
          : unapplied
            ? 'Анализ не применён'
            : job?.status === 'COMPLETED' || result?.applied
              ? 'Обработка завершена'
              : operationLabel;
  const elapsed = active
    ? elapsedTime(job?.create_time).replace(' сек', ' с')
    : '';
  const retry = active && (job?.attempt ?? 0) > 1;
  const step = active ? progressStep(job) : null;
  const status = [
    label,
    step && `Этап ${step} из 4`,
    retry && `Попытка ${job?.attempt} из ${job?.max_attempts}`,
    queued && `В очереди: ${queued}`,
    result?.superseded && 'Новые замечания',
  ]
    .filter(Boolean)
    .join('. ');

  if (!active && !failed && !work.length && !result && !job) {
    return <p className="text-xs text-text-secondary">{operationLabel}</p>;
  }

  return (
    <>
      <span className="sr-only" role="status">
        {status}
      </span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="flex h-6 max-w-full items-center gap-2 rounded-sm text-left text-xs text-text-primary outline-none hover:bg-bg-card focus-visible:ring-1 focus-visible:ring-accent-primary sm:gap-2.5"
            aria-label={`Фоновая работа: ${status}`}
            data-testid="business-document-status-toggle"
          >
            <Icon
              aria-hidden="true"
              className={cn('size-3.5 shrink-0', toneClasses[tone].text)}
            />
            <span className="w-44 min-w-0 truncate font-medium">{label}</span>
            {step && (
              <span className="shrink-0 tabular-nums text-text-secondary">
                {step}/4
              </span>
            )}
            {elapsed && (
              <span className="shrink-0 tabular-nums text-text-secondary">
                {elapsed}
              </span>
            )}
            {retry && (
              <span className="shrink-0 tabular-nums text-text-secondary">
                <span className="hidden sm:inline">Попытка </span>
                {job?.attempt}/{job?.max_attempts}
              </span>
            )}
            {queued > 0 && (
              <span className="shrink-0 tabular-nums text-text-secondary">
                В очереди: {queued}
              </span>
            )}
            {result?.superseded && (
              <span className="flex h-6 shrink-0 items-center rounded-sm bg-state-warning/10 px-2 text-[11px] text-text-primary">
                <span className="hidden sm:inline">Новые замечания</span>
                <span className="sm:hidden">Новые</span>
              </span>
            )}
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          aria-label="Подробности фоновой работы"
          className="max-h-[min(70vh,560px)] w-[min(480px,calc(100vw-24px))] overflow-y-auto p-0"
          data-testid="business-document-activity"
        >
          {(active || job) && (
            <BusinessDocumentProgress
              job={job}
              operationState={document.operation_state}
              operationLabel={active ? operationLabel : label}
            />
          )}
          {failed && !hasBusinessDocumentError(job?.error) && (
            <div className="px-4 py-3 text-xs">
              <BusinessDocumentError error={document.last_error} />
            </div>
          )}
          <BackgroundWork
            document={document}
            onFocusSection={(id) => {
              setOpen(false);
              onFocusSection(id);
            }}
          />
        </PopoverContent>
      </Popover>
    </>
  );
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
