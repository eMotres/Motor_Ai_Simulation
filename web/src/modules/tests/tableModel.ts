// Tests section — the pure table model: formatting, delta levels, the flattened measured | model | delta rows, the short
// note of a row, and the parser of pasted bench rows.  No React, no network (node --test drives it).
import type { CompareRow, TestCompare, TestKind, TestRecord } from './types';

/** Columns of a pasted row, in the order a bench sheet gives them. */
export const FREE_RUN_COLUMNS = ['rpm', 'shaft_power_w', 'v_ll_pp_v', 'v_ll_rms_v', 'temp_c'] as const;
export const LOAD_COLUMNS = ['rpm', 'torque_nm', 'i_phase_a', 'v_dc_v', 'i_dc_a', 'eff_pct', 'temp_winding_c', 'duration_s'] as const;

export function columnsFor(kind: TestKind): readonly string[] {
  return kind === 'load' ? LOAD_COLUMNS : FREE_RUN_COLUMNS;
}

/** A value with sensible digits: 1286 | 513.6 | 17.29 | 0.674; null -> an em dash. */
export function fmtValue(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

const UNITS: Record<string, string> = { mOhm: 'mΩ', MOhm: 'MΩ', Ohm: 'Ω', uH: 'µH', C: '°C', 'W/krpm^2': 'W/krpm²' };

/** The unit as printed (mOhm -> mΩ, uH -> µH, C -> °C ...). */
export function unitLabel(u: string | null | undefined): string {
  const s = String(u ?? '');
  return UNITS[s] ?? s;
}

export function fmtDelta(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}`;
}

/** Colour band of a deviation: within 5 % ok, within 15 % warn, beyond that bad. */
export function deltaLevel(v: number | null | undefined): 'ok' | 'warn' | 'bad' | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  const a = Math.abs(v);
  return a <= 5 ? 'ok' : a <= 15 ? 'warn' : 'bad';
}

export interface RowNote { key: string; params: Record<string, unknown> }

/** The one short note a row carries next to its numbers (translated by the caller), or null. */
export function rowNote(row: CompareRow): RowNote | null {
  const x = row.extra ?? {};
  if (row.key === 'loss' && typeof x.unexplained_w === 'number') return { key: 'unexplained', params: { w: fmtValue(x.unexplained_w as number) } };
  if (row.status === 'no_model' && row.action) return { key: 'noModelCompute', params: {} };
  if (row.status === 'no_model') return { key: 'noModel', params: {} };
  if (x.high_freq === true && typeof x.freq_hz === 'number') return { key: 'freqFlag', params: { hz: x.freq_hz } };
  if (row.key === 'r_ll_mean' && typeof x.remaining_mohm === 'number') return { key: 'remaining', params: { mohm: fmtValue(x.remaining_mohm as number) } };
  return null;
}

export type TableLine =
  | { type: 'group'; key: string; label: string; params: Record<string, unknown>; tip?: string | null }
  | { type: 'row'; row: CompareRow; measured: string; model: string; delta: string; level: 'ok' | 'warn' | 'bad' | null; note: RowNote | null };

/** One table per test: group headers and their rows; info rows with no number at all are dropped. */
export function tableLines(cmp: TestCompare | undefined | null): TableLine[] {
  const out: TableLine[] = [];
  for (const g of cmp?.groups ?? []) {
    const rows = g.rows.filter((r) => !(r.status === 'info' && r.measured === null && r.model === null && !r.params?.result));
    if (!rows.length) continue;
    out.push({ type: 'group', key: g.key, label: g.label, params: g.params ?? {}, tip: g.tip });
    for (const r of rows) {
      out.push({ type: 'row', row: r, measured: fmtValue(r.measured), model: fmtValue(r.model), delta: fmtDelta(r.delta_pct),
        level: deltaLevel(r.delta_pct), note: rowNote(r) });
    }
  }
  return out;
}

/** The tests of one sub-menu, newest first. */
export function testsOfKind(tests: TestRecord[], kind: TestKind): TestRecord[] {
  return tests.filter((t) => t.kind === kind).sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

/** How many rows of one test are within / beyond the bands (the chip on the card header). */
export function summary(cmp: TestCompare | undefined | null): { compared: number; worst: number | null } {
  let compared = 0;
  let worst: number | null = null;
  for (const g of cmp?.groups ?? []) for (const r of g.rows) {
    if (r.delta_pct === null || r.delta_pct === undefined) continue;
    compared += 1;
    if (worst === null || Math.abs(r.delta_pct) > Math.abs(worst)) worst = r.delta_pct;
  }
  return { compared, worst };
}

export interface ParseResult { rows: Array<Record<string, number>>; errors: Array<{ line: number; text: string }> }

function toNumber(s: string, decimalComma: boolean): number | null | undefined {
  const t = s.trim();
  if (t === '' || t === '-' || t === '—') return null;
  const v = Number(decimalComma ? t.replace(',', '.') : t);
  return Number.isFinite(v) ? v : undefined;
}

/**
 * Pasted bench rows -> request rows.  Two formats:
 *  - slash rows "613/75/74/26.7/45.2, 812/111/101.5/35.2" (records split by commas or new lines);
 *  - a sheet: one record per line, fields split by tabs, semicolons or spaces, a decimal comma allowed.
 * "-" = not measured.  A row with fewer fields leaves the trailing columns empty.
 */
export function parseRows(text: string, kind: TestKind): ParseResult {
  const cols = columnsFor(kind);
  const rows: Array<Record<string, number>> = [];
  const errors: Array<{ line: number; text: string }> = [];
  const slash = text.includes('/');
  const records = slash ? text.split(/[\n,]+/) : text.split(/\n+/);
  records.forEach((rec, i) => {
    const r = rec.trim();
    if (!r) return;
    const fields = slash ? r.split('/') : r.split(/\t|;|\s+/);
    if (fields.length > cols.length) { errors.push({ line: i + 1, text: r }); return; }
    const row: Record<string, number> = {};
    let bad = false;
    fields.forEach((f, j) => {
      const v = toNumber(f, !slash);
      if (v === undefined) bad = true;
      else if (v !== null) row[cols[j]] = v;
    });
    if (bad || row.rpm === undefined) { errors.push({ line: i + 1, text: r }); return; }
    rows.push(row);
  });
  return { rows, errors };
}

/** Rows back to the slash text the editor shows when a test is edited. */
export function rowsToText(rows: Array<Record<string, number>> | undefined, kind: TestKind): string {
  const cols = columnsFor(kind);
  return (rows ?? []).map((r) => {
    let last = 0;
    cols.forEach((c, j) => { if (r[c] !== undefined) last = j; });
    return cols.slice(0, last + 1).map((c) => (r[c] === undefined ? '-' : String(r[c]))).join('/');
  }).join('\n');
}

/** "18.13 / 18.03 / 17.94" or "18.13 18.03 17.94" -> numbers (null for an empty field). */
export function parseNumberList(text: string): Array<number | null> {
  return text.split(/[\s/;]+/).filter((s) => s.trim() !== '').map((s) => {
    const v = Number(s.replace(',', '.'));
    return Number.isFinite(v) ? v : null;
  });
}
