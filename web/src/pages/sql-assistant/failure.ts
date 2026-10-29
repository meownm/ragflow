import { AssistantRequestError } from '@/services/sql-assistant-service';
import axios from 'axios';

export function assistantFailure(error: unknown) {
  const response = axios.isAxiosError(error) ? error.response : undefined;
  const status =
    error instanceof AssistantRequestError ? error.status : response?.status;
  const code =
    error instanceof AssistantRequestError
      ? error.code
      : (response?.data?.data?.error_code as string | undefined);
  if (code === 'SQL_EXECUTION_UNKNOWN' || code === 'SQL_RESULT_UNAVAILABLE')
    return {
      message:
        'Исход выполнения ещё не подтверждён. Повторная отправка запрещена; обновите состояние для сверки с исполнителем.',
      recovery: 'RECONCILE',
    } as const;
  if (status === 401 || status === 403 || status === 404)
    return {
      message:
        'Доступ к операции или источнику недоступен. Ввод сохранён; проверьте права.',
      recovery: 'DENIED',
    } as const;
  if (code === 'SQL_METRIC_SELECTION_REQUIRED')
    return {
      message:
        'Найдены согласованные определения. Выберите определение или явно продолжите исследование.',
      recovery: 'RESELECT',
    } as const;
  if (
    code === 'SQL_REFERENCE_UNAVAILABLE' ||
    code === 'SQL_REFERENCE_STALE' ||
    code === 'SQL_DEFINITION_STALE'
  )
    return {
      message:
        'Выбранный объект изменился или недоступен. Обновите состояние и выберите его заново.',
      recovery: 'RESELECT',
    } as const;
  if (status === 409)
    return {
      message:
        'Исследование или выбранный контекст изменились. Обновите состояние и проверьте ввод перед отправкой.',
      recovery: 'REFRESH',
    } as const;
  if (status && status >= 400 && status < 500)
    return {
      message:
        'Проверьте вопрос и выбранные объекты. Операция не принята; ввод сохранён.',
      recovery: 'EDIT',
    } as const;
  return {
    message:
      'Сервис временно недоступен или ответ не подтверждён. Ввод сохранён. Повторите отправку позже; повтор использует тот же ключ операции.',
    recovery: 'RETRY_LATER',
  } as const;
}
