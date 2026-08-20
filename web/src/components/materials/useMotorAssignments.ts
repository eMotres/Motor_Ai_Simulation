/**
 * Hook: motor material assignments (region → material).
 *
 * The DEFAULT comes from the shared config (GET /api/materials).  On an
 * enforced backend an ordinary user's choices are a CLIENT-SIDE overlay
 * (localStorage `mat.assign.local`) merged over that default — one user's
 * assignment never mutates the shared config.  The per-request compute
 * override (MaterialOverrideSync) sends the merged assignment as ?mat=, so
 * the FEM solve is per-user.  The owner (admin / local dev) edits the shared
 * default directly via PATCH, exactly as before.
 */
import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../contexts/AuthContext';

export interface MotorAssignments {
  stator_core: string;
  slot: string;
  air_gap: string;
  rotor_core: string;
  magnet: string;
  shaft: string;
  slot_insulation?: string;   // slot liner (insulator)
  wire_insulation?: string;   // wire enamel (insulator)
}

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000') + '/api/materials';
const LOCAL_KEY = 'mat.assign.local';

function readLocalOverlay(): Partial<MotorAssignments> {
  try { return JSON.parse(localStorage.getItem(LOCAL_KEY) || '{}'); }
  catch { return {}; }
}

export function useMotorAssignments() {
  const { isAdmin, enforced } = useAuth();
  // Ordinary user on an enforced backend → assignments live client-side.
  const localMode = enforced && !isAdmin;

  const [assignments, setAssignments] = useState<MotorAssignments | null>(null);
  const [loading, setLoading]         = useState(true);
  const [saving, setSaving]           = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    fetch(API, { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((shared) => {
        let merged = shared as MotorAssignments;
        if (localMode) merged = { ...merged, ...readLocalOverlay() };
        setAssignments(merged); setLoading(false);
      })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [localMode]);

  useEffect(() => { refresh(); }, [refresh]);

  // The duty-apply flow writes the overlay directly (catalog ▶ as a user);
  // pick the change up without a remount.
  useEffect(() => {
    const on = () => refresh();
    window.addEventListener('mat-assign-local-changed', on);
    return () => window.removeEventListener('mat-assign-local-changed', on);
  }, [refresh]);

  const assign = useCallback((part: string, material: string) => {
    setSaving(true);
    setError(null);
    setAssignments(prev => ({ ...(prev ?? {} as MotorAssignments), [part]: material }));   // optimistic
    (async () => {
      try {
        if (localMode) {
          // client-side copy — never touches the shared config
          const cur = readLocalOverlay();
          (cur as Record<string, string>)[part] = material;
          try { localStorage.setItem(LOCAL_KEY, JSON.stringify(cur)); } catch { /* quota */ }
        } else {
          // the owner edits the shared default design
          const r = await fetch(API, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ part, material }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const d = await r.json();
          setAssignments(d.assignments);
        }
        setSaving(false);
        // A different material IS a different machine: the numbers on the
        // Simulation tab now belong to the previous one.  Drop them and recompute,
        // the same handshake an applied design uses — without this the tab kept
        // showing the old magnet's torque and losses with nothing to say so, which
        // read as "I changed the material and nothing happened".
        try {
          window.dispatchEvent(new CustomEvent('sim-design-applied'));
          window.dispatchEvent(new CustomEvent('sim-rerun'));
        } catch { /* SSR/no-window */ }
      } catch (e) { setError(String(e)); setSaving(false); }
    })();
  }, [localMode]);

  return { assignments, loading, saving, error, assign, refresh };
}
