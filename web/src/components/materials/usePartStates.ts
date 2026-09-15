/**
 * Hook: per-part ACCOUNTING state (included / reference / excluded).
 *
 * Exact twin of `useMotorAssignments`, one field over, and deliberately so —
 * the state map rides the same `?mat=` payload the material assignment does,
 * lands in the same config block, and is hashed by the same fingerprints.
 *
 *   included   (default) — ours: in the field, the mass, the inertia, the sheet.
 *   reference  — the customer's part sitting in our field (a frameless motor's
 *                shaft): solved with its assigned material, its losses honestly
 *                reported, and out of every mass, inertia and N·m/kg.
 *   excluded   — solved as air, weighs nothing, drawn nowhere.
 *
 * The DEFAULT comes from the shared config (GET /api/parts). On an enforced
 * backend an ordinary user's choices are a CLIENT-SIDE overlay (localStorage
 * `parts.state.local`) merged over it — one user's frameless experiment never
 * mutates the shared machine. The owner (admin / local dev) PATCHes the shared
 * default, exactly as with materials.
 */
import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../contexts/AuthContext';

export type PartState = 'included' | 'reference' | 'excluded';

export const PART_STATES: PartState[] = ['included', 'reference', 'excluded'];

/** Parts that can carry a state — same keys the material assignment uses. */
export const STATEFUL_PARTS = ['stator_core', 'rotor_core', 'magnet', 'slot', 'shaft',
                               // only present when sleeve_thickness > 0
                               'sleeve'] as const;

/** Excluding one of these removes the machine's magnetics, not its packaging. */
export const MAGNETICALLY_ACTIVE_PARTS: string[] = ['stator_core', 'rotor_core', 'magnet', 'slot'];

/** `{part: state}` holding ONLY the non-default entries. */
export type PartStates = Record<string, PartState>;

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') + '/api/parts';
const LOCAL_KEY = 'parts.state.local';

function readLocalOverlay(): PartStates {
  try { return JSON.parse(localStorage.getItem(LOCAL_KEY) || '{}') as PartStates; }
  catch { return {}; }
}

// A dozen consumers mount at once (five tree badges, seven mesh components) and
// each one wants the same three-byte answer.  Share ONE request between them and
// keep the answer until something says it changed — the same in-flight-promise
// pattern `useMotorMesh` uses, for the same reason.
let sharedInflight: Promise<PartStates> | null = null;
let sharedValue: PartStates | null = null;

function fetchShared(): Promise<PartStates> {
  if (sharedValue) return Promise.resolve(sharedValue);
  if (!sharedInflight) {
    sharedInflight = fetch(API, { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d: PartStates) => { sharedValue = d || {}; return sharedValue; })
      // A backend without the endpoint is a machine with every part included —
      // never a reason to leave the viewer blank.
      .catch(() => ({} as PartStates))
      .finally(() => { sharedInflight = null; });
  }
  return sharedInflight;
}

function invalidateShared(): void { sharedValue = null; }

/** Drop `included` entries so the map always means "what is NOT default". */
function prune(m: PartStates): PartStates {
  const out: PartStates = {};
  for (const [k, v] of Object.entries(m || {})) if (v && v !== 'included') out[k] = v;
  return out;
}

export function usePartStates() {
  const { isAdmin, enforced, tier } = useAuth();
  const localMode  = enforced && !isAdmin;
  const restricted = enforced && !isAdmin && tier !== 'pro' && tier !== 'team';

  const [states, setStates]   = useState<PartStates>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    fetchShared().then((shared: PartStates) => {
      let merged = { ...(shared || {}) };
      if (localMode) merged = { ...merged, ...readLocalOverlay() };
      setStates(prune(merged)); setLoading(false);
    });
  }, [localMode]);

  useEffect(() => { refresh(); }, [refresh]);

  // Same cross-instance handshake the material assignment uses: every consumer
  // (the viewer that must not draw an excluded part, the sync that puts the map
  // on `?mat=`) holds its own copy of this state, and without the broadcast one
  // of them keeps solving/drawing the previous accounting.
  useEffect(() => {
    const on = () => { invalidateShared(); refresh(); };
    window.addEventListener('part-state-changed', on);
    return () => window.removeEventListener('part-state-changed', on);
  }, [refresh]);

  const setState = useCallback((part: string, state: PartState) => {
    if (restricted) {
      setError('This motor’s build is fixed by its card — duplicate the motor '
             + 'to your space to experiment, or contact the vendor.');
      return;
    }
    setSaving(true);
    setError(null);
    setStates(prev => prune({ ...prev, [part]: state }));   // optimistic
    (async () => {
      try {
        if (localMode) {
          const cur = prune({ ...readLocalOverlay(), [part]: state });
          try { localStorage.setItem(LOCAL_KEY, JSON.stringify(cur)); } catch { /* quota */ }
        } else {
          const r = await fetch(API, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ part, state }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const d = await r.json();
          setStates(prune(d.parts || {}));
        }
        // The shared answer is now stale for every other consumer; the
        // broadcast below makes each of them re-read it.
        invalidateShared();
        setSaving(false);
        // No auto-recompute, for the reason the material assignment does not
        // do one either: it races its own trigger and solves the OLD machine.
        // The card dims itself until the user presses Run.
        try { window.dispatchEvent(new CustomEvent('part-state-changed')); }
        catch { /* SSR/no-window */ }
      } catch (e) { setError(String(e)); setSaving(false); }
    })();
  }, [localMode, restricted]);

  const stateOf = useCallback(
    (part: string): PartState => (states[part] as PartState) || 'included', [states]);

  return { states, stateOf, setState, loading, saving, error, refresh, restricted };
}
