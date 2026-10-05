// The Configure tab's Battery block follows the MACHINE (owner 2026-10-05: it showed 100
// NMC cells / 300–420 V next to a 40 mm machine whose own pack is 6S, 18 / 22.2 / 25.2 V).
//
//   * the pack the machine was saved with (family configuration, via
//     GET /api/catalog/{id}/configure_context, else the passport's own `battery`)
//     is what the block opens on;
//   * what the user then types is remembered for THAT machine only (per-machine map in
//     localStorage), never carried to another machine;
//   * a machine with no pack of its own keeps the stock default.
//
// Pure (no imports): web/src/lib/__tests__/configuratorBattery.test.mjs imports this file.

export type CellType = 'NMC' | 'LFP';
export interface BatteryLike { type: CellType; cells: number; nom: number; max: number; min: number; }

/** A pack as the machine's configuration states it (pack totals; per-cell voltages optional). */
export interface PackLike {
  cells?: number | null; chemistry?: string | null;
  v_min?: number | null; v_nom?: number | null; v_max?: number | null;
  v_cell_min?: number | null; v_cell_nom?: number | null; v_cell_max?: number | null;
}

// the same numbers as BatteryPanel's PRESETS
const PRESET: Record<CellType, { nom: number; max: number; min: number }> = {
  NMC: { nom: 3.7, max: 4.2, min: 3.0 },
  LFP: { nom: 3.2, max: 3.65, min: 2.5 },
};

const pos = (v: unknown): number | null => {
  const x = Number(v);
  return v != null && Number.isFinite(x) && x > 0 ? x : null;
};

/** The Battery block's state for a machine's pack, or `null` when the pack names no cells
 *  or no nominal voltage (then nothing is known and nothing is invented). */
export function batteryFromPack(b: PackLike | null | undefined): BatteryLike | null {
  if (!b) return null;
  const ns = Math.round(Number(b.cells ?? 0));
  if (!(ns > 0)) return null;
  const lfp = /lfp|lifepo|iron/i.test(String(b.chemistry ?? ''));
  const preset = lfp ? PRESET.LFP : PRESET.NMC;
  const nom = pos(b.v_cell_nom) ?? (pos(b.v_nom) != null ? Number(b.v_nom) / ns : null);
  if (nom == null) return null;
  const max = pos(b.v_cell_max) ?? (pos(b.v_max) != null ? Number(b.v_max) / ns : preset.max);
  const min = pos(b.v_cell_min) ?? (pos(b.v_min) != null ? Number(b.v_min) / ns : preset.min);
  return { type: lfp ? 'LFP' : 'NMC', cells: ns, nom, max, min };
}

export const BATTERY_BY_MACHINE_LS = 'configurator.batteryByRef.v1';

const isObj = (c: unknown): c is Record<string, unknown> =>
  !!c && typeof c === 'object' && !Array.isArray(c);

/** What the user typed for THIS machine, out of the raw localStorage text. */
export function readBatteryEdit(raw: string | null, refId: string): BatteryLike | null {
  if (!raw || !refId) return null;
  try {
    const all = JSON.parse(raw);
    const b = isObj(all) ? all[refId] : null;
    if (!isObj(b) || (b.type !== 'NMC' && b.type !== 'LFP')) return null;
    const cells = pos(b.cells), nom = pos(b.nom), max = pos(b.max), min = pos(b.min);
    return cells && nom && max && min ? { type: b.type, cells, nom, max, min } : null;
  } catch { return null; }
}

/** The raw text to store after the user changed this machine's battery. */
export function writeBatteryEdit(raw: string | null, refId: string, b: BatteryLike): string {
  let all: Record<string, unknown> = {};
  try { const p = raw ? JSON.parse(raw) : {}; if (isObj(p)) all = p; } catch { /* start over */ }
  all[refId] = b;
  return JSON.stringify(all);
}

/** The raw text after the user reset THIS machine's battery to the machine's own pack. */
export function clearBatteryEdit(raw: string | null, refId: string): string {
  let all: Record<string, unknown> = {};
  try { const p = raw ? JSON.parse(raw) : {}; if (isObj(p)) all = p; } catch { /* start over */ }
  delete all[refId];
  return JSON.stringify(all);
}

/** Which battery a machine opens on: the user's own edit, else the machine's pack, else null
 *  (= keep the stock default). */
export function wantedBattery(edit: BatteryLike | null, machine: BatteryLike | null): BatteryLike | null {
  return edit ?? machine;
}

export function sameBattery(a: BatteryLike, b: BatteryLike): boolean {
  return a.cells === b.cells && a.type === b.type && Math.abs(a.nom - b.nom) < 1e-6
    && Math.abs(a.max - b.max) < 1e-6 && Math.abs(a.min - b.min) < 1e-6;
}
