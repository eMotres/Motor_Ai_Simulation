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
  // WHAT that map is, component by component — written by the solver (which
  // component is the SOLVED per-element σE² of the coupled eddy run and which
  // is a model normalised to the reported watts).  Printed verbatim under the
  // view: a smeared model and a solved field look different and ARE different.
  loss_density_label?:   string;
  // Material classes NO loss model produced a value for ("magnets", "copper",
  // "iron").  The Loss view leaves them BLANK rather than colouring them the
  // bottom of the scale — on a loss map that is what air looks like, so an
  // unmodelled magnet would read as "no loss in the magnets".
  loss_density_unmodelled?: string[];
  // True when this payload came from the time-coupled eddy-current solve
  // (J⟳ / Loss views) rather than the fast magnetostatic snapshot.
  eddy?:                 boolean;
  P_cu_ac_solve_W?:      number;
  V_peak?:               number;

  // ── Thermal solve (Temp view) ───────────────────────────────────────────
  // The thermal payload carries its OWN solid sub-mesh in vertices/triangles/
  // domain_per_tri (outer air + gap dropped), plus nodal temperature and flux.
  temperature_per_node?: number[];             // °C
  heat_flux_per_tri?:    [number, number][];   // W/m² vector per element
  flux_mag_per_tri?:     number[];             // |q| W/m²
  T_min?:                number;
  T_max?:                number;
  components?: {
    winding?: { max: number; avg: number } | null;
    magnet?:  { max: number; avg: number } | null;
    stator?:  { max: number; avg: number } | null;
    rotor?:   { max: number; avg: number } | null;
  };
  ambient_temp?: number; h_conv?: number;
  k_steel?: number; k_magnet?: number; k_shaft?: number;

  poles_per_sector?: number;
  anti_periodic?:    boolean;

  // ── Provenance: WHERE this picture came from ────────────────────────────
  // "transient-snapshot" = the last simulation run's own final frame, replayed
  // (no solve).  "on-demand solve" = this view ran its own solve just now.
  // The header prints source_label verbatim, so a view always states which one
  // it is instead of leaving the user to guess whether the wait was real work.
  source?:        'transient-snapshot' | 'on-demand solve' | string;
  from_transient?: boolean;
  source_label?:   string;
  transient_steps_per_period?: number | null;
  transient_computed_at?:      string | null;
  // snapshot_only=true probe that found nothing cached — no solve was run.
  no_snapshot?:   boolean;
  reason?:        string;
}

/**
 * Expand a SECTOR solve (n_sectors > 1) into the FULL RING for display: rotate a
 * copy of the sector mesh + fields to each of the N sector positions.  Scalar
 * fields (|B|, loss density, demag, temperature) are replicated as-is; the SIGNED
 * potential A_z and current J_z flip sign on odd copies when the sector is
 * anti-periodic (an odd number of poles per sector), which reproduces the true
 * full-ring solution.  Heat-flux is a real vector → rotated, no sign flip.
 * n_sectors ≤ 1 (already the full disk) is returned unchanged.
 */
export function tileFullRing(p: FemPayload): FemPayload {
  const N = (p.n_sectors as number) | 0;
  if (!p || !p.vertices || N <= 1) return p;
  const anti = p.anti_periodic === true;
  const nv = p.vertices.length;

  const vertices: [number, number][] = [];
  const triangles: [number, number, number][] = [];
  const domain: number[] = [];
  const Az: number[] = [];
  const mk = <T,>(a?: T[]) => (a && a.length ? ([] as T[]) : undefined);
  const Bmag = mk(p.Bmag_per_tri);
  const Jz   = mk(p.J_z_per_tri);
  const loss = mk(p.loss_density_per_tri);
  const dem  = mk(p.demag_coef_per_tri);
  const fmag = mk(p.flux_mag_per_tri);
  const temp = mk(p.temperature_per_node);
  const hflux = mk(p.heat_flux_per_tri);
  const outlines: FemPayload['outlines'] = [];

  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  let azmin = Infinity, azmax = -Infinity;

  for (let k = 0; k < N; k++) {
    const th = (2 * Math.PI / N) * k, ca = Math.cos(th), sa = Math.sin(th);
    const sgn = anti && (k % 2 === 1) ? -1 : 1;
    const off = k * nv;
    for (const [x, y] of p.vertices) {
      const X = x * ca - y * sa, Y = x * sa + y * ca;
      vertices.push([X, Y]);
      if (X < xmin) xmin = X; if (X > xmax) xmax = X;
      if (Y < ymin) ymin = Y; if (Y > ymax) ymax = Y;
    }
    for (const t of p.triangles) triangles.push([t[0] + off, t[1] + off, t[2] + off]);
    for (const v of p.domain_per_tri) domain.push(v);
    for (const v of p.A_z_per_node) { const w = v * sgn; Az.push(w); if (w < azmin) azmin = w; if (w > azmax) azmax = w; }
    if (Bmag) for (const v of p.Bmag_per_tri) Bmag.push(v);
    if (Jz)   for (const v of p.J_z_per_tri!)   Jz.push(v * sgn);
    if (loss) for (const v of p.loss_density_per_tri!) loss.push(v);
    if (dem)  for (const v of p.demag_coef_per_tri!)   dem.push(v);
    if (fmag) for (const v of p.flux_mag_per_tri!)     fmag.push(v);
    if (temp) for (const v of p.temperature_per_node!) temp.push(v);
    if (hflux) for (const q of p.heat_flux_per_tri!) hflux.push([q[0] * ca - q[1] * sa, q[0] * sa + q[1] * ca]);
    for (const o of (p.outlines || []))
      outlines.push({ domain: o.domain,
        loops: o.loops.map(lp => lp.map(([x, y]) => [x * ca - y * sa, x * sa + y * ca] as [number, number])) });
  }

  return {
    ...p, n_sectors: 1, symmetry_mult: 1,
    n_vertices: vertices.length, n_triangles: triangles.length,
    vertices, triangles, domain_per_tri: domain, A_z_per_node: Az,
    Bmag_per_tri: Bmag ?? p.Bmag_per_tri,
    J_z_per_tri: Jz ?? p.J_z_per_tri,
    loss_density_per_tri: loss ?? p.loss_density_per_tri,
    demag_coef_per_tri: dem ?? p.demag_coef_per_tri,
    flux_mag_per_tri: fmag ?? p.flux_mag_per_tri,
    temperature_per_node: temp ?? p.temperature_per_node,
    heat_flux_per_tri: hflux ?? p.heat_flux_per_tri,
    outlines,
    extent: [xmin, xmax, ymin, ymax],
    A_z_min: Number.isFinite(azmin) ? azmin : p.A_z_min,
    A_z_max: Number.isFinite(azmax) ? azmax : p.A_z_max,
  };
}
