// Configure with a propeller (owner 2026-10-05).
//
// For a die whose only cooling is the propeller's slipstream (config/cooling_options.yaml:
// `propeller_air`, the Ø40 drone motors) the propeller is both the LOAD and the COOLING:
//   * speed knob -> propeller torque at that rpm -> the phase current that makes it (passport),
//   * the same rpm -> the slipstream air speed -> the housing film h -> the temperatures.
// Every propeller number comes from the backend (`GET /api/propellers/{id}/series`, the same
// functions as `/point`); this file only INTERPOLATES in that grid and inverts torque -> current.
// Pure functions: no fetch, no React.

/** The backend's `/series` answer (only what Configure reads). */
export interface PropSeries {
  propeller_id: string;
  rpm: number[];
  torque_Nm: number[];
  thrust_N: number[];
  shaft_power_W: number[];
  air_speed_ms: number[];
  /** housing film coefficient per sample (asked for with `housing_d_mm`) */
  h_W_m2K?: number[];
  extrapolated: boolean[];
  rpm_range_tested: [number, number] | null;
  power_estimated: boolean;
  t_ambient_c: number;
}

/** The grid Configure asks for: 0 … 40 000 rpm every 125 rpm (a Ø40 drone motor never
 *  exceeds it); linear interpolation in it is within 0.1 % of the model above 1 000 rpm. */
export const SERIES_RPM_MAX = 40000;
export const SERIES_N = 321;

/** What the propeller says at one rpm. */
export interface PropPoint {
  rpm: number;
  torque_Nm: number;
  thrust_N: number;
  shaft_power_W: number;
  air_speed_ms: number;
  h_W_m2K: number | null;
  /** beyond the tested rpm range: the coefficients are held at the edge value */
  extrapolated: boolean;
}

/** Linear interpolation in the grid; `null` when the rpm is outside the grid. */
export function seriesAt(s: PropSeries, rpm: number): PropPoint | null {
  const x = s.rpm;
  if (!x.length || !(rpm >= x[0]) || rpm > x[x.length - 1] + 1e-9) return null;
  let lo = 0, hi = x.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (x[m] <= rpm) lo = m; else hi = m; }
  const w = x[hi] === x[lo] ? 0 : (rpm - x[lo]) / (x[hi] - x[lo]);
  const f = (a: number[]) => a[lo] + (a[hi] - a[lo]) * w;
  const tested = s.rpm_range_tested;
  return {
    rpm, torque_Nm: f(s.torque_Nm), thrust_N: f(s.thrust_N), shaft_power_W: f(s.shaft_power_W),
    air_speed_ms: f(s.air_speed_ms), h_W_m2K: s.h_W_m2K ? f(s.h_W_m2K) : null,
    extrapolated: tested ? rpm > 0 && (rpm < tested[0] || rpm > tested[1]) : rpm > 0 && (s.extrapolated[lo] || s.extrapolated[hi]),
  };
}

// ── which cooling is the propeller ───────────────────────────────────────────────────────
/** The context's `cooling` block (GET /api/catalog/{id}/configure_context). */
export interface CoolingInfo {
  restricted?: boolean;
  cooling_options?: string[] | null;
  propellers?: string[] | null;
  die?: string; config?: string | null;
  /** the propeller THIS configuration opens on, and every configuration's (config/cooling_options.yaml) */
  default_propeller?: string | null;
  defaults?: Record<string, string> | null;
}

/** True when the propeller's slipstream is the machine's ONLY cooling. */
export function isPropellerCooled(c: CoolingInfo | null | undefined): boolean {
  const o = c?.cooling_options;
  return !!c?.restricted && Array.isArray(o) && o.length > 0 && o.every((x) => x === 'propeller_air');
}

/** A catalogue entry as `GET /api/propellers` lists it. */
export interface PropSummary {
  id: string; vendor: string; model: string; blades?: number | null;
  diameter_in?: number | null;
  data_quality?: string; power_data?: string; selectable: boolean;
  rpm_range_tested?: [number, number] | null;
}

/** The die's allowed propellers, in the allowed order; geometry-only ones stay listed
 *  (disabled, "no test data").  Ids the catalogue does not know are dropped. */
export function allowedPropellers(c: CoolingInfo | null | undefined, list: PropSummary[]): PropSummary[] {
  const ids = c?.propellers ?? [];
  const byId = new Map(list.map((p) => [p.id, p]));
  return ids.map((i) => byId.get(i)).filter((p): p is PropSummary => !!p);
}

/** "FPV 10*5 (10X5X3)" -> "FPV 10×5": the catalogue keeps the vendor's ASCII spelling and its
 *  product-page aliases in brackets; the picker prints the model only. */
export const modelLabel = (model: string) =>
  String(model).replace(/\s*\(.*$/, '').replace(/\s*\*\s*/g, '×').trim();

/** "T-MOTOR" -> "T-Motor" (the catalogue stores the vendor in capitals). */
export const vendorLabel = (vendor: string) =>
  String(vendor).split(/([-\s])/).map((w) => (w.length > 3 && w === w.toUpperCase() ? w[0] + w.slice(1).toLowerCase() : w)).join('');

/** The default pick: the first allowed propeller with a measured torque, else the first usable. */
export function defaultPropeller(list: PropSummary[]): string | null {
  return (list.find((p) => p.selectable && p.power_data === 'measured_torque')
    ?? list.find((p) => p.selectable))?.id ?? null;
}

// ── the load: propeller torque -> phase current ──────────────────────────────────────────
export type LoadResult =
  | { ok: true; I_A: number }
  | { ok: false; kind: 'torque'; need_Nm: number; have_Nm: number; I_A: number }
  | { ok: false; kind: 'no_prop' };

/**
 * The phase current (rms) at which the motor makes `need_Nm`.  `torqueAt(I)` is the passport's
 * torque at that current (rpm and every other knob fixed by the caller); it is increasing at
 * least up to the torque asked for.  Bisection on [0, iMax].  If even `iMax` falls short the
 * answer is a loud refusal that carries what the motor does give — never a quiet clamp.
 */
export function currentForTorque(need_Nm: number | null, torqueAt: (I: number) => number, iMax: number): LoadResult {
  if (need_Nm == null || !Number.isFinite(need_Nm)) return { ok: false, kind: 'no_prop' };
  if (need_Nm <= 0) return { ok: true, I_A: 0 };
  const top = torqueAt(iMax);
  if (!(top >= need_Nm)) return { ok: false, kind: 'torque', need_Nm, have_Nm: top, I_A: iMax };
  let lo = 0, hi = iMax;
  for (let i = 0; i < 50; i++) {
    const m = 0.5 * (lo + hi);
    if (torqueAt(m) < need_Nm) lo = m; else hi = m;
  }
  return { ok: true, I_A: 0.5 * (lo + hi) };
}

// ── temperatures against their limits ────────────────────────────────────────────────────
export const DEFAULT_MAGNET_LIMIT_C = 150;     // used only when the machine's magnet card names no limit

export interface TempLimits { winding_C: number; magnet_C: number; magnetIsDefault: boolean; windingBasis: string }

/** The context's `thermal_limits`, with the magnet fallback made explicit. */
export function tempLimits(t: { winding_C?: number | null; magnet_C?: number | null; winding_basis?: string } | null | undefined): TempLimits {
  const w = t?.winding_C != null && t.winding_C > 0 ? t.winding_C : 180;
  const m = t?.magnet_C != null && t.magnet_C > 0 ? t.magnet_C : DEFAULT_MAGNET_LIMIT_C;
  return { winding_C: w, magnet_C: m, magnetIsDefault: !(t?.magnet_C != null && t.magnet_C > 0), windingBasis: t?.winding_basis ?? 'class H default' };
}

export interface TempTile {
  /** the number to print, or null for "—" */
  value: number | null;
  /** over the limit: print `> limit` in red, never the (absurd) number */
  over: boolean;
  level?: 'ok' | 'warn' | 'bad';
}

/** A temperature tile: green well below the limit, amber within 20 K of it, red beyond. */
export function tempTile(T: number | null, limit: number): TempTile {
  if (T == null || !Number.isFinite(T)) return { value: null, over: false };
  if (T > limit) return { value: limit, over: true, level: 'bad' };
  return { value: T, over: false, level: T > limit - 20 ? 'warn' : 'ok' };
}

export interface Verdict { windingOver: boolean; magnetOver: boolean; over: boolean }
export function judgeTemps(T_winding_C: number, T_magnet_C: number, lim: TempLimits): Verdict {
  const w = T_winding_C > lim.winding_C, m = T_magnet_C > lim.magnet_C;
  return { windingOver: w, magnetOver: m, over: w || m };
}

// ── thermal zones on a knob ──────────────────────────────────────────────────────────────
/** CSS background for a slider rail from per-sample verdicts (`true` = continuous below the
 *  temperature limits, painted green; `false` = beyond, red; `null` = not judged, neutral). */
export function zoneGradient(ok: (boolean | null)[], green = '#16a34a', red = '#dc2626', neutral = 'rgba(148,163,184,0.35)'): string | null {
  const n = ok.length;
  if (n < 2 || ok.every((x) => x == null)) return null;
  const col = (x: boolean | null) => (x == null ? neutral : x ? green : red);
  const stops: string[] = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * 100, b = ((i + 1) / n) * 100;
    stops.push(`${col(ok[i])} ${a.toFixed(2)}%`, `${col(ok[i])} ${b.toFixed(2)}%`);
  }
  return `linear-gradient(to right, ${stops.join(', ')})`;
}

/** The sample positions of a zone bar over [min, max]: the CENTRE of each of n cells. */
export function zoneSamples(min: number, max: number, n = 48): number[] {
  return Array.from({ length: n }, (_, i) => min + ((i + 0.5) / n) * (max - min));
}

// ── the user's choice, remembered per machine ────────────────────────────────────────────
export const PROP_CHOICE_LS = 'configurator.propeller.v1';
export const DEFAULT_AMBIENT_C = 25;

export interface CoolChoice { propId?: string | null; ambient?: number; load?: 'prop' | 'manual' }

const okAmbient = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= -60 && v <= 80;

/** The remembered propeller / ambient air / load mode of ONE machine ({} when none). */
export function readCoolChoice(raw: string | null, refId: string): CoolChoice {
  try {
    const m = raw ? JSON.parse(raw) : null;
    const c = m && typeof m === 'object' ? (m as Record<string, unknown>)[refId] : null;
    if (!c || typeof c !== 'object') return {};
    const o = c as Record<string, unknown>;
    const out: CoolChoice = {};
    if (typeof o.propId === 'string' && o.propId) out.propId = o.propId;
    if (okAmbient(o.ambient)) out.ambient = o.ambient;
    if (o.load === 'prop' || o.load === 'manual') out.load = o.load;
    return out;
  } catch { return {}; }
}

export function writeCoolChoice(raw: string | null, refId: string, patch: CoolChoice): string {
  let m: Record<string, CoolChoice> = {};
  try { const j = raw ? JSON.parse(raw) : null; if (j && typeof j === 'object' && !Array.isArray(j)) m = j; } catch { /* start clean */ }
  m[refId] = { ...readCoolChoice(JSON.stringify(m), refId), ...patch };
  return JSON.stringify(m);
}

/** The propeller a CONFIGURATION opens on: its entry in the die's `defaults` if that propeller is
 *  allowed and has data; else the first allowed propeller with torque data (owner 2026-10-05). */
export function defaultPropellerFor(cooling: CoolingInfo | null | undefined, config: string | null | undefined,
                                    allowed: PropSummary[]): string | null {
  const id = config ? cooling?.defaults?.[config] : cooling?.default_propeller;
  const d = id ? allowed.find((p) => p.id === id && p.selectable) : undefined;
  return d ? d.id : defaultPropeller(allowed);
}

/** The propeller actually used: the user's remembered choice if it is still allowed and has data,
 *  else `preferred` (the configuration's default), else the first with torque data.  `null` when the
 *  die allows none. */
export function effectivePropeller(choice: CoolChoice, allowed: PropSummary[], preferred?: string | null): string | null {
  const c = choice.propId ? allowed.find((p) => p.id === choice.propId && p.selectable) : undefined;
  if (c) return c.id;
  const d = preferred ? allowed.find((p) => p.id === preferred && p.selectable) : undefined;
  return d ? d.id : defaultPropeller(allowed);
}
