export const RELOAD_GUARD_KEY = 'mas:stale-chunk-reload-at';
export const RELOAD_GUARD_WINDOW_MS = 60_000;

export type ReloadDecision = 'reload' | 'banner';
export interface TimestampStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** Decide whether a prior failed reload is recent enough to stop another loop. */
export function decideStaleChunkRecovery(
  previousTimestamp: string | null,
  now: number,
): ReloadDecision {
  if (previousTimestamp == null) return 'reload';
  if (!/^\d+(?:\.\d+)?$/.test(previousTimestamp)) return 'banner';
  const previous = Number(previousTimestamp);
  if (!Number.isFinite(previous)) return 'banner';
  const age = now - previous;
  // A future timestamp is treated conservatively as a recent attempt.
  return age < RELOAD_GUARD_WINDOW_MS ? 'banner' : 'reload';
}

/** Read and persist the guard timestamp; storage failures always stop automatic reloads. */
export function recordStaleChunkRecovery(
  storage: TimestampStorage | null | undefined,
  now: number,
): ReloadDecision {
  if (!storage) return 'banner';
  try {
    const decision = decideStaleChunkRecovery(storage.getItem(RELOAD_GUARD_KEY), now);
    if (decision === 'reload') storage.setItem(RELOAD_GUARD_KEY, String(now));
    return decision;
  } catch {
    return 'banner';
  }
}
