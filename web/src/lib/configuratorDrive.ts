// Configure's DRIVE menu (owner 2026-10-05: "Sine / PWM"; revised the same day:
// PWM lists only the drive variants COMPUTED for this motor, no live
// arbitrary-device calculation) — the pure half.  Nothing here fetches, reads a
// store or touches React, so web/src/lib/__tests__/configuratorDrive.test.mjs
// can state the rules (verbatim-copy convention).
//
// THE DATA: the machine's passport carries `pwm_variants` — each entry one
// (device, carrier) pair solved for THIS machine, with computed points
// { motor_pwm_loss_W, inverter_loss_W{cond,sw,dead}, tj_C, eta_drive_pct,
//   eta_shaft_pct, p_cont_max_W }.  Configure reads between those points the way
// it reads the sine passport's loss grid (bilinear over current × speed) and
// REFUSES outside the computed envelope.  scaleMotor() never sees any of this,
// so the Sine numbers cannot move.
import type { Knobs, PwmVariant, PwmVariantPoint } from './motorScaling';

// ── the variants of a passport ──────────────────────────────────────────────

/** The usable variants (an id, a device, a positive carrier, ≥ 1 point). */
export function usableVariants(list: PwmVariant[] | null | undefined): PwmVariant[] {
  if (!Array.isArray(list)) return [];
  return list.filter((v) => v && typeof v.id === 'string' && v.id
    && typeof v.device === 'string' && v.device
    && Number(v.carrier_hz) > 0
    && v.points && typeof v.points === 'object' && Object.keys(v.points).length > 0);
}

const trimNum = (v: number, d: number) => String(Number(v.toFixed(d)));

// ── the TWO dropdowns: transistor, then PWM frequency (owner 2026-10-05) ─────────────────────
// A variant is one (device, carrier) pair; the transistor menu lists the devices, the frequency
// menu lists only the carriers computed for the chosen device, and the pair names exactly one
// variant.  Pure functions over the passport's `pwm_variants`.

/** `48 kHz` — the frequency option's label. */
export const carrierLabel = (hz: number): string => `${trimNum(Number(hz) / 1000, 1)} kHz`;

/** The devices, in the order the passport lists them, each once (with its technology for the tooltip). */
export function variantDevices(vs: PwmVariant[]): { device: string; technology: string | null }[] {
  const out: { device: string; technology: string | null }[] = [];
  for (const v of vs) if (!out.some((d) => d.device === v.device)) out.push({ device: v.device, technology: v.technology ?? null });
  return out;
}

/** The carriers computed for `device` in this motor, ascending. */
export function variantCarriers(vs: PwmVariant[], device: string): number[] {
  return Array.from(new Set(vs.filter((v) => v.device === device).map((v) => Number(v.carrier_hz)))).sort((a, b) => a - b);
}

/** The ONE variant of a pair (the first, if a passport ever lists a pair twice). */
export function variantFor(vs: PwmVariant[], device: string, carrierHz: number): PwmVariant | undefined {
  return vs.find((v) => v.device === device && Math.abs(Number(v.carrier_hz) - carrierHz) < 1e-6 * Math.max(1, carrierHz));
}

/** Stable key of a variant's pair — what "the same drive" means (ids may be renumbered). */
export const pairKey = (v: Pick<PwmVariant, 'device' | 'carrier_hz'> | null | undefined): string =>
  (v ? `${v.device}|${Number(v.carrier_hz)}` : '');

/** Change the transistor: keep the current frequency if the new device has it, else the NEAREST computed
 *  one (a tie goes to the lower frequency).  Undefined when the device has no variant. */
export function switchDevice(vs: PwmVariant[], cur: PwmVariant | null | undefined, device: string): PwmVariant | undefined {
  const cs = variantCarriers(vs, device);
  if (!cs.length) return undefined;
  const want = cur ? Number(cur.carrier_hz) : cs[0];
  const exact = variantFor(vs, device, want);
  if (exact) return exact;
  let best = cs[0];
  for (const c of cs) if (Math.abs(c - want) < Math.abs(best - want) - 1e-9) best = c;
  return variantFor(vs, device, best);
}

/** Change the frequency of the current transistor. */
export function switchCarrier(vs: PwmVariant[], cur: PwmVariant | null | undefined, carrierHz: number): PwmVariant | undefined {
  return cur ? variantFor(vs, cur.device, carrierHz) : undefined;
}

/** The variant a stored choice names: by id, else by the (device, carrier) pair, else the first. */
export function resolveVariant(vs: PwmVariant[], c: { drive_variant?: string; drive_device?: string; drive_carrier_hz?: number }): PwmVariant | undefined {
  return vs.find((v) => v.id === c.drive_variant)
    ?? (c.drive_device && c.drive_carrier_hz ? variantFor(vs, c.drive_device, Number(c.drive_carrier_hz)) : undefined)
    ?? vs[0];
}

/** `IQE018N06NM6SC · 48 kHz` — the option label: device and carrier only (owner 2026-10-05, no
 *  variant details printed by default; the technology, dead time, parallel count, modulation and
 *  bus ride in the picker's tooltip). */
export function variantLabel(v: PwmVariant): string {
  return [v.device, `${trimNum(Number(v.carrier_hz) / 1000, 1)} kHz`].join(' · ');
}

/** The read-only facts shown under the picker, in display order. */
export interface VariantFacts {
  deadTime: string | null;      // "100 ns" / "0.5 µs" (a number with a unit)
  nParallel: number | null;
  /** i18n key of a KNOWN modulation name (`configureDrive.mod*`), else null */
  modulationKey: string | null;
  mMax: number | null;
  /** the bus range as numbers; the words around them come from i18n */
  bus: { min: number | null; nom: number | null; max: number | null } | null;
  provenance: string | null;
}

/** Known modulation names -> the locale key that names them.  An unknown name
 *  is data in someone else's words: it is not shown rather than shown untranslated. */
export function modulationKey(raw: string | null | undefined): string | null {
  const t = String(raw ?? '').toLowerCase();
  if (!t) return null;
  if (t.includes('svpwm') && t.includes('cent')) return 'configureDrive.modSvpwmCentred';
  if (t.includes('svpwm')) return 'configureDrive.modSvpwm';
  if (t.includes('third')) return 'configureDrive.modThird';
  if (t.includes('sine') || t.includes('spwm')) return 'configureDrive.modSine';
  return null;
}

export function variantFacts(v: PwmVariant): VariantFacts {
  const dt = Number(v.dead_time_s);
  const deadTime = Number.isFinite(dt) && v.dead_time_s != null && dt >= 0
    ? (dt < 1e-6 ? `${trimNum(dt * 1e9, 1)} ns` : `${trimNum(dt * 1e6, 2)} µs`) : null;
  const b = v.bus_v;
  const bn = (x: unknown) => (x != null && Number.isFinite(Number(x)) ? Number(x) : null);
  const bmin = bn(b?.min), bnom = bn(b?.nom), bmax = bn(b?.max);
  const bus = bmin != null || bnom != null || bmax != null
    ? { min: bmin != null ? Number(trimNum(bmin, 1)) : null,
        nom: bnom != null ? Number(trimNum(bnom, 1)) : null,
        max: bmax != null ? Number(trimNum(bmax, 1)) : null } : null;
  const pv = v.provenance;
  const provenance = pv == null ? null
    : typeof pv === 'string' ? pv : JSON.stringify(pv);
  const np = Number(v.n_parallel);
  const mm = Number(v.m_max);
  return {
    deadTime,
    nParallel: v.n_parallel != null && Number.isFinite(np) && np > 0 ? np : null,
    modulationKey: modulationKey(v.modulation),
    mMax: v.m_max != null && Number.isFinite(mm) ? mm : null,
    bus, provenance,
  };
}

// ── reading between computed points ─────────────────────────────────────────

export interface VariantPoint {
  rpm: number; I: number; key: string; p: PwmVariantPoint;
}

const num = (x: unknown): number | null => {
  if (x == null || x === '') return null;
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
};

/** A point's coordinates [rpm, A rms]: the value's own `rpm` / `I_A` fields,
 *  else the key (`"3000rpm_40A"`, `"rpm=3000,I=40"`).  `null` for a point with
 *  neither (a named duty — it cannot be read between). */
export function pointCoords(key: string, p: PwmVariantPoint): { rpm: number; I: number } | null {
  const q = p as Record<string, unknown>;
  let rpm = num(q.rpm), I = num(q.I_A ?? q.i_a ?? q.I_rms_A ?? q.current_a_rms);
  if (rpm == null) { const m = /(-?\d+(?:\.\d+)?)\s*rpm/i.exec(key) ?? /rpm\s*[=:_]?\s*(-?\d+(?:\.\d+)?)/i.exec(key); if (m) rpm = Number(m[1]); }
  if (I == null) { const m = /(-?\d+(?:\.\d+)?)\s*A\b/.exec(key) ?? /\bI(?:_A)?\s*[=:_]?\s*(-?\d+(?:\.\d+)?)/.exec(key); if (m) I = Number(m[1]); }
  return rpm != null && I != null && rpm >= 0 && I >= 0 ? { rpm, I } : null;
}

export function variantPoints(v: PwmVariant): VariantPoint[] {
  const out: VariantPoint[] = [];
  for (const [key, p] of Object.entries(v.points || {})) {
    const c = p && typeof p === 'object' ? pointCoords(key, p) : null;
    if (c) out.push({ ...c, key, p });
  }
  return out;
}

export interface Envelope { rpmMin: number; rpmMax: number; iMin: number; iMax: number; n: number; }

export function variantEnvelope(v: PwmVariant): Envelope | null {
  const pts = variantPoints(v);
  if (!pts.length) return null;
  return {
    rpmMin: Math.min(...pts.map((q) => q.rpm)), rpmMax: Math.max(...pts.map((q) => q.rpm)),
    iMin: Math.min(...pts.map((q) => q.I)), iMax: Math.max(...pts.map((q) => q.I)),
    n: pts.length,
  };
}

/** What Configure shows for one drive variant at one operating point. */
export interface VariantReading {
  motor_pwm_loss_W: number | null;
  inv_cond_W: number | null;
  inv_sw_W: number | null;
  inv_dead_W: number | null;
  inv_total_W: number | null;
  /** the copper of the coil links on the controller board (K x I^2): heat of the controller, not of the motor */
  board_copper_W: number | null;
  tj_C: number | null;
  eta_drive_pct: number | null;
  eta_shaft_pct: number | null;
  p_cont_max_W: number | null;
}

export type VariantRefusal =
  | { kind: 'no_coords' }
  | { kind: 'speed'; rpm: number; lo: number; hi: number }
  | { kind: 'current'; I: number; lo: number; hi: number }
  | { kind: 'gap' };

/** Relative slack on the envelope edges (a typed 3000 rpm is not "outside"
 *  a grid that stops at 3000.0). */
const EDGE_TOL = 0.005;

function inv(p: PwmVariantPoint): { c: number | null; s: number | null; d: number | null; t: number | null } {
  const q = p.inverter_loss_W;
  if (q == null) return { c: null, s: null, d: null, t: null };
  if (typeof q === 'number') return { c: null, s: null, d: null, t: Number.isFinite(q) ? q : null };
  const c = num(q.cond), s = num(q.sw), d = num(q.dead);
  const t = c != null && s != null && d != null ? c + s + d : null;
  return { c, s, d, t };
}

/** The nine fields of one computed point. */
export function readingOf(p: PwmVariantPoint): VariantReading {
  const i = inv(p);
  return {
    motor_pwm_loss_W: num(p.motor_pwm_loss_W),
    inv_cond_W: i.c, inv_sw_W: i.s, inv_dead_W: i.d, inv_total_W: i.t,
    board_copper_W: num(p.board_copper_W),
    tj_C: num(p.tj_C), eta_drive_pct: num(p.eta_drive_pct),
    eta_shaft_pct: num(p.eta_shaft_pct), p_cont_max_W: num(p.p_cont_max_W),
  };
}

const KEYS: (keyof VariantReading)[] = ['motor_pwm_loss_W', 'inv_cond_W', 'inv_sw_W',
  'inv_dead_W', 'inv_total_W', 'board_copper_W', 'tj_C', 'eta_drive_pct', 'eta_shaft_pct', 'p_cont_max_W'];

/** The bracketing axis values around x: [lo, hi] (equal when x sits on a node
 *  or the axis has one node). */
function bracket(axis: number[], x: number): [number, number] | null {
  if (axis.length === 1) return Math.abs(x - axis[0]) <= EDGE_TOL * Math.max(1, Math.abs(axis[0])) + 1e-9 ? [axis[0], axis[0]] : null;
  const lo = axis[0], hi = axis[axis.length - 1];
  const slack = EDGE_TOL * Math.max(1, Math.abs(hi - lo));
  if (x < lo - slack || x > hi + slack) return null;
  const xc = Math.min(hi, Math.max(lo, x));
  for (let i = 0; i < axis.length - 1; i++) {
    if (xc >= axis[i] - 1e-12 && xc <= axis[i + 1] + 1e-12) return [axis[i], axis[i + 1]];
  }
  return null;
}

/**
 * Bilinear read of a variant at (rpm, I) — or a loud, specific refusal.
 *
 * The bracketing cell's four corners must all be computed points; a gap in the
 * grid is a refusal, never a quiet fill.  A field that is missing at any corner
 * stays `null` (the tile shows "—").
 */
export function readVariant(v: PwmVariant, rpm: number, I: number):
  { ok: true; values: VariantReading; exact: boolean } | { ok: false; refusal: VariantRefusal } {
  const pts = variantPoints(v);
  if (!pts.length) return { ok: false, refusal: { kind: 'no_coords' } };
  const env = variantEnvelope(v)!;
  const rAxis = [...new Set(pts.map((q) => q.rpm))].sort((a, b) => a - b);
  const iAxis = [...new Set(pts.map((q) => q.I))].sort((a, b) => a - b);
  const rb = bracket(rAxis, rpm);
  if (!rb) return { ok: false, refusal: { kind: 'speed', rpm, lo: env.rpmMin, hi: env.rpmMax } };
  const ib = bracket(iAxis, I);
  if (!ib) return { ok: false, refusal: { kind: 'current', I, lo: env.iMin, hi: env.iMax } };
  const at = (r: number, i: number) => pts.find((q) => q.rpm === r && q.I === i);
  const c00 = at(rb[0], ib[0]), c01 = at(rb[0], ib[1]), c10 = at(rb[1], ib[0]), c11 = at(rb[1], ib[1]);
  if (!c00 || !c01 || !c10 || !c11) return { ok: false, refusal: { kind: 'gap' } };
  const t = rb[1] > rb[0] ? Math.min(1, Math.max(0, (rpm - rb[0]) / (rb[1] - rb[0]))) : 0;
  const u = ib[1] > ib[0] ? Math.min(1, Math.max(0, (I - ib[0]) / (ib[1] - ib[0]))) : 0;
  const r00 = readingOf(c00.p), r01 = readingOf(c01.p), r10 = readingOf(c10.p), r11 = readingOf(c11.p);
  const out = {} as VariantReading;
  for (const k of KEYS) {
    const a = r00[k], b = r01[k], c = r10[k], d = r11[k];
    out[k] = a == null || b == null || c == null || d == null ? null
      : (1 - t) * ((1 - u) * a + u * b) + t * ((1 - u) * c + u * d);
  }
  const exact = (t === 0 || t === 1) && (u === 0 || u === 1);
  return { ok: true, values: out, exact };
}

/** True when a build knob moved off the machine as loaded.  The variants were
 *  computed for the loaded build; only current and speed are free. */
export function buildTuned(k: Knobs, r: Knobs | null): boolean {
  if (!r) return false;
  const moved = (a: number, b: number) => Math.abs(a - b) > 1e-6 * Math.max(1, Math.abs(b));
  return moved(k.N, r.N) || moved(k.L_mm, r.L_mm) || moved(k.wireH_mm, r.wireH_mm)
    || moved(k.nP, r.nP) || moved(k.split ?? 1, r.split ?? 1);
}

// ── per-machine memory of the choice ────────────────────────────────────────

export const DRIVE_LS = 'configurator.drive.v1';

export interface DriveChoice { drive?: 'pwm'; drive_variant?: string; drive_device?: string; drive_carrier_hz?: number; }

/** Only the drive fields of a knob set. */
export function pickDrive(k: Pick<Knobs, 'drive' | 'drive_variant' | 'drive_device' | 'drive_carrier_hz'>): DriveChoice {
  const out: DriveChoice = {};
  if (k.drive === 'pwm') out.drive = 'pwm';
  if (k.drive_variant) out.drive_variant = k.drive_variant;
  if (k.drive_device) out.drive_device = k.drive_device;
  if (Number(k.drive_carrier_hz) > 0) out.drive_carrier_hz = Number(k.drive_carrier_hz);
  return out;
}

const isObj = (c: unknown): c is Record<string, unknown> =>
  !!c && typeof c === 'object' && !Array.isArray(c);

/** One machine's remembered drive out of the raw localStorage text. */
export function readDriveChoice(raw: string | null, refId: string): DriveChoice | null {
  if (!raw || !refId) return null;
  try {
    const all = JSON.parse(raw);
    const c = isObj(all) ? all[refId] : null;
    if (!isObj(c)) return null;
    const out: DriveChoice = {};
    if (c.drive === 'pwm') out.drive = 'pwm';
    if (typeof c.drive_variant === 'string' && c.drive_variant) out.drive_variant = c.drive_variant;
    if (typeof c.drive_device === 'string' && c.drive_device) out.drive_device = c.drive_device;
    if (typeof c.drive_carrier_hz === 'number' && c.drive_carrier_hz > 0) out.drive_carrier_hz = c.drive_carrier_hz;
    return out.drive || out.drive_variant ? out : null;
  } catch { return null; }
}

/** The raw text to store after this machine's drive changed. */
export function writeDriveChoice(raw: string | null, refId: string, c: DriveChoice): string {
  let all: Record<string, unknown> = {};
  try { const p = raw ? JSON.parse(raw) : {}; if (isObj(p)) all = p; } catch { /* start over */ }
  if (c.drive === 'pwm') all[refId] = c; else delete all[refId];
  return JSON.stringify(all);
}

// ── the saved configuration's record ────────────────────────────────────────

/** What a saved configuration says about its drive (the comparison table's
 *  "Drive" column and any datasheet or report built from it later). */
export interface DriveRecord {
  mode: 'sine' | 'pwm';
  variant_id?: string;
  device?: string;
  technology?: string | null;
  carrier_hz?: number;
  dead_time_s?: number | null;
  n_parallel?: number | null;
  inverter_loss_W?: number | null;
  tj_C?: number | null;
  eta_drive_pct?: number | null;
}

// ── the device's own limits ─────────────────────────────────────────────────
// The variant's numbers are COMPUTED; these checks only hold them against the
// catalogue card's published limits (a read of the card, never a calculation),
// so a variant computed on a bus, current or temperature beyond its device is
// refused loudly instead of being shown as a result.

/** The Controller module refuses a bus MAXIMUM above V_DSS and warns above
 *  0.80 of it; the tuner refuses at 0.90 (52.2 V on a 60 V part stays legal). */
export const BUS_MARGIN = 0.90;

/** The slice of a catalogue row the checks read. */
export interface DeviceLimits {
  v_dss_V?: number | null;
  i_d_100c_A?: number | null;
  t_j_max_c?: number | null;
}

export type LimitProblem =
  | { kind: 'tj'; tj: number; limit: number }
  | { kind: 'rating'; amps: number; limit: number }
  | { kind: 'vds'; vdss: number; bus: number; max: number }
  | { kind: 'bus'; lo: number | null; hi: number | null; packMin: number | null; packMax: number | null };

/** The pack's voltage window [V] (empty .. full) the bus ranges are judged against. */
export interface PackWindow { min: number | null; max: number | null; }

export function limitProblems(v: PwmVariant, r: VariantReading, iRms: number,
                              dev: DeviceLimits | null, pack: PackWindow | null): LimitProblem[] {
  const out: LimitProblem[] = [];
  const packMaxV = pack?.max ?? null;
  const vbMax = num(v.bus_v?.max);
  const vbMin = num(v.bus_v?.min);
  // A variant is computed over a bus range; a pack that reaches outside it is not what
  // was computed (Sine keeps working — only this variant is refused).
  const aboveV = vbMax != null && packMaxV != null && packMaxV > vbMax * 1.001;
  const belowV = vbMin != null && pack?.min != null && pack.min < vbMin * 0.999;
  if (aboveV || belowV) {
    out.push({ kind: 'bus', lo: vbMin, hi: vbMax, packMin: pack?.min ?? null, packMax: packMaxV });
  }
  if (!dev) return out;
  const busMax = Math.max(vbMax ?? 0, packMaxV ?? 0);
  const vdss = num(dev.v_dss_V);
  if (vdss != null && busMax > BUS_MARGIN * vdss + 1e-9) {
    out.push({ kind: 'vds', vdss, bus: busMax, max: BUS_MARGIN * vdss });
  }
  const id = num(dev.i_d_100c_A);
  const nPar = Math.max(1, Math.round(Number(v.n_parallel) || 1));
  const iSw = iRms / Math.SQRT2 / nPar;       // one switch carries the leg half the period
  if (id != null && iSw > id) out.push({ kind: 'rating', amps: iSw, limit: id });
  const tjMax = num(dev.t_j_max_c);
  if (tjMax != null && r.tj_C != null && r.tj_C > tjMax) {
    out.push({ kind: 'tj', tj: r.tj_C, limit: tjMax });
  }
  return out;
}

/** `Sine` or `PWM · IQE018N06NM6SC · 48 kHz` — one cell, one short line. */
export function driveText(d: DriveRecord | null | undefined, sineWord = 'Sine'): string {
  if (!d || d.mode !== 'pwm') return sineWord;
  const bits = ['PWM'];
  if (d.device) bits.push(d.device);
  if (d.carrier_hz) bits.push(`${trimNum(d.carrier_hz / 1000, 1)} kHz`);
  return bits.join(' · ');
}
