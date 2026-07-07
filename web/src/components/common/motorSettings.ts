// The loaded motor is "my copy": always present, and every mesh / sim /
// geometry edit auto-saves into it.  A motor is a backend preset
// (config/motor_presets.json) carrying geometry + mesh + simulation.  The web
// UI keeps most mesh/sim params in localStorage (mesh.* / sim.*) — including
// ones config.yaml never holds (per-part sizes, steps, coil temp, demag).
// These helpers read/write those blocks, track the active motor, ensure one
// always exists, auto-sync edits, and fork/open motors.

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

// [localStorage suffix, preset key].  localStorage values are JSON-encoded.
const MESH_MAP: [string, string][] = [
  ['meshSize', 'mesh_size_mm'], ['minSize', 'min_size_mm'],
  ['normalDev', 'normal_deviation'], ['outerAir', 'outer_air_factor'],
  ['gapLayers', 'gap_layers'], ['nSectors', 'n_sectors'],
  ['componentMesh', 'component_mesh'],
];
const SIM_MAP: [string, string][] = [
  ['current', 'max_current'], ['frequency', 'frequency'], ['rpm', 'rpm'],
  ['gamma', 'phase_offset_deg'], ['stepsPP', 'steps_per_period'],
  ['coilTemp', 'coil_temp_c'], ['endWinding', 'end_winding_factor'],
  ['demag', 'demag'], ['connection', 'connection'],
];

function lsGet(ns: string, key: string): unknown {
  try {
    const raw = localStorage.getItem(`${ns}.${key}`);
    return raw == null ? undefined : JSON.parse(raw);
  } catch { return undefined; }
}
function lsSet(ns: string, key: string, v: unknown): void {
  try { localStorage.setItem(`${ns}.${key}`, JSON.stringify(v)); } catch { /* quota */ }
}
function readBlock(ns: string, map: [string, string][]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [lsKey, pKey] of map) { const v = lsGet(ns, lsKey); if (v !== undefined) out[pKey] = v; }
  return out;
}
function applyBlock(ns: string, map: [string, string][], block?: Record<string, unknown> | null): void {
  if (!block) return;
  for (const [lsKey, pKey] of map) if (block[pKey] !== undefined) lsSet(ns, lsKey, block[pKey]);
}

export const readMeshSettings = (): Record<string, unknown> => readBlock('mesh', MESH_MAP);
export const readSimSettings  = (): Record<string, unknown> => readBlock('sim', SIM_MAP);

/** The current Simulation summary the user SEES (persisted by PhysicsDashboard to
 *  `sim.lastSummary`), mapped to the backend's card-metric keys.  Sent on save so
 *  a motor's card shows those exact numbers — not a stale config/.last_transient.
 *  Undefined → backend falls back to the last FEM run (previous behaviour). */
export function readSimMetrics(): Record<string, number> | undefined {
  try {
    const s = JSON.parse(localStorage.getItem('sim.lastSummary') || 'null');
    if (!s || typeof s !== 'object') return undefined;
    const m: Record<string, number> = {};
    const set = (k: string, v: unknown) => { const n = Number(v); if (Number.isFinite(n)) m[k] = n; };
    set('T_avg_Nm', s.T_em_avg_Nm); set('efficiency', s.efficiency);
    set('T_ripple_pct', s.T_ripple_pct); set('V_peak', s.V_phase_peak_V);
    // Card voltage — LINE-TO-LINE peak of the actual waveforms (DC-bus sizing);
    // the phase peak stays in V_peak for reference but the card ignores it.
    set('V_line_peak_V', s.V_line_peak_V);
    set('P_mech_W', s.P_mech_W);
    set('mass_total_kg', s.mass_total_kg);
    return Object.keys(m).length ? m : undefined;
  } catch { return undefined; }
}

export interface ActiveMotor { id: string; name: string; }

export function getActiveMotor(): ActiveMotor | null {
  try { const raw = localStorage.getItem('motor.active'); return raw ? JSON.parse(raw) as ActiveMotor : null; }
  catch { return null; }
}
export function setActiveMotor(m: ActiveMotor | null): void {
  try {
    if (m) localStorage.setItem('motor.active', JSON.stringify(m));
    else localStorage.removeItem('motor.active');
  } catch { /* ignore */ }
  try { window.dispatchEvent(new CustomEvent('motor:active-changed', { detail: m })); } catch { /* ignore */ }
}

/** Seed the browser-side mesh + sim from a loaded preset, and mark it active.
 *  Call right BEFORE window.location.reload() in a Load handler. */
export function seedSettingsFromPreset(preset: any, name?: string): void {
  if (!preset) return;
  applyBlock('mesh', MESH_MAP, preset.mesh);
  applyBlock('sim', SIM_MAP, preset.simulation);
  if (preset.id) setActiveMotor({ id: preset.id, name: name ?? preset.name ?? preset.id });
}

/** Make sure there's always a working motor.  A brand-new user (no active
 *  motor) gets a "My motor" working copy created from the current state. */
export async function ensureActiveMotor(): Promise<ActiveMotor | null> {
  const cur = getActiveMotor();
  if (cur) return cur;
  try {
    const r = await fetch(`${API}/api/presets`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: 'my_motor', name: 'My motor',
        mesh: readMeshSettings(), simulation: readSimSettings() }),
    });
    if (!r.ok) return null;
    const d = await r.json();
    const m = { id: d.id ?? 'my_motor', name: 'My motor' };
    setActiveMotor(m);
    return m;
  } catch { return null; }
}

/** Auto-save the current geometry + mesh + simulation into the active motor
 *  (debounced).  Called on every mesh / sim / geometry edit. */
let _syncTimer: ReturnType<typeof setTimeout> | null = null;
export function syncActiveMotor(): void {
  const active = getActiveMotor();
  if (!active) return;
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(async () => {
    try {
      const cfg = await fetch(`${API}/api/config`).then(r => r.json()).catch(() => null);
      const geometry: Record<string, number> = {};
      if (cfg?.geometry) for (const [k, v] of Object.entries(cfg.geometry)) if (typeof v === 'number') geometry[k] = v;
      await fetch(`${API}/api/presets/${encodeURIComponent(active.id)}/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ geometry, mesh: readMeshSettings(), simulation: readSimSettings() }),
      });
    } catch { /* ignore — config.yaml + localStorage still hold the live state */ }
  }, 900);
}

/** Open a motor as the user's editable copy (forks a template), seed the
 *  browser settings + mark active.  Caller reloads after. */
export async function openMotor(presetId: string, name: string): Promise<boolean> {
  try {
    const res = await fetch(`${API}/api/presets/${encodeURIComponent(presetId)}/open`,
      { method: 'POST' }).then(r => r.json()).catch(() => null);
    if (res?.preset) { seedSettingsFromPreset(res.preset, name); return true; }
  } catch { /* ignore */ }
  return false;
}

/** "Save as new motor": snapshot the current geometry + mesh + simulation as a
 *  NEW named motor (adds a Motors-tab card), and switch to it. */
export async function createMotorFromCurrent(name: string): Promise<ActiveMotor> {
  const r = await fetch(`${API}/api/presets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, mesh: readMeshSettings(), simulation: readSimSettings(),
      metrics: readSimMetrics() }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
  const d = await r.json();
  const m = { id: d.id, name };
  setActiveMotor(m);
  return m;
}

/** "Overwrite this motor": save the current geometry + mesh + simulation back into
 *  the ACTIVE motor's preset (same id) and refresh its Motors-tab card. Unlike
 *  "Save as new", this does NOT fork — it republishes the motor you're editing. */
export async function overwriteActiveMotor(): Promise<ActiveMotor> {
  const active = getActiveMotor();
  if (!active) throw new Error('No active motor to overwrite');
  const r = await fetch(`${API}/api/presets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: active.id, name: active.name,
      mesh: readMeshSettings(), simulation: readSimSettings(), metrics: readSimMetrics() }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
  return active;
}
