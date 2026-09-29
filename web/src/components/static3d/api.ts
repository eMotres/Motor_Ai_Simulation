/** Types and fetches for the 3D tab.
 *
 * Everything here mirrors `routes/static3d.py` one-for-one.  The fields that
 * look like decoration — `sector`, `fingerprint`, `solve`, `stale_geometry`,
 * `decimated` — are the ones the panel is obliged to put on screen: a picture
 * of half a machine that does not say it is half a machine is a wrong picture.
 */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API.replace(/\/$/, '')}/api/static3d`;

export interface SectorInfo {
  num_slots: number;
  num_poles: number;
  pole_pairs: number;
  stator_od_mm: number;
  stator_bore_mm: number;
  rotor_od_mm: number;
  air_gap_mm: number;
  mid_gap_r_mm: number;
  stack_mm: number;
  Br_T: number;
  mu_rec: number;
  materials: Record<string, string>;
  sector_deg: number;
  n_sectors: number;
  antiperiodic: boolean;
  topology: string;
}

/** One connected piece of a cross-section: an outer ring and its holes, mm. */
export interface PolyPart {
  outer: number[][];
  holes: number[][][];
}

export interface GeomRegion {
  name: string;
  kind: string;
  material: string | null;
  centroid_mm: number[] | null;
  polarity: number | null;
  parts: PolyPart[];
  area_mm2: number;
  mu_r: number;
  stack_kf: number;
  nonlinear: boolean;
  M_A_per_m: number[];
  M_dir_deg: number | null;
  M_mag_A_per_m: number;
}

export interface GeometryPayload {
  units: string;
  preset: string;
  fingerprint: string;
  materials_requested: Record<string, string>;
  materials_mode: string;
  what: string;
  regions: GeomRegion[];
  coils: {
    sides: { index: number; parts: PolyPart[] }[];
    n_sides_sector: number;
    n_sides_full_ring: number;
    end_turn_band_mm: number;
    note: string;
  };
  extrusion: {
    stack_mm: number; z_lo_mm: number; z_hi_mm: number;
    modelled_z_lo_mm: number; modelled_z_hi_mm: number;
    mirror_plane_z0: boolean; end_winding_h_mm: number;
  };
  sector: { sector_deg: number; n_sectors: number; antiperiodic: boolean };
  machine: SectorInfo;
}

export interface SurfaceRegion {
  name: string;
  kind: string;
  material: string | null;
  polarity: number | null;      // magnets only: sign of M against the tangent
  positions: number[];          // flat xyz, mm
  indices: number[];
  tri_count: number;
  vertex_count: number;
  values: number[] | null;      // one per triangle — the SOLVED, per-element field
  // One per VERTEX: the same numbers area-averaged onto the shared nodes, so a
  // renderer can interpolate across the face (the smooth / "nodal" plot every
  // FEA post-processor draws).  It is an interpolation of `values`, not another
  // solve — which is why both travel and the view says which one it drew.
  values_node: number[] | null;
}

export interface MeshCounts {
  tets: number; nodes: number; tets_solid: number; tets_air: number;
  tets_per_region: Record<string, number>;
  n_tri_2d: number | null; n_nodes_2d: number | null;
  axial_layers: number | null; z_levels_mm: number[] | null;
  stack_half_mm: number | null; r_box_mm: number | null;
  z_box_mm: number | null; sector_deg: number | null;
  antiperiodic: boolean | null;
}

export interface SurfacePayload {
  units: string;
  regions: SurfaceRegion[];
  faces_total: number;
  faces_shown: number;
  decimated: boolean;
  max_tris: number;
  cut: { z_mm: number | null; theta_deg: number | null; air: boolean };
  counts: MeshCounts;
  preset?: string;
  fingerprint?: string;
  fidelity?: string;
  knobs?: Record<string, number>;
  build_wall_s?: number;
  sector?: SectorInfo;
  what?: string;
}

export interface SolveMeta {
  excitation: string; formulation: string; iron: string;
  laminated_iron: boolean; element_order: number;
  tets: number; nodes: number; ndofs: number; solver: string;
  solve_time_s: number; wall_s: number;
  picard_iterations: number | null; picard_converged: boolean | null;
  picard_residual: number | null; picard_tol: number | null;
  picard_max_iter: number | null;
  boundary_flux_Wb: number; Br_T: number; mu_rec: number;
}

export interface FieldPayload extends SurfacePayload {
  available: true;
  quantity: string;
  scale: { vmin: number; vmax: number; unit: string; clip: string; note: string };
  solve: SolveMeta;
  solved_utc: string;
  spill: {
    z_over_half: number[]; B1_T: number[]; profile: number[];
    B1_mid_T: number; k_flux_self: number; stack_half_m: number;
  } | null;
  demag_slices: {
    slices: { z_mid_m: number; worst_H_A_per_m: number; mean_H_A_per_m: number }[];
    mid_worst_H: number; end_worst_H: number;
    mid_mean_H: number; end_mean_H: number;
    worst_shift_A_per_m: number; note: string;
  } | null;
  vectors: {
    points: number[]; vectors: number[]; shown: number; total: number;
    decimated: boolean;
  } | null;
  stale_geometry: boolean | null;
  stale_reason: string | null;
  fingerprint_live: string;
  fingerprint_solved: string | null;
}

export interface FieldOffer {
  available: false;
  preset: string; fingerprint: string; fidelity: string;
  quantity: string; nonlinear: boolean;
  quote_s: number | null; quote_basis: string | null;
  quote_note: string; reason: string;
}

export interface MachinePayload {
  preset: string;
  fingerprint: string;
  materials_requested: Record<string, string>;
  materials_mode: string;
  materials_note: string;
  machine: SectorInfo;
  fidelities: Record<string, Record<string, number | string>>;
  cost_quote_s: Record<string, number>;
  cost_basis: Record<string, string>;
  passport: {
    version: string; generated_utc: string;
    k_flux: number; k_flux_self: number;
    k_T: number | null; k_L: number | null;
    machine: Record<string, unknown>;
    model: Record<string, unknown>;
    match: {
      comparable: boolean; matches: boolean | null;
      differences?: { key: string; passport: unknown; shown: unknown }[];
      n_differences?: number; why?: string; source?: string;
    };
  };
  cached: {
    stem: string; current_knobs: boolean; fingerprint: string;
    fidelity: string; kind: string; solved_utc: string;
    wall_s: number; bytes: number;
  }[];
}

export interface SolveProgress {
  running: boolean; phase: string; progress: number;
  elapsed_s: number; error: string | null; quote_s: number | null;
  fidelity: string | null; nonlinear: boolean | null; message: string | null;
}

async function get<T>(path: string, params: Record<string, string | number | boolean | null | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined) qs.set(k, String(v));
  });
  const r = await fetch(`${BASE}${path}?${qs.toString()}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 400)}`);
  return r.json() as Promise<T>;
}

export const fetchMachine = (preset: string, materials: string) =>
  get<MachinePayload>('/machine', { preset, materials });

export const fetchGeometry = (preset: string, materials: string) =>
  get<GeometryPayload>('/geometry', { preset, materials });

export const fetchMesh = (
  preset: string, materials: string, fidelity: string,
  cut: { z?: number | null; theta?: number | null }, air: boolean, maxTris: number,
) => get<SurfacePayload>('/mesh', {
  preset, materials, fidelity, cut_z_mm: cut.z ?? null,
  cut_theta_deg: cut.theta ?? null, air, max_tris: maxTris,
});

export const fetchField = (
  preset: string, materials: string, fidelity: string, nonlinear: boolean,
  quantity: string, cut: { z?: number | null; theta?: number | null },
  vectors: boolean, maxTris: number,
) => get<FieldPayload | FieldOffer>('/field', {
  preset, materials, fidelity, nonlinear, quantity,
  cut_z_mm: cut.z ?? null, cut_theta_deg: cut.theta ?? null,
  vectors, max_tris: maxTris, max_vectors: vectors ? 1200 : 0,
});

export async function startSolve(body: {
  preset: string; materials: string; fidelity: string; nonlinear: boolean;
}): Promise<{ started: boolean; quote_s: number | null }> {
  const r = await fetch(`${BASE}/solve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

export const fetchSolveProgress = () => get<SolveProgress>('/solve/progress', {});

export async function cancelSolve(): Promise<void> {
  await fetch(`${BASE}/solve/cancel`, { method: 'POST' });
}

/** Seconds → the shortest honest phrasing. */
export function quote(s: number | null | undefined): string {
  if (s === null || s === undefined) return 'unknown';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}
