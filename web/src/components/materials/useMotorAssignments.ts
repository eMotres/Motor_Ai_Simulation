/**
 * Hook: motor material assignments (region → material).
 *
 * The DEFAULT comes from the shared config (GET /api/materials). A signed-in
 * user's own choices are stored PER-USER in Firestore (users/{uid}/workspace/
 * assignments) and merged over the default, so one user's assignment never
 * mutates the shared config (multi-user). assign() writes per-user when signed
 * in; anonymous / local-dev falls back to the shared PATCH (back-compat, for
 * editing the default design). The per-request compute override
 * (MaterialOverrideSync) sends this merged assignment so the FEM solve is
 * per-user (Stage 2b).
 */
import { useState, useEffect, useCallback } from 'react';
import { doc, getDoc, setDoc } from 'firebase/firestore';
import { db } from '../../lib/firebase';
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

export function useMotorAssignments() {
  const { user } = useAuth();
  const uid = user?.uid ?? null;

  const [assignments, setAssignments] = useState<MotorAssignments | null>(null);
  const [loading, setLoading]         = useState(true);
  const [saving, setSaving]           = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    fetch(API, { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(async (shared) => {
        let merged = shared as MotorAssignments;
        if (db && uid) {                       // overlay the user's own assignments
          try {
            const snap = await getDoc(doc(db, 'users', uid, 'workspace', 'assignments'));
            if (snap.exists()) merged = { ...merged, ...(snap.data() as Partial<MotorAssignments>) };
          } catch { /* fall back to the shared default */ }
        }
        setAssignments(merged); setLoading(false);
      })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [uid]);

  useEffect(() => { refresh(); }, [refresh]);

  const assign = useCallback((part: string, material: string) => {
    setSaving(true);
    setError(null);
    setAssignments(prev => ({ ...(prev ?? {} as MotorAssignments), [part]: material }));   // optimistic
    (async () => {
      try {
        if (db && uid) {
          // per-user — never touches the shared config (multi-user isolation)
          await setDoc(doc(db, 'users', uid, 'workspace', 'assignments'),
                       { [part]: material }, { merge: true });
        } else {
          // anonymous / local dev: edit the shared default (back-compat)
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
  }, [uid]);

  return { assignments, loading, saving, error, assign, refresh };
}
