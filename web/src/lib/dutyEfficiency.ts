/** Shaft efficiency from one stored duty. `pMechW` is raw rotor T*omega.  `freeRunW` is the bench free-run extra loss of the
 *  "With free-run losses" view (lib/freeRunLoss.ts): 0 / absent = pure simulation, which is what every SAVE passes (a stored
 *  result is never rewritten by a display switch). */
export function dutyEfficiencyPct(s: any, pMechW: number, freeRunW = 0): number | null {
  const pm = Math.abs(Number(pMechW) || 0);
  const loss = Number(s?.P_loss_total_W);
  if (s?.P_loss_total_W == null || !Number.isFinite(loss) || loss < 0) return null;
  const brg = Number(s?.P_bearings_W);
  const wind = Number(s?.P_windage_W);
  const fr = Number.isFinite(freeRunW) ? freeRunW : 0;
  const known = Number.isFinite(brg) || Number.isFinite(wind) || fr !== 0;
  const extra = (Number.isFinite(brg) ? brg : 0) + (Number.isFinite(wind) ? wind : 0) + fr;
  const gen = s?.op_mode === 'generator';
  if (pm <= 0) return (Number(s?.efficiency) || 0) * 100;
  const pElec = gen ? Math.max(0, pm - loss) : pm + loss;
  if (!known) return (gen ? pElec / pm : pm / pElec) * 100;
  const pShaft = gen ? pm + extra : Math.max(0, pm - extra);
  return (gen ? (pShaft > 0 ? pElec / pShaft : 0)
              : (pElec > 0 ? pShaft / pElec : 0)) * 100;
}
