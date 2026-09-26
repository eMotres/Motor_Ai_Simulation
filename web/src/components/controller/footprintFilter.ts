/**
 * "Fits the selected board" — the catalogue's footprint filter.
 *
 * Owner, 2026-09-26: a board is laid out for ONE land pattern; the parts that
 * drop onto it are the cards sharing its `footprint.compatibility_group`. The
 * group's height / top-tab warning is computed by the backend
 * (`devices.footprint_groups`), so the table and any report read one sentence.
 *
 * Pure and import-free on purpose: `__tests__/footprintFilter.test.mjs` pins it.
 */
import type { DeviceRow } from './controllerApi';

/** `''` = no board chosen: every part is shown. */
export const ANY_BOARD = '';

/** Every compatibility group present in the rows, sorted, each once. */
export function boardGroups(rows: DeviceRow[]): string[] {
  const s = new Set<string>();
  for (const r of rows) {
    const g = r.footprint?.compatibility_group;
    if (g) s.add(g);
  }
  return [...s].sort();
}

/** The rows that fit a board of `group`; a card with no footprint never "fits". */
export function fitsBoard(rows: DeviceRow[], group: string): DeviceRow[] {
  if (group === ANY_BOARD) return rows;
  return rows.filter(r => r.footprint?.compatibility_group === group);
}

/** The group's one-line warning, or null when its parts agree. */
export function boardWarning(rows: DeviceRow[], group: string): string | null {
  if (group === ANY_BOARD) return null;
  const r = rows.find(x => x.footprint?.compatibility_group === group);
  return r?.footprint?.group_warning ?? null;
}
