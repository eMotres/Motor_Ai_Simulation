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
import { useAuth, useApiReady } from '../../contexts/AuthContext';
import { setDutyMaterial } from '../../lib/dutySettings';
import { useMotorStore } from '../../stores/motorStore';

export interface MotorAssignments {
  stator_core: string;
  slot: string;
  air_gap: string;
  rotor_core: string;
  magnet: string;
  shaft: string;
  slot_insulation?: string;   // insulation (insulator)
  wire_insulation?: string;   // wire enamel (insulator)
  /** Carbon-fibre retaining ring on the rotor OD. Served by GET /api/materials
   *  ONLY when the machine has one (sleeve_thickness > 0), so it is optional
   *  and the Materials tab hides its row on every machine without a sleeve. */
  sleeve?: string;
}

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') + '/api/materials';
const LOCAL_KEY = 'mat.assign.local';

function readLocalOverlay(): Partial<MotorAssignments> {
  try { return JSON.parse(localStorage.getItem(LOCAL_KEY) || '{}'); }
  catch { return {}; }
}

export function useMotorAssignments() {
  const { isAdmin, enforced, tier } = useAuth();
  // GET /api/materials answers 401 to an anonymous caller, and this hook
  // mounts at the App root (MaterialOverrideSync) — the landing's first paint
  // knocked on it (live, 2026-09-16).  Same gate as the geometry/schema
  // probes; signing in re-runs the effect below and fills the assignment.
  const ready = useApiReady();
  // Ordinary user on an enforced backend → assignments live client-side.
  const localMode = enforced && !isAdmin;
  // Free-tier client → the motor card's materials are read-only (pro/team
  // engineers still tune materials on their own copy).
  const restricted = enforced && !isAdmin && tier !== 'pro' && tier !== 'team';

  const [assignments, setAssignments] = useState<MotorAssignments | null>(null);
  const [loading, setLoading]         = useState(true);
  const [saving, setSaving]           = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!ready) return;
    setLoading(true);
    fetch(API, { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((shared) => {
        let merged = shared as MotorAssignments;
        if (localMode) merged = { ...merged, ...readLocalOverlay() };
        setAssignments(merged); setLoading(false);
      })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [localMode, ready]);

  // The part LIST depends on the geometry: a sleeve exists only while
  // sleeve_thickness > 0, and the backend adds/drops the `sleeve` key of the
  // assignment accordingly — so a geometry edit must re-read it, or the
  // Materials tab keeps the old part list until something else refreshes it
  // (user 2026-09-04: "не вижу sleeve в дереве материалов").
  const sleeveT = useMotorStore(st => st.geometry?.sleeve_thickness);
  useEffect(() => { refresh(); }, [refresh, sleeveT]);

  // The duty-apply flow writes the overlay directly (catalog ▶ as a user);
  // pick the change up without a remount.  'mat-assign-changed' is the
  // CROSS-INSTANCE handshake: every component holds its own copy of this
  // hook's state, and without the broadcast the copy feeding ?mat= to the
  // solver (MaterialOverrideSync) kept the OLD assignment after the panel
  // saved a new one — a run after a material change silently used the
  // previous steel (measured live 2026-08-25: B15AHV950M rode a run made
  // after the switch to 20SW1200).
  useEffect(() => {
    const on = () => refresh();
    window.addEventListener('mat-assign-local-changed', on);
    window.addEventListener('mat-assign-changed', on);
    return () => {
      window.removeEventListener('mat-assign-local-changed', on);
      window.removeEventListener('mat-assign-changed', on);
    };
  }, [refresh]);

  const assign = useCallback((part: string, material: string) => {
    // FREE-tier clients: the motor's materials are FIXED by its card (user
    // 2026-08-25 — "менять можно только те материалы, которые есть в карточке
    // мотора").  The library stays browsable; assignment is the vendor's.
    // Central chokepoint on purpose: every assign entry point (panel,
    // cross-section, detail view) hits this one gate.
    if (restricted) {
      const cur = (assignments as Record<string, string> | null)?.[part];
      if (material !== cur) {
        setError('This motor’s materials are fixed by its card — '
          + 'duplicate the motor to your space to experiment, or contact the vendor.');
        return;
      }
    }
    // ── the change belongs to the ACTIVE DUTY too ────────────────────────────
    // A duty of this project is a whole thermal scenario ("peak 200C wire 120C
    // NdFeB"), so the materials it is characterised with are ITS materials
    // (user 2026-09-01: "нужно запоминать какие магниты, и не только магниты:
    // все материалы, для каждого duty").  This is the one chokepoint every
    // assign entry point (panel, cross-section, detail view, the Simulation
    // badge's own picker) passes through, so filing it here files it once.
    // localStorage only — the stored duty changes on an explicit Save to duty
    // and never here (the no-silent-state rule).  No duty active → no-op, and
    // everything below behaves exactly as it did before this feature.
    //
    // The MACHINE-LEVEL write below still happens, unchanged: with no duty
    // selected it is the only record of the change, and with one selected the
    // duty override wins at request time anyway.  The consequence, stated
    // plainly: the machine's OWN assignment ends up at whatever the last edit
    // was, whichever duty was active when it was made — a duty remembers its
    // own materials, the machine remembers the last ones touched.
    try { setDutyMaterial(part, material); } catch { /* memory is a convenience */ }
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
        // NO auto-recompute (user 2026-08-25: "не надо автоматом запускать
        // пересчёт") — it raced its own trigger and solved with the OLD
        // material.  Instead: broadcast the change so EVERY hook instance
        // (incl. the one feeding ?mat= to the solver) adopts it, and let the
        // summary card dim itself with "⚠ materials changed" until the user
        // presses Run.  The card's staleness check carries the honesty that
        // the auto-rerun was papering over.
        try {
          window.dispatchEvent(new CustomEvent('mat-assign-changed'));
        } catch { /* SSR/no-window */ }
      } catch (e) { setError(String(e)); setSaving(false); }
    })();
  }, [localMode, restricted, assignments]);

  return { assignments, loading, saving, error, assign, refresh, restricted };
}
