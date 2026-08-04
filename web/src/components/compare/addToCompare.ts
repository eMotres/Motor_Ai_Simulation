/**
 * "Add to Compare" — snapshot the CURRENT design (geometry + mesh + operating
 * point) together with the FEM summary on screen, and store it SERVER-side via
 * /api/sims/saved (config/saved_simulations.json).  Comparison points therefore
 * survive a reload, a different browser and a different machine — localStorage
 * would not.  ComparePanel renders these rows.
 */
import { readMeshSettings, readSimSettings } from '../common/motorSettings';
import { liveGeoSig } from '../common/geoSig';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

/**
 * Flat params, keyed the way ComparePanel's PARAM_META / column-picker expects.
 * readSimSettings() speaks the PRESET vocabulary (max_current, phase_offset_deg),
 * so the operating point is re-keyed here; geometry comes straight from the config
 * so every dimension the user might compare on is captured, not a hand-picked few.
 */
async function buildParams(): Promise<Record<string, unknown>> {
  const sim  = readSimSettings()  as Record<string, unknown>;
  const mesh = readMeshSettings() as Record<string, unknown>;
  const geometry: Record<string, number> = {};
  try {
    const cfg = await (await fetch(`${API}/api/config`)).json();
    for (const [k, v] of Object.entries(cfg?.geometry ?? {})) {
      if (typeof v === 'number') geometry[k] = v;
    }
  } catch { /* best-effort: the operating point alone is still comparable */ }
  // The operating point must be the one the RESULTS were solved at, not whatever
  // the panel is set to now — the two drift apart as soon as the user edits I or γ
  // without re-running (the summary card flags exactly that as "stale"), and a
  // comparison row pairing one design's inputs with another's outputs is a lie.
  const s = readShownSummary() as Record<string, unknown>;
  const solved = (v: unknown, fallback: unknown) => (Number.isFinite(Number(v)) ? Number(v) : fallback);
  // MATERIALS are part of the design, not decoration: the same geometry with a
  // different magnet or steel is a different machine and a different row. They
  // were missing from the snapshot, so a stored point could be applied back as
  // geometry only and silently picked up whatever materials happened to be
  // assigned at the time — which is how a Fe16N2 result gets re-checked against
  // NdFeB without a word. Prefixed so they never collide with a geometry key.
  const materials: Record<string, string> = {};
  try {
    const cfg = await (await fetch(`${API}/api/config`)).json();
    for (const [part, name] of Object.entries(cfg?.materials ?? {})) {
      if (typeof name === 'string' && name) materials[`mat_${part}`] = name;
    }
  } catch { /* best-effort — a point without materials is still comparable */ }
  return {
    ...materials,
    // The machine stamp rides WITH the point, exactly as the geometry fields do
    // — one string that says "this row's numbers were solved on this motor", so
    // a stored point can be checked instead of assumed.  `geo_sig` is the
    // geometry captured above; `geo_sig_solved` is the stamp of the RUN whose
    // results this row carries.  They differ when the user edited the machine
    // and did not re-run, which is precisely the row that must not be believed.
    // Both read in the STORE's dialect (`liveGeoSig` / the run's own stamp), so
    // the two are comparable with `===`; the flat geometry fields below stay in
    // the config's dialect because they are display columns, not identity.
    geo_sig:        liveGeoSig(),
    geo_sig_solved: (s._geoSig as string) ?? '',
    // operating point
    I_phase_rms:        solved(s.I_phase_rms_A, sim.max_current),
    gamma_deg:          solved(s.gamma_deg,     sim.phase_offset_deg),
    rpm:                solved(s.rpm,           sim.rpm),
    frequency_hz:       sim.frequency,
    coil_temp_c:        solved(s.coil_temp_C,   sim.coil_temp_c),
    end_winding_factor: solved(s.end_winding_factor, sim.end_winding_factor),
    connection:         sim.connection,
    steps_per_period:   sim.steps_per_period,
    // mesh
    n_sectors:    mesh.n_sectors,
    mesh_size_mm: mesh.mesh_size_mm,
    min_size_mm:  mesh.min_size_mm,
    // geometry
    ...geometry,
  };
}

/** The summary the user is actually LOOKING at (PhysicsDashboard persists it). */
function readShownSummary(): Record<string, unknown> {
  try { return JSON.parse(localStorage.getItem('sim.lastSummary') || 'null') || {}; }
  catch { return {}; }
}

/** True when there is a FEM result on screen worth snapshotting. */
export function hasSummaryToSave(): boolean {
  const s = readShownSummary();
  return Number.isFinite(Number((s as any).T_em_avg_Nm));
}

/** Default label: the MOTOR's name and when it was saved — nothing else (user
 *  request, 2026-08-04).  The current and gamma are columns of the row already,
 *  so repeating them in the name only crowded it.  Renameable in Compare. */
export function defaultPointName(): string {
  const when = new Date().toLocaleString(undefined,
    { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  let motor = '';
  try {
    const raw = localStorage.getItem('motor.active');
    const m = raw ? JSON.parse(raw) as { name?: string } : null;
    motor = String(m?.name ?? '').trim();
  } catch { /* no active motor — the timestamp alone still separates points */ }
  return motor ? `${motor} · ${when}` : when;
}

export async function addCurrentPointToCompare(name: string): Promise<void> {
  const params  = await buildParams();
  const results = readShownSummary();
  // A comparison row pairing one machine's geometry with another machine's
  // results is not a data-quality nit — it is the whole point of the table
  // being wrong, permanently, in a store that outlives the session.  Refuse.
  const solved = String(params.geo_sig_solved || '');
  const live   = String(params.geo_sig || '');
  if (solved && live && solved !== live) {
    throw new Error(
      'These results were solved on a DIFFERENT machine than the one loaded — '
      + 'the point would pair this geometry with another motor\'s numbers. '
      + 'Run Simulation on the current geometry first, then add the point.');
  }
  const r = await fetch(`${API}/api/sims/saved`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, params, results }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 160)}`);
  try { window.dispatchEvent(new CustomEvent('compare-points-changed')); } catch { /* SSR */ }
}
