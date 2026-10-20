export interface AuthEventCountInput { ts: string; event: string; reason?: string }

export function countAuthRejects24h(events: AuthEventCountInput[], nowMs = Date.now()): number {
  const cutoff = nowMs / 1000 - 86400;
  return events.filter((event) => event.event === 'reject' && event.reason !== 'idle_timeout'
    && Date.parse(event.ts) / 1000 >= cutoff).length;
}
