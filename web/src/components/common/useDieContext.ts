/**
 * useDieContext — the ACTIVE die (if any) and its die-defining keys, polled
 * from /api/family/context the way the Geometry table polls its locks.
 *
 * Read by the Geometry table (flag "die-defining — changing it makes a new
 * die" and confirm before the change) and by the Optimize/Sweep pickers (flag
 * the variable, show the "allow new lamination" consent the backend requires).
 * Warn BEFORE the change, not after — the after-the-fact release is what left
 * the owner's optimised machine unsaveable on 2026-09-20.
 */
import { useEffect, useState } from 'react';
import { pageVisible } from '../../lib/pageVisible';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export interface DieContext {
  /** a die is active in the editor (owner's server context) */
  active: boolean;
  die: string | null;
  config: string | null;
  dieLocked: boolean;
  /** the keys that make the die THIS die (backend DIE_IDENTITY_KEYS) */
  dieKeys: Set<string>;
}

const NONE: DieContext = { active: false, die: null, config: null,
                           dieLocked: false, dieKeys: new Set() };

export function useDieContext(pollMs = 10_000): DieContext {
  const [ctx, setCtx] = useState<DieContext>(NONE);
  useEffect(() => {
    let alive = true;
    const load = () => fetch(`${API}/api/family/context`, { cache: 'no-store' })
      .then(r => r.json())
      .then(c => {
        if (!alive) return;
        setCtx(c?.active && c?.can_write ? {
          active: true, die: String(c.die ?? ''), config: c.config ?? null,
          dieLocked: c.die_locked === true,
          dieKeys: new Set<string>(Array.isArray(c.die_keys) ? c.die_keys : []),
        } : NONE);
      })
      .catch(() => { if (alive) setCtx(NONE); });
    void load();
    const onChange = () => { void load(); };
    window.addEventListener('family-changed', onChange);
    window.addEventListener('sim-design-applied', onChange);
    const id = setInterval(() => { if (pageVisible()) void load(); }, pollMs);
    return () => {
      alive = false;
      window.removeEventListener('family-changed', onChange);
      window.removeEventListener('sim-design-applied', onChange);
      clearInterval(id);
    };
  }, [pollMs]);
  return ctx;
}
