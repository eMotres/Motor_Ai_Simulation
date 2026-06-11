// Shared types for the FEM solve payload, kept in a separate file so HMR
// reload boundaries don't conflict with FemFieldChart's HMR-tracked changes.

export interface FemPayload {
  n_vertices:    number;
  n_triangles:   number;
  vertices:      [number, number][];
  triangles:     [number, number, number][];
  domain_per_tri: number[];
  A_z_per_node:  number[];
  Bmag_per_tri:  number[];
  extent:        [number, number, number, number];
  outlines:      { domain: number; loops: [number, number][][] }[];

  T_em_Nm:       number;
  P_cu_W:        number;
  P_fe_W:        number;
  P_mag_eddy_W:  number;
  P_loss_total_W:number;
  P_mech_W:      number;
  efficiency:    number;
  freq_Hz:       number;
  rpm:           number;

  n_sectors:     number;
  symmetry_mult: number;
  solve_time_s:  number;
  total_time_s:  number;

  A_z_min:       number;
  A_z_max:       number;
  B_mag_max:     number;

  demag_report?: Array<{
    tag: number;
    magnet_index: number;
    H_min_kA_per_m: number;
    H_knee_kA_per_m: number;
    knee_proximity: number;
    demagnetised: boolean;
  }>;
  demag_coef_per_tri?: number[];
  // A/m² in each triangle — non-zero only inside coil polygons.
  J_z_per_tri?:        number[];
  // Cycle-averaged loss density [W/m³] per triangle (eddy solve only) — the
  // Ansys-style "Total Loss" spatial map.  Non-zero in iron / copper / magnets.
  loss_density_per_tri?: number[];
  loss_dens_max?:        number;
  // True when this payload came from the time-coupled eddy-current solve
  // (J⟳ / Loss views) rather than the fast magnetostatic snapshot.
  eddy?:                 boolean;
  P_cu_ac_solve_W?:      number;
  V_peak?:               number;
}
