// Propellers > Designer (owner 2026-11-15): the pure helpers behind the page.  No React, no window:
// web/src/lib/__tests__/propellerDesign.test.mjs imports this file.

export type Group = 'inner' | 'outer' | 'airfoil' | 'topology';
export const GROUP_ORDER: readonly Group[] = ['inner', 'outer', 'airfoil', 'topology'];

/** One row of GET /api/propellers/design/family `variables`. */
export interface VarRow {
  name: string;
  value: number | string;
  lo?: number | null;
  hi?: number | null;
  options?: Array<number | string>;
  unit: string;
  group: Group;
  basis?: string;
  confirmed?: boolean;
}

export type Vector = Record<string, number | string>;

export interface Objectives { thrust_per_shaft_W: number | null; cooling_air_axial_ms: number | null }

/** The evaluate record the page needs (a subset of the API answer). */
export interface EvalRecord {
  design_id: string;
  operating_point: string;
  operating: { rpm: number; torque_limit_Nm: number; motor_radius_mm: number };
  thrust_N: number;
  torque_Nm: number;
  shaft_power_W: number;
  tip_mach: number;
  objectives: Objectives;
  constraints: Record<string, boolean>;
  constraints_met: boolean;
  profile: { r_m: Array<number | null>; axial_total_ms: Array<number | null>; swirl_ms: Array<number | null> };
  flags: string[];
}

export interface RunRecord {
  id: number;
  label: string;
  vector: Vector;
  designId: string;
  op: string;
  rpm: number;
  thrust_per_shaft_W: number | null;
  air_ms: number | null;
  constraintsMet: boolean;
  tipMach: number;
}

export const GROUP_KEYS: Record<Group, string> = {
  inner: 'propDesignGroupInner', outer: 'propDesignGroupOuter', airfoil: 'propDesignGroupAirfoil', topology: 'propDesignGroupTopology',
};

/** Rows by group in display order; a row of an unknown group goes to the end group rather than disappearing. */
export function groupRows(rows: readonly VarRow[]): Array<{ group: Group; rows: VarRow[] }> {
  const out = GROUP_ORDER.map((g) => ({ group: g, rows: [] as VarRow[] }));
  for (const r of rows) {
    const slot = out.find((o) => o.group === r.group) ?? out[out.length - 1];
    slot.rows.push(r);
  }
  return out.filter((o) => o.rows.length > 0);
}

/** True when a numeric value lies outside its stated bounds (or is not a finite number).  Rows without bounds are never out. */
export function outOfBounds(row: Pick<VarRow, 'lo' | 'hi'>, value: unknown): boolean {
  if (row.lo == null && row.hi == null) return false;
  if (typeof value !== 'number' || !Number.isFinite(value)) return true;
  if (row.lo != null && value < row.lo) return true;
  if (row.hi != null && value > row.hi) return true;
  return false;
}

/** Parse a text field to a number; null for empty / not a number (the page then keeps the old value and shows the field red). */
export function parseNumber(text: string): number | null {
  const t = text.trim().replace(',', '.');
  if (t === '') return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** The variables whose value differs between two vectors (names, in the order of `b`). */
export function changedNames(a: Vector | null, b: Vector): string[] {
  if (!a) return Object.keys(b);
  return Object.keys(b).filter((k) => a[k] !== b[k]);
}

/** Short run label: the changed variables, or "start". */
export function runLabel(prev: Vector | null, next: Vector, max = 3): string {
  const ch = changedNames(prev, next);
  if (!prev) return 'start';
  if (ch.length === 0) return 'same';
  const head = ch.slice(0, max).map((n) => `${n}=${typeof next[n] === 'number' ? Number((next[n] as number).toPrecision(4)) : next[n]}`);
  return head.join(', ') + (ch.length > max ? ` +${ch.length - max}` : '');
}

export function toRun(id: number, label: string, vector: Vector, r: EvalRecord): RunRecord {
  return {
    id, label, vector: { ...vector }, designId: r.design_id, op: r.operating_point, rpm: r.operating.rpm,
    thrust_per_shaft_W: r.objectives.thrust_per_shaft_W, air_ms: r.objectives.cooling_air_axial_ms,
    constraintsMet: r.constraints_met, tipMach: r.tip_mach,
  };
}

/** Indices of the non-dominated runs when BOTH objectives are to be maximised.  A run with a missing objective is never on the front. */
export function paretoFront(runs: ReadonlyArray<Pick<RunRecord, 'thrust_per_shaft_W' | 'air_ms'>>): number[] {
  const ok = runs.map((r, i) => ({ r, i })).filter(({ r }) => r.thrust_per_shaft_W != null && r.air_ms != null);
  const front: number[] = [];
  for (const { r, i } of ok) {
    const dominated = ok.some(({ r: q, i: j }) => j !== i
      && (q.thrust_per_shaft_W as number) >= (r.thrust_per_shaft_W as number) && (q.air_ms as number) >= (r.air_ms as number)
      && ((q.thrust_per_shaft_W as number) > (r.thrust_per_shaft_W as number) || (q.air_ms as number) > (r.air_ms as number)));
    if (!dominated) front.push(i);
  }
  return front;
}

/** The cost of cooling air: efficiency (thrust per shaft watt) given up per m/s gained, between two points on the front sorted by air speed.
 *  null when fewer than two comparable points. */
export function frontCost(runs: ReadonlyArray<Pick<RunRecord, 'thrust_per_shaft_W' | 'air_ms'>>, front: readonly number[]): number | null {
  const pts = front.map((i) => runs[i]).filter((r) => r.thrust_per_shaft_W != null && r.air_ms != null)
    .sort((a, b) => (a.air_ms as number) - (b.air_ms as number));
  if (pts.length < 2) return null;
  const a = pts[0], b = pts[pts.length - 1];
  const dAir = (b.air_ms as number) - (a.air_ms as number);
  if (dAir <= 0) return null;
  return ((a.thrust_per_shaft_W as number) - (b.thrust_per_shaft_W as number)) / dAir;
}

export interface OutlinePoint { r_mm: number; chord_mm: number; twist_deg: number }

/** Planform polygon (leading edge up) around the pitch axis (default quarter chord): [[r, y], ...] closed. */
export function planformPolygon(outline: readonly OutlinePoint[], pitchAxis = 0.25): Array<[number, number]> {
  const le = outline.map((p) => [p.r_mm, p.chord_mm * pitchAxis] as [number, number]);
  const te = outline.map((p) => [p.r_mm, -p.chord_mm * (1 - pitchAxis)] as [number, number]).reverse();
  return [...le, ...te];
}

/** Chart rows for the radial velocity profile; null speeds are kept as null (a gap, never zero). */
export function profileRows(p: EvalRecord['profile']): Array<{ r_mm: number; axial: number | null; swirl: number | null }> {
  return p.r_m.map((r, i) => ({ r_mm: (r ?? 0) * 1000, axial: p.axial_total_ms[i] ?? null, swirl: p.swirl_ms[i] ?? null }));
}

/** Flags worth a visible chip (all of them stay in the tooltip). */
export const KEY_FLAGS = ['root_flow_not_validated', 'design_inputs_unconfirmed', 'manufacturing_limits_unconfirmed',
  'airfoil_choice_not_resolved_by_polar', 'high_tip_mach', 'rpm_capped'] as const;

export function keyFlags(flags: readonly string[]): string[] {
  return KEY_FLAGS.filter((f) => flags.includes(f));
}

export function fmt(v: number | null | undefined, digits = 2): string {
  return v == null || !Number.isFinite(v) ? '—' : v.toFixed(digits);
}
