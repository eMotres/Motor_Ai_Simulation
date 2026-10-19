// The propeller catalogue as the server serves it (src/motor_ai_sim/routes/propellers.py):
// read-only, ungated.  Configure asks for the list once and for one `/series` per
// (propeller, ambient, housing) — the propeller physics lives only in the backend.
import { SERIES_N, SERIES_RPM_MAX, type CoolingInfo, type PropSeries, type PropSummary } from './configuratorPropeller';
import { s1CacheKey, type S1Result } from './configuratorPropellerS1';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

let _list: Promise<PropSummary[] | null> | null = null;

/** Every catalogue entry; cached for the page's life, and a failed fetch is retried next time. */
export function fetchPropellers(): Promise<PropSummary[] | null> {
  if (_list) return _list;
  _list = fetch(`${API}/api/propellers`, { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => (j && Array.isArray(j.propellers) ? (j.propellers as PropSummary[]) : null))
    .catch(() => null)
    .then((v) => { if (v == null) _list = null; return v; });
  return _list;
}

/** The same per-die/config allowlist Configure reads from configure_context. */
export async function fetchPropellerCoolingOptions(die: string, config: string): Promise<CoolingInfo | null> {
  const q = new URLSearchParams({ die, config });
  try {
    const r = await fetch(`${API}/api/propellers/cooling-options?${q}`, { cache: 'no-store' });
    return r.ok ? await r.json() as CoolingInfo : null;
  } catch { return null; }
}

export interface PropellerThermalPoint {
  propeller_id: string;
  rpm: number;
  air_speed_ms: number;
  slipstream_position: string;
  rho_kg_m3?: number;
}

/** One backend-computed slipstream point, matching Thermal's own RPM and ambient. */
export async function fetchPropellerThermalPoint(id: string, rpm: number,
                                                 ambientC: number): Promise<PropellerThermalPoint | null> {
  const q = new URLSearchParams({ rpm: String(rpm), temp_c: String(ambientC) });
  try {
    const r = await fetch(`${API}/api/propellers/${encodeURIComponent(id)}/point?${q}`, { cache: 'no-store' });
    if (!r.ok) return null;
    const point = await r.json() as PropellerThermalPoint;
    return point && point.propeller_id === id && point.rpm === rpm
      && Number.isFinite(point.air_speed_ms) && point.air_speed_ms >= 0
      ? point : null;
  } catch { return null; }
}

const _s1 = new Map<string, Promise<S1Result | null>>();

/** The continuous (S1) limit of one die/configuration with this propeller (signed-in; the
 *  global fetch adds the token), for the selected drive (`sine` or a computed PWM variant id — the
 *  board build is its `_x2` / `_x2_board2` estimate variant) and the
 *  pack's voltage window (null = the passport's own pack).  Cached per request; a failure is not. */
export function fetchPropellerS1(id: string, die: string, config: string, ambientC: number,
                                 drive = 'sine', pack: { min: number; max: number } | null = null,
                                 escBusA: number | null = null
): Promise<S1Result | null> {
  const key = s1CacheKey(id, die, config, ambientC, drive, pack, escBusA);
  const hit = _s1.get(key);
  if (hit) return hit;
  const q = new URLSearchParams({ die, config, ambient_c: String(ambientC), drive });
  if (pack) { q.set('pack_v_min', String(pack.min)); q.set('pack_v_max', String(pack.max)); }
  // an external ESC: its continuous bus current (none = the model's bench ESC)
  if (escBusA != null) q.set('esc_i_bus_max_A', String(escBusA));
  // the board's cooling class is decided by the server from the machine: never sent
  const p = fetch(`${API}/api/propellers/${encodeURIComponent(id)}/s1?${q}`, { cache: 'no-store' })
    .then((r) => (r.ok ? (r.json() as Promise<S1Result>) : null))
    .catch(() => null)
    .then((v) => { if (v == null) _s1.delete(key); return v; });
  _s1.set(key, p);
  return p;
}

const _series = new Map<string, Promise<PropSeries | null>>();

/** One propeller's answer on the rpm grid, with the housing film coefficient of every sample. */
export function fetchSeries(id: string, ambientC: number, housingMm: number): Promise<PropSeries | null> {
  const key = seriesCacheKey(id, ambientC, housingMm);
  const hit = _series.get(key);
  if (hit) return hit;
  const q = new URLSearchParams({
    rpm_max: String(SERIES_RPM_MAX), n: String(SERIES_N),
    temp_c: String(ambientC), housing_d_mm: String(housingMm),
  });
  const p = fetch(`${API}/api/propellers/${encodeURIComponent(id)}/series?${q}`, { cache: 'no-store' })
    .then((r) => (r.ok ? (r.json() as Promise<PropSeries>) : null))
    .catch(() => null)
    .then((v) => { if (v == null) _series.delete(key); return v; });
  _series.set(key, p);
  return p;
}

/** The cache key of one `/series` request (propeller, ambient, housing diameter). */
export const seriesCacheKey = (id: string, ambientC: number, housingMm: number): string =>
  `${id}|${ambientC.toFixed(1)}|${housingMm.toFixed(1)}`;

/** Put answers the server already gave (the one-call load below) into the request caches, so the
 *  page's own request for the same thing is answered without a second trip. */
export function primeSeries(id: string, ambientC: number, housingMm: number, s: PropSeries): void {
  _series.set(seriesCacheKey(id, ambientC, housingMm), Promise.resolve(s));
}
export function primeS1(id: string, die: string, config: string, ambientC: number, drive: string,
                        pack: { min: number; max: number } | null, r: S1Result): void {
  _s1.set(s1CacheKey(id, die, config, ambientC, drive, pack, null), Promise.resolve(r));
}

/** ONE Configure load as the server resolves it (GET /api/catalog/{id}/configure_load,
 *  motor_ai_sim.configure_load): the context, the configuration + duty the load lands on and, for a
 *  propeller-cooled machine, the propeller it opens on with its series and its S1 limit for the
 *  preset's drive and pack - one answer, so the page renders the loaded point once. */
export interface ConfigureLoadAnswer {
  motor_id: string;
  context: import('./configureContextApi').ConfigureContext;
  config: string;
  duty: string | null;
  propeller: null | {
    id: string; ambient_c: number; housing_d_mm: number | null;
    die: string | null; config: string; drive: string; pack: { min: number; max: number } | null;
    series: PropSeries | null; series_error: string | null;
    s1: S1Result | null; s1_error: string | null;
  };
}

export async function fetchConfigureLoad(motorId: string, q: {
  config: string; duty: string | null; ambientC: number; housingMm: number | null;
}): Promise<ConfigureLoadAnswer | null> {
  const p = new URLSearchParams({ config: q.config, ambient_c: String(q.ambientC),
    series_rpm_max: String(SERIES_RPM_MAX), series_n: String(SERIES_N) });
  if (q.duty) p.set('duty', q.duty);
  if (q.housingMm != null && q.housingMm > 0) p.set('housing_d_mm', String(q.housingMm));
  try {
    const r = await fetch(`${API}/api/catalog/${encodeURIComponent(motorId)}/configure_load?${p}`,
      { cache: 'no-store' });
    return r.ok ? ((await r.json()) as ConfigureLoadAnswer) : null;
  } catch { return null; }
}
