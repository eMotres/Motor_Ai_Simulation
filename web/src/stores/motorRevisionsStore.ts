// The product revisions (Rev A, Rev B ...) the signed-in account may see, keyed by motor (lib/motorRevisions.ts).  ONE request serves
// every row of the catalogue, the project page and the strips: concurrent callers share the in-flight request and a copy younger than
// TTL_MS is reused.  A 401 / network failure leaves the map empty and retries on the next ask (nothing is shown rather than a guess).
import { useEffect } from 'react';
import { create } from 'zustand';
import { buildRevisionMap, revisionFor, type RevisionMap, type RevisionRow } from '../lib/motorRevisions';
import { motorRevisionsApi } from '../lib/motorRevisionsApi';

const TTL_MS = 60_000;

interface RevisionState {
  map: RevisionMap;
  loadedAt: number;
  /** load (or reload) the index; `force` skips the TTL */
  ensure: (force?: boolean) => Promise<void>;
  reset: () => void;
}

let inflight: Promise<void> | null = null;

export const useMotorRevisionsStore = create<RevisionState>()((set, get) => ({
  map: new Map(),
  loadedAt: 0,
  ensure: (force = false) => {
    if (inflight) return inflight;
    if (!force && get().loadedAt > 0 && Date.now() - get().loadedAt < TTL_MS) return Promise.resolve();
    inflight = motorRevisionsApi.index()
      .then((rows) => set({ map: buildRevisionMap(rows), loadedAt: Date.now() }))
      .catch(() => set({ map: new Map(), loadedAt: 0 }))
      .finally(() => { inflight = null; });
    return inflight;
  },
  reset: () => set({ map: new Map(), loadedAt: 0 }),
}));

try { window.addEventListener('family-changed', () => { void useMotorRevisionsStore.getState().ensure(true); }); } catch { /* no window */ }

/** The revision of one motor (null when it has none or the list is not loaded yet); asks for the list on mount (TTL-shared). */
export function useMotorRevision(motor: { die: string; config: string; canonical_die?: string | null }): RevisionRow | null {
  const map = useMotorRevisionsStore((s) => s.map);
  useEffect(() => { void useMotorRevisionsStore.getState().ensure(); }, []);
  return revisionFor(map, motor);
}
