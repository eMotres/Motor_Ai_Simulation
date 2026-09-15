// BOOST-CHARGING of a generator's own pack, computed analytically from the
// passport — the Configure-tab twin of the FEM charging run.
//
// The FEM path (routes/simulation._battery_charge_block + _charge_outer_loops)
// answers the same question by solving the machine under a PWM voltage and
// integrating the bridge's switch states.  That costs minutes.  This costs
// microseconds, and it is the SAME model with the switching taken out:
//
//   P_mech   = |T(knobs)| · ω                      (torque from the passport)
//   P_loss   = copper + iron + magnet               (+ the measured PWM deltas
//                                                    when the PWM toggle is on)
//   P_charge = P_mech − P_loss                      IDEAL BRIDGE: no dead time,
//                                                   no device conduction or
//                                                   switching loss, no DC-link
//                                                   ripple.  A real charger
//                                                   delivers less, never more.
//   V_bus    = V_oc + I·R_pack ,  I = P_charge/V_bus
//            → V_bus² − V_oc·V_bus − P·R = 0, solved EXACTLY (the FEM path
//              iterates the same fixed point because each iterate costs a
//              full transient; here it is a quadratic).
//
// Three things can stop the point being real, and the card names which:
//   modulation — the fundamental the machine needs does not fit the bus
//                (m = 2·V1_peak/V_bus above 1.15 even with 3rd-harmonic
//                injection).  Not a limit you can charge through: the
//                inverter cannot synthesise that voltage at all.
//   current    — I_charge above the pack's own charge-current ceiling.
//   pack       — V_bus above the pack's maximum terminal voltage.
//
// PLACEHOLDERS.  r_int / capacity / charge ceiling default exactly as
// src/motor_ai_sim/simulation/battery.py defaults them, and say so, so a
// number nobody measured never reaches a tile looking like one that was.

import { scaleMotor, maxCurrent, turnsFactor, type Passport, type PackSpec, type Knobs, type ScaledResult } from './motorScaling';

/** Linear-modulation ceiling with 3rd-harmonic (or space-vector) injection —
 *  the same MAX_MODULATION_INDEX the solver refuses above. */
export const M_LIMIT = 1.15;
/** Pure sine-triangle ceiling, shown beside it so the margin is readable. */
export const M_LIMIT_SPWM = 1.0;

// PLACEHOLDER per-cell internal resistances [mΩ] — mirrors
// simulation/battery.py _R_INT_DEFAULT_MOHM, including its provenance note.
const R_INT_DEFAULT_MOHM: Record<string, number> = {
  nmc: 12, nca: 12, lco: 15, lifepo4: 8, lfp: 8, lto: 6,
};
const R_INT_FALLBACK_MOHM = 12;
const CAPACITY_DEFAULT_AH = 10;

export interface Pack {
  v_oc_V: number;
  cells: number;
  n_parallel: number;
  r_int_mohm: number;      // per cell
  R_pack_ohm: number;
  capacity_ah: number;     // whole pack
  i_charge_max_A: number;  // 0 = none declared
  v_max_V: number;         // 0 = none declared
  chemistry: string | null;
  /** which of the numbers above nobody measured */
  placeholders: Record<string, string>;
}

/** A pack as the DC link sees it — the TypeScript twin of
 *  simulation/battery.py pack_from_config().  Returns null when there is no
 *  battery: "no battery" is a real answer and must not be faked as a 0 V pack. */
export function packFromSpec(b?: PackSpec | null): Pack | null {
  if (!b) return null;
  const ph: Record<string, string> = {};
  const chem = b.chemistry ?? null;
  const ns = Math.max(1, Math.round(Number(b.cells ?? 1)));
  const np = Math.max(1, Math.round(Number(b.n_parallel ?? 1)));
  if (b.n_parallel == null) ph.n_parallel = 'assumed 1 string';
  let rInt = Number(b.r_int_mohm);
  if (!(rInt > 0)) {
    rInt = R_INT_DEFAULT_MOHM[String(chem ?? '').trim().toLowerCase()] ?? R_INT_FALLBACK_MOHM;
    ph.r_int_mohm = `placeholder for ${chem ?? 'unknown'} — not measured on this pack`;
  }
  let cap = Number(b.capacity_ah);
  if (!(cap > 0)) {
    cap = CAPACITY_DEFAULT_AH;
    ph.capacity_ah = 'placeholder — capacity is not derivable from a voltage spec';
  }
  let iMax = Number(b.i_charge_max_a);
  if (!(iMax > 0)) {
    iMax = cap * np;                 // 1 C
    ph.i_charge_max_A = 'placeholder — 1 C of the (placeholder) capacity';
  }
  let vOc = Number(b.v_nom);
  if (!(vOc > 0)) {
    if (Number(b.v_min) > 0 && Number(b.v_max) > 0) {
      vOc = 0.5 * (Number(b.v_min) + Number(b.v_max));
      ph.v_oc = 'midpoint of v_min/v_max — the pack carries no nominal';
    } else return null;
  } else {
    ph.v_oc = 'pack nominal (no state-of-charge model)';
  }
  return {
    v_oc_V: vOc, cells: ns, n_parallel: np, r_int_mohm: rInt,
    R_pack_ohm: (ns * rInt * 1e-3) / np,
    capacity_ah: cap * np,
    i_charge_max_A: iMax,
    v_max_V: Number(b.v_max) > 0 ? Number(b.v_max) : 0,
    chemistry: chem, placeholders: ph,
  };
}

export type ChargeLimit = 'none' | 'current' | 'pack' | 'modulation';

export interface ChargeResult {
  /** false when this machine is not a generator, or has no pack */
  available: boolean;
  P_mech_W: number;
  P_loss_W: number;
  P_charge_W: number;
  I_charge_A: number;
  V_oc_V: number;
  V_bus_V: number;
  V_rise_V: number;
  C_rate: number | null;
  eta_charge: number | null;
  R_pack_ohm: number;
  P_pack_r_loss_W: number;
  /** fundamental phase voltage the machine needs at this point [V peak] */
  V1_peak_V: number;
  modulation_index: number;
  /** the most the bus can synthesise at this V_bus [V peak] */
  V1_max_peak_V: number;
  i_charge_max_A: number;
  v_max_V: number;
  limited_by: ChargeLimit;
  charging: boolean;
  pack: Pack | null;
  note: string;
}

const NO_CHARGE: ChargeResult = {
  available: false, P_mech_W: 0, P_loss_W: 0, P_charge_W: 0, I_charge_A: 0,
  V_oc_V: 0, V_bus_V: 0, V_rise_V: 0, C_rate: null, eta_charge: null,
  R_pack_ohm: 0, P_pack_r_loss_W: 0, V1_peak_V: 0, modulation_index: 0,
  V1_max_peak_V: 0, i_charge_max_A: 0, v_max_V: 0, limited_by: 'none',
  charging: false, pack: null, note: '',
};

/** Is this passport a generator with a pack to charge? */
export function canCharge(p: Passport): boolean {
  const role = String(p.role ?? p.mode0 ?? '').toLowerCase();
  return role === 'generator' && !!packFromSpec(p.battery);
}

/** Charge state at ONE tuned point.  `res` may be supplied when the caller
 *  already has the scaled result (the panel does) — it must be the result of
 *  scaleMotor(p, k, poles), or the two cards will disagree. */
export function chargeAt(p: Passport, k: Knobs, poles?: number,
                         res?: ScaledResult): ChargeResult {
  const pack = packFromSpec(p.battery);
  if (!pack || String(p.role ?? p.mode0 ?? '').toLowerCase() !== 'generator') {
    return { ...NO_CHARGE };
  }
  const r = res ?? scaleMotor(p, k, poles);
  const P_mech = Math.abs(r.P_mech_W);
  const P_loss = r.P_loss_W;
  const P = P_mech - P_loss;                       // ideal bridge
  const R = pack.R_pack_ohm;
  const Voc = pack.v_oc_V;
  // V_bus² − V_oc·V_bus − P·R = 0 — the positive root.  With P < 0 (this point
  // empties the pack) the same root is below V_oc, which is the honest reading.
  const disc = Voc * Voc + 4 * P * R;
  const V_bus = disc > 0 ? 0.5 * (Voc + Math.sqrt(disc)) : Voc;
  const I = V_bus > 1e-9 ? P / V_bus : 0;

  // Modulation feasibility — the machine's own terminal fundamental against
  // what the bridge can synthesise from this bus.
  const V1 = r.Vphase_peak_V;
  const m = V_bus > 1e-9 ? (2 * V1) / V_bus : Infinity;
  const V1max = 0.5 * M_LIMIT * V_bus;

  let limited: ChargeLimit = 'none';
  if (!(m <= M_LIMIT)) limited = 'modulation';
  else if (pack.i_charge_max_A > 0 && I > pack.i_charge_max_A) limited = 'current';
  else if (pack.v_max_V > 0 && V_bus > pack.v_max_V) limited = 'pack';

  const note = limited === 'modulation'
    ? `the machine needs ${V1.toFixed(1)} V peak per phase and the bus can `
      + `synthesise ${V1max.toFixed(1)} V (m = ${m.toFixed(3)} against the `
      + `${M_LIMIT} linear limit) — this point is not reachable on this pack`
    : limited === 'current'
      ? `I_charge ${I.toFixed(1)} A is above the pack's ${pack.i_charge_max_A.toFixed(0)} A charge ceiling`
      : limited === 'pack'
        ? `the bus rises to ${V_bus.toFixed(1)} V, past the pack's ${pack.v_max_V.toFixed(0)} V maximum`
        : (P > 0
          ? 'ideal bridge — a real charger delivers less, never more'
          : `not charging: the machine's own losses (${P_loss.toFixed(0)} W) exceed `
            + `the shaft power (${P_mech.toFixed(0)} W), so the bridge draws from the pack`);

  return {
    available: true,
    P_mech_W: P_mech, P_loss_W: P_loss, P_charge_W: P,
    I_charge_A: I, V_oc_V: Voc, V_bus_V: V_bus, V_rise_V: V_bus - Voc,
    C_rate: pack.capacity_ah > 1e-9 ? Math.abs(I) / pack.capacity_ah : null,
    eta_charge: P_mech > 1 ? Math.max(0, P) / P_mech : null,
    R_pack_ohm: R, P_pack_r_loss_W: I * I * R,
    V1_peak_V: V1, modulation_index: m, V1_max_peak_V: V1max,
    i_charge_max_A: pack.i_charge_max_A, v_max_V: pack.v_max_V,
    limited_by: limited, charging: P > 0, pack, note,
  };
}

/** The current range the max-charge sweep is allowed to walk.
 *
 *  TWO bounds, and both are the machine's, not a preference:
 *    • the passport's own MEASURED current sweep — outside it the torque is an
 *      extrapolation of the saturation curve, and a max-charge answer built on
 *      an extrapolated torque is a guess with a number attached;
 *    • maxCurrent() — the conductor and the measured DEMAGNETISATION limit.
 *      The measured sweep deliberately runs past the demag knee (that is how
 *      it finds it), so without this bound the sweep would happily "win" at a
 *      current that permanently weakens the magnets.
 */
export function measuredCurrentRange(p: Passport, k: Knobs): [number, number] {
  const cc = p.current;
  const fN = turnsFactor(p, k);
  const fConn = p.nP0 && k.nP ? p.nP0 / k.nP : 1;
  const back = (Ieq: number) => Ieq / Math.max(1e-9, fN * fConn);
  const [lo, hi] = (cc && cc.I_A.length >= 2)
    ? [back(Math.min(...cc.I_A)), back(Math.max(...cc.I_A))]
    : [0.25 * back(p.I0_A), 1.5 * back(p.I0_A)];
  const cap = maxCurrent(p, k);
  return [Math.min(lo, cap), Math.min(hi, cap)];
}

export interface MaxCharge {
  /** the winning phase current [A rms], or null when no current in the
   *  measured range charges at all */
  I_A: number | null;
  charge: ChargeResult | null;
  /** what stopped the sweep going higher */
  limited_by: ChargeLimit;
  /** true when the winner sits at the TOP of the sweep range — nothing in the
   *  pack or the modulation stopped it, the machine's own current ceiling
   *  (conductor / measured demagnetisation knee) did */
  at_range_top: boolean;
  /** the range the sweep walked [A rms] */
  range_A: [number, number];
  n_tried: number;
}

/** Sweep the current knob over the passport's own measured sweep range at the
 *  tuned rpm and keep the FEASIBLE point with the most charge power.
 *
 *  Feasible means all three limits hold; the winner's `limited_by` is
 *  therefore 'none', and `MaxCharge.limited_by` says which limit the sweep ran
 *  into just past the winner — the thing an engineer would change to get more.
 */
export function maxCharge(p: Passport, k: Knobs, poles?: number,
                          steps = 40): MaxCharge {
  if (!canCharge(p)) {
    return { I_A: null, charge: null, limited_by: 'none', at_range_top: false,
             range_A: [0, 0], n_tried: 0 };
  }
  const [lo, hi] = measuredCurrentRange(p, k);
  let best: { I: number; c: ChargeResult } | null = null;
  let blocker: ChargeLimit = 'none';
  const n = Math.max(4, steps);
  for (let i = 0; i <= n; i++) {
    const I = lo + ((hi - lo) * i) / n;
    if (!(I > 0)) continue;
    const c = chargeAt(p, { ...k, I_A: I }, poles);
    if (c.limited_by !== 'none') {
      if (blocker === 'none') blocker = c.limited_by;
      continue;
    }
    if (!c.charging) continue;
    if (!best || c.P_charge_W > best.c.P_charge_W) best = { I, c };
  }
  return {
    I_A: best ? best.I : null,
    charge: best ? best.c : null,
    limited_by: blocker,
    at_range_top: !!best && best.I >= hi - (hi - lo) / n - 1e-9,
    range_A: [lo, hi],
    n_tried: n + 1,
  };
}

export interface ChargeMapRow {
  rpm: number;
  I_A: number | null;
  P_charge_W: number;
  I_charge_A: number;
  V_bus_V: number;
  C_rate: number | null;
  eta_charge: number | null;
  limited_by: ChargeLimit;
}

/** P_charge(rpm) at the max-charge current — the boost-mode interpolation.
 *
 *  The speed axis is the PASSPORT'S OWN: the loss grid's rpm list (densified
 *  between its ends), because outside it the iron and magnet loss are an
 *  extrapolation and the charge power is mostly loss bookkeeping. */
export function chargeMap(p: Passport, k: Knobs, poles?: number,
                          n = 21): ChargeMapRow[] {
  if (!canCharge(p)) return [];
  const rs = p.loss_grid?.rpm?.length ? p.loss_grid.rpm
    : p.speed?.rpm?.length ? p.speed.rpm : [p.rpm0];
  const lo = Math.min(...rs);
  const hi = Math.max(...rs);
  const out: ChargeMapRow[] = [];
  const m = Math.max(2, n);
  for (let i = 0; i < m; i++) {
    const rpm = lo + ((hi - lo) * i) / (m - 1);
    const kk = { ...k, rpm };
    const best = maxCharge(p, kk, poles, 24);
    if (best.charge) {
      out.push({
        rpm, I_A: best.I_A, P_charge_W: best.charge.P_charge_W,
        I_charge_A: best.charge.I_charge_A, V_bus_V: best.charge.V_bus_V,
        C_rate: best.charge.C_rate, eta_charge: best.charge.eta_charge,
        limited_by: best.limited_by,
      });
    } else {
      // nothing in the measured current range charges here — report the row
      // with its blocker rather than dropping it: a hole in the curve is
      // information (below this speed the machine cannot fill the pack).
      const c = chargeAt(p, kk, poles);
      out.push({
        rpm, I_A: null, P_charge_W: Math.min(0, c.P_charge_W),
        I_charge_A: c.I_charge_A, V_bus_V: c.V_bus_V, C_rate: c.C_rate,
        eta_charge: c.eta_charge,
        limited_by: best.limited_by !== 'none' ? best.limited_by : c.limited_by,
      });
    }
  }
  return out;
}
