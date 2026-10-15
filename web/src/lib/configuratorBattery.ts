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
export type BatteryField = 'cells' | 'min' | 'nom' | 'max';
export interface BatteryLike {
  type: CellType; cells: number; nom: number; max: number; min: number;
  /** Raw text is kept while a field is edited, including empty/nonfinite input. */
  draft?: Partial<Record<BatteryField, string>>;
}

/** Text shown in an editable battery field. */
export function batteryFieldText(b: BatteryLike, field: BatteryField): string {
  return Object.prototype.hasOwnProperty.call(b.draft ?? {}, field)
    ? String(b.draft?.[field] ?? '') : String(b[field]);
}

/** Apply one field edit without losing invalid text or the last finite numeric value. */
export function editBatteryField(b: BatteryLike, field: BatteryField, raw: string): BatteryLike {
  const value = raw.trim() === '' ? NaN : Number(raw);
  return {
    ...b,
    ...(Number.isFinite(value) ? { [field]: value } : {}),
    draft: { ...(b.draft ?? {}), [field]: raw },
  };
}

/** Parsed field value; null means the visible draft is empty or nonfinite. */
export function batteryFieldNumber(b: BatteryLike, field: BatteryField): number | null {
  const raw = batteryFieldText(b, field);
  if (raw.trim() === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/** Numeric pack values are available only when every visible field is valid. */
export function validBatteryNumbers(b: BatteryLike): Pick<BatteryLike, BatteryField> | null {
  if (batteryInputIssues(b).length) return null;
  return {
    cells: batteryFieldNumber(b, 'cells')!, min: batteryFieldNumber(b, 'min')!,
    nom: batteryFieldNumber(b, 'nom')!, max: batteryFieldNumber(b, 'max')!,
  };
}

/** Per-cell voltage fields must describe an ordered, positive operating window. */
export function batteryInputIssues(b: Pick<BatteryLike, 'cells' | 'min' | 'nom' | 'max'>): Array<'cells' | 'min' | 'nom' | 'max'> {
  const d = b as BatteryLike;
  const cells = batteryFieldNumber(d, 'cells');
  const min = batteryFieldNumber(d, 'min');
  const nom = batteryFieldNumber(d, 'nom');
  const max = batteryFieldNumber(d, 'max');
  const issues: Array<'cells' | 'min' | 'nom' | 'max'> = [];
  if (cells == null || cells <= 0 || !Number.isInteger(cells)) issues.push('cells');
  if (min == null || min <= 0 || (nom != null && min >= nom)) issues.push('min');
  if (nom == null || nom <= 0 || (min != null && nom <= min) || (max != null && nom > max)) issues.push('nom');
  if (max == null || max <= 0 || (nom != null && max < nom)) issues.push('max');
  return issues;
}

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

/** A present configuration with no pack explicitly means no saved pack. */
export function batteryForMachineConfig(
  hasConfig: boolean,
  configPack: PackLike | null | undefined,
  contextPack: PackLike | null | undefined,
  passportPack: PackLike | null | undefined,
): BatteryLike | null {
  if (hasConfig) return batteryFromPack(configPack);
  return batteryFromPack(contextPack) ?? batteryFromPack(passportPack);
}

export const BATTERY_BY_MACHINE_LS = 'configurator.batteryByRef.v1';

/** Battery edits follow a selected machine configuration when one is known. */
export function batteryEditKey(refId: string, config: string | null | undefined): string {
  return config ? `${refId}|config:${config}` : refId;
}

/** Preserve a legacy per-machine edit only for its trusted original config. */
export function readBatteryEditForConfig(
  raw: string | null, refId: string, config: string | null | undefined,
  originalConfig: string | null | undefined,
): BatteryLike | null {
  const key = batteryEditKey(refId, config);
  return readBatteryEdit(raw, key) ?? (config && config === originalConfig ? readBatteryEdit(raw, refId) : null);
}

export function writeBatteryEditForConfig(
  raw: string | null, refId: string, config: string | null | undefined, b: BatteryLike,
): string {
  return writeBatteryEdit(raw, batteryEditKey(refId, config), b);
}

export function clearBatteryEditForConfig(
  raw: string | null, refId: string, config: string | null | undefined,
  originalConfig: string | null | undefined,
): string {
  let next = clearBatteryEdit(raw, batteryEditKey(refId, config));
  if (config && config === originalConfig) next = clearBatteryEdit(next, refId);
  return next;
}

/** The panel's stock default (BatteryPanel's `defaultBattery()`): 100 NMC cells.  It is what
 *  the block shows while a machine has no pack of its own — never something a USER chose
 *  for a machine, so a stored "edit" equal to it is not an edit. */
export const STOCK_BATTERY: BatteryLike = { type: 'NMC', cells: 100, nom: 3.7, max: 4.2, min: 3.0 };

export function isStockBattery(b: BatteryLike): boolean {
  return batteryInputIssues(b).length === 0
    && b.type === STOCK_BATTERY.type && b.cells === STOCK_BATTERY.cells
    && Math.abs(b.nom - STOCK_BATTERY.nom) < 1e-6 && Math.abs(b.max - STOCK_BATTERY.max) < 1e-6
    && Math.abs(b.min - STOCK_BATTERY.min) < 1e-6
    && (['cells', 'min', 'nom', 'max'] as BatteryField[])
      .every((k) => batteryFieldNumber(b, k) === STOCK_BATTERY[k]);
}

/** One-time cleanup of the stored per-machine edits: drop every entry that equals the stock
 *  default (written without a user choice) — so it can never shadow a machine's own pack. */
export function dropStockEdits(raw: string | null): string | null {
  if (!raw) return raw;
  try {
    const all = JSON.parse(raw);
    if (!isObj(all)) return raw;
    let changed = false;
    for (const k of Object.keys(all)) {
      const b = all[k];
      if (isObj(b) && b.type && isStockBattery({ type: b.type as CellType, cells: Number(b.cells), nom: Number(b.nom),
          max: Number(b.max), min: Number(b.min), draft: isObj(b.draft) ? b.draft as Partial<Record<BatteryField, string>> : undefined })) {
        delete all[k]; changed = true;
      }
    }
    return changed ? JSON.stringify(all) : raw;
  } catch { return raw; }
}

const isObj = (c: unknown): c is Record<string, unknown> =>
  !!c && typeof c === 'object' && !Array.isArray(c);

/** What the user typed for THIS machine, out of the raw localStorage text. */
export function readBatteryEdit(raw: string | null, refId: string): BatteryLike | null {
  if (!raw || !refId) return null;
  try {
    const all = JSON.parse(raw);
    const b = isObj(all) ? all[refId] : null;
    if (!isObj(b) || (b.type !== 'NMC' && b.type !== 'LFP')) return null;
    // Preserve finite invalid edits so Configure can identify the exact field
    // after reload instead of silently replacing the user's value with a pack default.
    const number = (v: unknown) => typeof v === 'number' && Number.isFinite(v) ? v : null;
    const cells = number(b.cells), nom = number(b.nom), max = number(b.max), min = number(b.min);
    if (cells == null || nom == null || max == null || min == null) return null;
    const draftObj = isObj(b.draft) ? b.draft : null;
    const draft = draftObj
      ? Object.fromEntries((['cells', 'min', 'nom', 'max'] as BatteryField[])
        .filter((k) => typeof draftObj[k] === 'string').map((k) => [k, draftObj[k] as string]))
      : undefined;
    const out: BatteryLike = { type: b.type, cells, nom, max, min, ...(draft && Object.keys(draft).length ? { draft } : {}) };
    return isStockBattery(out) ? null : out;       // the stock default is never "the user's choice"
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

/** UI state for a machine with no known pack and no user-specific edit. */
export function batteryForDisplay(edit: BatteryLike | null, machine: BatteryLike | null): BatteryLike {
  return wantedBattery(edit, machine) ?? { ...STOCK_BATTERY };
}

export function sameBattery(a: BatteryLike, b: BatteryLike): boolean {
  const numericSame = a.cells === b.cells && a.type === b.type && Math.abs(a.nom - b.nom) < 1e-6
    && Math.abs(a.max - b.max) < 1e-6 && Math.abs(a.min - b.min) < 1e-6;
  if (!numericSame) return false;
  if (!batteryInputIssues(a).length && !batteryInputIssues(b).length) return true;
  return (['cells', 'min', 'nom', 'max'] as BatteryField[])
    .every((k) => batteryFieldText(a, k) === batteryFieldText(b, k));
}
