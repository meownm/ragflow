import { Button, buttonVariants } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { Routes } from '@/routes';
import {
  BusinessDocumentRequestError,
  checkBusinessDocumentEvaAccess,
  type BusinessDocumentEvaAccess,
} from '@/services/business-document-service';
import { useQuery } from '@tanstack/react-query';
import { KeyRound, RefreshCw } from 'lucide-react';

export const evaAccessQueryKey = ['business-document-eva-access'];

export function useEvaAccess(enabled = true) {
  const { data: user } = useFetchUserInfo();
  return useQuery({
    queryKey: [...evaAccessQueryKey, user?.id],
    queryFn: checkBusinessDocumentEvaAccess,
    enabled: enabled && Boolean(user?.id),
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: 'always',
  });
}

type State = BusinessDocumentEvaAccess['state'] | 'UNCHECKED';
const errorStates: Record<string, State> = {
  EVA_USER_TOKEN_MISSING: 'MISSING',
  EVA_CREDENTIALS_MISSING: 'MISSING',
  EVA_USER_TOKEN_REJECTED: 'REJECTED',
  EVA_USER_TOKEN_INVALID: 'REJECTED',
  EVA_PERMISSION_DENIED: 'FORBIDDEN',
  EVA_SPACE_FORBIDDEN: 'FORBIDDEN',
  EVA_USER_TOKEN_UNAVAILABLE: 'UNAVAILABLE',
  EVA_UNAVAILABLE: 'UNAVAILABLE',
  EVA_USER_CHANGE_FAILED: 'UNAVAILABLE',
};
export function isEvaAccessError(error: unknown) {
  return (
    error instanceof BusinessDocumentRequestError &&
    Boolean(errorStates[error.code])
  );
}
const labels: Record<State, { label: string; description: string }> = {
  AVAILABLE: {
    label: 'EVA: доступна',
    description:
      'Личный доступ для чтения подтверждён. Запись зависит от ваших прав в EVA.',
  },
  MISSING: {
    label: 'EVA: нужен токен',
    description:
      'Добавьте личный EVA-токен для поиска страниц, импорта, обновлений и сохранения изменений. Локальная работа с требованиями доступна.',
  },
  REJECTED: {
    label: 'EVA: обновите токен',
    description:
      'EVA не принимает ваш личный токен. Возможно, он истёк или был отозван. Чтение и запись EVA недоступны.',
  },
  FORBIDDEN: {
    label: 'EVA: нет доступа',
    description:
      'Недостаточно прав в EVA на страницу или операцию. Обратитесь к администратору пространства.',
  },
  UNAVAILABLE: {
    label: 'EVA: недоступна',
    description:
      'Не удалось проверить доступ к EVA. Попробуйте снова позднее; обновлять токен из-за сетевой ошибки не требуется.',
  },
  NOT_CONFIGURED: {
    label: 'EVA: не подключена',
    description:
      'Подключение EVA для бизнес-требований не настроено. Обратитесь к администратору RAGFlow.',
  },
  UNCHECKED: {
    label: 'EVA: доступ не проверен',
    description:
      'Проверяем личный доступ к EVA. Сохранённое значение токена ещё не подтверждает его действительность.',
  },
};

export function EvaAccessNotice({
  access,
  compact = false,
  error,
  returnTo,
  onRetry,
}: {
  access: ReturnType<typeof useEvaAccess>;
  compact?: boolean;
  error?: unknown;
  returnTo: string;
  onRetry?: () => void;
}) {
  const accessError = isEvaAccessError(error)
    ? (error as BusinessDocumentRequestError)
    : undefined;
  const checkedAfterError =
    accessError &&
    access.data?.state === 'AVAILABLE' &&
    access.dataUpdatedAt > accessError.receivedAt;
  const code =
    accessError &&
    (!checkedAfterError || errorStates[accessError.code] === 'FORBIDDEN')
      ? accessError.code
      : undefined;
  const state: State = code
    ? errorStates[code]
    : (access.data?.state ?? (access.error ? 'UNAVAILABLE' : 'UNCHECKED'));
  if (!compact && state === 'AVAILABLE') return null;
  const tokenAction = ['MISSING', 'REJECTED'].includes(state);
  const settings = `${Routes.UserSetting}/profile?${new URLSearchParams({
    ...(access.data?.connector_id ? { eva: access.data.connector_id } : {}),
    returnTo,
  })}#eva-token`;
  const content = (
    <div className="space-y-3" role="status">
      <p className="text-sm font-medium">{labels[state].label}</p>
      <p className="text-xs text-text-secondary">{labels[state].description}</p>
      <div className="flex flex-wrap gap-2">
        {tokenAction && (
          <a
            className={buttonVariants({ size: 'sm', variant: 'outline' })}
            href={settings}
            target="_blank"
            rel="noreferrer"
            data-testid="eva-token-settings-link"
          >
            <KeyRound className="size-3.5" />
            {state === 'MISSING' ? 'Добавить токен' : 'Обновить токен'}
          </a>
        )}
        {state !== 'AVAILABLE' && (
          <Button
            size="sm"
            variant="ghost"
            disabled={access.isFetching}
            onClick={() => {
              onRetry?.();
              void access.refetch();
            }}
          >
            <RefreshCw className="size-3.5" />
            {access.isFetching
              ? 'Проверяем…'
              : state === 'UNAVAILABLE'
                ? 'Повторить проверку'
                : state === 'UNCHECKED'
                  ? 'Проверить доступ'
                  : 'Проверить снова'}
          </Button>
        )}
      </div>
    </div>
  );
  if (compact)
    return (
      <Popover>
        <PopoverTrigger asChild>
          <Button size="sm" variant="ghost" data-testid="eva-access-indicator">
            <span
              className={`size-1.5 rounded-full ${state === 'AVAILABLE' ? 'bg-state-success' : state === 'REJECTED' ? 'bg-state-error' : 'bg-state-warning'}`}
            />
            {access.isFetching ? 'EVA: проверяем…' : labels[state].label}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-[min(360px,calc(100vw-24px))]">
          {content}
        </PopoverContent>
      </Popover>
    );
  return (
    <div
      className="border-s-2 border-state-warning bg-state-warning/5 p-3"
      data-testid="eva-access-notice"
    >
      {content}
    </div>
  );
}
