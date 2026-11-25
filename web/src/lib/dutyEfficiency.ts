/** Shaft efficiency from one stored duty. `pMechW` is raw rotor T*omega. */
export function dutyEfficiencyPct(s: any, pMechW: number): number | null {
  // shaft-side convention, motor at no load: the shaft delivers nothing (it supplies the core loss) - no efficiency
  if (s?.shaft_convention === 'fe_on_shaft_k3d_v1' && s?.op_mode !== 'generator' && !(Number(pMechW) > 0)) return 0;
  const pm = Math.abs(Number(pMechW) || 0);
  const loss = Number(s?.P_loss_total_W);
  if (s?.P_loss_total_W == null || !Number.isFinite(loss) || loss < 0) return null;
  const brg = Number(s?.P_bearings_W);
  const wind = Number(s?.P_windage_W);
  const known = Number.isFinite(brg) || Number.isFinite(wind);
  const extra = (Number.isFinite(brg) ? brg : 0) + (Number.isFinite(wind) ? wind : 0);
  const gen = s?.op_mode === 'generator';
  if (pm <= 0) return (Number(s?.efficiency) || 0) * 100;
  const pElec = gen ? Math.max(0, pm - loss) : pm + loss;
  if (!known) return (gen ? pElec / pm : pm / pElec) * 100;
  const pShaft = gen ? pm + extra : Math.max(0, pm - extra);
  return (gen ? (pShaft > 0 ? pElec / pShaft : 0)
              : (pElec > 0 ? pShaft / pElec : 0)) * 100;
}
