// Instant analytical "passport" scaling for the simple tuner (no FEM).
//
// A PASSPORT is one base FEM result of a reference motor + its inputs. Changing
// lamination length, turns, or wire thickness rescales torque / back-EMF /
// resistance / losses / efficiency INSTANTLY — the magnet field is fixed by the
// (unchanged) cross-section, so at first order these scale exactly:
//
//   length  L  : torque, EMF, iron, magnet loss, mass ∝ L ; R = R_active·L + R_end
//   turns   N  : torque, EMF ∝ N ; R ∝ N           (wire cross-section unchanged)
//   wire    h  : wire_width is FIXED, so area ∝ wire_height(h) ; R ∝ 1/h ; Imax ∝ h
//   connect nP : 4 coils/phase re-wired (4S→nP1, 2P·2S→nP2, 4P→nP4) — pure
//                electrical voltage↔current trade: series count = 4/nP, so
//                torque,EMF ∝ nP0/nP ; R ∝ (nP0/nP)² ; Imax ∝ nP/nP0
//   voltage    : terminal V = back-EMF + load drop.  The drop (mostly the
//                synchronous reactance X·I, X = ωL, L ∝ turns²·L_stack·series²)
//                is pinned at the loaded base point (Vload0−Vemf0) and rescaled,
//                so V matches FEM at base and scales physically off it.
//
// v1 is LINEAR. Deferred (see docs/MULTI_USER_PLAN.md): non-linear copper AC /
// proximity loss vs wire thickness, and coil-to-magnet-distance loss effects.

export interface Passport {
  N0: number;          // base turns (num_wires_per_slot)
  L0_mm: number;       // base lamination length (motor_length)
  wireH0_mm: number;   // base wire thickness (wire_height); wire_width fixed
  I0_A: number;        // base phase current (rms)
  rpm0: number;        // base speed
  nP0: number;         // base parallel paths (4S→1, 2P·2S→2, 4P→4); 4 coils/phase
  /** STRANDS IN HAND the base point was measured at (geometry.wire_parallel,
   *  ABSENT = 1).  `N0` and the `N` knob are PHYSICAL wire ROWS per slot — that
   *  is what the wire coating and the copper mass are built on — so the SERIES
   *  turns are (N/wire_parallel0)·wire_split0.  The strands are fixed by the
   *  build (they are not a knob), so they cancel out of every turns ratio
   *  below.  Recorded so a 24-turn coil and 24 wires wound 2-in-hand — the same
   *  slot, a factor 2 apart in EMF and 4 in R — can be told apart in the
   *  passport instead of both reading "N0 = 24". */
  wire_parallel0?: number;
  /** STRIPS PER WIRE ROW the base point was measured at (geometry.wire_split,
   *  ABSENT = 1 — the passport predates the split, and the machines it was
   *  measured on were unsplit).  A row's strips are consecutive SERIES turns,
   *  so they MULTIPLY the turn count: N0 rows at wire_split0 = S is S·N0/k
   *  electrical turns.  Unlike the strands this does not always cancel — a
   *  reference passport measured unsplit can be scaled onto a machine that IS
   *  split (Configure matches passports by cross-section, not by build), and
   *  then the turns ratio is off by S.  `Knobs.split` is that machine's own
   *  value; see `turnsFactor`. */
  wire_split0?: number;
  T0_Nm: number;       // base torque at (I0, rpm0)
  Vemf0_peak_V: number;// base back-EMF peak at (N0, L0, rpm0)   (no-load terminal V)
  Vload0_peak_V?: number; // base LOADED terminal-V peak at (I0, rpm0); enables the reactive-drop model
  R0_ohm: number;      // base phase resistance
  endWindFrac: number; // 0..1 fraction of R0 that is end-winding (does NOT scale with L)
  Pfe0_W: number;      // base iron (core) loss
  /** The BASE POINT's iron loss per half [W].  The loss grid scales ONE core
   *  number; this pair is the ratio the scaled value is split by, so the tuner
   *  can say how much of the iron heat the stator has to shed and how much the
   *  rotor does.  Absent on passports generated before the split — the tuner
   *  then puts the whole iron loss on the stator and flags it. */
  P_fe_stator0_W?: number | null;
  P_fe_rotor0_W?: number | null;
  Pmag0_W: number;     // base magnet eddy loss
  mass0_kg: number;    // base active mass
  /** Rated-point torque ripple [%] from the fine (48-step) base solve — a
   *  datasheet figure of the base operating point; never rescaled by knobs. */
  ripple0_pct?: number | null;
  /** The convention the numbers above were SOLVED in ("motor"/"generator") —
   *  a generator solved as a motor is a different operating point. */
  mode0?: string | null;
  // FEM speed-sweep curves at the base operating point (I0, γ, L0): iron and
  // magnet+shaft eddy loss vs rpm.  When present, scaleMotor interpolates these
  // (× length factor) instead of the analytical f^1.5 / f² laws.  Assumed ~current-
  // independent (loss dominated by the magnet flux); a 2D I×rpm map is future work.
  speed?: { rpm: number[]; Pfe_W: number[]; Pmag_W: number[] };
  // 3D end-effect (Stage A): flux the 2D passport cannot see spilling past the
  // laminations.  k_flux_vs_L maps stack length [mm] → k, measured at a few 3D
  // points; scaleMotor interpolates it under the length slider.  Absent/null =
  // not measured — the tuner then shows honest 2D values, flagged as such.
  end3d?: { k_flux: number; k_flux_vs_L?: Record<string, number> | null;
            fidelity?: string } | null;
  // Saturation + demagnetisation calibration: FEM points over phase current at
  // base turns.  Read in AMPERE-TURN space — tooth saturation is set by
  // MMF = turns × coil current, so NI_frac = fN·fI·fConn maps every knob combo
  // onto this one measured curve.  demag_keep_pct locates the current where
  // irreversible demagnetisation begins.  Absent = linear Kt (old passports).
  current?: { I_A: number[]; T_Nm: number[]; V_peak_V: number[];
              demag_keep_pct: number[] } | null;
  // 2-D loss surface: rows = currents (ampere-turn reading), cols = rpm.
  // Captures what two 1-D slices cannot — iron loss depends on BOTH the flux
  // level (current/saturation) and the frequency.  Absent = 1-D fallbacks.
  loss_grid?: { I_A: number[]; rpm: number[];
                Pfe_W: number[][]; Pmag_W: number[][];
                /** solved copper / DC I²R at each grid point — the skin and
                 *  proximity add-on the DC law cannot see (~+25 % here). */
                cuAC?: number[][] } | null;
  /** Winding window measured on the CAD polygons [mm²] and the conductor area
   *  sitting in it at the base winding.  The tuner scales the copper with turns
   *  and wire height (width is fixed by the slot) and divides by the window, so
   *  it can say when a variant stops being windable.  Absent on old passports. */
  A_slot_mm2?: number | null;
  A_cu0_mm2?: number | null;
  slot_fill0_pct?: number | null;
  /** Bench (small-signal, I≈0) inductances of the base machine — the LCR-meter
   *  value.  Scaled by the tuner: L ∝ N² · L_stack · (series count)². */
  ldq0?: { Ld_mH: number; Lq_mH: number;
           I_probe_arms?: number; connection?: string | null } | null;
  /** MEASURED PWM deltas — what a real inverter's carrier adds on top of the
   *  sine numbers above (src/motor_ai_sim/passport_pwm.py).  Absent/null = not
   *  measured, and the tuner then has no PWM toggle for this machine rather
   *  than a toggle backed by an assumption. */
  pwm?: PwmBlock | null;
  /** The pack this machine is wired to, verbatim from its family configuration.
   *  A generator's charging block is computed against it; the PWM block was
   *  switched against its v_nom. */
  battery?: PackSpec | null;
  /** What the machine IS ("motor" / "generator"), as the configuration declares
   *  it — `mode0` above says which convention the numbers were SOLVED in. Only
   *  a generator gets the charging block. */
  role?: string | null;
}

/** A pack as the family yaml stores it. */
export interface PackSpec {
  chemistry?: string | null;
  cells?: number;            // NS — series cells
  n_parallel?: number;       // NP — parallel strings (absent = 1)
  v_min?: number; v_nom?: number; v_max?: number;
  r_int_mohm?: number;       // PER CELL
  capacity_ah?: number;      // per string
  i_charge_max_a?: number;
}

/** One measured PWM point: the sine baseline, the PWM run beside it, and the
 *  deltas between them.  Mirrors passport_pwm.py's `points` entries. */
export interface PwmPoint {
  rpm: number; I_A: number; rated?: boolean;
  f_sw_Hz: number; f_sw_eff_Hz?: number | null; f_elec_Hz: number;
  carriers_per_period: number; n_steps_per_period: number;
  samples_per_carrier: number;
  resolution: 'coarse' | 'partial' | 'resolved' | string;
  V1_peak_V: number; V1_delta_deg: number;
  modulation_index?: number | null;
  dP_mag_W: number; dP_fe_W: number; dP_cu_ac_W: number;
  dT_pct?: number;
  ripple_sine_pct?: number | null;
  ripple_pwm_pct?: number | null;
  /** the switching ripple current [A rms] these deltas were measured at —
   *  the abscissa every exponent below is fitted against */
  I_ripple_A?: number | null;
  I_dc_mean_A?: number | null;
  I_dc_rms_A?: number | null;
  I_dc_ripple_pp_A?: number | null;
}

export interface PwmBlock {
  fidelity: 'quick' | 'full' | string;
  controller_class: string;
  controller_class_id?: string;
  /** the carriers actually MEASURED (2 on a quick block) */
  f_sw_Hz: number[];
  /** every carrier this power stage offers — the picker shows all of them and
   *  flags the ones outside the measured pair */
  f_sw_class_Hz?: number[];
  f_sw_ref_Hz: number;
  v_bus_V: number;
  I0_A: number; rpm0: number;
  rpm_grid: number[];
  points: PwmPoint[];
  /** exponents of each delta against the ripple current — `*_source` says
   *  whether the number was measured on this machine or assumed from physics */
  fit: {
    n_mag: number; n_fe: number; n_cu: number;
    n_ripple: number; n_dc_ripple: number;
    n_mag_source?: string; n_fe_source?: string; n_cu_source?: string;
    n_ripple_source?: string; n_dc_ripple_source?: string;
    ref?: { f_sw_Hz?: number; rpm?: number; I_A?: number; I_ripple_A?: number };
    [k: string]: unknown;
  };
  envelope: {
    I_ripple_min_A?: number | null; I_ripple_max_A?: number | null;
    f_sw_min_Hz: number; f_sw_max_Hz: number;
    rpm_min: number; rpm_max: number;
  };
  coarse_bias?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface Knobs {
  N: number;       // wire ROWS per slot (see Passport.wire_parallel0/wire_split0)
  L_mm: number;    // lamination length
  wireH_mm: number;// wire thickness
  nP: number;      // parallel paths (winding connection: 4S→1, 2P·2S→2, 4P→4)
  I_A: number;     // phase current (rms)
  rpm: number;     // speed
  /** STRIPS PER WIRE ROW of the machine being configured (geometry.wire_split).
   *  Not a slider — it comes from the loaded build, because the strips are
   *  SERIES turns and a machine split S ways has S× the turns of the same rows
   *  unsplit.  ABSENT = the passport's own `wire_split0` (itself absent = 1),
   *  which makes the turns ratio the plain row ratio — the behaviour every
   *  caller had before the split existed. */
  split?: number;
  // ── EXCITATION (optional; absent = the sine numbers, exactly as before) ──
  /** true = add the passport's MEASURED PWM deltas to the losses and the
   *  torque ripple.  Ignored when the passport carries no `pwm` block. */
  pwm?: boolean;
  /** carrier [Hz].  Defaults to the measured reference carrier. */
  f_sw_Hz?: number;
  /** DC link [V].  Defaults to the pack's v_nom (what the block was measured
   *  against), because the ripple current is ∝ V_bus. */
  v_bus_V?: number;
}

export interface ScaledResult {
  T_Nm: number;
  P_mech_W: number;
  Vphase_peak_V: number;
  /** No-load back-EMF (phase peak) at the tuned knobs — what KV is quoted from. */
  Vemf_peak_V: number;
  /** No-load KV, rpm per volt of LINE peak back-EMF (√3 × phase peak). */
  KV_rpm_per_Vline: number;
  R_ohm: number;
  P_cu_W: number;
  P_fe_W: number;
  P_mag_W: number;
  P_loss_W: number;
  /** HEAT TO REMOVE, per side [W] — the two numbers a cooling design is sized
   *  on.  stator = scaled stator iron + all copper (PWM copper delta included);
   *  rotor = scaled rotor iron + magnet/solid loss (PWM iron delta split by the
   *  same base ratio, PWM magnet delta to the rotor).  They sum to P_loss_W. */
  P_loss_stator_W: number;
  P_loss_rotor_W: number;
  /** false = the passport carries no base iron split, so the WHOLE iron loss
   *  was billed to the stator (an assumption — regenerate the passport). */
  loss_split_measured: boolean;
  efficiency: number;   // 0..1
  mass_kg: number;
  torque_per_mass: number;
  /** 3D end-effect factor applied to T/EMF at this length (null = not measured,
   *  values are pure 2D). */
  k_end3d: number | null;
  // ── Full Simulation-card mirror (user 2026-08-25) ──────────────────────────
  power_per_mass_W_kg: number;
  loss_density_W_kg: number;
  Vline_peak_V: number;
  /** RMS values are sinusoid approximations (peak/√2) — the passport keeps
   *  peaks only; the honest waveform lives in the FEM run. */
  Vphase_rms_V: number;
  Vline_rms_V: number;
  /** Magnet flux linkage [mWb, phase peak] from the EMF — needs pole count;
   *  null when the caller could not supply poles. */
  psi_pm_mWb: number | null;
  Km_Nm_sqrtW: number;
  Km_per_mass: number;
  /** Small-signal inductances scaled to the tuned winding [mH] (null when the
   *  passport carries no bench measurement). */
  Ld_mH: number | null;
  Lq_mH: number | null;
  /** Br retention [%] at this operating point, interpolated from the measured
   *  demag curve (null = passport has no current sweep). */
  demag_keep_pct: number | null;
  /** Wire coating [%] of the tuned winding: measured base copper × turns × wire
   *  height over the measured window (null on passports without the geometry). */
  slot_fill_pct: number | null;
  /** Torque constant [N·m/A rms] at the operating point — measured T over the
      operating current, saturation included (the bench number a controller
      datasheet quotes is the low-current limit of this). */
  Kt_Nm_per_A: number | null;
  /** Saturation coefficient [%]: measured T over the linear extrapolation of
   *  the lightest measured point (null without the sweep). */
  saturation_pct: number | null;
  // ── PWM (null throughout when the toggle is off or the passport has no
  //    measured block; the loss/efficiency fields above ALREADY include the
  //    deltas when it is on) ──────────────────────────────────────────────
  /** true when the numbers above carry the PWM deltas. */
  pwm_on: boolean;
  /** magnet eddy, iron and AC-copper watts the carrier adds [W]. */
  pwm_dP_mag_W: number | null;
  pwm_dP_fe_W: number | null;
  pwm_dP_cu_ac_W: number | null;
  /** torque ripple under PWM [%] — the passport's sine ripple plus the
   *  measured PWM increment, scaled by the ripple current. */
  pwm_ripple_pct: number | null;
  /** DC-link current ripple, peak-to-peak [A] — what the link capacitor and
   *  the pack see. */
  pwm_I_dc_ripple_A: number | null;
  /** the switching ripple current itself [A rms] — the abscissa of every law
   *  above, shown so the extrapolation flag can be read rather than trusted. */
  pwm_I_ripple_A: number | null;
  /** "coarse" / "partial" / "resolved" of the underlying measurement, plus
   *  whether the exponents were measured or assumed. */
  pwm_fidelity: string | null;
  /** true when the tuned point sits outside the measured envelope — the
   *  numbers are then the clamped edge of the measurement, not an answer. */
  pwm_extrapolated: boolean;
  pwm_note: string | null;
}

/** Linear interpolation of ys at x over sorted xs; extrapolates beyond the ends
 *  using the end segment's slope (loss floored at 0). */
function interp(xs: number[], ys: number[], x: number): number {
  const n = xs.length;
  if (n === 0) return 0;
  if (n === 1) return ys[0];
  if (x <= xs[0]) {
    const s = (ys[1] - ys[0]) / (xs[1] - xs[0]);
    return Math.max(0, ys[0] + s * (x - xs[0]));
  }
  if (x >= xs[n - 1]) {
    const s = (ys[n - 1] - ys[n - 2]) / (xs[n - 1] - xs[n - 2]);
    return Math.max(0, ys[n - 1] + s * (x - xs[n - 1]));
  }
  let i = 1;
  while (i < n && xs[i] < x) i++;
  const t = (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
  return ys[i - 1] + t * (ys[i] - ys[i - 1]);
}

/** Like interp(), but CLAMPED at both ends: outside the measured range the
 *  nearest measured point is the honest bound, and the caller flags it.  Used
 *  for the PWM deltas, where a linear extrapolation of a power law is how a
 *  tuner ends up quoting watts nobody measured. */
function interpClamp(xs: number[], ys: number[], x: number): number {
  const n = xs.length;
  if (!n) return 0;
  if (n === 1) return ys[0];
  if (x <= xs[0]) return ys[0];
  if (x >= xs[n - 1]) return ys[n - 1];
  return interp(xs, ys, x);
}

/** Bilinear read of one measured PWM field over (rpm, equivalent base-turns
 *  current), clamped at every edge.  The current axis usually holds ONE row —
 *  which is physically right, not a gap: the carrier ripple current is set by
 *  V_bus / (f_sw·L) and does not know the fundamental current, so the deltas
 *  it drives do not move with the current knob (only through saturation's
 *  effect on L, second order and stated). */
function pwmField(pts: PwmPoint[], key: keyof PwmPoint,
                  rpm: number, Ieq: number): number {
  const currents = Array.from(new Set(pts.map((q) => q.I_A))).sort((a, b) => a - b);
  const byI = currents.map((I) => {
    const row = pts.filter((q) => q.I_A === I).sort((a, b) => a.rpm - b.rpm);
    return interpClamp(row.map((q) => q.rpm),
                       row.map((q) => Number(q[key] ?? 0)), rpm);
  });
  return interpClamp(currents, byI, Ieq);
}

/** What the carrier adds, from the passport's own measurement.
 *
 *  THE LAW, and every piece of it is measured except where it says otherwise:
 *
 *    I_ripple ∝ V_bus / (f_sw · L_phase),  L_phase ∝ N²·L_stack·nS²
 *      → rr = (V_bus/V_bus0)·(f_sw0/f_sw) / (fN²·fL·fConn²)
 *
 *    ΔP_mag = Δ_measured(rpm) · fL · rr^n_mag        (n from the passport's fit)
 *    ΔP_fe  = Δ_measured(rpm) · fL · rr^n_fe
 *    ΔP_cu  = Δ_measured(rpm) · (R/R0) · rr^n_cu     (I_ripple²·R_ac ∝ R_dc)
 *    ripple = ripple_sine + Δripple_measured(rpm) · rr^n_ripple
 *    I_dc_ripple = Δ_measured(rpm) · rr^n_dc / (fN·fConn)   (I_dc ∝ I_phase)
 *
 *  The exponents are FITTED on the machine's own two carriers; `fit.*_source`
 *  says "measured" or "assumed", and the tile repeats it.  Outside the
 *  measured envelope the ripple current is clamped and `extrapolated` is set —
 *  a power law extrapolated two octaves is not a measurement.
 */
function pwmDeltas(p: Passport, k: Knobs, f: {
  fN: number; fL: number; fConn: number; fR: number; Ieq: number;
}): {
  dP_mag: number; dP_fe: number; dP_cu: number; ripple: number | null;
  I_dc_ripple: number; I_ripple: number; extrapolated: boolean;
  fidelity: string; note: string;
} | null {
  const b = p.pwm;
  if (!b || !b.points || b.points.length === 0) return null;
  const fsRef = Number(b.f_sw_ref_Hz) || b.f_sw_Hz[0];
  const ref = b.points.filter((q) => Math.abs(q.f_sw_Hz - fsRef) < 1e-6);
  const pts = ref.length ? ref : b.points;
  const fSw = Number(k.f_sw_Hz) > 0 ? Number(k.f_sw_Hz) : fsRef;
  const vBus = Number(k.v_bus_V) > 0 ? Number(k.v_bus_V) : b.v_bus_V;
  // inductance of the tuned winding relative to the measured one
  const fLind = f.fN * f.fN * f.fL * f.fConn * f.fConn;
  const rrRaw = (vBus / b.v_bus_V) * (fsRef / fSw) / Math.max(1e-9, fLind);

  const ripRef = Number(b.fit?.ref?.I_ripple_A ?? b.envelope?.I_ripple_max_A ?? 0);
  const env = b.envelope || { f_sw_min_Hz: fsRef, f_sw_max_Hz: fsRef,
                              rpm_min: b.rpm0, rpm_max: b.rpm0 };
  const ripMax = (Number(env.I_ripple_max_A) || ripRef) * 1.5;
  const ripMin = (Number(env.I_ripple_min_A) || ripRef) / 1.5;
  const rawRip = ripRef * rrRaw;
  const outSpan = fSw < env.f_sw_min_Hz * 0.999 || fSw > env.f_sw_max_Hz * 1.001;
  const outRpm = k.rpm < env.rpm_min * 0.999 || k.rpm > env.rpm_max * 1.001;
  const outRip = ripRef > 0 && (rawRip > ripMax || rawRip < ripMin);
  const ripUsed = ripRef > 0 ? Math.min(ripMax, Math.max(ripMin, rawRip)) : rawRip;
  const rr = ripRef > 0 ? ripUsed / ripRef : rrRaw;

  const pw = (x: number, n: number) => Math.pow(Math.max(1e-9, x), n);
  const fit = b.fit || ({} as PwmBlock['fit']);
  const dMag = pwmField(pts, 'dP_mag_W', k.rpm, f.Ieq) * f.fL * pw(rr, Number(fit.n_mag ?? 2));
  const dFe = pwmField(pts, 'dP_fe_W', k.rpm, f.Ieq) * f.fL * pw(rr, Number(fit.n_fe ?? 2));
  const dCu = pwmField(pts, 'dP_cu_ac_W', k.rpm, f.Ieq) * f.fR * pw(rr, Number(fit.n_cu ?? 2));
  const dDc = pwmField(pts, 'I_dc_ripple_pp_A', k.rpm, f.Ieq)
    * pw(rr, Number(fit.n_dc_ripple ?? 1))
    / Math.max(1e-9, f.fN * f.fConn);
  // torque ripple: the SINE ripple of this machine (a datasheet figure the
  // knobs never move) plus the measured PWM increment, scaled by the ripple.
  const dRipRow = pts.map((q) => ({
    ...q,
    _d: (Number(q.ripple_pwm_pct ?? 0) - Number(q.ripple_sine_pct ?? 0)),
  })) as (PwmPoint & { _d: number })[];
  const dRip = pwmField(dRipRow as PwmPoint[], '_d' as keyof PwmPoint, k.rpm, f.Ieq)
    * pw(rr, Number(fit.n_ripple ?? 1));
  const sineRip = p.ripple0_pct != null ? Number(p.ripple0_pct)
    : Number(pts[0]?.ripple_sine_pct ?? 0);

  const resn = (pts.find((q) => q.rated) ?? pts[0])?.resolution ?? 'unknown';
  const spc = (pts.find((q) => q.rated) ?? pts[0])?.samples_per_carrier ?? 0;
  const measured = String(fit.n_mag_source ?? 'assumed').startsWith('measured');
  const why: string[] = [];
  if (outSpan) why.push(`${(fSw / 1000).toFixed(1)} kHz is outside the measured `
    + `${(env.f_sw_min_Hz / 1000).toFixed(0)}–${(env.f_sw_max_Hz / 1000).toFixed(0)} kHz span`);
  if (outRpm) why.push(`${k.rpm.toFixed(0)} rpm is outside the measured `
    + `${env.rpm_min.toFixed(0)}–${env.rpm_max.toFixed(0)} rpm span`);
  if (outRip) why.push(`the ripple current (${rawRip.toFixed(2)} A) is past `
    + `1.5× the measured maximum — clamped to ${ripUsed.toFixed(2)} A`);
  return {
    dP_mag: dMag, dP_fe: dFe, dP_cu: dCu,
    ripple: Number.isFinite(sineRip + dRip) ? sineRip + dRip : null,
    I_dc_ripple: dDc, I_ripple: ripUsed,
    extrapolated: outSpan || outRpm || outRip,
    fidelity: `${resn} (${Number(spc).toFixed(1)} samples/carrier), exponents `
      + `${measured ? 'measured' : 'assumed'}`,
    note: why.length
      ? `outside the measured envelope: ${why.join('; ')} — these are the `
        + 'clamped edge of the measurement, not an answer for this point'
      : `${b.controller_class} · measured at `
        + `${b.f_sw_Hz.map((x) => (x / 1000).toFixed(0)).join(' / ')} kHz on a `
        + `${b.v_bus_V.toFixed(0)} V bus`,
  };
}

/** Turns ratio — ELECTRICAL turns over the passport's, never rows over rows.
 *
 *  turns = (rows / strands in hand) × strips per row, because a `wire_split`
 *  row's strips are consecutive SERIES turns.  The strands in hand are fixed by
 *  the build and cancel; the SPLIT does not always, since Configure matches a
 *  reference passport by cross-section and the loaded machine may be split when
 *  the measured one was not.  Everything that reads a measured table in
 *  ampere-turn space (torque, EMF, the saturation and demag curves) goes
 *  through this one function so the four call sites cannot drift apart.
 *
 *  Both split values default to 1 when absent — a passport written before the
 *  split, or a caller that has no machine to speak for, is an unsplit build. */
export function turnsFactor(p: Passport, k: Knobs): number {
  if (!p.N0) return 1;
  const kPar0 = Math.max(1, Math.round(p.wire_parallel0 ?? 1));
  const kPar  = kPar0;                    // strands in hand are not a knob
  const s0 = Math.max(1, Math.round(p.wire_split0 ?? 1));
  const s  = Math.max(1, Math.round(k.split ?? s0));
  return ((k.N / kPar) * s) / ((p.N0 / kPar0) * s0);
}

/** Instant analytical scaling of a passport to a new (length, turns, wire, I, rpm).
 *  `poles` (optional) enables ψ_PM — the electrical frequency needs it. */
export function scaleMotor(p: Passport, k: Knobs, poles?: number): ScaledResult {
  const fN = turnsFactor(p, k);
  const fL = p.L0_mm ? k.L_mm / p.L0_mm : 1;
  const fH = p.wireH0_mm ? k.wireH_mm / p.wireH0_mm : 1;
  const fI = p.I0_A ? k.I_A / p.I0_A : 1;
  const fRpm = p.rpm0 ? k.rpm / p.rpm0 : 1;
  // Winding connection: 4 coils/phase re-wired. Series count = 4/nP, so torque &
  // back-EMF ∝ nP0/nP and resistance ∝ (nP0/nP)². Pure electrical, no field change.
  const fConn = p.nP0 && k.nP ? p.nP0 / k.nP : 1;

  // 3D end-effect: the passport's 2D numbers assume infinitely long iron; the
  // measured k(L) says how much flux really links at THIS stack length.  The
  // BASE point was 2D too, so what scales T/EMF is the RATIO k(L)/k(L0) on top
  // of the absolute k(L) — equivalently T = T0_2D·(…)·k(L) with T0_2D taken
  // as the raw 2D base.  Losses stay 2D (conservative, same convention as the
  // Simulation card).  Null when the machine has no Stage A measurement.
  const e3 = p.end3d;
  const kOfL = (L: number): number | null => {
    if (!e3) return null;
    const m = e3.k_flux_vs_L;
    if (m) {
      // Entries, not key round-trips: the backend writes '35.0' and
      // String(Number('35.0')) is '35' — the lookup missed its own key and a
      // NaN k poisoned torque/EMF/KV across the card (measured live
      // 2026-08-25: "куча пустых клеток").
      const pts = Object.entries(m)
        .map(([kk, vv]) => [Number(kk), Number(vv)] as [number, number])
        .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
        .sort((a, b) => a[0] - b[0]);
      if (pts.length >= 2) {
        const xs = pts.map((q) => q[0]);
        const ys = pts.map((q) => q[1]);
        // clamp, no extrapolation: beyond the measured L-range the end effect
        // is unknown — the nearest measured point is the honest bound.
        if (L <= xs[0]) return ys[0];
        if (L >= xs[xs.length - 1]) return ys[xs.length - 1];
        return interp(xs, ys, L);
      }
      if (pts.length === 1) return pts[0][1];
    }
    return e3.k_flux ?? null;
  };
  const kEnd = kOfL(k.L_mm);
  const kEnd0 = kOfL(p.L0_mm);
  // Flux factor for the tuned machine relative to the 2D base solve.
  const fEnd = kEnd != null && kEnd0 != null ? kEnd : 1;

  // Torque: saturation-aware when the passport carries the current sweep —
  // the measured T(I) curve read at the EQUIVALENT base-turns current
  // I_eq = I·fN·fConn (same ampere-turns → same iron state → same flux).
  // T then scales only by the remaining linear factors, length and 3D.
  // Falls back to the linear law for passports without the sweep.
  const cc = p.current;
  const hasSat = !!(cc && cc.I_A.length >= 3);
  const NIfrac = fN * fI * fConn;                 // ampere-turns vs the base point
  const T = hasSat
    ? interp(cc!.I_A, cc!.T_Nm, NIfrac * p.I0_A) * fL * fEnd
    : p.T0_Nm * fN * fL * fI * fConn * fEnd;

  // Resistance: active copper ∝ L, end-winding ~const; ∝ N (more turns of same
  // wire); ∝ 1/area = 1/wire_height (wire_width fixed); ∝ (nP0/nP)² (connection).
  const R = ((1 - p.endWindFrac) * fL + p.endWindFrac) * p.R0_ohm * fN / fH * fConn * fConn;

  // Back-EMF (no-load) ∝ N·L·rpm·(series count), ×k(L) — same flux that makes
  // the torque makes the EMF.
  const Vemf = p.Vemf0_peak_V * fN * fL * fRpm * fConn * fEnd;
  // Loaded terminal voltage = EMF + load drop.  The drop is dominated by the
  // synchronous reactance X·I (X = ωL, L ∝ turns²·L_stack·series²); its value at
  // the loaded base point, (Vload0 − Vemf0), is rescaled by fI·fRpm·fN²·fL·fConn².
  // Falls back to the resistive drop R·I when no loaded base voltage is supplied.
  const Vdrop0 = p.Vload0_peak_V && p.Vload0_peak_V > p.Vemf0_peak_V
    ? p.Vload0_peak_V - p.Vemf0_peak_V : 0;
  const Vdrop = Vdrop0 > 0
    ? Vdrop0 * fI * fRpm * fN * fN * fL * fConn * fConn
    : R * k.I_A * Math.SQRT2;
  const Vphase = Vemf + Vdrop;

  const omega = (2 * Math.PI * k.rpm) / 60;
  const P_mech = T * omega;

  const P_cu_dc = 3 * k.I_A * k.I_A * R;          // 3-phase I²R (DC)
  // Iron + magnet loss: FEM speed-curve (interpolated over rpm, × length factor)
  // when the passport carries one, else the analytical f^1.5 / f² laws.
  const sp = p.speed;
  const hasCurve = !!(sp && sp.rpm.length >= 2);
  // Best: the 2-D loss surface, bilinear over (equivalent base-turns current,
  // rpm) — the current axis is read in ampere-turns, same as the torque curve.
  const lg = p.loss_grid;
  const hasGrid = !!(lg && lg.I_A.length >= 2 && lg.rpm.length >= 2);
  const gridLoss = (rows: number[][]): number => {
    const Ieq = NIfrac * p.I0_A;
    const byI = lg!.I_A.map((_, r) => interp(lg!.rpm, rows[r], k.rpm));
    return interp(lg!.I_A, byI, Ieq);
  };
  const P_fe = hasGrid ? gridLoss(lg!.Pfe_W) * fL
    : hasCurve ? interp(sp!.rpm, sp!.Pfe_W, k.rpm) * fL : p.Pfe0_W * fL * Math.pow(fRpm, 1.5);
  const P_mag = hasGrid ? gridLoss(lg!.Pmag_W) * fL
    : hasCurve ? interp(sp!.rpm, sp!.Pmag_W, k.rpm) * fL : p.Pmag0_W * fL * fRpm * fRpm;
  // Copper = DC I²R + PROXIMITY.  The two must be separated, not lumped into
  // one ratio: the measured grid shows the proximity watts are the SAME at
  // every current (2.8 / 11 / 25 / 44 / 69 / 100 W across 0.5…1.5·I0 on the
  // 85 mm) because they are driven by the rotating MAGNET field, not by the
  // phase current.  A ratio therefore explodes at low current (17× measured)
  // and understates it at high current.  Reconstruct the watts from the
  // stored factor, interpolate THOSE, and scale them by their own physics:
  // proximity ∝ conductors × h³ × stack × f²  (f² is already inside the grid).
  const P_prox = (() => {
    if (!hasGrid || !lg!.cuAC || lg!.cuAC.length !== lg!.I_A.length) return 0;
    const rows = lg!.cuAC.map((row, r) => {
      const dc = 3 * lg!.I_A[r] * lg!.I_A[r] * p.R0_ohm;
      return row.map((a) => Math.max(0, dc * (a - 1)));
    });
    const w = gridLoss(rows);                     // watts at the base winding
    return Math.max(0, w) * fN * fH * fH * fH * fL;
  })();
  // ── PWM: the watts the carrier adds on top of the sine numbers ───────────
  // Measured, not modelled — see pwmDeltas().  The toggle is the ONLY thing
  // that changes the loss chain; with it off every number below is exactly
  // what it was before this block existed.
  const Ieq0 = NIfrac * p.I0_A;
  const fR = p.R0_ohm > 1e-12 ? R / p.R0_ohm : 1;
  const pwmOn = !!(k.pwm && p.pwm && p.pwm.points && p.pwm.points.length);
  const pd = pwmOn ? pwmDeltas(p, k, { fN, fL, fConn, fR, Ieq: Ieq0 }) : null;
  // Added AS MEASURED, sign included.  A delta that came out slightly negative
  // (the magnet term on a machine whose carrier ripple is small against its
  // solve resolution) is what the two solves said; clamping it to zero would
  // make the tiles stop summing to the total, which is the one property that
  // lets an engineer check the card against itself.
  const P_cu = P_cu_dc + P_prox + (pd ? pd.dP_cu : 0);
  const P_fe_t = P_fe + (pd ? pd.dP_fe : 0);
  const P_mag_t = P_mag + (pd ? pd.dP_mag : 0);
  const P_loss = P_cu + P_fe_t + P_mag_t;

  // ── HEAT TO REMOVE, per side ─────────────────────────────────────────────
  // The loss grid scales ONE iron number, so the scaled core loss is split by
  // the RATIO the passport measured at its base point.  Everything else is
  // already attributed by construction: copper is stator, magnet/solid is
  // rotor.  The PWM deltas follow their own term — the iron delta by the same
  // ratio (it is iron loss), the copper delta to the stator, the magnet delta
  // to the rotor — so the two sides still add up to P_loss exactly.
  const feS0 = Number(p.P_fe_stator0_W ?? NaN);
  const feR0 = Number(p.P_fe_rotor0_W ?? NaN);
  const feSplit = Number.isFinite(feS0) && Number.isFinite(feR0) && feS0 + feR0 > 0;
  const feRatio = feSplit ? feS0 / (feS0 + feR0) : 1;
  const P_fe_stator = P_fe_t * feRatio;
  const P_loss_stator = P_cu + P_fe_stator;
  const P_loss_rotor = P_loss - P_loss_stator;

  const eff = P_mech > 0 ? P_mech / (P_mech + P_loss) : 0;
  const mass = p.mass0_kg * fL;

  // No-load KV, the drone-bench convention: rpm per volt of LINE peak EMF.
  const VemfLine = Vemf * Math.sqrt(3);
  const KV = VemfLine > 1e-9 ? k.rpm / VemfLine : 0;

  // ── Simulation-card mirror ────────────────────────────────────────────────
  // ψ_PM from the EMF fundamental: E_phase_peak = ω_e·ψ (needs pole pairs).
  const fe = poles && poles > 0 ? (k.rpm / 60) * (poles / 2) : 0;
  const psi = fe > 0 ? (Vemf / (2 * Math.PI * fe)) * 1000 : null;
  // Km on the DC copper loss (same convention as the Simulation tile).
  // Km is quoted on the DC copper loss (the Simulation card's convention —
  // the AC part is speed-dependent and would make Km a function of rpm).
  const Km = P_cu_dc > 1e-9 ? T / Math.sqrt(P_cu_dc) : 0;
  // Demag retention + saturation coefficient off the measured current sweep,
  // both read at the same ampere-turn point as the torque.
  const Ieq = NIfrac * p.I0_A;
  const demagKeep = hasSat ? interp(cc!.I_A, cc!.demag_keep_pct, Ieq) : null;
  const satPct = (() => {
    if (!hasSat) return null;
    const slope0 = cc!.I_A[0] > 1e-9 ? cc!.T_Nm[0] / cc!.I_A[0] : 0;
    const Tlin = slope0 * Ieq;
    return Tlin > 1e-9 ? 100 * interp(cc!.I_A, cc!.T_Nm, Ieq) / Tlin : null;
  })();

  return {
    T_Nm: T, P_mech_W: P_mech, Vphase_peak_V: Vphase,
    Vemf_peak_V: Vemf, KV_rpm_per_Vline: KV, R_ohm: R,
    P_cu_W: P_cu, P_fe_W: P_fe_t, P_mag_W: P_mag_t, P_loss_W: P_loss,
    P_loss_stator_W: P_loss_stator, P_loss_rotor_W: P_loss_rotor,
    loss_split_measured: feSplit,
    efficiency: eff, mass_kg: mass,
    torque_per_mass: mass > 0 ? T / mass : 0,
    k_end3d: kEnd,
    power_per_mass_W_kg: mass > 0 ? P_mech / mass : 0,
    loss_density_W_kg: mass > 0 ? P_loss / mass : 0,
    Vline_peak_V: Vphase * Math.sqrt(3),
    Vphase_rms_V: Vphase / Math.SQRT2,
    Vline_rms_V: (Vphase * Math.sqrt(3)) / Math.SQRT2,
    psi_pm_mWb: psi,
    Km_Nm_sqrtW: Km,
    Kt_Nm_per_A: k.I_A > 1e-9 ? T / k.I_A : null,
    Km_per_mass: mass > 0 ? Km / mass : 0,
    // Inductance scales with the square of the series turns and (to first
    // order) with the stack: L ∝ N²·L·(nS)².  Slot + gap leakage follow the
    // stack exactly; the end-winding part does not, so long stacks read a few
    // percent high — stated in the tile's tooltip.
    Ld_mH: p.ldq0 ? p.ldq0.Ld_mH * fN * fN * fL * fConn * fConn : null,
    Lq_mH: p.ldq0 ? p.ldq0.Lq_mH * fN * fN * fL * fConn * fConn : null,
    // Copper in the slot ∝ turns × wire height (the wire WIDTH is fixed by the
    // slot), over the window the teeth leave — geometry that no knob moves.
    slot_fill_pct: (p.A_cu0_mm2 && p.A_slot_mm2)
      ? (100 * p.A_cu0_mm2 * fN * fH) / p.A_slot_mm2 : null,
    demag_keep_pct: demagKeep != null ? Math.min(100, demagKeep) : null,
    saturation_pct: satPct != null ? Math.min(100, satPct) : null,
    pwm_on: !!pd,
    pwm_dP_mag_W: pd ? pd.dP_mag : null,
    pwm_dP_fe_W: pd ? pd.dP_fe : null,
    pwm_dP_cu_ac_W: pd ? pd.dP_cu : null,
    pwm_ripple_pct: pd ? pd.ripple : null,
    pwm_I_dc_ripple_A: pd ? pd.I_dc_ripple : null,
    pwm_I_ripple_A: pd ? pd.I_ripple : null,
    pwm_fidelity: pd ? pd.fidelity : null,
    pwm_extrapolated: !!(pd && pd.extrapolated),
    pwm_note: pd ? pd.note : null,
  };
}

/** Largest phase current the winding can carry, scaled by conductor area
 *  (wire_height; wire_width fixed) and parallel paths (more paths share the
 *  phase current, so the phase can carry more). Lets the tuner cap I to the wire. */
export function maxCurrent(p: Passport, k: Knobs): number {
  const fH = p.wireH0_mm ? k.wireH_mm / p.wireH0_mm : 1;
  const fConnI = p.nP0 && k.nP ? k.nP / p.nP0 : 1;
  // Wire ceiling: the base point scaled by conductor area and parallel paths,
  // with 25 % headroom — the base current is an OPERATING point, not the
  // conductor's limit, and treating them as equal flagged every 1 A nudge as
  // an overload (user 2026-08-26).  The honest ceilings are the current
  // DENSITY (shown on its own tile) and the measured demag limit below.
  const wire = p.I0_A * fH * fConnI * 1.25;
  // Demagnetisation cap from the passport's measured retention curve: the
  // base-turns current where Br retention crosses 99.5 %, converted to the
  // tuned winding via ampere-turns (more turns demagnetise at LESS current).
  const cc = p.current;
  if (cc && cc.I_A.length >= 2) {
    const xs = cc.I_A, keep = cc.demag_keep_pct;
    let Idm = xs[xs.length - 1];   // retention never crossed inside the data:
                                   // the last MEASURED current is the known-safe bound
    for (let i = 1; i < xs.length; i++) {
      if (keep[i] < 99.5 && keep[i - 1] >= 99.5) {
        const t = (99.5 - keep[i - 1]) / (keep[i] - keep[i - 1]);
        Idm = xs[i - 1] + t * (xs[i] - xs[i - 1]);
        break;
      }
    }
    const fN = turnsFactor(p, k);
    const fConn = p.nP0 && k.nP ? p.nP0 / k.nP : 1;
    return Math.min(wire, Idm / (fN * fConn));
  }
  return wire;
}
