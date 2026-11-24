/**
 * What the Mesh and Simulation tabs hold in this browser (their fields persist to localStorage on every change), by the names of a
 * run request.  Pure (no `import.meta`, no store), so `node --test` loads it with a stubbed storage.  A field the tabs never
 * wrote is ABSENT: nothing is defaulted here, except the Simulation tab's own constant (field-based rotor eddy is always on).
 */
import { parseMagnetTempC } from './magnetTemp.ts';

/** What the panels hold right now, by the request names. */
export interface CurrentSettings {
  steps?: number; gap_layers?: number; mesh_size_mm?: number; min_size_mm?: number;
  outer_air_factor?: number; n_sectors?: number; demag?: boolean;
  /** The field-based rotor eddy of the Simulation tab (`sim.fieldLosses`; the tab has no toggle: always on, SimulationPanel `fieldLosses = true`). */
  rotor_eddy?: boolean;
  component_mesh: Record<string, number>;
  /** The Simulation tab's MAGNET TEMPERATURE field (`sim.magnetTempC`, text) as a number: absent when blank / not a number = the card as written. */
  magnet_temp_c?: number;
}


type Store = Pick<Storage, 'getItem'>;
function num(key: string, st: Store): number | undefined {
  try {
    const raw = st.getItem(key);
    if (raw == null) return undefined;
    const v = Number(JSON.parse(raw));
    return Number.isFinite(v) ? v : undefined;
  } catch { return undefined; }
}

function ls(): Store {
  try { return localStorage; } catch { return { getItem: () => null }; }
}

/** The stored settings of this browser (the fields the panels persist). */
export function readCurrentSettings(st: Store = ls()): CurrentSettings {
  let comp: Record<string, number> = {};
  try {
    const raw = st.getItem('mesh.componentMesh');
    const o = raw ? JSON.parse(raw) : {};
    if (o && typeof o === 'object') comp = o as Record<string, number>;
  } catch { /* unreadable: no per-part sizes */ }
  let demag: boolean | undefined;
  try {
    const raw = st.getItem('sim.demag');
    if (raw != null) demag = Boolean(JSON.parse(raw));
  } catch { /* keep undefined */ }
  let rotor_eddy = true;     // the Simulation tab's own value: a constant true; a stored value (a duty restore) wins
  try {
    const raw = st.getItem('sim.fieldLosses');
    if (raw != null) rotor_eddy = Boolean(JSON.parse(raw));
  } catch { /* keep the tab's constant */ }
  let magnet_temp_c: number | undefined;
  try {
    const raw = st.getItem('sim.magnetTempC');
    if (raw != null) { const v = parseMagnetTempC(JSON.parse(raw)); if (v != null) magnet_temp_c = v; }
  } catch { /* unreadable: the card as written */ }
  return {
    steps: num('sim.stepsPP', st), gap_layers: num('mesh.gapLayers', st), mesh_size_mm: num('mesh.meshSize', st),
    min_size_mm: num('mesh.minSize', st), outer_air_factor: num('mesh.outerAir', st), n_sectors: num('mesh.nSectors', st),
    demag, rotor_eddy, component_mesh: comp, ...(magnet_temp_c !== undefined ? { magnet_temp_c } : {}),
  };
}

/** Who set the steps on the Simulation tab: stored as JSON (`"eddy_default"`) by the tab, accepted as a plain string too. */
export function readStepsSource(st: Store = ls()): string | null {
  try {
    const raw = st.getItem('sim.stepsSource');
    if (raw == null) return null;
    try { const v = JSON.parse(raw); return typeof v === 'string' ? v : null; } catch { return raw; }
  } catch { return null; }
}
