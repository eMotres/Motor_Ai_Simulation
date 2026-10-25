import { PRESET, type BatteryField, type BatteryLike, type CellType } from '../../lib/configuratorBattery.ts';

/** Specification 2_1 <-> the shared battery block (Configure's BatteryInputs).
 *
 *  The stored answer is the customer's: pack voltages may be rounded contract values and
 *  the chemistry may be any text.  Loading never rewrites anything.  cells × cell voltage =
 *  pack voltage either way round (owner 2026-10-25), with the stated pack as the authority:
 *  a blank cell voltage is SHOWN as pack ÷ cells, a typed cell voltage moves its pack. */
export const SPEC_CHEMISTRY: Record<CellType, string> = { NMC: 'NMC', LFP: 'LiFePO4' };
const CELL_PROP: Record<BatteryField, string> = { cells: 'series_cells', min: 'cell_voltage_min_V', nom: 'cell_voltage_nominal_V', max: 'cell_voltage_max_V' };
const PACK_PROP: Record<'min' | 'nom' | 'max', string> = { min: 'voltage_min_V', nom: 'voltage_nominal_V', max: 'voltage_max_V' };
const VOLTS = ['min', 'nom', 'max'] as const;

const finite = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) ? v : null;
const blank = (v: unknown) => v === undefined || v === null || (typeof v === 'string' && !v.trim());
const round = (v: number) => Math.round(v * 1000) / 1000;

/** Only the two canonical names select a toggle button; "LiPo" or "NMC, <cell name>" select nothing. */
export function specChemistryType(chemistry: unknown): CellType | null {
  const text = typeof chemistry === 'string' ? chemistry.trim().toLowerCase() : '';
  if (text === 'nmc') return 'NMC';
  if (text === 'lifepo4' || text === 'lifepo₄' || text === 'lfp') return 'LFP';
  return null;
}

/** What the block shows for a stored answer — the stored numbers; a blank cell voltage is shown as
 *  pack ÷ cells when both are stored (cells × cell = pack, one value determines the other, owner 2026-10-25). */
export function specBatteryView(stored: Record<string, unknown>): { bat: BatteryLike; selected: CellType | null; chemistryNote: string | null; pack: { min: number | null; nom: number | null; max: number | null } } {
  const value = deriveCells({ ...stored }, VOLTS, true);
  const selected = specChemistryType(value.chemistry);
  const draft: Partial<Record<BatteryField, string>> = {};
  const number = (field: BatteryField) => { const v = finite(value[CELL_PROP[field]]); if (v == null) draft[field] = blank(value[CELL_PROP[field]]) ? '' : String(value[CELL_PROP[field]]); return v ?? NaN; };
  const bat: BatteryLike = { type: selected ?? 'NMC', cells: number('cells'), min: number('min'), nom: number('nom'), max: number('max') };
  if (Object.keys(draft).length) bat.draft = draft;
  const chemistryNote = !selected && typeof value.chemistry === 'string' && value.chemistry.trim() ? value.chemistry : null;
  return { bat, selected, chemistryNote, pack: { min: finite(value[PACK_PROP.min]), nom: finite(value[PACK_PROP.nom]), max: finite(value[PACK_PROP.max]) } };
}

function derivePack(next: Record<string, unknown>, volts: ReadonlyArray<'min' | 'nom' | 'max'>, allowed: Set<string>, onlyEmpty: boolean) {
  const cells = finite(next[CELL_PROP.cells]);
  for (const k of volts) {
    const cell = finite(next[CELL_PROP[k]]);
    if (cells == null || cell == null || !allowed.has(PACK_PROP[k]) || (onlyEmpty && !blank(next[PACK_PROP[k]]))) continue;
    next[PACK_PROP[k]] = round(cells * cell);
  }
  return next;
}

/** Cell voltages from pack ÷ cells (onlyEmpty: fill blanks only). */
function deriveCells(next: Record<string, unknown>, volts: ReadonlyArray<'min' | 'nom' | 'max'>, onlyEmpty: boolean, allowed?: Set<string>) {
  const cells = finite(next[CELL_PROP.cells]);
  for (const k of volts) {
    const pack = finite(next[PACK_PROP[k]]);
    if (cells == null || cells <= 0 || pack == null || (allowed && !allowed.has(CELL_PROP[k])) || (onlyEmpty && !blank(next[CELL_PROP[k]]))) continue;
    next[CELL_PROP[k]] = round(pack / cells);
  }
  return next;
}

/** A cell count from pack ÷ cell voltage, only when it comes out whole (never rounded silently). */
function deriveCount(next: Record<string, unknown>, k: 'min' | 'nom' | 'max', allowed: Set<string>) {
  const pack = finite(next[PACK_PROP[k]]), cell = finite(next[CELL_PROP[k]]);
  if (!allowed.has(CELL_PROP.cells) || finite(next[CELL_PROP.cells]) != null || pack == null || cell == null || cell <= 0) return;
  const n = pack / cell;
  if (Math.abs(n - Math.round(n)) < 0.02 && Math.round(n) > 0) next[CELL_PROP.cells] = Math.round(n);
}

/** The user typed into one block field (called per keystroke).  The stated PACK voltages are the authority
 *  (Astra review 2026-10-25: deriving both ways per keystroke turned 1 -> 10 -> 100 cells into a 37 kV pack):
 *    - a cell voltage typed: stored, and with a cell count its pack voltage follows (cells × cell);
 *      with no count, a whole count is derived from the stated pack;
 *    - the cell count typed: stored; a stated pack stays, its cell voltage is SHOWN as pack ÷ cells (a stored cell
 *      voltage there is dropped); a pack not stated yet follows from a stored cell voltage. */
export function specBatteryFieldEdit(value: Record<string, unknown>, field: BatteryField, raw: string, allowed: Set<string>): Record<string, unknown> {
  if (!allowed.has(CELL_PROP[field])) return value;
  const parsed = raw.trim() === '' ? NaN : Number(raw);
  const next: Record<string, unknown> = { ...value, [CELL_PROP[field]]: Number.isFinite(parsed) ? parsed : '' };
  if (field !== 'cells') {
    if (finite(next[CELL_PROP.cells]) == null) { deriveCount(next, field, allowed); return next; }
    return derivePack(next, [field], allowed, false);
  }
  for (const k of VOLTS) if (finite(next[PACK_PROP[k]]) != null) delete next[CELL_PROP[k]];
  return derivePack(next, VOLTS, allowed, true);
}

/** The user typed a pack voltage: stored as typed; its cell voltage is then SHOWN as pack ÷ cells (a stored one is
 *  dropped), or, with no cell count yet, a whole count is derived from a stored cell voltage. */
export function specBatteryPackEdit(value: Record<string, unknown>, k: 'min' | 'nom' | 'max', pack: unknown, allowed: Set<string>): Record<string, unknown> {
  const next: Record<string, unknown> = { ...value, [PACK_PROP[k]]: pack };
  if (finite(pack) == null) return next;
  deriveCount(next, k, allowed);
  if (finite(next[CELL_PROP.cells]) != null) delete next[CELL_PROP[k]];
  return next;
}
export const SPEC_PACK_PROP = PACK_PROP;

/** The user picked a chemistry: store its name; preset cell voltages fill only where neither the cell nor the pack
 *  voltage is stated (a stated pack already determines its cell voltage); nothing stored is replaced. */
export function specBatteryChemistry(value: Record<string, unknown>, type: CellType, allowed: Set<string>): Record<string, unknown> {
  const next: Record<string, unknown> = { ...value, chemistry: SPEC_CHEMISTRY[type] };
  for (const k of VOLTS) if (allowed.has(CELL_PROP[k]) && blank(next[CELL_PROP[k]]) && finite(next[PACK_PROP[k]]) == null) next[CELL_PROP[k]] = PRESET[type][k];
  return derivePack(next, VOLTS, allowed, true);
}
