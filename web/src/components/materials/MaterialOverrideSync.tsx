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
      if (Object.keys(materials).length === 0) return null;   // only built-ins in use
      return JSON.stringify({ assignment: assignments, materials });
    });
    return () => setMatGetter(null);
  }, [assignments, library]);

  return null;
}
