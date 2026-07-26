/**
 * "Add to Compare" — snapshot the CURRENT design (geometry + mesh + operating
 * point) together with the FEM summary on screen, and store it SERVER-side via
 * /api/sims/saved (config/saved_simulations.json).  Comparison points therefore
 * survive a reload, a different browser and a different machine — localStorage
 * would not.  ComparePanel renders these rows.
 */
import { readMeshSettings, readSimSettings } from '../common/motorSettings';

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
  return {
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

/** Default label — what tells two points apart at a glance; renameable in Compare. */
export function defaultPointName(): string {
  const sim = readSimSettings() as Record<string, unknown>;
  const s = readShownSummary() as Record<string, unknown>;
  // Same rule as buildParams: label the point by what was SOLVED, not by the
  // panel's current setting, so the name matches the row's numbers.
  const I = Number(Number.isFinite(Number(s.I_phase_rms_A)) ? s.I_phase_rms_A : sim.max_current);
  const g = Number(Number.isFinite(Number(s.gamma_deg))     ? s.gamma_deg     : sim.phase_offset_deg);
  const when = new Date().toLocaleString(undefined,
    { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  return [
    Number.isFinite(I) ? `${I} A` : null,
    Number.isFinite(g) ? `γ ${g}°` : null,
    when,
  ].filter(Boolean).join(' · ');
}

export async function addCurrentPointToCompare(name: string): Promise<void> {
  const params  = await buildParams();
  const results = readShownSummary();
  const r = await fetch(`${API}/api/sims/saved`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, params, results }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 160)}`);
  try { window.dispatchEvent(new CustomEvent('compare-points-changed')); } catch { /* SSR */ }
}
