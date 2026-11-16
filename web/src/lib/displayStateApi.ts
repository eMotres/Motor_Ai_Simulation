/** `/api/display-state` from the browser (staff only; pure helpers: lib/displayState.ts). */
import { useCallback, useEffect, useState } from 'react';
import type { DisplayState, DisplayStateRec, DisplayStates } from './displayState';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

async function call<T>(method: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}/api/display-state`, {
    method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${r.status}`);
  }
  return data as T;
}

export type DisplayTarget = { kind: 'project'; project_id: string } | { kind: 'production'; die: string; config: string };

export const displayStateApi = {
  list: () => call<DisplayStates>('GET'),
  set: (target: DisplayTarget, state: DisplayState | null, reason = '') =>
    call<DisplayStateRec & { key: string }>('PUT', { ...target, state, reason }),
};

/** The states for a staff page (`enabled` false = a customer or factory view: nothing is loaded, nothing changes). */
export function useDisplayStates(enabled: boolean): { states: DisplayStates | null; reload: () => void } {
  const [states, setStates] = useState<DisplayStates | null>(null);
  const reload = useCallback(() => {
    if (!enabled) { setStates(null); return; }
    displayStateApi.list().then(setStates).catch(() => setStates(null));   // a failed load shows every row as before
  }, [enabled]);
  useEffect(() => { reload(); }, [reload]);
  return { states, reload };
}
