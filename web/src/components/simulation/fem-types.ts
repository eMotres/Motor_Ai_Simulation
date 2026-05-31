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
  bh_curve_magnet?: Array<{ H_kA_per_m: number; B_T: number }>;
  magnet_op_points?: Array<{
    magnet_index: number;
    H_op_kA_per_m: number;
    H_mean_kA_per_m: number;
    B_op_T: number;
  }>;
}
