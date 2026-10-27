/** The power supply block of the wizard (field 2_1, owner 2026-10-27): ONE block for the whole project, a choice of a battery
 *  (chemistry, cells in series, cell voltages - the pack voltages follow) or a DC power source (one voltage).  Pure functions,
 *  tested in node.  The stored answer only gains `supply_type` and `dc_source_V`; nothing stored for the other choice is removed
 *  or rewritten, and nothing is written on load.  The per-point DC voltage of earlier key points stays in the data, unshown. */
import { keyPointsStored } from '../../keyPoints.ts';
import type { SpecDraft } from '../../wizardState.ts';
import { formatNumber } from './operatingPoints.ts';

export type SupplyType = 'battery' | 'dc_source';
type Translate = (key: string, args?: Record<string, unknown>) => string;

const rec = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {});
const num = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) ? v : null;
const blank = (v: unknown) => v === undefined || v === null || (typeof v === 'string' && !v.trim());

/** Not stated = battery (every earlier answer is a battery). */
export function supplyType(value: unknown): SupplyType {
  return rec(value).supply_type === 'dc_source' ? 'dc_source' : 'battery';
}
/** The answer with the supply type chosen; every other stored value is kept. */
export function withSupplyType(value: unknown, type: SupplyType): Record<string, unknown> {
  return { ...rec(value), supply_type: type };
}
/** The answer with the DC source voltage typed (a cleared box stores nothing). */
export function withDcSourceV(value: unknown, volts: unknown): Record<string, unknown> {
  return { ...rec(value), supply_type: 'dc_source', dc_source_V: typeof volts === 'number' && Number.isFinite(volts) ? volts : '' };
}

/** The supply block holds no value at all. */
export function supplyEmpty(value: unknown): boolean {
  return Object.values(rec(value)).every(blank);
}

/** The DC voltages earlier key points carried (the per-point input is gone): shown once, as a hint, while the block is empty. */
export function legacyPointVoltages(draft: SpecDraft): Array<{ name: string; volts: number }> {
  if (!keyPointsStored(draft)) return [];
  const rows = (draft.fields?.key_points as { value?: unknown } | undefined)?.value;
  if (!Array.isArray(rows)) return [];
  return rows.flatMap((row) => {
    const r = rec(row), volts = num(r.voltage_V);
    return volts !== null && volts > 0 ? [{ name: typeof r.name === 'string' ? r.name.trim() : '', volts }] : [];
  });
}
/** "48 V (Takeoff), 50 V (Cruise)". */
export function legacyVoltageText(items: Array<{ name: string; volts: number }>): string {
  return items.map((x) => `${formatNumber(x.volts)} V${x.name ? ` (${x.name})` : ''}`).join(', ');
}

/** The review line: "Battery 192S NMC, 633 / 710 / 806 V" or "DC source 48 V"; '' while nothing usable is stored. */
export function supplySummary(value: unknown, t: Translate): string {
  const v = rec(value);
  if (supplyType(v) === 'dc_source') {
    const volts = num(v.dc_source_V);
    return volts === null ? '' : t('wizard.supply.reviewDc', { value: formatNumber(volts) });
  }
  const cells = num(v.series_cells), cell = (k: string) => num(v[k]);
  const pack = ([['voltage_min_V', 'cell_voltage_min_V'], ['voltage_nominal_V', 'cell_voltage_nominal_V'], ['voltage_max_V', 'cell_voltage_max_V']] as const)
    .map(([p, c]) => num(v[p]) ?? (cells !== null && cell(c) !== null ? cells * (cell(c) as number) : null))
    .filter((x): x is number => x !== null);
  const chemistry = typeof v.chemistry === 'string' ? v.chemistry.trim() : '';
  const head = [cells !== null ? `${formatNumber(cells)}S` : '', chemistry].filter(Boolean).join(' ');
  const volts = pack.length ? `${pack.map(formatNumber).join(' / ')} V` : '';
  if (!head && !volts) return '';
  return `${t('wizard.supply.battery')} ${[head, volts].filter(Boolean).join(', ')}`;
}
