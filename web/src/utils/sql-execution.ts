/** UNKNOWN requires reconciliation and cannot authorize another submission. */
export function isSqlRunUnresolved(status: string) {
  return ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED', 'UNKNOWN'].includes(status);
}
