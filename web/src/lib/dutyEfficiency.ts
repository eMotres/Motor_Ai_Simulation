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
  // ONE efficiency per point (PR: shaft convention follow-up): a MARKED summary carries its own efficiency, solved from the
  // unrounded powers (the summary's P_mech_W / P_loss_total_W are rounded to 0.1 W, which moved small points: 58.8 vs 61.6 %,
  // 80 vs 0 at cut-in).  It is the stored value; the bearings + windage the card already takes off the shaft are applied on
  // top of that same electrical power.  Unmarked summaries keep the formula below, bit for bit.
  if (s?.shaft_convention === 'fe_on_shaft_k3d_v1' && s?.efficiency != null) {
    const eff = Number(s.efficiency);
    if (Number.isFinite(eff) && eff >= 0) {
      if (!known || extra === 0 || !(eff > 0) || !(pm > 0)) return eff * 100;
      // eff = P/(P+L) (motor) or (P-L)/P (generator): the shaft-side loss changes the shaft power, not the electrical one
      return (gen ? eff * pm / (pm + extra) : eff * Math.max(0, pm - extra) / pm) * 100;
    }
  }
  if (pm <= 0) return (Number(s?.efficiency) || 0) * 100;
  const pElec = gen ? Math.max(0, pm - loss) : pm + loss;
  if (!known) return (gen ? pElec / pm : pm / pElec) * 100;
  const pShaft = gen ? pm + extra : Math.max(0, pm - extra);
  return (gen ? (pShaft > 0 ? pElec / pShaft : 0)
              : (pElec > 0 ? pShaft / pElec : 0)) * 100;
}
