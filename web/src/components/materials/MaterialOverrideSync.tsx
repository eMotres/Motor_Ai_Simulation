import { useEffect } from 'react';
import { useMotorAssignments } from './useMotorAssignments';
import { useMaterialsLibrary, MATERIAL_CATEGORIES } from './useMaterialsLibrary';
import { setMatGetter } from '../../lib/apiAuth';
import { stripMeta } from '../../lib/materialsActions';

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

  useEffect(() => {
    setMatGetter(() => {
      if (!assignments || !library) return null;
      const materials: Record<string, unknown> = {};
      for (const name of Object.values(assignments)) {
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
      if (Object.keys(assignments).length === 0) return null;
      return JSON.stringify({ assignment: assignments, materials });
    });
    return () => setMatGetter(null);
  }, [assignments, library]);

  return null;
}
