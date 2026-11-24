/**
 * The two mesh / solve presets, "Optimization" and "Simulation" (owner 2026-11-17).
 *
 * The numbers come from the server (`GET /api/mesh/presets`, resolved for the machine that is open, from
 * config/mesh_presets.yaml); nothing is stated here.  Choosing a preset FILLS the same stored fields the Mesh tab and the
 * Simulation tab already edit by hand (mesh.* and sim.stepsPP / sim.demag), then tells the mounted panels to re-read them.
 * Every manual setting stays editable; when the stored fields no longer equal a preset's values the selector reads "custom".
 */
import { canWriteServer } from './localAuth';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export type PresetName = 'optimization' | 'simulation';
export type PresetChoice = PresetName | 'custom';

export interface ResolvedPreset {
  name: PresetName;
  label: string;
  error?: string;
  values?: {
    n_steps_per_period: number; gap_layers: number; mesh_size_mm: number; min_size_mm: number;
    outer_air_factor: number; n_sectors: number; demag: boolean; rotor_eddy: boolean; eddy?: boolean;
    component_mesh: Record<string, number>;
  };
  cogging_steps_per_period?: number;
  sources?: Record<string, string>;
  h0?: { mm: number; limited_by: string };
}

export interface PresetSet { version: number; presets: ResolvedPreset[] }

/** What the panels hold right now, by the request names. */
export interface CurrentSettings {
  steps?: number; gap_layers?: number; mesh_size_mm?: number; min_size_mm?: number;
  outer_air_factor?: number; n_sectors?: number; demag?: boolean; rotor_eddy?: boolean;
  component_mesh: Record<string, number>;
}

const REL = 1e-3;
const close = (a: number | undefined, b: number): boolean =>
  a !== undefined && Number.isFinite(a) && Math.abs(a - b) <= REL * Math.max(1, Math.abs(b));

/** The wire cell factor 1 is stored as "no key" (the canonical mesh). */
const wireCell = (m: Record<string, number>): number => (typeof m.coil_rel === 'number' && m.coil_rel > 0 ? m.coil_rel : 1);

/**
 * Which preset the current settings equal, or 'custom'.  The Simulation preset reads "96 or more" (owner): a step count at
 * or above its value still matches; the Optimization preset matches exactly (48).
 */
export function matchPreset(cur: CurrentSettings, set: PresetSet | null): PresetChoice {
  if (!set) return 'custom';
  for (const p of set.presets) {
    const v = p.values;
    if (!v) continue;
    const stepsOk = p.name === 'simulation'
      ? cur.steps !== undefined && cur.steps >= v.n_steps_per_period - 1e-9
      : close(cur.steps, v.n_steps_per_period);
    if (!stepsOk) continue;
    if (!close(cur.gap_layers, v.gap_layers) || !close(cur.mesh_size_mm, v.mesh_size_mm)
        || !close(cur.min_size_mm, v.min_size_mm) || !close(cur.outer_air_factor, v.outer_air_factor)
        || cur.n_sectors !== v.n_sectors) continue;
    if (cur.demag !== undefined && cur.demag !== v.demag) continue;
    if (cur.rotor_eddy !== undefined && cur.rotor_eddy !== v.rotor_eddy) continue;
    const pm = v.component_mesh;
    const keys = ['stator', 'rotor', 'magnet', 'outer'] as const;
    if (!keys.every(k => close(cur.component_mesh[k], pm[k]))) continue;
    if (!close(wireCell(cur.component_mesh), wireCell(pm))) continue;
    return p.name;
  }
  return 'custom';
}

function num(key: string): number | undefined {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return undefined;
    const v = Number(JSON.parse(raw));
    return Number.isFinite(v) ? v : undefined;
  } catch { return undefined; }
}

/** The stored settings of this browser (the fields the panels persist). */
export function readCurrentSettings(): CurrentSettings {
  let comp: Record<string, number> = {};
  try {
    const raw = localStorage.getItem('mesh.componentMesh');
    const o = raw ? JSON.parse(raw) : {};
    if (o && typeof o === 'object') comp = o as Record<string, number>;
  } catch { /* unreadable: no per-part sizes */ }
  let demag: boolean | undefined;
  try {
    const raw = localStorage.getItem('sim.demag');
    if (raw != null) demag = Boolean(JSON.parse(raw));
  } catch { /* keep undefined */ }
  let rotor_eddy: boolean | undefined;
  try {
    const raw = localStorage.getItem('sim.fieldLosses');
    if (raw != null) rotor_eddy = Boolean(JSON.parse(raw));
  } catch { /* keep undefined */ }
  return {
    rotor_eddy,
    steps: num('sim.stepsPP'), gap_layers: num('mesh.gapLayers'), mesh_size_mm: num('mesh.meshSize'),
    min_size_mm: num('mesh.minSize'), outer_air_factor: num('mesh.outerAir'), n_sectors: num('mesh.nSectors'),
    demag, component_mesh: comp,
  };
}

export interface PresetDiff { key: string; preset: number | boolean; current: number | boolean | undefined }

/** What differs between the stored settings (the ones the next run is built from) and a preset: the list behind "custom". */
export function presetDiff(cur: CurrentSettings, p: ResolvedPreset | undefined): PresetDiff[] {
  const v = p?.values;
  if (!v) return [];
  const out: PresetDiff[] = [];
  const num = (key: string, a: number | undefined, b: number, ge = false) => {
    if (ge ? !(a !== undefined && a >= b - 1e-9) : !close(a, b)) out.push({ key, preset: b, current: a });
  };
  num('steps_per_period', cur.steps, v.n_steps_per_period, p?.name === 'simulation');
  num('gap_layers', cur.gap_layers, v.gap_layers);
  num('mesh_size_mm', cur.mesh_size_mm, v.mesh_size_mm);
  num('min_size_mm', cur.min_size_mm, v.min_size_mm);
  if (cur.n_sectors !== v.n_sectors) out.push({ key: 'n_sectors', preset: v.n_sectors, current: cur.n_sectors });
  if (cur.demag !== undefined && cur.demag !== v.demag) out.push({ key: 'demag', preset: v.demag, current: cur.demag });
  if (cur.rotor_eddy !== undefined && cur.rotor_eddy !== v.rotor_eddy) out.push({ key: 'rotor_eddy', preset: v.rotor_eddy, current: cur.rotor_eddy });
  return out;
}

export async function fetchMeshPresets(): Promise<PresetSet> {
  const r = await fetch(`${API}/api/mesh/presets`, { cache: 'no-store' });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { const j = await r.json(); if (typeof j?.detail === 'string') detail = j.detail; } catch { /* no body */ }
    throw new Error(detail);
  }
  return (await r.json()) as PresetSet;
}

/** The step count a preset asks for: the cogging run has its own number. */
export function stepsOf(p: ResolvedPreset, cogging = false): number | undefined {
  if (!p.values) return undefined;
  return cogging && p.cogging_steps_per_period ? p.cogging_steps_per_period : p.values.n_steps_per_period;
}

/**
 * Put a preset's values into the stored fields and tell the mounted panels to re-read them.  Returns false when the preset
 * could not be resolved for this machine (nothing is written).  The shared server mesh block is written only for a writer
 * of the shared config, exactly like a manual change on the Mesh tab.
 */
export async function applyPreset(p: ResolvedPreset, cogging = false): Promise<boolean> {
  const v = p.values;
  if (!v) return false;
  const put = (k: string, val: unknown) => { try { localStorage.setItem(k, JSON.stringify(val)); } catch { /* quota */ } };
  put('mesh.meshSize', v.mesh_size_mm); put('mesh.minSize', v.min_size_mm);
  put('mesh.outerAir', v.outer_air_factor); put('mesh.gapLayers', v.gap_layers);
  put('mesh.nSectors', v.n_sectors);
  const comp: Record<string, number> = {};
  for (const k of ['stator', 'rotor', 'magnet', 'outer'] as const) if (v.component_mesh[k] > 0) comp[k] = v.component_mesh[k];
  if (wireCell(v.component_mesh) !== 1) comp.coil_rel = wireCell(v.component_mesh);   // 1 = "no key"
  put('mesh.componentMesh', comp);
  put('sim.stepsPP', stepsOf(p, cogging)); put('sim.stepsSource', 'user');
  put('sim.demag', v.demag); put('sim.fieldLosses', v.rotor_eddy);
  if (canWriteServer()) {
    try {
      await fetch(`${API}/api/mesh/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mesh_size_mm: v.mesh_size_mm, min_size_mm: v.min_size_mm, outer_air_factor: v.outer_air_factor,
                               gap_layers: v.gap_layers, n_sectors: v.n_sectors }),
      });
    } catch { /* the panel is right already; the server block follows on the next manual save */ }
  }
  window.dispatchEvent(new CustomEvent('sim-settings-restored'));
  window.dispatchEvent(new CustomEvent('mesh-preset-applied', { detail: { name: p.name } }));
  return true;
}
