import { shaftBalance } from './shaftBalance.ts';

const MARKER = 'fe_on_shaft_k3d_v1';
const fin = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export interface MechanicalContext {
  P_mech_extra_W: number | null;
  P_mech_extra_source: 'run' | 'live_bearings' | 'unknown';
  P_bearings_W: number | null;
  P_windage_W: number | null;
}

/** A saved null with provenance is resolved too: never fetch today's friction for it. */
export function hasMechanicalContext(s: any): boolean {
  return s?.P_mech_extra_W != null
    || ['run', 'live_bearings', 'unknown'].includes(s?.P_mech_extra_source);
}

/** SummaryTable's rule: run total first; otherwise LIVE bearings, never a sum of stale run components. */
export function resolveMechanicalContext(s: any, live: any): MechanicalContext {
  if (hasMechanicalContext(s)) {
    return {
      P_mech_extra_W: fin(s.P_mech_extra_W),
      P_mech_extra_source: s.P_mech_extra_source ?? 'run',
      P_bearings_W: s.P_mech_extra_source ? fin(s.P_bearings_W) : fin(s.P_bearings_W ?? 0),
      P_windage_W: fin(s.P_windage_W),
    };
  }
  return {
    P_mech_extra_W: live?.has_bearings ? fin(live.P_mech_extra_W ?? 0) : null,
    P_mech_extra_source: live?.has_bearings ? 'live_bearings' : 'unknown',
    P_bearings_W: live?.has_bearings ? fin(live.P_bearings_W ?? 0) : null,
    P_windage_W: fin(live?.P_windage_W),
  };
}

// Tab-local handoff from the displayed card, keyed by the ENTIRE raw summary, including run/build identity.
// It is deliberately not localStorage: another tab or a reload must not supply a stale live response.
let pageMechanical: { summary: string; context: MechanicalContext } | null = null;
export function rememberPageMechanicalContext(summary: any, context: MechanicalContext): void {
  pageMechanical = { summary: JSON.stringify(summary), context: { ...context } };
}

/** Capture once at the start of Save. The atomic run request persists this context beside its efficiency. */
export function withPageMechanicalContext(s: any): any {
  if (s?.shaft_convention !== MARKER) return s; // legacy saves keep their historical shape/formula
  const context = pageMechanical?.summary === JSON.stringify(s)
    ? pageMechanical.context : resolveMechanicalContext(s, null);
  return { ...s, ...context };
}

/** Marked saved efficiency consumes ONLY the resolved total; absent/unknown is never inferred from components. */
export function shaftExtraW(s: any): number | null {
  return fin(s?.P_mech_extra_W);
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
