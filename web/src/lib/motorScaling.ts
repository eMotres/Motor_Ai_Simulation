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
//                EMF, KV and the drop are FUNDAMENTALS (passport extraction_rev
//                ≥ 2); the voltage the bus must cover is the loaded WAVEFORM
//                peak, i.e. that fundamental × the base point's crest factor.
//
// Losses: iron and magnet from the measured I×rpm grid; copper = DC I²R plus a
// proximity term reconstructed from the grid's measured AC factor and scaled
// ∝ conductors × h³ × stack (see P_prox below) — so wire thickness and turns
// DO move the AC copper.  Known limit (audit 2026-09-30): one global N·h³ law
// cannot see rows that move into the strong field near the slot opening
// (AC copper −70 % on the Ø40 at the slot's last row); a per-row field model
// is the planned replacement (docs/PASSPORT_ALGORITHM.md §6.3).

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
  /** Base no-load back-EMF at (N0, L0, rpm0), phase, star-equivalent [V].
   *  extraction_rev ≥ 2: the FUNDAMENTAL amplitude (`emf_basis`); older
   *  passports stored the sampled waveform peak under this key. */
  Vemf0_peak_V: number;
  /** base LOADED terminal-voltage WAVEFORM peak at (I0, rpm0); enables the
   *  reactive-drop model and is what the voltage limit compares against */
  Vload0_peak_V?: number;
  /** base LOADED terminal-voltage FUNDAMENTAL (extraction_rev ≥ 2) — the
   *  drop model then works on fundamentals and the crest factor
   *  Vload0_peak_V / Vload0_fund_V carries the waveform back to a peak. */
  Vload0_fund_V?: number | null;
  /** "fundamental" | "waveform peak …" — what Vemf0_peak_V holds. */
  emf_basis?: string | null;
  /** no-load waveform peak, for reference only (extraction_rev ≥ 2) */
  Vemf0_wave_peak_V?: number | null;
  /** end-winding factor of the base solve; endWindFrac = 1 − 1/k_end0 */
  k_end0?: number | null;
  /** passport extraction revision (absent = before the 2026-09-30 fixes) */
  extraction_rev?: number | null;
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
            fidelity?: string;
            /** MEASURED 3-D torque factor (Stage B) — the only factor Kt / Km
             *  may carry (owner 2026-09-30); absent = Kt / Km stay 2-D */
            k_T?: number | null } | null;
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
                cuAC?: number[][];
                /** extraction_rev ≥ 2: the DC and AC copper watts behind cuAC
                 *  (3·I²·R_eqstar and solved − DC), stored separately */
                Pcu_dc_W?: number[][]; Pcu_ac_W?: number[][] } | null;
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
  /** COMPUTED drive variants — each one a (device, carrier) pair solved for
   *  THIS machine (passport pilot, owner 2026-10-05).  Configure's PWM menu
   *  lists exactly these and nothing else; absent/empty = "PWM not computed
   *  for this motor".  See PwmVariant. */
  pwm_variants?: PwmVariant[] | null;
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

/** One computed operating point of a drive variant.  The coordinates are the
 *  base-build phase current [A rms] and speed [rpm] it was solved at; they ride
 *  inside the value (`rpm`, `I_A`) or, failing that, in the key (`"3000rpm_40A"`). */
export interface PwmVariantPoint {
  rpm?: number; I_A?: number;
  motor_pwm_loss_W?: number | null;
  inverter_loss_W?: { cond?: number | null; sw?: number | null; dead?: number | null } | number | null;
  tj_C?: number | null;
  eta_drive_pct?: number | null;
  eta_shaft_pct?: number | null;
  p_cont_max_W?: number | null;
  [k: string]: unknown;
}

/** A drive variant as the passport record carries it (schema agreed with the
 *  passport pilot, 2026-10-05). */
export interface PwmVariant {
  id: string;
  device: string;
  technology?: string | null;            // "Si" | "SiC" | "GaN"
  carrier_hz: number;
  dead_time_s?: number | null;
  modulation?: string | null;            // e.g. "SVPWM centred"
  m_max?: number | null;
  n_parallel?: number | null;
  bus_v?: { min?: number | null; nom?: number | null; max?: number | null } | null;
  provenance?: string | Record<string, unknown> | null;
  points: Record<string, PwmVariantPoint>;
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
  // ── DRIVE (Configure's Sine | PWM menu, owner 2026-10-05).  scaleMotor()
  //    never reads these: the PWM numbers come from the passport's COMPUTED
  //    `pwm_variants` (lib/configuratorDrive.ts), so the sine numbers cannot
  //    move.  Absent = Sine. ──
  /** 'pwm' = show the picked computed drive variant; absent/'sine' = Sine */
  drive?: 'sine' | 'pwm';
  /** id of the passport's pwm_variants entry (device + carrier) */
  drive_variant?: string;
  /** the same variant as the two dropdowns name it: transistor + PWM frequency.  The pair is what
   *  is remembered, so a variant that is renumbered in a new passport is still found. */
  drive_device?: string;
  drive_carrier_hz?: number;
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
   *  on.  stator = scaled stator iron + all copper; rotor = scaled rotor iron +
   *  magnet/solid loss.  They sum to P_loss_W. */
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
  /** What the torque, P_mech, Kt, Km and Km/mass carry (owner 2026-09-30):
   *  "3-D" = the passport's MEASURED torque factor k_T, "3-D flux" = the flux
   *  factor k(L) (k_T not measured), "2-D" = no 3-D result.  EMF and KV
   *  always carry the flux factor. */
  kt_km_basis: '2-D' | '3-D flux' | '3-D';
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
      // 2026-08-25: "a pile of empty cells").
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
  // TORQUE factor (owner 2026-09-30): the passport's measured k_T when it has
  // one — measured at one stack length, so it is scaled along the length by
  // the flux curve's own ratio k(L)/k(L0) — else the flux factor itself.
  const kTm = Number(p.end3d?.k_T ?? NaN);
  const kTmeas = Number.isFinite(kTm) && kTm > 0.5 && kTm <= 1.2 ? kTm : null;
  const fTq = kTmeas != null
    ? kTmeas * (kEnd != null && kEnd0 != null && kEnd0 > 0 ? kEnd / kEnd0 : 1)
    : fEnd;

  // Torque: saturation-aware when the passport carries the current sweep —
  // the measured T(I) curve read at the EQUIVALENT base-turns current
  // I_eq = I·fN·fConn (same ampere-turns → same iron state → same flux).
  // T then scales only by the remaining linear factors, length and 3D.
  // Falls back to the linear law for passports without the sweep.
  const cc = p.current;
  const hasSat = !!(cc && cc.I_A.length >= 3);
  const NIfrac = fN * fI * fConn;                 // ampere-turns vs the base point
  const T = hasSat
    ? interp(cc!.I_A, cc!.T_Nm, NIfrac * p.I0_A) * fL * fTq
    : p.T0_Nm * fN * fL * fI * fConn * fTq;

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
  // Like with like: when the passport carries the loaded FUNDAMENTAL, the drop
  // is fundamental − fundamental and the result is carried back to a waveform
  // peak by the base crest factor (the voltage limit needs the peak); an
  // older passport has peak − peak, exactly as before.
  const vLoadFund = Number(p.Vload0_fund_V ?? 0);
  const hasFund = vLoadFund > 0 && Number(p.Vload0_peak_V ?? 0) > 0;
  const vLoadRef = hasFund ? vLoadFund : Number(p.Vload0_peak_V ?? 0);
  const crest = hasFund ? Number(p.Vload0_peak_V) / vLoadFund : 1;
  const Vdrop0 = vLoadRef > p.Vemf0_peak_V ? vLoadRef - p.Vemf0_peak_V : 0;
  const Vdrop = Vdrop0 > 0
    ? Vdrop0 * fI * fRpm * fN * fN * fL * fConn * fConn
    : R * k.I_A * Math.SQRT2;
  const Vphase = (Vemf + Vdrop) * crest;

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
  // Copper = DC I²R + PROXIMITY.  The stored factor is solved copper over the
  // DC loss of the SAME winding at the passport's own current axis (the LINE
  // current in delta, R0 the equivalent-star R) — so dc·(a − 1) below is the
  // AC watts for star and delta alike.  The floor at 0 only drops numerical
  // noise; a delta passport of extraction_rev < 2 stored a ≈ 1/3 of the true
  // factor and loses its whole AC copper here — regenerate it.  The two must be separated, not lumped into
  // one ratio: the measured grid shows the proximity watts are the SAME at
  // every current (2.8 / 11 / 25 / 44 / 69 / 100 W across 0.5…1.5·I0 on the
  // 85 mm) because they are driven by the rotating MAGNET field, not by the
  // phase current.  A ratio therefore explodes at low current (17× measured)
  // and understates it at high current.  Reconstruct the watts from the
  // stored factor, interpolate THOSE, and scale them by their own physics:
  // proximity ∝ conductors × h³ × stack × f²  (f² is already inside the grid).
  const P_prox = (() => {
    if (!hasGrid) return 0;
    // extraction_rev ≥ 2 stores the AC WATTS themselves (the ratio is
    // ill-conditioned near I = 0); older passports only the ratio.
    const acw = lg!.Pcu_ac_W;
    const rows = (acw && acw.length === lg!.I_A.length)
      ? acw.map((row) => row.map((w) => Math.max(0, Number(w))))
      : (lg!.cuAC && lg!.cuAC.length === lg!.I_A.length)
        ? lg!.cuAC.map((row, r) => {
          const dc = 3 * lg!.I_A[r] * lg!.I_A[r] * p.R0_ohm;
          return row.map((a) => Math.max(0, dc * (a - 1)));
        })
        : null;
    if (!rows) return 0;
    const w = gridLoss(rows);                     // watts at the base winding
    return Math.max(0, w) * fN * fH * fH * fH * fL;
  })();
  // The loss chain is the sine one.  (The old "measured PWM deltas" toggle was
  // removed 2026-10-05: PWM is now only what was COMPUTED for the motor —
  // `pwm_variants`, read by lib/configuratorDrive.ts — and never enters here.)
  const P_cu = P_cu_dc + P_prox;
  const P_fe_t = P_fe;
  const P_mag_t = P_mag;
  const P_loss = P_cu + P_fe_t + P_mag_t;

  // ── HEAT TO REMOVE, per side ─────────────────────────────────────────────
  // The loss grid scales ONE iron number, so the scaled core loss is split by
  // the RATIO the passport measured at its base point.  Everything else is
  // already attributed by construction: copper is stator, magnet/solid is
  // rotor — so the two sides add up to P_loss exactly.
  const feS0 = Number(p.P_fe_stator0_W ?? NaN);
  const feR0 = Number(p.P_fe_rotor0_W ?? NaN);
  const feSplit = Number.isFinite(feS0) && Number.isFinite(feR0) && feS0 + feR0 > 0;
  const feRatio = feSplit ? feS0 / (feS0 + feR0) : 1;
  const P_fe_stator = P_fe_t * feRatio;
  const P_loss_stator = P_cu + P_fe_stator;
  const P_loss_rotor = P_loss - P_loss_stator;

  const eff = P_mech > 0 ? P_mech / (P_mech + P_loss) : 0;
  const mass = p.mass0_kg * fL;

  // No-load KV, the drone-bench convention: rpm per volt of LINE peak EMF —
  // the FUNDAMENTAL on a passport of extraction_rev ≥ 2.
  const VemfLine = Vemf * Math.sqrt(3);
  const KV = VemfLine > 1e-9 ? k.rpm / VemfLine : 0;

  // ── Simulation-card mirror ────────────────────────────────────────────────
  // ψ_PM from the EMF fundamental: E_phase_peak = ω_e·ψ (needs pole pairs).
  const fe = poles && poles > 0 ? (k.rpm / 60) * (poles / 2) : 0;
  const psi = fe > 0 ? (Vemf / (2 * Math.PI * fe)) * 1000 : null;
  // Km on the DC copper loss (same convention as the Simulation tile).
  // Km is quoted on the DC copper loss (the Simulation card's convention —
  // the AC part is speed-dependent and would make Km a function of rpm).
  // Kt / Km / Km-per-mass carry the same torque factor as the torque (owner
  // 2026-09-30), so torque and Kt stay consistent.
  const Tk = T;
  const Km = P_cu_dc > 1e-9 ? Tk / Math.sqrt(P_cu_dc) : 0;
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
    Kt_Nm_per_A: k.I_A > 1e-9 ? Tk / k.I_A : null,
    kt_km_basis: kTmeas != null ? '3-D' : (kEnd != null && kEnd0 != null ? '3-D flux' : '2-D'),
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
