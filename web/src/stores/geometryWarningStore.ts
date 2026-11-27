/** The warning the geometry routes attach (`_warnings`) when a saved configuration's winding does not fit its slot: the viewer still
 *  draws the machine and the geometry window shows the warning (which field, the limit) instead of staying empty (owner 2026-11-27). */
import { create } from 'zustand';

export interface GeometryWarning {
  code: string; field: string; message: string; value?: number | null; max?: number;
  turns?: number; series_turns?: number; wire_split?: number; column_mm?: number; limit_mm?: number; side_at_mm?: number; over_mm?: number;
}

interface GeometryWarningState { warnings: GeometryWarning[]; setWarnings: (w: GeometryWarning[]) => void }

export const useGeometryWarningStore = create<GeometryWarningState>()((set) => ({
  warnings: [],
  setWarnings: (warnings) => set({ warnings }),
}));

/** The warnings of a mesh answer, and the answer without the non-mesh `_warnings` / `_timing` keys. */
export function splitWarnings<T extends Record<string, unknown>>(result: T): { warnings: GeometryWarning[]; rest: Omit<T, '_warnings'> } {
  const { _warnings, ...rest } = result as Record<string, unknown>;
  const warnings = Array.isArray(_warnings) ? (_warnings as GeometryWarning[]).filter((w) => w && typeof w.message === 'string') : [];
  return { warnings, rest: rest as Omit<T, '_warnings'> };
}
