// Which geometry parameters Sweep / Optimize / DOE may vary — the pure part (owner 2026-10-30).
//
// The server holds the set (`sweep_whitelist` of the workspace config; GET/PUT /api/geometry/optimizable).  An EMPTY list
// there means "every parameter is allowed" (`all_allowed`).  The Geometry table's switches and the Optimization variable
// list both read the `optimizable` flag of each schema row; these helpers keep that flag in step with the server's answer
// without a reload.  No runtime imports: lib/__tests__/optimizableSet.test.mjs imports this file itself.

export interface OptimizableAnswer {
  /** the effective set, schema order (every numeric parameter when `all_allowed`) */
  optimizable: string[];
  /** what the file holds ([] = unrestricted) */
  stored: string[];
  all_allowed: boolean;
  /** parameters the guards let through whatever the list says (the load angle) */
  always_allowed: string[];
  known: string[];
  /** may THIS account change it (an engineer only reads on the one shared config) */
  can_edit: boolean;
}

interface Row { name: string; type?: string; optimizable?: boolean }

/** A parameter that CAN vary: a word-valued knob never is. */
const numeric = (r: Row): boolean => r.type !== 'string';

/** The schema with every row's `optimizable` flag set from `names` (or all numeric rows on, when `allAllowed`). */
export function withOptimizable<T extends Row>(schema: T[], names: string[], allAllowed: boolean): T[] {
  const on = new Set(names);
  return schema.map((r) => {
    const flag = allAllowed ? true : on.has(r.name);
    return r.optimizable === flag ? r : { ...r, optimizable: flag };
  });
}

/** How many numeric parameters are switched on right now. */
export function enabledCount(schema: Row[]): number {
  return schema.filter((r) => numeric(r) && r.optimizable !== false).length;
}

/** The only one left on: switching it off would send an empty list, which the server reads as "everything allowed". */
export function isLastEnabled(schema: Row[], name: string, allAllowed: boolean): boolean {
  if (allAllowed) return false;
  const row = schema.find((r) => r.name === name);
  return !!row && numeric(row) && row.optimizable !== false && enabledCount(schema) <= 1;
}

/** The request body for one switch. */
export function toggleBody(name: string, enable: boolean): { enable: string[] } | { disable: string[] } {
  return enable ? { enable: [name] } : { disable: [name] };
}

/** The names that would be enabled after one switch, for the optimistic update. */
export function afterToggle(schema: Row[], name: string, enable: boolean): string[] {
  const on = new Set(schema.filter((r) => numeric(r) && r.optimizable !== false).map((r) => r.name));
  if (enable) on.add(name); else on.delete(name);
  return schema.filter((r) => on.has(r.name)).map((r) => r.name);
}

/** The server's refusal as one short line: the first invalid parameter's message, else the plain detail. */
export function refusalText(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail;
  const d = detail as { invalid_parameters?: Array<{ message?: string }>; error?: string } | null;
  const m = d?.invalid_parameters?.find((p) => p?.message)?.message;
  return m || d?.error || fallback;
}
