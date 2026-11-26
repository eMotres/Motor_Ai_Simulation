import { shaftBalance } from './shaftBalance.ts';

const MARKER = 'fe_on_shaft_k3d_v1';
const fin = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The mechanical context a MARKED point is saved with: the SAME watts the Electromagnetic tab's tiles use (SummaryTable: the run's
 *  own `P_mech_extra_W` first, else the bearings + windage components it carries).  Strict finite numbers only (`Number(null)` is
 *  NOT a known loss); null = the machine has no bearing model, the friction is unknown. */
export function shaftExtraW(s: any): number | null {
  const e = fin(s?.P_mech_extra_W);
  if (e != null) return e;
  const b = fin(s?.P_bearings_W);
  const w = fin(s?.P_windage_W);
  return b == null && w == null ? null : (b ?? 0) + (w ?? 0);
}

/** The basis `dutyEfficiencyPct` stores beside a MARKED point's efficiency (null for any other point, which stores none):
 *  "coupling" = the friction is known and taken off the shaft once (the page's shaft tile); "shaft_before_bearings" = no bearing
 *  model. Derived from the same context and the same balance as the stored value; null when the page shows no efficiency. */
export function dutyEfficiencyBasis(s: any): 'coupling' | 'shaft_before_bearings' | null {
  if (s?.shaft_convention !== MARKER) return null;
  const b = shaftBalance({ ...s, P_mech_W: fin(s?.P_mech_W) }, shaftExtraW(s));
  return b?.eta == null ? null : (b.mechKnown ? 'coupling' : 'shaft_before_bearings');
}

/** Shaft efficiency from one stored duty. `pMechW` is raw rotor T*omega. */
export function dutyEfficiencyPct(s: any, pMechW: number): number | null {
  // A MARKED point is saved with the page's own balance (lib/shaftBalance, the Electromagnetic tab's tile), the same mechanical
  // context, full precision: one efficiency per point, the number the page shows (null where the page shows "-").
  if (s?.shaft_convention === MARKER) {
    const b = shaftBalance({ ...s, P_mech_W: fin(pMechW) }, shaftExtraW(s));
    return b?.eta == null ? null : b.eta * 100;
  }
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
