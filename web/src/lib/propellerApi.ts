// The propeller catalogue as the server serves it (src/motor_ai_sim/routes/propellers.py):
// read-only, ungated.  Configure asks for the list once and for one `/series` per
// (propeller, ambient, housing) — the propeller physics lives only in the backend.
import { SERIES_N, SERIES_RPM_MAX, type CoolingInfo, type PropSeries, type PropSummary } from './configuratorPropeller';

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

const _series = new Map<string, Promise<PropSeries | null>>();

/** One propeller's answer on the rpm grid, with the housing film coefficient of every sample. */
export function fetchSeries(id: string, ambientC: number, housingMm: number): Promise<PropSeries | null> {
  const key = `${id}|${ambientC.toFixed(1)}|${housingMm.toFixed(1)}`;
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
