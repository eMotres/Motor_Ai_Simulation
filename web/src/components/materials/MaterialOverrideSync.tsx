import { useEffect } from 'react';
import { useMotorAssignments } from './useMotorAssignments';
import { usePartStates } from './usePartStates';
import { useMaterialsLibrary, MATERIAL_CATEGORIES } from './useMaterialsLibrary';
import { setMatGetter } from '../../lib/apiAuth';
import { stripMeta } from '../../lib/materialsActions';
import { activeDutyMaterials } from '../../lib/dutySettings';
import { effectiveAssignment } from '../../lib/dutyMaterials';

/**
 * Publishes the active per-user material override to the fetch interceptor so the
 * FEM solve uses the signed-in user's OWN materials (Stage 2b). It sends the
 * current assignment + the resolved props of any NON-built-in material in use
 * (mine / global) as `mat=` on simulation physics requests. When only built-ins
 * are assigned the getter returns null → no override → the solve is identical to
 * the shared-config behaviour. Renders nothing; mount once at the app root.
 */
export default function MaterialOverrideSync(): null {
  const { assignments } = useMotorAssignments();
  const { library } = useMaterialsLibrary();
  // Per-part accounting (included / reference / excluded) rides this SAME
  // payload on purpose: every backend cache key and fingerprint that already
  // hashes the material context then separates the three states for free, and
  // a client-mode user's frameless build reaches the solve the same way their
  // materials do — through the request, never through the shared config.
  const { states: partStates } = usePartStates();

  useEffect(() => {
    setMatGetter(() => {
      if (!assignments || !library) return null;
      // ── the ACTIVE DUTY's MATERIALS ───────────────────────────────────────
      // Read here, at REQUEST time, not captured in this effect's closure: the
      // duty selection changes without this component re-rendering, and a solve
      // must never carry the previously selected duty's materials.  Every part
      // the duty has an opinion about is laid over the machine's assignment,
      // each one validated against the library first (lib/dutyMaterials.ts) —
      // so the machine's ASSIGNMENT is never rewritten and no duty can smuggle
      // a name past the Materials tab that the library does not have.
      //
      // When the duty's map agrees with the machine (or says nothing, or no
      // duty is selected) `assign` IS `assignments` — the same object — and the
      // JSON below is byte-identical to the one this getter has always
      // produced, which is what keeps every backend fingerprint and cache key
      // that hashes this string honest.
      const assign = effectiveAssignment(
        assignments as unknown as Record<string, string | undefined>,
        activeDutyMaterials(),
        library as unknown as Record<string, Record<string, unknown>>);
      const materials: Record<string, unknown> = {};
      for (const name of Object.values(assign)) {
        if (!name || materials[name]) continue;
        for (const cat of MATERIAL_CATEGORIES) {
          const m = (library as Record<string, Record<string, any>>)[cat]?.[name];
          if (m && m._source && m._source !== 'builtin') {
            materials[name] = { ...stripMeta(m), category: cat };
            break;
          }
        }
      }
      // Send the ASSIGNMENT whenever there is one, even if every material in it
      // is a built-in. Returning null here (the old "only built-ins in use"
      // short-circuit) dropped the assignment along with the empty props map, so
      // the backend silently fell back to config/motor_config.yaml — picking a
      // library magnet in the UI had NO effect on the solve. `materials` may stay
      // empty: the backend resolves built-in names from its own library.
      const hasParts = partStates && Object.keys(partStates).length > 0;
      if (Object.keys(assign).length === 0 && !hasParts) return null;
      return JSON.stringify({
        assignment: assign, materials,
        // Omitted entirely when every part is included, so an ordinary
        // machine's `mat=` string — and therefore its cache key — is
        // byte-identical to the one it had before this feature existed.
        ...(hasParts ? { parts: partStates } : {}),
      });
    });
    // Tell the views that were waiting: from here on a physics request carries
    // the override, so a cache probe made now matches the key of the solve
    // (lib/apiAuth.matOverrideReady).
    if (assignments && library) {
      try { window.dispatchEvent(new CustomEvent('mat-override-ready')); } catch { /* no window */ }
    }
    return () => setMatGetter(null);
  }, [assignments, library, partStates]);

  return null;
}
