// The loaded motor is "my copy": always present, and every mesh / sim /
// geometry edit auto-saves into it.  A motor is a backend preset
// (config/motor_presets.json) carrying geometry + mesh + simulation.  The web
// UI keeps most mesh/sim params in localStorage (mesh.* / sim.*) — including
// ones config.yaml never holds (per-part sizes, steps, coil temp, demag).
// These helpers read/write those blocks, track the active motor, ensure one
// always exists, auto-sync edits, and fork/open motors.

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

import { geoSignature, liveGeoSig } from './geoSig';

// [localStorage suffix, preset key].  localStorage values are JSON-encoded.
const MESH_MAP: [string, string][] = [
  ['meshSize', 'mesh_size_mm'], ['minSize', 'min_size_mm'],
  // 'normalDev' dropped: the transient never accepted normal_deviation, so the
  // knob only ever moved the mesh PREVIEW. Presets no longer carry it.
  ['outerAir', 'outer_air_factor'],
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
    // …but ONLY if those numbers belong to the machine being saved.  This entry
    // outlives its motor: it is written once per run and then sits in
    // localStorage across preset switches and reloads, so "Save as new motor"
    // could stamp a brand-new card with the torque and mass of whatever was
    // solved last — a wrong number wearing the right label, on a card nothing
    // later re-checks.  A stamp mismatch means "not this machine": drop the
    // metrics and let the backend fall back to a deterministic recompute.
    const live = liveGeoSig();
    if (s._geoSig && live && s._geoSig !== live) {
      console.warn('[stale] sim.lastSummary was solved on a different machine — '
        + 'saving this motor WITHOUT metrics rather than with another motor\'s');
      return undefined;
    }
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

export interface ActiveMotor {
  id: string;
  name: string;
  /** True when this motor is loaded as a WORKING COPY: it is someone else's (or
   *  locked), so the editor may drive it, solve it and tune it, but the 0.9 s
   *  auto-save is suspended — the backend would refuse every one of those writes,
   *  and a 403 every 0.9 s is not a UI, it is a stutter.  The first explicit Save
   *  is "Save as new", under your own ownership. */
  readOnly?: boolean;
  /** Who owns it (shown in the read-only chip's tip, so "ask them" has a name). */
  owner?: string;
  /** Admin-set lock: read-only for everyone but an admin, the owner included. */
  locked?: boolean;
}

export function getActiveMotor(): ActiveMotor | null {
  try { const raw = localStorage.getItem('motor.active'); return raw ? JSON.parse(raw) as ActiveMotor : null; }
  catch { return null; }
}
export function setActiveMotor(m: ActiveMotor | null): void {
  try {
    if (m) localStorage.setItem('motor.active', JSON.stringify(m));
    else localStorage.removeItem('motor.active');
  } catch { /* ignore */ }
  resetSyncSignature();   // a different motor: the autosave's memory is void
  try { window.dispatchEvent(new CustomEvent('motor:active-changed', { detail: m })); } catch { /* ignore */ }
}

/** Seed the browser-side mesh + sim from a loaded preset, and mark it active.
 *  Call right BEFORE window.location.reload() in a Load handler.
 *  `access` carries what the backend said about writing this motor (from the
 *  apply/open response) — absent means "writable", the pre-ownership default. */
export function seedSettingsFromPreset(
  preset: any, name?: string,
  access?: { writable?: boolean; owner?: string; locked?: boolean },
): void {
  if (!preset) return;
  applyBlock('mesh', MESH_MAP, preset.mesh);
  applyBlock('sim', SIM_MAP, preset.simulation);
  if (preset.id) setActiveMotor({
    id: preset.id, name: name ?? preset.name ?? preset.id,
    readOnly: access?.writable === false,
    owner: access?.owner, locked: access?.locked,
  });
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
    // 403 = "my_motor" already exists and belongs to someone else (or is
    // locked).  Leaving NO active motor is the honest outcome: nothing
    // auto-saves anywhere, and the Save button offers "Save as new motor…",
    // which creates one under this account's own ownership.
    if (!r.ok) {
      if (r.status === 403) console.warn('[motor] no working motor: ' + (await r.text()));
      return null;
    }
    const d = await r.json();
    const m = { id: d.id ?? 'my_motor', name: 'My motor' };
    setActiveMotor(m);
    return m;
  } catch { return null; }
}

/** Auto-save the current geometry + mesh + simulation into the active motor
 *  (debounced).  Called on every mesh / sim / geometry edit.
 *
 *  WRITES ONLY WHAT CHANGED.  It used to POST unconditionally on every fire —
 *  including the burst of "changes" a preset LOAD produces as each panel
 *  re-seeds itself from the newly applied config.  Those writes carry whatever
 *  the panels happen to hold at that instant, which during a restore is not yet
 *  the loaded motor; that is how a half-restored state got written back into the
 *  preset and resurrected the machine the user had just left.  A payload
 *  identical to the last one we sent is not an edit — it is an echo, and echoes
 *  are exactly the writes that did the damage.  The signature covers geometry,
 *  mesh AND simulation, so a genuine mesh-only or sim-only edit still saves. */
let _syncTimer: ReturnType<typeof setTimeout> | null = null;
let _lastSyncSig: string | null = null;
export function syncActiveMotor(): void {
  const active = getActiveMotor();
  if (!active) return;
  // A WORKING COPY (someone else's motor, or a locked one) never auto-saves.
  // The backend would refuse each write, and retrying every 0.9 s would turn a
  // clear "this isn't yours" into a stream of failures nobody reads.  The
  // editor still works; the Save button offers "Save as new" instead.
  if (active.readOnly) return;
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(async () => {
    try {
      const cfg = await fetch(`${API}/api/config`).then(r => r.json()).catch(() => null);
      const geometry: Record<string, number> = {};
      if (cfg?.geometry) for (const [k, v] of Object.entries(cfg.geometry)) if (typeof v === 'number') geometry[k] = v;
      const mesh = readMeshSettings(), simulation = readSimSettings();
      // The geometry hash the preset gets stamped with — the same construction
      // every other stamp in the app uses, so a consumer can compare a preset's
      // geometry against a result's without translating between two dialects.
      const geo_sig = geoSignature(geometry);
      const sig = JSON.stringify([active.id, geo_sig, mesh, simulation]);
      if (sig === _lastSyncSig) return;          // nothing moved — do not write
      _lastSyncSig = sig;
      const r = await fetch(`${API}/api/presets/${encodeURIComponent(active.id)}/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ geometry, mesh, simulation, geo_sig }),
      });
      // Belt and braces: a motor can become un-writable while it is open (an
      // admin locks it, ownership was never resolved on this path).  The FIRST
      // refusal turns the session into a working copy, so there is no second.
      if (r.status === 403) {
        const detail = await r.json().then((j) => String(j?.detail ?? '')).catch(() => '');
        console.warn('[motor] auto-save suspended — ' + (detail || 'not yours to write'));
        setActiveMotor({ ...active, readOnly: true });
      }
    } catch { /* ignore — config.yaml + localStorage still hold the live state */ }
  }, 900);
}

/** Forget the autosave's "already saved this" memory.  Called when the ACTIVE
 *  motor changes: the next edit then writes even if its payload happens to
 *  match the previous motor's — a different preset is a different file. */
export function resetSyncSignature(): void { _lastSyncSig = null; }

/** Open a motor as the user's editable copy (forks a template), seed the
 *  browser settings + mark active.  Caller reloads after. */
export async function openMotor(presetId: string, name: string): Promise<boolean> {
  try {
    const res = await fetch(`${API}/api/presets/${encodeURIComponent(presetId)}/open`,
      { method: 'POST' }).then(r => r.json()).catch(() => null);
    // The response says which motor you actually got (your own fork, or the
    // original read-only) and whether you may write it — carried into the
    // active-motor record so the editor knows before the first edit.
    if (res?.preset) {
      seedSettingsFromPreset(res.preset, name, {
        writable: res.writable, owner: res.owner, locked: res.locked,
      });
      return true;
    }
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
  if (active.readOnly) {
    throw new Error(active.locked
      ? `"${active.name}" is locked — only an admin can change it. Use "Save as new…".`
      : `"${active.name}" belongs to ${active.owner ?? 'someone else'} — use "Save as new…".`);
  }
  const r = await fetch(`${API}/api/presets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: active.id, name: active.name,
      mesh: readMeshSettings(), simulation: readSimSettings(), metrics: readSimMetrics() }),
  });
  if (!r.ok) {
    // A 403 carries the owner's name — show THAT, not "HTTP 403", and switch
    // the session to a working copy so the auto-save stops trying too.
    const detail = await r.json().then((j) => String(j?.detail ?? '')).catch(() => '');
    if (r.status === 403) setActiveMotor({ ...active, readOnly: true });
    throw new Error(detail || `HTTP ${r.status}`);
  }
  return active;
}
