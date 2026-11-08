// Tests section — the pure table model: formatting, delta levels, the flattened measured | model | delta rows, the short
// note of a row, and the parser of pasted bench rows.  No React, no network (node --test drives it).
import type {
  CompareGroup, CompareRow, CreateDutyAction, JobRow, LoadPlotRow, LoadSeriesQuantity, SimulationCurve, TestCompare, TestKind, TestRecord, TestTab,
} from './types';
import { TEST_TABS } from './types.ts';

/** Columns of a pasted row, in the order a bench sheet gives them. */
export const FREE_RUN_COLUMNS = ['rpm', 'shaft_power_w', 'v_ll_pp_v', 'v_ll_rms_v', 'temp_c', 'v_ll_fund_pk_v'] as const;
export const LOAD_COLUMNS = ['rpm', 'torque_nm', 'i_phase_a', 'v_dc_v', 'i_dc_a', 'eff_pct', 'temp_winding_c', 'duration_s', 'time_min', 'j_a_mm2'] as const;

export function columnsFor(kind: TestKind): readonly string[] {
  return kind === 'load' ? LOAD_COLUMNS : FREE_RUN_COLUMNS;
}

/** A value with sensible digits: 1234 | 512.6 | 19.42 | 0.717; null -> an em dash. */
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

// ── tabs: a view of the comparison groups (owner 2026-11-04) ─────────────────────────────────────────────────────
// Resistance | Inductance | Constants (Kv, Kt, EMF) | Bearings | Free-run | Loads | Parameters (everything else).  The
// stored records and the compare payload are unchanged; these pure functions only decide where a group is SHOWN.

/** The tab a record kind falls back to: where a group with no rule of its own, or a record with no group at all, lands. */
export function defaultTabOfKind(kind: TestKind): TestTab {
  return kind === 'load' ? 'loads' : kind === 'free_run' ? 'free_run' : 'parameters';
}

/** A remembered or linked tab value, read safely: the retired `constants` tab (merged into Parameters, owner 2026-11-05) and the
 *  old `load` value map to today's tabs; anything unknown gives `fallback`. */
export function normalizeTab(v: unknown, fallback: TestTab = 'resistance'): TestTab {
  if (v === 'constants') return 'parameters';
  if (v === 'load') return 'loads';
  return typeof v === 'string' && (TEST_TABS as readonly string[]).includes(v) ? (v as TestTab) : fallback;
}

/** The record kind a new test added from a tab gets (the tab is only a view). */
export function kindOfTab(tab: TestTab): TestKind {
  if (tab === 'loads' || tab === 'power') return 'load';
  if (tab === 'free_run' || tab === 'bearings') return 'free_run';
  return 'parameters';
}

/** THE row-to-tab mapping.  Total on purpose: a group key it does not know falls back to the record kind's default tab, so
 *  a new row kind added on the server shows up there instead of vanishing. */
export function tabOfGroup(kind: TestKind, groupKey: string): TestTab {
  if (kind === 'parameters') {
    if (groupKey === 'resistance') return 'resistance';
    if (groupKey === 'inductance') return 'inductance';
  } else if (kind === 'free_run') {
    if (groupKey === 'emf_peak' || groupKey === 'emf_rms' || groupKey === 'kv_kt' || groupKey === 'kv_kt_ex') return 'parameters';
    if (groupKey === 'bearings_none' || groupKey === 'bearing_sum') return 'bearings';
  }
  return defaultTabOfKind(kind);
}

/** The tabs a record appears in: one per tab that one of its groups maps to; a record with no group at all stays in its
 *  kind's default tab (so it, its edit / delete buttons and its raw data are always reachable). */
export function recordTabs(kind: TestKind, cmp: TestCompare | undefined | null): TestTab[] {
  const out = new Set<TestTab>();
  for (const g of cmp?.groups ?? []) out.add(tabOfGroup(kind, g.key));
  if (!out.size) out.add(defaultTabOfKind(kind));
  return Array.from(out);
}

const KV_KT_GROUPS = ['kv_kt', 'kv_kt_ex'];

/** The comparison of one record restricted to the groups of one tab (the load chart belongs to the Loads tab). */
export function cmpForTab(cmp: TestCompare | undefined | null, kind: TestKind, tab: TestTab): TestCompare | undefined {
  if (!cmp) return undefined;
  const groups = cmp.groups.filter((g) => tabOfGroup(kind, g.key) === tab);
  // Kv and Kt stay at the top of Parameters (the free-run EMF groups follow them)
  if (tab === 'parameters') groups.sort((a, b) => Number(!KV_KT_GROUPS.includes(a.key)) - Number(!KV_KT_GROUPS.includes(b.key)));
  return { ...cmp, groups, series: tab === 'loads' ? cmp.series : null };
}

/** The records shown in one tab, newest first. */
export function testsOfTab(tests: TestRecord[], compare: Record<string, TestCompare | undefined>, tab: TestTab): TestRecord[] {
  return tests.filter((t) => recordTabs(t.kind, compare[t.id]).includes(tab))
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

export interface RowNote { key: string; params: Record<string, unknown> }

/** The one short note a row carries next to its numbers (translated by the caller), or null. */
export function rowNote(row: CompareRow): RowNote | null {
  const x = row.extra ?? {};
  if (row.key === 'cogging_pkpk' && x.pending === true && row.measured === null) return { key: 'benchPending', params: {} };
  if (row.key === 'loss' && typeof x.unexplained_w === 'number') return { key: 'unexplained', params: { w: fmtValue(x.unexplained_w as number) } };
  if (row.key === 'no_bearings') return { key: 'noBearings', params: {} };
  if (row.status === 'no_model') return { key: 'noModel', params: {} };
  if (x.high_freq === true && typeof x.freq_hz === 'number') return { key: 'freqFlag', params: { hz: x.freq_hz } };
  // an EMF row far from the others stays in the Kv / Kt slope and says so (the slope without it is the second group)
  if (row.key === 'emf_peak' && x.slope_flagged === true && typeof x.slope_dev_pct === 'number') {
    return { key: 'slopeFlagged', params: { pct: `${x.slope_dev_pct > 0 ? '+' : ''}${(x.slope_dev_pct as number).toFixed(1)}` } };
  }
  // the voltage basis (EMF audit 2026-11-07): Kv / Kt stay on the fundamental and say when the bench Kv is not on it;
  // the EMF-slope row says when the stored run lacks the bench's basis and the two sides are mixed
  if ((row.key === 'kv' || row.key === 'kt') && x.bench_not_fundamental === true) return { key: 'benchKvNotFundamental', params: {} };
  if (row.key === 'kv_slope' && x.basis_mixed === true) return { key: 'basisMixed', params: {} };
  // a Kv / Kt row that carries a correction factor says when that factor is not the configuration's own current one
  const p3 = x.end3d as { inherited?: boolean; current?: boolean } | undefined;
  if ((row.key === 'kv' || row.key === 'kt' || row.key === 'kv_slope') && p3 && x.corrected === true) {
    if (p3.inherited === true) return { key: 'inheritedFactor', params: {} };
    if (p3.current === false) return { key: 'storedFactor', params: {} };
  }
  // the remainder is the board and the joints only when the measurement is the larger one; a model above the reading
  // says so instead of filing a negative resistance on the board
  if (row.key === 'r_ll_mean' && typeof x.remaining_mohm === 'number') {
    const r = x.remaining_mohm as number;
    return r >= 0 ? { key: 'remaining', params: { mohm: fmtValue(r) } } : { key: 'modelAbove', params: { mohm: fmtValue(-r) } };
  }
  return null;
}

export type RowLine = { type: 'row'; row: CompareRow; measured: string; model: string; delta: string; level: 'ok' | 'warn' | 'bad' | null; note: RowNote | null };
export type TableLine =
  | { type: 'group'; key: string; label: string; params: Record<string, unknown>; tip?: string | null }
  | RowLine;

function rowLine(r: CompareRow): RowLine {
  return { type: 'row', row: r, measured: fmtValue(r.measured), model: fmtValue(r.model), delta: fmtDelta(r.delta_pct),
    level: deltaLevel(r.delta_pct), note: rowNote(r) };
}
function visibleRows(g: CompareGroup): CompareRow[] {
  return g.rows.filter((r) => !(r.status === 'info' && r.measured === null && r.model === null && !r.params?.result));
}

/** One table per test: group headers and their rows; info rows with no number at all are dropped. */
export function tableLines(cmp: TestCompare | undefined | null): TableLine[] {
  const out: TableLine[] = [];
  for (const g of cmp?.groups ?? []) {
    const rows = visibleRows(g);
    if (!rows.length) continue;
    out.push({ type: 'group', key: g.key, label: g.label, params: g.params ?? {}, tip: g.tip });
    for (const r of rows) out.push(rowLine(r));
  }
  return out;
}

export interface SeriesPoint { rpm: number; measured: number | null; model: number | null; delta: number | null; current?: number | null }
export interface GroupSeries { unit: string; points: SeriesPoint[] }

/** The rows of a group that form a series over speed (>= 2 distinct rpm, each with a measured or a model value), as the
 *  chart's points sorted by rpm; null for a group with no speed per row (resistance, inductance, one load point ...).
 *  The backend gives each row its speed as the number ``params.rpm``, so no label is parsed. */
export function seriesOf(g: CompareGroup): GroupSeries | null {
  const pts: SeriesPoint[] = [];
  for (const r of g.rows) {
    const rpm = r.params?.rpm;
    if (typeof rpm !== 'number' || !Number.isFinite(rpm)) continue;
    const measured = typeof r.measured === 'number' && Number.isFinite(r.measured) ? r.measured : null;
    const model = typeof r.model === 'number' && Number.isFinite(r.model) ? r.model : null;
    if (measured === null && model === null) continue;
    pts.push({ rpm, measured, model, delta: typeof r.delta_pct === 'number' && Number.isFinite(r.delta_pct) ? r.delta_pct : null });
  }
  pts.sort((a, b) => a.rpm - b.rpm);
  if (new Set(pts.map((p) => p.rpm)).size < 2) return null;
  return { unit: g.rows.find((r) => r.unit)?.unit ?? '', points: pts };
}

const finite = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The quantities a load sweep can be plotted as (x = rpm), cleaned: a point needs a finite rpm and a measured or a model
 *  value (a missing value stays null - the chart never draws it as 0), points sorted by rpm, a quantity with no point
 *  dropped.  Empty array when the record has no series (other kinds, old payloads). */
export function loadSeriesOptions(cmp: TestCompare | undefined | null): LoadSeriesQuantity[] {
  const out: LoadSeriesQuantity[] = [];
  for (const q of cmp?.series?.quantities ?? []) {
    const points = (q.points ?? []).flatMap((p) => {
      const rpm = finite(p.rpm); const measured = finite(p.measured); const model = finite(p.model);
      return rpm === null || (measured === null && model === null)
        ? [] : [{ rpm, current: finite(p.current), measured, model, delta_pct: finite(p.delta_pct) }];
    }).sort((a, b) => a.rpm - b.rpm);
    if (points.length) out.push({ ...q, points });
  }
  return out;
}

/** The selected quantity, else the first one (a stale or unknown key never leaves the chart empty). */
export function pickQuantity(options: LoadSeriesQuantity[], key: string | null | undefined): LoadSeriesQuantity | null {
  return options.find((q) => q.key === key) ?? options[0] ?? null;
}

/** One quantity as the chart's points (same shape as a table group's series; one point = a dot, no line). */
export function quantitySeries(q: LoadSeriesQuantity): GroupSeries {
  return { unit: q.unit, points: q.points.map((p) => ({ rpm: p.rpm, measured: p.measured, model: p.model,
    delta: p.delta_pct ?? null, current: p.current ?? null })) };
}

// ── per-speed load plots (x = torque or current, one line per speed) ─────────────────────────────────────────────────

export type LoadPlotKey = 't_winding_time' | 'torque_time' | 'power_time' | 'torque_current' | 'loss_torque' | 'eff_torque' | 'idc_torque'
  | 't_winding_torque' | 't_magnet_torque' | 't_housing_torque';
export interface LoadPlotSpec { key: LoadPlotKey; yUnit: string; xUnit: string; x: 'current' | 'torque' | 'time' }
/** The plots in menu order; the first is the default.  The text of each (name, axis labels) is in the locale files. */
export const LOAD_PLOTS: readonly LoadPlotSpec[] = [
  { key: 't_winding_time', yUnit: 'C', xUnit: 'min', x: 'time' },
  { key: 'torque_time', yUnit: 'N·m', xUnit: 'min', x: 'time' },
  { key: 'power_time', yUnit: 'kW', xUnit: 'min', x: 'time' },
  { key: 'torque_current', yUnit: 'N·m', xUnit: 'A', x: 'current' },
  { key: 'loss_torque', yUnit: 'W', xUnit: 'N·m', x: 'torque' },
  { key: 'eff_torque', yUnit: '%', xUnit: 'N·m', x: 'torque' },
  { key: 'idc_torque', yUnit: 'A', xUnit: 'N·m', x: 'torque' },
  { key: 't_winding_torque', yUnit: 'C', xUnit: 'N·m', x: 'torque' },
  { key: 't_magnet_torque', yUnit: 'C', xUnit: 'N·m', x: 'torque' },
  { key: 't_housing_torque', yUnit: 'C', xUnit: 'N·m', x: 'torque' },
];

/** The bench rows of a load record as the plots read them (rows without a finite rpm dropped). */
export function loadPlotRows(cmp: TestCompare | undefined | null): LoadPlotRow[] {
  return (cmp?.series?.points ?? []).filter((r) => finite(r?.rpm) !== null);
}

const speedOf = (rpm: number) => Math.round(rpm);

/** True when at least one speed has two or more rows: only then the per-speed plots mean something.  A sweep over many
 *  speeds at one current (one row per speed) keeps the chart against rpm. */
export function isPerSpeedSweep(rows: LoadPlotRow[]): boolean {
  const n = new Map<number, number>();
  for (const r of rows) n.set(speedOf(r.rpm), (n.get(speedOf(r.rpm)) ?? 0) + 1);
  return Array.from(n.values()).some((c) => c >= 2);
}

/** The record's simulation curves, cleaned: a finite rpm, points with finite numbers only (a missing value stays null). */
export function simulationCurves(cmp: TestCompare | undefined | null): SimulationCurve[] {
  const out: SimulationCurve[] = [];
  for (const c of cmp?.series?.simulation_curves ?? []) {
    const rpm = finite(c?.rpm);
    if (rpm === null) continue;
    const points = (c.points ?? []).map((p) => ({ current_a: finite(p?.current_a), torque_nm: finite(p?.torque_nm),
      loss_w: finite(p?.loss_w), eff_system_pct: finite(p?.eff_system_pct), i_dc_a: finite(p?.i_dc_a) }));
    if (points.length) out.push({ rpm, points });
  }
  return out;
}

/** The plots of a record are per speed when some speed has two rows, or when it carries simulation curves. */
export function usesSpeedPlots(rows: LoadPlotRow[], curves: SimulationCurve[]): boolean {
  return curves.length > 0 || isPerSpeedSweep(rows);
}

export interface SpeedPoint { x: number; y: number }
export interface SpeedSeries { rpm: number; measured: SpeedPoint[]; model: SpeedPoint[]; simulation: SpeedPoint[] }
export interface SpeedPlot { key: LoadPlotKey; yUnit: string; xUnit: string; speeds: SpeedSeries[] }

const pt = (x: number | null, y: number | null): SpeedPoint | null => (x === null || y === null ? null : { x, y });

/** (x, y) of one row for one plot, measured side; null when a needed value is missing (never a 0). */
function measuredPoint(r: LoadPlotRow, key: LoadPlotKey): SpeedPoint | null {
  const T = finite(r.torque); const I = finite(r.current); const vdc = finite(r.v_dc); const idc = finite(r.i_dc);
  const omega = (finite(r.rpm) ?? 0) * Math.PI / 30;
  const pIn = vdc !== null && idc !== null ? Math.abs(vdc * idc) : null;
  const pShaft = T !== null ? Math.abs(T) * omega : null;
  const t = finite(r.time_min);
  switch (key) {
    case 't_winding_time': return pt(t, finite(r.t_winding));
    case 'torque_time': return pt(t, T);
    case 'power_time': return pt(t, pShaft !== null ? pShaft / 1000 : null);
    case 'torque_current': return pt(I, T);
    case 'loss_torque': return pt(T, pIn !== null && pShaft !== null ? Math.abs(pIn - pShaft) : null);
    case 'eff_torque': return pt(T, pIn !== null && pIn > 0 && pShaft !== null ? 100 * Math.min(pIn, pShaft) / Math.max(pIn, pShaft) : null);
    case 'idc_torque': return pt(T, idc);
    case 't_winding_torque': return pt(T, finite(r.t_winding));
    case 't_magnet_torque': return pt(T, finite(r.t_magnet));
    case 't_housing_torque': return pt(T, finite(r.t_housing));
  }
}
/** The model's own value of the same plot at the same x (only where the model has it; nothing is derived). */
function modelPoint(r: LoadPlotRow, key: LoadPlotKey): SpeedPoint | null {
  const m = r.model ?? {};
  const T = finite(r.torque); const I = finite(r.current);
  switch (key) {
    case 'torque_current': return pt(I, finite(m.torque));
    case 'eff_torque': return pt(T, finite(m.eff_system));
    case 't_winding_torque': return pt(T, finite(m.t_winding));
    case 't_magnet_torque': return pt(T, finite(m.t_magnet));
    default: return null;
  }
}
/** One point of a simulation curve for one plot; the temperature plots have none. */
function curvePoint(p: SimulationCurve['points'][number], key: LoadPlotKey): SpeedPoint | null {
  switch (key) {
    case 'torque_current': return pt(finite(p.current_a), finite(p.torque_nm));
    case 'loss_torque': return pt(finite(p.torque_nm), finite(p.loss_w));
    case 'eff_torque': return pt(finite(p.torque_nm), finite(p.eff_system_pct));
    case 'idc_torque': return pt(finite(p.torque_nm), finite(p.i_dc_a));
    default: return null;
  }
}

/** One plot of a load record: one series per speed (rpm rounded), rows of a speed sorted by x, straight segments between
 *  them; the model values (where a stored duty matched) as a second set and the record's simulation curves as a third (a
 *  point with a missing x or y is skipped, never a 0).  A speed that only a curve has is still listed.  null when nothing
 *  can be plotted. */
export function perSpeedSeries(rows: LoadPlotRow[], key: LoadPlotKey, curves: SimulationCurve[] = []): SpeedPlot | null {
  const spec = LOAD_PLOTS.find((p) => p.key === key);
  if (!spec) return null;
  const by = new Map<number, SpeedSeries>();
  const at = (rpm: number) => {
    const s = by.get(rpm) ?? { rpm, measured: [], model: [], simulation: [] };
    by.set(rpm, s);
    return s;
  };
  for (const r of rows) {
    if (finite(r.rpm) === null) continue;
    const m = measuredPoint(r, key); const d = modelPoint(r, key);
    if (!m && !d) continue;
    const s = at(speedOf(r.rpm));
    if (m) s.measured.push(m);
    if (d) s.model.push(d);
  }
  for (const c of curves) {
    const pts = c.points.flatMap((p) => { const q = curvePoint(p, key); return q ? [q] : []; });
    if (pts.length) at(speedOf(c.rpm)).simulation.push(...pts);
  }
  if (!by.size) return null;
  const speeds = Array.from(by.values()).sort((a, b) => a.rpm - b.rpm);
  for (const s of speeds) for (const l of [s.measured, s.model, s.simulation]) l.sort((a, b) => a.x - b.x);
  return { key, yUnit: spec.yUnit, xUnit: spec.xUnit, speeds };
}

/** The plots that have data in this record (the menu), in menu order. */
export function loadPlotOptions(rows: LoadPlotRow[], curves: SimulationCurve[] = []): LoadPlotKey[] {
  return LOAD_PLOTS.filter((p) => perSpeedSeries(rows, p.key, curves) !== null).map((p) => p.key);
}

/** `measured` = the bench line; `model` = stored-duty points (hollow dots); `simulation` = a simulation curve (dashed, no
 *  dots).  `label` is what the legend names: a stored-duty point set is "point" when the same speed also has a curve. */
export interface SpeedChartLine {
  id: string; rpm: number; kind: 'measured' | 'model' | 'simulation'; label: 'measured' | 'simulation' | 'point'; color: number;
}
/** The plot as the chart's flat data: one datum per point (x plus the value under its line's id), sorted by x, and the
 *  lines per speed (bench, stored-duty points, simulation curve).  `color` indexes the speed so all sets of a speed share a
 *  hue; the lines have different x values, so the chart joins each line over the gaps (connectNulls). */
export function speedChartData(plot: SpeedPlot): { data: Array<Record<string, number>>; lines: SpeedChartLine[] } {
  const data: Array<Record<string, number>> = [];
  const lines: SpeedChartLine[] = [];
  plot.speeds.forEach((s, i) => {
    const sets: Array<[SpeedPoint[], SpeedChartLine['kind'], string, SpeedChartLine['label']]> = [
      [s.measured, 'measured', 'm', 'measured'], [s.model, 'model', 'd', s.simulation.length ? 'point' : 'simulation'],
      [s.simulation, 'simulation', 's', 'simulation']];
    for (const [pts, kind, prefix, label] of sets) {
      if (!pts.length) continue;
      const id = `${prefix}${i}`;
      lines.push({ id, rpm: s.rpm, kind, label, color: i });
      for (const p of pts) data.push({ x: p.x, [id]: p.y });
    }
  });
  data.sort((a, b) => a.x - b.x);
  return { data, lines };
}

/** Height of one table row, px. */
export const TABLE_ROW_PX = 20;
/** A long load table scrolls inside this height, as tall as the tallest chart beside it. */
export const LOAD_TABLE_MAX_PX = 520;

/** px height of the stacked tables (one row per line + header, 8 px between): the full-height chart beside them. */
export function blocksHeight(blocks: Array<{ lines: unknown[] }>): number {
  return blocks.reduce((h, b) => h + (b.lines.length + 2) * TABLE_ROW_PX + 8, 0);
}

export interface TableBlock {
  key: string; label: string; params: Record<string, unknown>; tip?: string | null;
  basis?: string | null; basisKey?: string | null; basisParams: Record<string, unknown>;
  lines: RowLine[]; series: GroupSeries | null;
}

/** One block per group: its rows and, when they form a series over speed, the two curves (measured | model). */
export function tableBlocks(cmp: TestCompare | undefined | null): TableBlock[] {
  const out: TableBlock[] = [];
  for (const g of cmp?.groups ?? []) {
    const rows = visibleRows(g);
    if (!rows.length) continue;
    out.push({ key: g.key, label: g.label, params: g.params ?? {}, tip: g.tip, basis: g.basis ?? null,
      basisKey: g.basis_key ?? null, basisParams: g.basis_params ?? {}, lines: rows.map(rowLine), series: seriesOf(g) });
  }
  return out;
}

/** The point a compute action stands for: rpm rounded to 10, current to 0.5 A (the server's `comparison.action_key`). */
export function actionKey(a: CreateDutyAction): string {
  const d = a.body.duty;
  return `${Math.round(d.rpm / 10) * 10}|${Math.round(d.current_arms * 2) / 2}`;
}

/** Every "no model point" action of one test's table, one per distinct point, in table order ("Compute all missing"). */
export function missingActions(cmp: TestCompare | undefined | null): CreateDutyAction[] {
  const seen = new Set<string>();
  const out: CreateDutyAction[] = [];
  for (const g of cmp?.groups ?? []) for (const r of g.rows) {
    if (!r.action || r.action.kind !== 'create_duty') continue;
    const k = actionKey(r.action);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(r.action);
  }
  return out;
}

/** What a set of queued solves is doing, from the job list (`GET /api/jobs`): finished = every run left the queue. */
export interface ComputeProgress {
  total: number; done: number; failed: number; running: number; unlisted: number; error: string | null; finished: boolean;
}
export function computeProgress(runIds: string[], jobs: JobRow[]): ComputeProgress {
  const byId = new Map(jobs.map((j) => [j.run_id, j]));
  let done = 0, failed = 0, running = 0, unlisted = 0;
  let error: string | null = null;
  for (const id of runIds) {
    const job = byId.get(id);
    if (job?.state === 'done') done += 1;
    else if (job && (job.state === 'failed' || job.state === 'cancelled' || job.state === 'interrupted')) {
      failed += 1;
      error = error ?? job.error ?? job.state;
    } else {
      running += 1;                                          // queued or running ...
      if (!job) unlisted += 1;                               // ... or not (yet) in the list
    }
  }
  return { total: runIds.length, done, failed, running, unlisted, error, finished: runIds.length > 0 && running === 0 };
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
 *  - slash rows "1000/140/80/28.3/40, 1500/240/120/42.4" (records split by commas or new lines);
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

/** "20.0 / 20.1 / 19.9" or "20.0 20.1 19.9" -> numbers (null for an empty field). */
export function parseNumberList(text: string): Array<number | null> {
  return text.split(/[\s/;]+/).filter((s) => s.trim() !== '').map((s) => {
    const v = Number(s.replace(',', '.'));
    return Number.isFinite(v) ? v : null;
  });
}

/** One line of the Power sub-menu (owner 2026-11-05): a load test by its name and its highest power. */
export interface PowerLine {
  id: string; date: string; title: string; rpm: number; torque: number; powerKw: number;
  effPct: number | null; tempC: number | null; jAmm2: number | null;
  vDc: number | null; iDc: number | null; iPhase: number | null; mode: TestMode | null;
}

export type TestMode = 'motor' | 'generator';

/** A value per kilogram of the motor; null without a mass. */
export function perMass(value: number, kg: number | null | undefined): number | null {
  return kg && kg > 0 ? value / kg : null;
}

/** Motor or generator: the test's own title when it says so, else the direction of power in the row (DC above shaft = motor); null when neither tells. */
export function testMode(title: string, row: Record<string, unknown>, shaftW: number): TestMode | null {
  if (/\bgenerat/i.test(title)) return 'generator';
  if (/\bmotoring\b/i.test(title)) return 'motor';
  const dc = Number(row.v_dc_v) * Number(row.i_dc_a);
  if (!(dc > 0) || !(shaftW > 0)) return null;
  return dc > shaftW ? 'motor' : 'generator';
}

/** Efficiency of a row, in either direction: DC over shaft for a generator, shaft over DC for a motor, so the smaller over the larger.
 *  The row's own stored value wins. */
function rowEfficiency(row: Record<string, unknown>, shaftW: number): number | null {
  if (typeof row.eff_pct === 'number') return row.eff_pct;
  const dc = Number(row.v_dc_v) * Number(row.i_dc_a);
  if (!(dc > 0)) return null;
  return (100 * Math.min(dc, shaftW)) / Math.max(dc, shaftW);
}

/** The load tests with torque and speed, highest power first: a logged run (rows with minutes) at its last row, what it settled at; any other test at its row of highest torque x speed. */
export function powerSummary(tests: TestRecord[]): PowerLine[] {
  const out: PowerLine[] = [];
  for (const t of tests) {
    if (t.kind !== 'load') continue;
    let best: PowerLine | null = null;
    const rows = (t.rows ?? []) as unknown as Array<Record<string, unknown>>;
    const logged = rows.length > 1 && rows.every((r) => finite(r.time_min) !== null);
    for (const raw of rows) {
      const rpm = Number(raw.rpm), torque = Number(raw.torque_nm);
      if (!(rpm > 0) || !(torque > 0)) continue;
      const shaftW = (torque * rpm * Math.PI) / 30;
      if (!logged && best && shaftW / 1000 <= best.powerKw) continue;
      best = { id: t.id, date: t.date, title: t.title ?? '', rpm, torque, powerKw: shaftW / 1000, effPct: rowEfficiency(raw, shaftW),
        tempC: typeof raw.temp_winding_c === 'number' ? raw.temp_winding_c : null,
        jAmm2: finite(raw.j_a_mm2), vDc: finite(raw.v_dc_v), iDc: finite(raw.i_dc_a), iPhase: finite(raw.i_phase_a),
        mode: testMode(t.title ?? '', raw, shaftW) };
    }
    if (best) out.push(best);
  }
  return out.sort((a, b) => b.powerKw - a.powerKw);
}

/** The tab Tests opens on when the viewer has not chosen one yet: Power when there is a load test to list. */
export function defaultTab(tests: TestRecord[]): TestTab {
  return powerSummary(tests).length ? 'power' : 'resistance';
}

const LOAD_POINT_GROUP = 'load_point';

/** One quantity of the load table: a column for the bench value, and for Simulation and the difference where any point has them. */
export interface LoadColumn { key: string; row: CompareRow; hasModel: boolean }
export interface LoadTable { columns: LoadColumn[]; points: Array<{ block: TableBlock; cells: Record<string, RowLine> }> }

const kwOf = (torque: number, rpm: number) => (Math.abs(torque) * rpm * Math.PI) / 30 / 1000;

/** Shaft power from the torque row of a load point (owner 2026-11-05: a column right after Torque); at one speed its difference is the torque's. */
function powerRow(torque: CompareRow, rpm: number): CompareRow {
  return { ...torque, key: 'shaft_power', label: 'Power', params: {}, unit: 'kW', tip: 'shaft_power', detail: undefined, action: null,
    measured: torque.measured === null ? null : kwOf(torque.measured, rpm),
    model: torque.model === null ? null : kwOf(torque.model, rpm) };
}

/** The shaft power of a raw bench row, kW; null without a torque or a speed. */
export function rawPowerKw(row: Record<string, unknown>): number | null {
  const torque = finite(row.torque_nm); const rpm = finite(row.rpm);
  return torque === null || rpm === null ? null : kwOf(torque, rpm);
}

/** The load points of a test as ONE table, owner 2026-11-05 ("more compact"): a row per point, a column per quantity, instead of a small
 *  table per point.  Null when the blocks hold no load point; the other groups stay as they are. */
export function loadTable(blocks: TableBlock[]): LoadTable | null {
  const points = blocks.filter((b) => b.key.startsWith(LOAD_POINT_GROUP));
  if (!points.length) return null;
  const columns = new Map<string, LoadColumn>();
  const rows = points.map((block) => {
    const cells: Record<string, RowLine> = {};
    const rpm = finite(block.params.rpm);
    const lines = block.lines.flatMap((l) => (l.row.key === 'torque' && rpm !== null ? [l, rowLine(powerRow(l.row, rpm))] : [l]));
    for (const line of lines) {
      cells[line.row.key] = line;
      const col = columns.get(line.row.key) ?? { key: line.row.key, row: line.row, hasModel: false };
      col.hasModel = col.hasModel || line.row.model != null;
      columns.set(line.row.key, col);
    }
    return { block, cells };
  });
  return { columns: Array.from(columns.values()), points: rows };
}

/** A quantity's name for a column head: what stands in brackets goes to the tooltip, it only widens the column. */
export function shortHead(text: string): string {
  return text.replace(/\s*[(（].*[)）]\s*$/, '');
}

/** The blocks shown as their own small tables: everything but the load points. */
export function otherBlocks(blocks: TableBlock[]): TableBlock[] {
  return blocks.filter((b) => !b.key.startsWith(LOAD_POINT_GROUP));
}

/** px height of everything in the table column once the load points are folded: the chart beside it follows this. */
export function foldedHeight(blocks: TableBlock[]): number {
  const table = loadTable(blocks);
  return (table ? Math.min(LOAD_TABLE_MAX_PX, (table.points.length + 1) * TABLE_ROW_PX) + 8 : 0) + blocksHeight(otherBlocks(blocks));
}

const noteId = (n: RowNote) => `${n.key}|${JSON.stringify(n.params ?? {})}`;

/** The notes of one point, each once: a point's quantities often share one note. */
export function pointNotes(cells: Record<string, RowLine>): RowNote[] {
  const seen = new Map<string, RowNote>();
  for (const l of Object.values(cells)) if (l.note) seen.set(noteId(l.note), l.note);
  return Array.from(seen.values());
}

/** The notes every point has: said once above the column, not repeated on each row. */
export function commonNotes(table: LoadTable): RowNote[] {
  return pointNotes(table.points[0].cells).filter((n) =>
    table.points.every((p) => pointNotes(p.cells).some((m) => noteId(m) === noteId(n))));
}

/** The notes of a point that the whole table does not share. */
export function ownNotes(table: LoadTable, cells: Record<string, RowLine>): RowNote[] {
  const common = new Set(commonNotes(table).map(noteId));
  return pointNotes(cells).filter((n) => !common.has(noteId(n)));
}

/** A document a browser can play on the test card, by its file name. */
export function isVideoName(name: string | null | undefined): boolean {
  return /\.(mp4|m4v|mov|webm)$/i.test(name ?? '');
}

/** A document a browser can show on the test card as a picture, by its file name. */
export function isPictureName(name: string | null | undefined): boolean {
  return /\.(png|jpe?g|webp|gif)$/i.test(name ?? '');
}

/** The type a stored picture or video opens as: the file API serves every file as plain bytes. */
export function mediaMime(name: string): string {
  if (/\.webm$/i.test(name)) return 'video/webm';
  if (/\.png$/i.test(name)) return 'image/png';
  if (/\.webp$/i.test(name)) return 'image/webp';
  if (/\.gif$/i.test(name)) return 'image/gif';
  if (/\.jpe?g$/i.test(name)) return 'image/jpeg';
  return 'video/mp4';
}

/** A test's documents by how the card shows them: pictures on one line, videos on the next, the rest as plain files. */
export function testFiles<T extends { name: string }>(docs: T[]): { pictures: T[]; videos: T[]; other: T[] } {
  return {
    pictures: docs.filter((d) => isPictureName(d.name)),
    videos: docs.filter((d) => isVideoName(d.name)),
    other: docs.filter((d) => !isPictureName(d.name) && !isVideoName(d.name)),
  };
}
