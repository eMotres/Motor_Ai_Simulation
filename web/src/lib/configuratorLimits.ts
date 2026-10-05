// Configure's slider RANGES (owner 2026-10-05) — the pure half.
//
// The old ranges were heuristic multiples of the passport's base point.  They
// are now PHYSICAL limits, each with a named basis, recomputed live from the
// knobs they depend on:
//
//   stack length  max = set BY HAND per motor by an admin (server, catalogue
//                 card); no value = today's rule, shown as "default"
//   wire          min = 0.2 mm (manufacturing limit), step 0.1 mm, max = the
//                 thickest wire at which N rows still fit the slot
//   turns / slot  max = the rows that fit the slot at the chosen wire
//   phase current max = what the machine's inverter device allows (server,
//                 from the Controller settings) — Sine mode too; none saved =
//                 the passport rule, said as "no controller set"
//   speed         max = the speed at which the line voltage reaches the fully
//                 charged pack (x the modulation index): from the passport's
//                 voltage model when it has one, else Kv x V_max x m
//   connection    free; only a WARNING when the line voltage exceeds the pack
//
// An admin may NARROW any of these locally (per machine), never widen them.
// No imports at runtime, so web/src/lib/__tests__/configuratorLimits.test.mjs
// imports this very file.

export type KnobKey = 'L_mm' | 'N' | 'wireH_mm' | 'I_A' | 'rpm';
export interface KRange { min: number; max: number; }

/** Where a range's maximum comes from — the card prints it, the tooltip explains it. */
export type LimitBasis =
  | 'hand'          // L: set by an admin for this motor
  | 'default'       // today's rule (a multiple of the passport base)
  | 'fit'           // wire / turns: the slot
  | 'inverter'      // I: the machine's inverter device
  | 'no_controller' // I: nothing saved -> the passport rule
  | 'envelope'      // rpm: the passport's voltage model at the pack maximum
  | 'kv'            // rpm: Kv x V_max x m
  | 'no_battery';   // rpm: no pack known -> the passport rule

export interface PhysRange extends KRange { basis: LimitBasis; }

/** Manufacturing limit of the flat wire [mm]. */
export const WIRE_MIN_MM = 0.2;
/** Wire-thickness step [mm]. */
export const WIRE_STEP_MM = 0.1;
/** Modulation index the speed limit assumes when the Controller states none. */
export const DEFAULT_MODULATION = 0.89;

// TODO(erp-stock): restrict the wire-thickness choices to the warehouse stock
// list once the ERP feed exists.  The hook is `allowedWireH` in
// `physicalRanges()` below: a sorted list of stocked thicknesses [mm]; null =
// no restriction (today).  Do not read the ERP repo from here.

/** The slice of a passport the rules read. */
export interface RangePassport {
  L0_mm: number; N0: number; wireH0_mm: number; I0_A: number; rpm0: number;
}

/** The slot geometry (referencePassports `fit`). */
export interface SlotFit {
  slotHeight_mm: number; insulation_mm: number; wireSpacingY_mm: number;
}

/** Today's heuristic ranges (the old `rangesForRef`) — the "default" rule. */
export function defaultRanges(p: RangePassport): Record<KnobKey, KRange> {
  const r2 = (v: number, lo: number, hi: number) => ({
    min: Math.max(0, Number((v * lo).toPrecision(2))),
    max: Number((v * hi).toPrecision(2)),
  });
  return {
    L_mm:     r2(p.L0_mm, 0.3, 3.0),
    N:        { min: Math.max(1, Math.round(p.N0 * 0.3)), max: Math.ceil(p.N0 * 2.0) },
    wireH_mm: r2(p.wireH0_mm, 0.3, 2.5),
    I_A:      { min: 0, max: Number((p.I0_A * 2.0).toPrecision(2)) },
    rpm:      { min: 0, max: Number((p.rpm0 * 2.0).toPrecision(2)) },
  };
}

// ── the slot fit — the SAME inequality the solver enforces ──────────────────
// geometry_constraints._wire_height_max:  wire_height <= (slot - 2 ins) / N - dy
// i.e. N rows of (wire + dy) between two insulation layers.  tests/
// test_configure_limits.py pins that the two agree.

const avail = (f: SlotFit) => f.slotHeight_mm - 2 * f.insulation_mm;

/** How many wire rows of thickness `h` fit the slot. */
export function fitRows(f: SlotFit, h: number): number {
  const pitch = h + f.wireSpacingY_mm;
  return pitch > 0 ? Math.max(0, Math.floor(avail(f) / pitch + 1e-9)) : 0;
}

/** The thickest wire on the 0.1 mm grid at which `n` rows fit (never above the exact bound). */
export function fitWireH(f: SlotFit, n: number): number {
  const exact = avail(f) / Math.max(1, n) - f.wireSpacingY_mm;
  return Math.floor(exact / WIRE_STEP_MM + 1e-9) * WIRE_STEP_MM;
}

/** True when `n` rows of wire `h` do not fit. */
export function overflows(f: SlotFit, n: number, h: number): boolean {
  return n * (h + f.wireSpacingY_mm) > avail(f) + 1e-9;
}

// ── speed ───────────────────────────────────────────────────────────────────

/** The speed [rpm] at which a line voltage that is linear in speed
 *  (V = a·rpm + b — EMF and the reactive drop are both ∝ rpm) reaches `vLim`.
 *  `null` when it never does or the model is degenerate. */
export function envelopeRpm(v0: number, v1000: number, vLim: number): number | null {
  const a = (v1000 - v0) / 1000;
  if (!(a > 1e-12) || !(vLim > v0)) return null;
  return (vLim - v0) / a;
}

export interface SpeedLimit { rpm: number | null; basis: 'envelope' | 'kv' | 'no_battery'; }

/** v0 / v1000: the passport's line-peak voltage at 0 and 1000 rpm for the
 *  CURRENT knobs (null when the passport has no loaded-voltage model). */
export function speedLimit(a: {
  vMax: number | null; m: number; kvRpmPerV: number | null;
  v0: number | null; v1000: number | null;
}): SpeedLimit {
  if (!(a.vMax && a.vMax > 0)) return { rpm: null, basis: 'no_battery' };
  if (a.v0 != null && a.v1000 != null) {
    const r = envelopeRpm(a.v0, a.v1000, a.m * a.vMax);
    if (r != null) return { rpm: r, basis: 'envelope' };
  }
  if (a.kvRpmPerV != null && a.kvRpmPerV > 0) {
    return { rpm: a.kvRpmPerV * a.vMax * a.m, basis: 'kv' };
  }
  return { rpm: null, basis: 'no_battery' };
}

// ── the ranges ──────────────────────────────────────────────────────────────

export interface RangeInputs {
  p: RangePassport;
  fit: SlotFit;
  /** the live knobs the ranges depend on */
  N: number; wireH_mm: number;
  /** hand-set stack-length maximum of this motor [mm] (server) */
  lMaxMm: number | null;
  /** the machine's inverter ceiling, phase current [A rms] (server); null = none saved */
  iMaxA: number | null;
  speed: SpeedLimit;
  /** TODO(erp-stock): stocked wire thicknesses [mm], ascending; null = unrestricted */
  allowedWireH?: number[] | null;
}

export function physicalRanges(a: RangeInputs): Record<KnobKey, PhysRange> {
  const d = defaultRanges(a.p);
  const rows = fitRows(a.fit, a.wireH_mm);
  const nMax = Math.max(1, rows);
  const hFit = fitWireH(a.fit, a.N);
  let hMax = Math.max(WIRE_MIN_MM, Math.min(d.wireH_mm.max, hFit));
  if (a.allowedWireH && a.allowedWireH.length) {
    // TODO(erp-stock): snap to the stocked list; unused until the feed exists
    const ok = a.allowedWireH.filter((h) => h >= WIRE_MIN_MM && h <= hMax + 1e-9);
    if (ok.length) hMax = ok[ok.length - 1];
  }
  return {
    L_mm: a.lMaxMm != null && a.lMaxMm > 0
      ? { min: d.L_mm.min, max: a.lMaxMm, basis: 'hand' }
      : { ...d.L_mm, basis: 'default' },
    N: { min: Math.min(d.N.min, nMax), max: nMax, basis: 'fit' },
    wireH_mm: { min: WIRE_MIN_MM, max: hMax, basis: 'fit' },
    I_A: a.iMaxA != null && a.iMaxA > 0
      ? { min: 0, max: a.iMaxA, basis: 'inverter' }
      : { ...d.I_A, basis: 'no_controller' },
    rpm: a.speed.rpm != null && a.speed.rpm > 0
      ? { min: 0, max: a.speed.rpm, basis: a.speed.basis === 'kv' ? 'kv' : 'envelope' }
      : { ...d.rpm, basis: 'no_battery' },
  };
}

/** An admin's local override, clamped INSIDE the physical range: it can only narrow. */
export function narrowRange(phys: KRange, ov: KRange | null | undefined): KRange {
  if (!ov) return { min: phys.min, max: phys.max };
  const lo = Math.max(phys.min, Number.isFinite(ov.min) ? ov.min : phys.min);
  const hi = Math.min(phys.max, Number.isFinite(ov.max) ? ov.max : phys.max);
  return lo <= hi ? { min: lo, max: hi } : { min: phys.min, max: phys.max };
}

// ── the connection warning ──────────────────────────────────────────────────

/** The line-peak voltage the winding needs against the pack NOMINAL; the
 *  connection itself stays free.  `null` = fine (or no pack to judge by). */
export function lineVoltageWarning(vLinePeak: number, packNomV: number | null):
  { line: number; nominal: number } | null {
  if (!(packNomV && packNomV > 0) || !(vLinePeak > packNomV)) return null;
  return { line: vLinePeak, nominal: packNomV };
}

// ── per-machine memory of the admin's narrowing ─────────────────────────────

export const RANGES_LS_V2 = 'configurator.ranges.v2';
export type Overrides = Partial<Record<KnobKey, KRange>>;

const isObj = (c: unknown): c is Record<string, unknown> =>
  !!c && typeof c === 'object' && !Array.isArray(c);
const KEYS: KnobKey[] = ['L_mm', 'N', 'wireH_mm', 'I_A', 'rpm'];

export function readOverrides(raw: string | null, refId: string): Overrides {
  if (!raw || !refId) return {};
  try {
    const all = JSON.parse(raw);
    const c = isObj(all) ? all[refId] : null;
    if (!isObj(c)) return {};
    const out: Overrides = {};
    for (const k of KEYS) {
      const r = c[k];
      if (isObj(r) && Number.isFinite(Number(r.min)) && Number.isFinite(Number(r.max))) {
        out[k] = { min: Number(r.min), max: Number(r.max) };
      }
    }
    return out;
  } catch { return {}; }
}

export function writeOverrides(raw: string | null, refId: string, ov: Overrides): string {
  let all: Record<string, unknown> = {};
  try { const p = raw ? JSON.parse(raw) : {}; if (isObj(p)) all = p; } catch { /* start over */ }
  if (Object.keys(ov).length) all[refId] = ov; else delete all[refId];
  return JSON.stringify(all);
}
