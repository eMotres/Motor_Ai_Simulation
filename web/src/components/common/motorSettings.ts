// Bundle the working MESH + SIMULATION settings with the loaded motor.
//
// A "motor" is a backend preset (config/motor_presets.json) that now carries
// geometry + mesh + simulation blocks.  The web UI keeps most mesh/sim params
// in localStorage (mesh.* / sim.*) — including ones config.yaml never holds
// (per-part sizes, steps, coil temp, demag).  These helpers:
//   • read those localStorage blocks into a preset-shaped dict (to SAVE),
//   • write a preset's blocks back into localStorage (to LOAD),
//   • remember which motor is currently loaded (motor.active).

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

// [localStorage suffix, preset key].  localStorage values are JSON-encoded
// (the panels persist via JSON.stringify), so we parse/stringify here too.
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
  for (const [lsKey, pKey] of map) {
    const v = lsGet(ns, lsKey);
    if (v !== undefined) out[pKey] = v;
  }
  return out;
}
function applyBlock(ns: string, map: [string, string][], block?: Record<string, unknown> | null): void {
  if (!block) return;
  for (const [lsKey, pKey] of map) {
    if (block[pKey] !== undefined) lsSet(ns, lsKey, block[pKey]);
  }
}

export const readMeshSettings = (): Record<string, unknown> => readBlock('mesh', MESH_MAP);
export const readSimSettings  = (): Record<string, unknown> => readBlock('sim', SIM_MAP);

export interface ActiveMotor { id: string; name: string; }

export function getActiveMotor(): ActiveMotor | null {
  try {
    const raw = localStorage.getItem('motor.active');
    return raw ? (JSON.parse(raw) as ActiveMotor) : null;
  } catch { return null; }
}
export function setActiveMotor(m: ActiveMotor | null): void {
  try {
    if (m) localStorage.setItem('motor.active', JSON.stringify(m));
    else localStorage.removeItem('motor.active');
  } catch { /* ignore */ }
  // Let every SaveToMotorButton (across tabs/panels) update live.
  try { window.dispatchEvent(new CustomEvent('motor:active-changed', { detail: m })); } catch { /* ignore */ }
}

/** Seed the browser-side mesh + sim settings from a loaded preset, and mark it
 *  active.  Call this right BEFORE window.location.reload() in a Load handler. */
export function seedSettingsFromPreset(preset: any, name?: string): void {
  if (!preset) return;
  applyBlock('mesh', MESH_MAP, preset.mesh);
  applyBlock('sim', SIM_MAP, preset.simulation);
  const id = preset.id;
  if (id) setActiveMotor({ id, name: name ?? preset.name ?? id });
}

/** Save the current mesh and/or simulation settings onto the active motor.
 *  Returns the motor name on success; throws if no motor is loaded. */
export async function saveSettingsToActiveMotor(
  kind: 'mesh' | 'simulation',
): Promise<string> {
  const active = getActiveMotor();
  if (!active) throw new Error('NO_ACTIVE_MOTOR');
  const body = kind === 'mesh'
    ? { mesh: readMeshSettings() }
    : { simulation: readSimSettings() };
  const r = await fetch(`${API}/api/presets/${encodeURIComponent(active.id)}/settings`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    if (r.status === 404) { setActiveMotor(null); throw new Error('MOTOR_GONE'); }
    throw new Error(`HTTP ${r.status}: ${await r.text()}`);
  }
  return active.name;
}

/** Create a NEW motor from the current geometry + full mesh + simulation. */
export async function createMotorFromCurrent(name: string): Promise<ActiveMotor> {
  const r = await fetch(`${API}/api/presets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, mesh: readMeshSettings(), simulation: readSimSettings() }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
  const d = await r.json();
  const m = { id: d.id, name };
  setActiveMotor(m);
  return m;
}
