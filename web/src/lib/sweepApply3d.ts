// Which 3-D end-effect factor a SWEEP POINT is scaled by (pure; the panel keeps the localStorage reads).
//
// Every point is scaled by its OWN k_flux when it carries one (`point.end3d.k_flux`: the passport value of the machine the point
// was solved for), else by the loaded machine's k as before.  The switch (Simulation's "3D" button off) shows the raw 2-D
// numbers for every point.  A value outside (0, 1.2] is not a factor.
export const validK = (v: unknown): number | null => {
  const k = Number(v);
  return v != null && Number.isFinite(k) && k > 0 && k <= 1.2 ? k : null;
};

export const pointApply3dK = (
  point: { end3d?: { k_flux?: unknown } | null } | null | undefined,
  machineK: number | null,
  switchOn: boolean,
): number | null => {
  if (!switchOn) return null;
  return validK(point?.end3d?.k_flux) ?? machineK;
};
