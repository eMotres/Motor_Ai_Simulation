/** ONE answer to "does this machine have a retaining sleeve?" (owner 2026-10-26) - the web mirror of
 *  `src/motor_ai_sim/sleeve.py`; keep the two in step.
 *
 *  «если нет слива, то не надо о нём ничего писать» - a rotor whose `sleeve_thickness` is 0 has NO sleeve, even when its
 *  material assignment still carries a sleeve card (`sleeve: M40X_UD_60`).  Every surface that would name the sleeve
 *  (materials panel, Mechanical and Thermal rows, Compare columns) asks here.
 *
 *  Accepts a thickness in mm, a geometry object (`sleeve_thickness` / `sleeve_thickness_mm`, also nested under
 *  `geometry`), or a solved result that says so (`has_sleeve`, or a `sleeve` entry among the solved `parts`).
 *  No React, no window: `node --test` imports this file. */

const THICKNESS_KEYS = ['sleeve_thickness', 'sleeve_thickness_mm'] as const;
const NESTED_KEYS = ['geometry', 'geo', 'geometry_params'] as const;

const finite = (v: unknown): number | null => {
  if (typeof v === 'boolean' || v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};

const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

/** The stated sleeve thickness in mm, or null when the source states none. */
export function sleeveThicknessMm(source: unknown): number | null {
  if (isRecord(source)) {
    for (const key of THICKNESS_KEYS) if (key in source) return finite(source[key]);
    for (const key of NESTED_KEYS) {
      const inner = source[key];
      if (isRecord(inner)) {
        const found = sleeveThicknessMm(inner);
        if (found !== null) return found;
      }
    }
    return null;
  }
  return finite(source);
}

/** True only when the machine really has a retaining sleeve (thickness > 0, or a solved result that carries one). */
export function hasSleeve(source: unknown): boolean {
  if (isRecord(source)) {
    const thickness = sleeveThicknessMm(source);
    if (thickness !== null) return thickness > 0;
    if (typeof source.has_sleeve === 'boolean') return source.has_sleeve;
    const parts = source.parts;
    return isRecord(parts) && isRecord(parts.sleeve);
  }
  const thickness = finite(source);
  return thickness !== null && thickness > 0;
}

/** The part rows of a materials list: the sleeve row is appended only for a machine that has a sleeve - never because a
 *  material assignment still names one. */
export function withSleevePart<T>(parts: readonly T[], sleevePart: T, geometry: unknown): readonly T[] {
  return hasSleeve(geometry) ? [...parts, sleevePart] : parts;
}
