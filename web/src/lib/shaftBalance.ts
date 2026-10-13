// The power balance of a run at the SHAFT — ONE pure formula for the Electromagnetic tab's tiles
// (owner 2026-10-13: the new tile panel was added without removing the old one, and the two
// carried two efficiencies, 98.43 % and 98.6 %; there is ONE, and it is the shaft's).
//
// It is exactly `shaft_view` of src/motor_ai_sim/report.py (the document's one balance) and
// `efficiency_shaft` of routes/simulation.py — the card used to compute it inline:
//
//   P_rotor = |P_mech|                       (T·ω of the run; the 3-D factor is already in it)
//   motor:     P_shaft = P_rotor − X          P_elec = P_rotor + L_em      η = P_shaft / P_elec
//   generator: P_shaft = P_rotor + X          P_elec = P_rotor − L_em      η = P_elec  / P_shaft
//
// X = bearings + windage (the analytic SKF block, NOT the field solve), L_em = the solved
// electromagnetic loss.  No bearings on the machine = X is UNKNOWN, not zero: P_shaft falls back
// to the rotor power, η to the electromagnetic one, and `mechKnown` says so.  The direction comes
// from the run's own `op_mode` — generator numbers are quoted positive, so the sign says nothing.
//
// No React, no fetch: node tests import it.

export interface ShaftBalanceIn {
  P_mech_W?: number | null;
  P_loss_total_W?: number | null;
  op_mode?: string | null;
  /** the stored electromagnetic efficiency, a fraction — used only when the rotor power is 0 */
  efficiency?: number | null;
}

export interface ShaftBalance {
  gen: boolean;
  /** true when the bearings + windage watts are known (the machine has bearings) */
  mechKnown: boolean;
  /** |P_mech| — the rotor's T·ω */
  pRotor: number;
  /** at the coupling: rotor ∓ X (rotor itself when X is unknown) */
  pShaft: number;
  /** at the terminals: rotor ± electromagnetic loss (null without a loss) */
  pElec: number | null;
  /** electromagnetic loss (solved) */
  lossEm: number | null;
  /** electromagnetic + bearings + windage (null when X is unknown) */
  lossAll: number | null;
  /** X as a share of the rotor power */
  xMech: number | null;
  /** electromagnetic-only efficiency, a fraction */
  etaEm: number | null;
  /** shaft efficiency, a fraction (null when X is unknown) */
  etaShaft: number | null;
  /** THE efficiency: the shaft's when known, else the electromagnetic one — a fraction */
  eta: number | null;
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** extraW = bearings + windage in watts, null when the machine has no bearing model. */
export function shaftBalance(s: ShaftBalanceIn, extraW: number | null | undefined): ShaftBalance | null {
  const pm = num(s.P_mech_W);
  if (pm == null) return null;
  const gen = s.op_mode === 'generator';
  const x = num(extraW);
  const mechKnown = x != null;
  const pRotor = Math.abs(pm);
  const lossEm = num(s.P_loss_total_W);
  const pShaft = mechKnown ? (gen ? pRotor + x : Math.max(0, pRotor - x)) : pRotor;
  const pElec = lossEm != null ? (gen ? Math.max(0, pRotor - lossEm) : pRotor + lossEm) : null;
  const etaEm = pRotor > 0 && lossEm != null
    ? (gen ? Math.max(0, pRotor - lossEm) / pRotor : pRotor / (pRotor + lossEm))
    : num(s.efficiency);
  const etaShaft = !mechKnown || pElec == null ? null
    : (gen ? (pShaft > 0 ? pElec / pShaft : 0) : (pElec > 0 ? pShaft / pElec : 0));
  return {
    gen, mechKnown, pRotor, pShaft, pElec, lossEm,
    lossAll: mechKnown && lossEm != null ? lossEm + (x as number) : null,
    xMech: mechKnown && pRotor > 0 ? (x as number) / pRotor : null,
    etaEm, etaShaft, eta: etaShaft ?? etaEm,
  };
}
