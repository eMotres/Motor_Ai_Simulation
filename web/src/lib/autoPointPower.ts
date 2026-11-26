/** Compact optimizer results: torque is the field diagnostic; marked shaft watts are stored separately. */
export function autoPointFieldTorque(p: any): number {
  if (p?.shaft_conv === 1) return p.torque == null ? NaN : Number(p.torque);
  const t = Number(p?.torque);
  if (Number.isFinite(t)) return t;
  const td = Number(p?.td), m = Number(p?.mass);
  return Number.isFinite(td) && Number.isFinite(m) ? td * m : NaN;
}

export function autoPointPowerKw(p: any, omega: number): number {
  if (p?.shaft_conv === 1) return p.P_mech_W == null ? NaN : Number(p.P_mech_W) / 1000;
  const t = autoPointFieldTorque(p);
  return omega > 0 && Number.isFinite(t) ? t * omega / 1000 : NaN;
}
