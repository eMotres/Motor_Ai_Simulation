// The Electromagnetic tab's result tiles (owner 2026-10-12: «Сделай точно такую же панель в
// Electromagnetic») — the SAME tile block Configure shows, fed from the FEM run summary (the
// fields SummaryTable.tsx reads).  Pure: no React, no fetch, no i18n — the tips are locale keys
// the renderer resolves, and no value is ever invented: every number here is a field of the run
// summary (or an arithmetic of fields shown on the same tiles, so the tiles add up).
//
// OMISSION RULE: a tile whose value the run lacks is OMITTED (no empty tile, no zero), and a row
// that ends up empty is omitted with it.  That is why a plain magnetostatic run shows no
// temperature or controller tiles at all.
import { tempTileSpec, type TempTileSpec } from './configuratorTiles';
import { tempLimits, judgeTemps, type TempLimits } from './configuratorPropeller';
import { shaftBalance } from './shaftBalance';
import { shownMass, perMass, type MassSummary } from './fullMass';
import type { SixPhaseResult } from '../components/simulation/sixPhase';

/** The run summary, as far as these tiles read it: every field optional, nothing invented. */
export interface EmTileSummary {
  T_em_avg_Nm?: number | null;
  T_ripple_pct?: number | null;
  torque_method?: string | null;
  /** 'motor' | 'generator' — the direction of the power balance (generator numbers are positive) */
  op_mode?: string | null;
  /** the stored electromagnetic efficiency, a fraction (read only when the rotor power is 0) */
  efficiency?: number | null;
  P_mech_W?: number | null;
  P_loss_total_W?: number | null;
  P_core_W?: number | null;
  /** per half (stator / rotor): the iron loss split behind the IRON LOSS tile's tooltip */
  P_core_terms?: Record<string, { hysteresis_W: number; eddy_W: number; excess_W: number; model?: string }> | null;
  /** axial magnet slicing: the factor the 2-D magnet eddy loss was multiplied by */
  magnet_segmentation?: { slice_mm?: number | null; stack_mm?: number | null; width_mm?: number | null;
    factor?: number | null } | null;
  P_stranded_W?: number | null;
  P_mag_W?: number | null;
  P_shaft_W?: number | null;
  P_sleeve_W?: number | null;
  /** imposed-voltage run whose rotor eddy was not solved: magnet / shaft losses are missing */
  solid_loss_not_solved?: boolean | null;
  eddy_settled?: boolean | null;
  P_loss_stator_W?: number | null;
  P_loss_rotor_W?: number | null;
  P_loss_split_measured?: boolean | null;
  loss_density_W_kg?: number | null;
  mass_total_kg?: number | null;
  mass_active_kg?: number | null;
  /** the FULL motor mass of the simulated geometry + the per-mass figures on it, all from the server (lib/fullMass.ts) */
  mass_full_kg?: number | null;
  mass_non_active_kg?: number | null;
  mass_partial?: boolean | null;
  mass_partial_reason?: string | null;
  mass_basis?: string | null;
  mass_scope?: string | null;
  torque_per_mass_Nm_kg?: number | null;
  power_per_mass_W_kg?: number | null;
  torque_per_mass_full_Nm_kg?: number | null;
  power_per_mass_full_W_kg?: number | null;
  Km_per_mass_full_Nm_sqrtW_kg?: number | null;
  loss_density_full_W_kg?: number | null;
  V_phase_peak_V?: number | null;
  V_phase_rms_V?: number | null;
  V_line_peak_V?: number | null;
  V_line_rms_V?: number | null;
  V_line_peak_solved_V?: number | null;
  V_line_rms_solved_V?: number | null;
  star_delta?: string | null;
  J_coil_A_per_mm2?: number | null;
  /** delta: the current in ONE winding (line setpoint / sqrt 3) */
  I_winding_rms_A?: number | null;
  wire_parallel?: number | null;
  wire_split?: number | null;
  turns_per_coil?: number | null;
  n_parallel_eff?: number | null;
  A_phase_mm2?: number | null;
  slot_fill_pct?: number | null;
  R_phase_ohm?: number | null;
  R_line_line_ohm?: number | null;
  coil_temp_C?: number | null;
  I_phase_rms_A?: number | null;
  Ld_mH?: number | null;
  Lq_mH?: number | null;
  Ld_fp_secant_mH?: number | null;
  Lq_fp_secant_mH?: number | null;
  ldq_method?: string | null;
  ldq_tangent_clamped?: boolean | null;
  bench_ldq?: { Ld_mH: number; Lq_mH: number; Lq_over_Ld: number | null } | null;
  saliency_Lq_over_Ld?: number | null;
  B_gap_mean_T?: number | null;
  six_phase?: SixPhaseResult | null;
  psi_pm_Wb?: number | null;
  KV_rpm_per_V_line?: number | null;
  KV_noload_rpm_per_V_line?: number | null;
  Km_Nm_sqrtW?: number | null;
  Km_per_mass_Nm_sqrtW_kg?: number | null;
  rotor_inertia?: { J_kg_cm2?: number | null; J_kg_m2?: number | null } | null;
  demag?: {
    loss_pct?: number | null;
    area_derated_pct?: number | null;
    /** the worst single element — a corner flag, not the magnet's figure */
    br_corner?: { br_pct?: number | null; r_mm?: number | null; theta_deg?: number | null } | null;
    br_worst_pct?: number | null;
  } | null;
  saturation?: { droop_pct?: number | null; T_linear_Nm?: number | null } | null;
  end3d?: { k_flux?: number | null; inherited?: boolean | null; T_corrected_Nm?: number | null;
    V_line_peak_corrected_V?: number | null } | null;
  /** the part masses ("Stator core (B10AHV900M)" — the material is in the name) */
  mass_components?: Array<{
    name: string; mass_kg: number; mass_modelled_kg?: number | null; state?: string | null; note?: string | null;
  }> | null;
  /** the coupled block, loosely typed: only the tiles' own fields are read */
  coupling?: {
    coil_temp_c?: number | null;
    magnet_temp_c?: number | null;
    controller?: {
      t_j_c?: number | null;
      losses?: { total_W?: number | null } | null;
      efficiency?: { inverter?: number | null; shaft?: number | null; wall_to_shaft?: number | null } | null;
      limits_verdict?: string | null;
      thermal?: { t_j_max_c?: number | null } | null;
    } | null;
    time_to_limit?: { limits_c?: Record<string, number> | null } | null;
    continuous_rating?: {
      ok?: boolean | null; feasible?: boolean | null;
      verified?: boolean | null; trustworthy?: boolean | null;
      I_cont_A_rms?: number | null;
      power?: { P_shaft_W?: number | null } | null;
      limits_c?: Record<string, number> | null;
    } | null;
  } | null;
}

/** The view knobs that change a tile's BASIS (never its meaning): the R@25°C rescale the
 *  SummaryTable's R cells carry, and which 3-D factor the torque (so Kt, Km) carries. */
export interface EmTileView {
  /** copper ρ(T) ratio the card is showing (1 = R at the solve temperature) */
  rf?: number;
  /** '3-D' | '3-D flux' when the 3-D correction is applied, null for the raw 2-D solve */
  k3dBasis?: '3-D' | '3-D flux' | null;
  /** The mechanical half of the loss picture (bearings + windage, analytic SKF — NOT the field
   *  solve): the run's own block, else the live fetch for runs stored before it existed.
   *  `extra_W` null = the machine has NO bearing model: the friction is UNKNOWN, not zero.
   *  Absent altogether = the caller has no mechanical block (the tiles then say so). */
  mech?: EmMech | null;
}

export interface EmMech {
  bearings_W: number | null;
  windage_W: number | null;
  /** bearings + windage; null = unknown */
  extra_W: number | null;
  /** the assigned pair, e.g. "2 × 6805-2RS" */
  cards?: string | null;
  temp_C?: number | null;
  gap_W?: number | null;
  faces_W?: number | null;
}

export interface EmTileSpec {
  id: string;
  /** locale key of the label (configure.* family) … */
  labelKey?: string;
  /** …or a quantity symbol that has no locale key: Ld, Lq, ψ_PM */
  label?: string;
  tipKey?: string;
  /** extra tooltip lines (method / basis / provenance), locale keys */
  tipExtraKeys?: string[];
  tipVars?: Record<string, string | number>;
  /** verbatim text the RUN carries (a part's note): appended to the tooltip as it is */
  tipText?: string;
  unit: string;
  d: number;
  value: number | null;
  /** a wider tile (the part-mass row: "Stator core (B10AHV900M)" does not fit one tile) */
  width?: number;
  /** a tile that is ALWAYS shown, as "—", with this locale key as the reason (bearings of a
   *  machine that has none: an unknown loss is never printed as zero and never silently absent) */
  blankTipKey?: string;
  /** printed instead of the number: "> 200" for a temperature over its limit */
  display?: string;
  level?: 'ok' | 'warn' | 'bad';
  labelColor?: string;
  valueColor?: string;
  /** the lead-cable tile renders its own JSX (gauge + conductor Ø) */
  kind?: 'leadCable';
}

/** The width of a part-mass tile: two ordinary tiles and the gap between them (TILE_W of
 *  components/common/ResultTiles.tsx = 132, gap 6), so the row keeps the grid of the rows above. */
export const EM_WIDE_TILE_W = 2 * 132 + 6;

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const _n = (v: number) => String(Number(v.toFixed(0)));
const _n2 = (v: number) => String(Number(v.toFixed(2)));
const _n3 = (v: number | null) => (v == null ? '-' : String(Number(v.toFixed(3))));

/** the tile only when the run has the value (no empty tiles, no zeros) */
const tile = (t: EmTileSpec | null): EmTileSpec | null =>
  (t != null && (t.value != null || t.display != null || t.blankTipKey != null) ? t : null);
const row = (...ts: (EmTileSpec | null)[]): EmTileSpec[] => ts.filter((x): x is EmTileSpec => x != null);

/** The method line behind a measured dq quantity (differential / secant / bench). */
export function ldqMethodKey(s: EmTileSummary, ld: boolean): string {
  if (ld && s.Ld_mH == null) return 'emTiles.mBench';
  if (!ld && s.Lq_mH == null) return 'emTiles.mBench';
  return (s.ldq_method ?? '').startsWith('differential') && !s.ldq_tangent_clamped
    ? 'emTiles.mDiff' : 'emTiles.mSecant';
}

/** The torque-method line (the card's header has it too). */
export function torqueMethodKey(s: EmTileSummary): string | null {
  const m = s.torque_method;
  if (m === 'coulomb_virtual_work') return 'emTiles.mCoulomb';
  if (m === 'maxwell_stress') return 'emTiles.mMaxwell';
  return m ? 'emTiles.mHybrid' : null;
}

/** The temperatures a coupled run's tiles are judged against: the limits the run itself used
 *  (`time_to_limit` / the S1 rating), else the project's — winding 200 °C, magnet = card. */
export function emTempLimits(s: EmTileSummary): TempLimits {
  const lim = s.coupling?.time_to_limit?.limits_c ?? s.coupling?.continuous_rating?.limits_c ?? null;
  return tempLimits({ winding_C: lim?.winding, magnet_C: lim?.magnet });
}

/** The one warning line above the tiles: present only when a temperature is over its limit. */
export function emWarning(s: EmTileSummary): {
  lim: TempLimits; T_winding_C: number | null; T_magnet_C: number | null;
  windingOver: boolean; magnetOver: boolean;
} | null {
  const tw = num(s.coupling?.coil_temp_c), tm = num(s.coupling?.magnet_temp_c);
  if (tw == null && tm == null) return null;
  const lim = emTempLimits(s);
  const v = judgeTemps(tw ?? -Infinity, tm ?? -Infinity, lim);
  return v.over ? { lim, T_winding_C: tw, T_magnet_C: tm, windingOver: v.windingOver, magnetOver: v.magnetOver } : null;
}

/** The tiles of the Electromagnetic tab, in Configure's row order.  Rows that end up empty are
 *  dropped; tiles whose value the run lacks never appear. */
export function emTileRows(s: EmTileSummary, view: EmTileView = {}): EmTileSpec[][] {
  const rfRaw = num(view.rf);
  const rf = rfRaw != null && rfRaw > 0 ? rfRaw : 1;
  const k3d = view.k3dBasis ? [view.k3dBasis === '3-D' ? 'configure.ktTip3d' : 'configure.ktTipFlux'] : ['configure.ktTip2d'];
  const torqueMethod = torqueMethodKey(s);
  const pLoss = num(s.P_loss_total_W);
  const isDelta = String(s.star_delta ?? 'star').toLowerCase().startsWith('d');
  // THE POWER BALANCE AT THE SHAFT (lib/shaftBalance.ts = report.shaft_view): POWER is what leaves
  // the coupling, ELEC POWER what the terminals carry, TOTAL LOSS is ALL losses (electromagnetic +
  // bearings + windage) and EFFICIENCY is the shaft's — ONE efficiency, so the tiles add up:
  // EFFICIENCY = POWER / (POWER + TOTAL LOSS) for a motor.  A machine without a bearing model has an
  // UNKNOWN friction (not zero): POWER is then the rotor T·ω, EFFICIENCY the electromagnetic one,
  // and both say so.
  const mech = view.mech ?? null;
  const bal = shaftBalance(s, mech?.extra_W ?? null);
  const mechKnown = bal?.mechKnown === true;
  const dir = bal?.gen ? '+' : '−';

  // ── 1 torque · power · elec power · mass · efficiency · ripple · densities ──
  const T = num(s.T_em_avg_Nm);
  const etaPct = bal?.eta != null ? 100 * bal.eta : null;
  const partial = s.solid_loss_not_solved === true;       // magnet / shaft eddy not solved → optimistic
  const etaFlag = etaPct != null && (partial || !mechKnown);
  const ripple = num(s.T_ripple_pct);
  // THE MASS IS THE FULL MOTOR MASS (owner 2026-10-20): the server's full mass of the loaded configuration; a geometry that
  // belongs to no configuration with a CAD build shows its active mass, labelled so, never an unlabelled "Mass".
  const ms = shownMass(s as MassSummary);
  const pm = (k: 'torque' | 'power' | 'km' | 'loss') => perMass(s as MassSummary, k);
  const massTile = tile({
    id: 'mass',
    labelKey: ms.kind === 'full' ? 'configure.mass' : ms.kind === 'active-no-cad' ? 'configure.massNoCad' : 'configure.massActive',
    tipKey: ms.kind === 'full' ? 'emTiles.massFullTip' : ms.kind === 'active-no-cad' ? 'emTiles.massNoCadTip' : 'emTiles.massTip',
    tipVars: { active: ms.activeKg != null ? _n2(ms.activeKg) : '—', basis: s.mass_basis ?? '—',
      reason: s.mass_partial_reason ?? '—' },
    unit: 'kg', d: 2, value: ms.kg });
  const r1 = row(
    tile({ id: 'torque', labelKey: 'configure.torque', tipKey: 'emTiles.torqueTip',
      tipExtraKeys: torqueMethod ? [...k3d, torqueMethod] : [...k3d], unit: 'N·m',
      d: T != null && Math.abs(T) < 0.1 ? 4 : 2, value: T }),
    tile({ id: 'power', labelKey: 'configure.power',
      tipKey: mechKnown ? 'emTiles.powerTip' : 'emTiles.powerTipRotor',
      tipVars: { rotor: _n2((bal?.pRotor ?? 0) / 1000), op: dir, mech: _n(mech?.extra_W ?? 0) },
      unit: 'kW', d: 2, value: bal != null ? bal.pShaft / 1000 : null }),
    tile({ id: 'elecPower', labelKey: 'configure.elecPower',
      tipKey: bal?.gen ? 'emTiles.elecPowerTipGen' : 'emTiles.elecPowerTipMotor',
      tipExtraKeys: mechKnown ? undefined : ['emTiles.elecPowerNoMech'], unit: 'kW', d: 2,
      value: bal?.pElec != null ? bal.pElec / 1000 : null }),
    massTile,
    tile({ id: 'efficiency', labelKey: 'configure.efficiency',
      tipKey: mechKnown ? 'emTiles.efficiencyTip' : 'emTiles.efficiencyTipNoMech',
      tipVars: { em: bal?.etaEm != null ? _n2(100 * bal.etaEm) : '—' },
      tipExtraKeys: partial ? ['emTiles.partial'] : undefined, unit: '%', d: 2,
      value: etaPct, display: etaPct != null && etaFlag ? `${etaPct.toFixed(2)}*` : undefined,
      level: etaPct == null ? undefined : etaFlag ? 'warn' : etaPct >= 92 ? 'ok' : etaPct >= 85 ? 'warn' : 'bad' }),
    tile({ id: 'tRipple', labelKey: 'configure.tRipple', tipKey: 'emTiles.tRippleTip',
      tipExtraKeys: torqueMethod ? [torqueMethod] : undefined, unit: '%', d: 1, value: ripple,
      level: ripple == null ? undefined : ripple <= 5 ? 'ok' : ripple <= 15 ? 'warn' : 'bad' }),
    tile({ id: 'tPerMass', labelKey: pm('torque').full ? 'configure.tPerMass' : 'configure.tPerMassActive',
      tipKey: pm('torque').full ? 'emTiles.tPerMassFullTip' : 'emTiles.tPerMassTip', unit: 'N·m/kg', d: 2,
      value: pm('torque').value }),
    tile({ id: 'pPerMass', labelKey: pm('power').full ? 'configure.pPerMass' : 'configure.pPerMassActive',
      tipKey: pm('power').full ? 'emTiles.pPerMassFullTip' : 'emTiles.pPerMassTip', unit: 'kW/kg', d: 2,
      value: pm('power').value != null ? (pm('power').value as number) / 1000 : null }),
  );

  // ── 2 total loss · iron · copper · magnet · shaft · sleeve · bearings · windage · heat · density · controller ──
  const ctl = s.coupling?.controller ?? null;
  const tj = num(ctl?.t_j_c), tjMax = num(ctl?.thermal?.t_j_max_c);
  const s1 = s.coupling?.continuous_rating ?? null;
  const s1p = num(s1?.power?.P_shaft_W);
  const s1Verified = s1 != null && s1.ok === true && s1.verified === true
    && s1.feasible !== false && s1.trustworthy !== false;
  const splitUnknown = s.P_loss_split_measured === false;
  const notSolved = s.solid_loss_not_solved === true;
  // the iron loss split behind the tooltip, per half (empty on a run before the split existed)
  const half = (k: string) => {
    const v = s.P_core_terms?.[k];
    return v ? { tot: _n2(v.hysteresis_W + v.eddy_W + v.excess_W), h: _n2(v.hysteresis_W), e: _n2(v.eddy_W), x: _n2(v.excess_W) } : null;
  };
  const hs = half('stator'), hr = half('rotor');
  // the loss model behind it: the manufacturer's MEASURED P(B, f) table, else the fitted Bertotti terms
  const ironSurface = Object.values(s.P_core_terms ?? {}).some((v) => v.model === 'measured_surface');
  const seg = s.magnet_segmentation ?? null;
  const segSliced = num(seg?.factor) != null && (num(seg?.slice_mm) ?? 0) > 0 && (num(seg?.factor) as number) < 0.999;
  const unsettled = s.eddy_settled === false;
  const wall = num(ctl?.efficiency?.wall_to_shaft);
  const r2 = row(
    // ALL losses: electromagnetic + bearings + windage when the friction is known
    tile({ id: 'totalLoss', labelKey: 'configure.totalLoss',
      tipKey: mechKnown ? 'emTiles.totalLossTip' : 'emTiles.totalLossTipEm',
      tipVars: { em: _n(pLoss ?? 0), mech: _n(mech?.extra_W ?? 0) }, unit: 'W', d: 0,
      value: bal?.lossAll ?? pLoss }),
    tile({ id: 'ironLoss', labelKey: 'configure.ironLoss', tipKey: 'emTiles.ironLossTip',
      tipExtraKeys: hs || hr ? [ironSurface ? 'emTiles.ironMeasured' : 'emTiles.ironBertotti',
        ...(hs ? ['emTiles.ironSplitStator'] : []), ...(hr ? ['emTiles.ironSplitRotor'] : [])] : undefined,
      tipVars: { st: hs?.tot ?? '-', sh: hs?.h ?? '-', se: hs?.e ?? '-', sx: hs?.x ?? '-',
        rt: hr?.tot ?? '-', rh: hr?.h ?? '-', re: hr?.e ?? '-', rx: hr?.x ?? '-' },
      unit: 'W', d: 0, value: num(s.P_core_W) }),
    tile({ id: 'copperLoss', labelKey: 'configure.copperLoss', tipKey: 'emTiles.copperLossTip', unit: 'W', d: 0, value: num(s.P_stranded_W) }),
    tile({ id: 'magnetLoss', labelKey: 'configure.magnetLoss', tipKey: 'emTiles.magnetLossTip',
      tipExtraKeys: [...(segSliced ? ['emTiles.magnetSeg'] : []), ...(unsettled && !notSolved ? ['emTiles.eddyUnsettled'] : [])],
      tipVars: { slice: num(seg?.slice_mm) ?? '-', stack: num(seg?.stack_mm) ?? '-', width: num(seg?.width_mm) ?? '-',
        factor: num(seg?.factor) != null ? (num(seg?.factor) as number).toPrecision(3) : '-' },
      unit: 'W', d: 0,
      value: notSolved ? null : num(s.P_mag_W), blankTipKey: notSolved ? 'emTiles.notSolved' : undefined,
      level: unsettled && !notSolved && num(s.P_mag_W) != null ? 'warn' : undefined }),
    tile({ id: 'shaftLoss', labelKey: 'configure.shaftLoss', tipKey: 'emTiles.shaftLossTip', unit: 'W', d: 1,
      value: num(s.P_shaft_W) }),
    tile({ id: 'sleeveLoss', labelKey: 'configure.sleeveLoss', tipKey: 'emTiles.sleeveLossTip', unit: 'W', d: 4,
      value: num(s.P_sleeve_W) }),
    // the mechanical half (analytic, never from the field solve): an unknown loss is "-", not 0
    ...(mech != null ? [
      tile({ id: 'bearings', labelKey: 'configure.bearings', tipKey: 'emTiles.bearingsTip',
        tipVars: { cards: mech.cards || '-', temp: mech.temp_C != null ? _n(mech.temp_C) : '-' },
        unit: 'W', d: 0, value: mech.extra_W != null ? mech.bearings_W : null,
        blankTipKey: mech.extra_W != null ? undefined : 'emTiles.noBearings' }),
      tile({ id: 'windage', labelKey: 'configure.windage', tipKey: 'emTiles.windageTip',
        tipVars: { gap: mech.gap_W != null ? _n2(mech.gap_W) : '-', faces: mech.faces_W != null ? _n2(mech.faces_W) : '-' },
        unit: 'W', d: 2, value: mech.windage_W,
        blankTipKey: mech.windage_W != null ? undefined : 'emTiles.noBearings' }),
    ] : []),
    tile({ id: 'statorHeat', labelKey: 'configure.statorHeat', tipKey: 'emTiles.statorHeatTip',
      tipExtraKeys: splitUnknown ? ['emTiles.splitUnknown'] : undefined, unit: 'W', d: 0, value: num(s.P_loss_stator_W) }),
    tile({ id: 'rotorHeat', labelKey: 'configure.rotorHeat', tipKey: 'emTiles.rotorHeatTip',
      tipExtraKeys: splitUnknown ? ['emTiles.splitUnknown'] : undefined, unit: 'W', d: 0, value: num(s.P_loss_rotor_W),
      level: splitUnknown ? 'warn' : undefined }),
    tile({ id: 'lossDensity', labelKey: pm('loss').full ? 'configure.lossDensity' : 'configure.lossDensityActive',
      tipKey: pm('loss').full ? 'emTiles.lossDensityFullTip' : 'emTiles.lossDensityTip', unit: 'W/kg', d: 0,
      value: pm('loss').value }),
    tile({ id: 'invLoss', labelKey: 'configureDrive.invLoss', tipKey: 'emTiles.ctrlLossTip', unit: 'W', d: 1,
      value: num(ctl?.losses?.total_W) }),
    tile({ id: 'tj', labelKey: 'configureDrive.tj', tipKey: 'emTiles.tjTip', unit: '°C', d: 0, value: tj,
      level: tj == null ? undefined : tjMax != null && tj > tjMax - 25 ? 'warn' : 'ok' }),
    tile({ id: 'motorEff', labelKey: 'configureDrive.motorEff', tipKey: 'emTiles.motorEffTip', unit: '%', d: 1,
      value: num(ctl?.efficiency?.shaft) != null ? (num(ctl?.efficiency?.shaft) as number) * 100 : null }),
    // inverter eta x shaft eta: a DIFFERENT quantity from the shaft efficiency, never folded into it
    tile({ id: 'wallToShaft', labelKey: 'configure.wallToShaft', tipKey: 'emTiles.wallToShaftTip', unit: '%', d: 2,
      value: wall != null ? wall * 100 : null, level: ctl?.limits_verdict === 'fail' ? 'bad' : undefined }),
    tile({ id: 'pContMax', labelKey: 'configureDrive.pContMax', tipKey: 'emTiles.pContTip',
      tipExtraKeys: s1p != null && !s1Verified ? ['emTiles.unverified'] : undefined, unit: 'kW', d: 2,
      value: s1p != null ? s1p / 1000 : null, level: s1p == null ? undefined : s1Verified ? 'ok' : 'warn' }),
  );

  // ── 3 the coupled temperatures (a coupled run only) ───────────────────────
  const lim = emTempLimits(s);
  const tempEmTile = (id: string, labelKey: string, tipKey: string, T: number | null, limit: number): EmTileSpec => {
    const t = tempTileSpec(id, labelKey, tipKey, T, limit);
    return { id: t.id, labelKey: t.labelKey, tipKey: t.tipKey, tipVars: { limit: _n(limit) },
      unit: t.unit, d: t.d, value: t.value, display: t.display, level: t.level };
  };
  const r3 = row(
    tile(tempEmTile('tWinding', 'configurePropeller.tWinding', 'configurePropeller.tWindingTip',
      num(s.coupling?.coil_temp_c), lim.winding_C)),
    tile(tempEmTile('tMagnet', 'configurePropeller.tMagnet', 'configurePropeller.tMagnetTip',
      num(s.coupling?.magnet_temp_c), lim.magnet_C)),
  );

  // ── 4 voltages + current density ──────────────────────────────────────────
  const vLinePeak = num(s.V_line_peak_solved_V) ?? num(s.V_line_peak_V);
  const r4 = row(
    tile({ id: 'dcBusMin', labelKey: 'configure.dcBusMin', tipKey: 'emTiles.dcBusMinTip', unit: 'V', d: 0, value: vLinePeak }),
    tile({ id: 'vLinePeak', labelKey: 'configure.vLinePeak', tipKey: 'emTiles.vLinePeakTip', unit: 'V', d: 1, value: vLinePeak }),
    tile({ id: 'vPhasePeak', labelKey: 'configure.vPhasePeak', tipKey: 'emTiles.vPhasePeakTip', unit: 'V', d: 1,
      value: num(s.V_phase_peak_V) }),
    tile({ id: 'vLineRms', labelKey: 'configure.vLineRms', tipKey: 'emTiles.vLineRmsTip', unit: 'V', d: 1,
      value: num(s.V_line_rms_solved_V) ?? (isDelta ? null : num(s.V_line_rms_V)) }),
    tile({ id: 'vPhaseRms', labelKey: 'configure.vPhaseRms', tipKey: 'emTiles.vPhaseRmsTip', unit: 'V', d: 1,
      value: num(s.V_phase_rms_V) }),
    tile({ id: 'iWinding', labelKey: 'configure.iWinding', tipKey: 'emTiles.iWindingTip', unit: 'A', d: 1,
      value: isDelta ? num(s.I_winding_rms_A) : null, level: 'warn' }),
    tile({ id: 'currDensity', labelKey: 'configure.currDensity', tipKey: 'emTiles.currDensityTip', unit: 'A/mm²', d: 1,
      value: num(s.J_coil_A_per_mm2),
      level: num(s.J_coil_A_per_mm2) == null ? undefined
        : (num(s.J_coil_A_per_mm2) as number) <= 12 ? 'ok' : (num(s.J_coil_A_per_mm2) as number) <= 25 ? 'warn' : 'bad' }),
  );

  // ── 5 phase section · fill factor · lead cable · resistances ──────────────
  const aPh = num(s.A_phase_mm2);
  const tCoil = num(s.coil_temp_C);
  const rBasis = rf !== 1 ? 'emTiles.rAt25' : 'emTiles.rAtSolve';
  const rVars = { t: tCoil != null ? _n(tCoil) : '—' };
  const r5 = row(
    tile({ id: 'phaseSection', labelKey: 'configure.phaseSection', tipKey: 'emTiles.phaseSectionTip', unit: 'mm²', d: 2, value: aPh }),
    tile({ id: 'fillFactor', labelKey: 'configure.fillFactor', tipKey: 'emTiles.fillFactorTip', unit: '%', d: 1,
      value: num(s.slot_fill_pct),
      level: num(s.slot_fill_pct) == null ? undefined
        : (num(s.slot_fill_pct) as number) <= 60 ? 'ok' : (num(s.slot_fill_pct) as number) <= 75 ? 'warn' : 'bad' }),
    aPh != null ? { id: 'leadCable', labelKey: 'configure.leadCable', unit: 'mm²', d: 2, value: aPh, kind: 'leadCable' as const } : null,
    tile({ id: 'rPhase', labelKey: 'configure.rPhase', tipKey: 'configure.rPhaseTip', tipExtraKeys: [rBasis], tipVars: rVars,
      unit: 'mΩ', d: 1, value: num(s.R_phase_ohm) != null ? (num(s.R_phase_ohm) as number) * 1000 * rf : null }),
    tile({ id: 'rLineLine', labelKey: 'configure.rLineLine', tipKey: 'configure.rLineLineTip', tipExtraKeys: [rBasis], tipVars: rVars,
      unit: 'mΩ', d: 1, value: num(s.R_line_line_ohm) != null ? (num(s.R_line_line_ohm) as number) * 1000 * rf : null }),
    // only when the winding is wound several wires in hand / split into strips (else = the plain turns)
    (num(s.wire_parallel) ?? 1) > 1 || (num(s.wire_split) ?? 1) > 1
      ? tile({ id: 'turnsCoil', labelKey: 'configure.turnsCoil', tipKey: 'emTiles.turnsCoilTip',
        tipVars: { hand: num(s.wire_parallel) ?? 1, strips: num(s.wire_split) ?? 1, paths: num(s.n_parallel_eff) ?? '-' },
        unit: '', d: 0, value: num(s.turns_per_coil) })
      : null,
  );

  // ── 6 Ld · Lq · ψ_PM · Lq/Ld — each tile carries its own method ───────────
  const ld = num(s.Ld_mH) ?? num(s.bench_ldq?.Ld_mH);
  const lq = num(s.Lq_mH) ?? num(s.bench_ldq?.Lq_mH);
  const sal = num(s.saliency_Lq_over_Ld) ?? num(s.bench_ldq?.Lq_over_Ld);
  const salMethod = num(s.saliency_Lq_over_Ld) != null ? ldqMethodKey(s, true) : 'emTiles.mBench';
  const set1 = (s.six_phase?.sets ?? []).find((x) => x.set === 1);
  const r6 = row(
    tile({ id: 'ld', label: 'Ld', tipKey: 'emTiles.ldTip', unit: 'mH', d: 3, value: ld,
      tipExtraKeys: [ldqMethodKey(s, true), ...(num(s.Ld_fp_secant_mH) != null ? ['emTiles.secantAlt'] : []),
        ...(num(s.Ld_mH) != null && num(s.bench_ldq?.Ld_mH) != null ? ['emTiles.benchAlt'] : [])],
      tipVars: { sec: _n3(num(s.Ld_fp_secant_mH)), bench: _n3(num(s.bench_ldq?.Ld_mH)) } }),
    tile({ id: 'lq', label: 'Lq', tipKey: 'emTiles.lqTip', unit: 'mH', d: 3, value: lq,
      tipExtraKeys: [ldqMethodKey(s, false), ...(num(s.Lq_fp_secant_mH) != null ? ['emTiles.secantAlt'] : []),
        ...(num(s.Lq_mH) != null && num(s.bench_ldq?.Lq_mH) != null ? ['emTiles.benchAlt'] : [])],
      tipVars: { sec: _n3(num(s.Lq_fp_secant_mH)), bench: _n3(num(s.bench_ldq?.Lq_mH)) } }),
    tile({ id: 'psiPm', label: 'ψ_PM', tipKey: 'emTiles.psiTip', unit: 'mWb', d: 2,
      value: num(s.psi_pm_Wb) != null ? (num(s.psi_pm_Wb) as number) * 1000 : null }),
    tile({ id: 'lqLd', labelKey: 'configure.lqLd', tipKey: 'configure.lqLdTip', tipExtraKeys: [salMethod],
      unit: '', d: 2, value: sal }),
    tile({ id: 'bGapMean', labelKey: 'configure.bGapMean', tipKey: 'emTiles.bGapMeanTip', unit: 'T', d: 3,
      value: num(s.B_gap_mean_T) }),
    // six-phase winding: the x-y inductance and what each of the two sets carries
    tile({ id: 'lxy', labelKey: 'configure.lxy', tipKey: 'emTiles.lxyTip', unit: 'mH', d: 4,
      value: num(s.six_phase?.inductances?.Lxy_mH) }),
    tile({ id: 'iSet', labelKey: 'configure.iSet', tipKey: 'emTiles.perSetTip', unit: 'A', d: 1,
      value: num(set1?.I_phase_rms_A) }),
    tile({ id: 'vSet', labelKey: 'configure.vSet', tipKey: 'emTiles.perSetTip', unit: 'V', d: 1,
      value: num(set1?.V1_phase_peak_V) }),
  );

  // ── 7 KV · Kt · Km · Km/mass (the controller-facing constants, in blue) ───
  const kvNl = num(s.KV_noload_rpm_per_V_line);
  const kt = num(s.T_em_avg_Nm) != null && (num(s.I_phase_rms_A) ?? 0) > 0
    ? (num(s.T_em_avg_Nm) as number) / (num(s.I_phase_rms_A) as number) : null;
  const blue = { labelColor: '#60a5fa', valueColor: '#60a5fa' } as const;
  const r7 = row(
    tile({ id: 'kv', labelKey: 'configure.kvNoLoad', tipKey: kvNl != null ? 'emTiles.kvTip' : 'emTiles.kvTipLoaded',
      unit: 'rpm/V', d: 1, value: kvNl ?? num(s.KV_rpm_per_V_line), ...blue }),
    tile({ id: 'kt', labelKey: 'configure.kt', tipKey: 'emTiles.ktTip', tipExtraKeys: k3d, unit: 'N·m/A',
      d: kt != null && Math.abs(kt) < 0.1 ? 4 : 3, value: kt, ...blue }),
    tile({ id: 'km', labelKey: 'configure.km', tipKey: 'emTiles.kmTip', tipExtraKeys: k3d, unit: 'N·m/√W', d: 3,
      value: num(s.Km_Nm_sqrtW), ...blue }),
    tile({ id: 'kmPerMass', labelKey: pm('km').full ? 'configure.kmPerMass' : 'configure.kmPerMassActive',
      tipKey: pm('km').full ? 'emTiles.kmPerMassFullTip' : 'emTiles.kmPerMassTip', tipExtraKeys: k3d,
      unit: 'N·m/(√W·kg)', d: 3, value: pm('km').value, ...blue }),
    tile({ id: 'rotorInertia', labelKey: 'configure.rotorInertia', tipKey: 'emTiles.rotorInertiaTip', unit: 'kg·cm²', d: 2,
      value: num(s.rotor_inertia?.J_kg_cm2) }),
  );

  // ── 8 demag · saturation · total (Configure's product convention) ─────────
  const dmLoss = num(s.demag?.loss_pct), droop = num(s.saturation?.droop_pct);
  const dmKept = dmLoss != null ? 100 - dmLoss : null;
  const corner = num(s.demag?.br_corner?.br_pct) ?? num(s.demag?.br_worst_pct);
  const k3dFlux = num(s.end3d?.k_flux);
  const k3dInh = s.end3d?.inherited === true;
  const satKept = droop != null ? Math.min(100, 100 - droop) : null;
  const r8 = row(
    tile({ id: 'demagKoef', labelKey: 'configure.demagKoef', tipKey: 'configure.demagKoefTip',
      tipExtraKeys: num(s.demag?.area_derated_pct) != null ? ['emTiles.fromRun', 'emTiles.demagArea'] : ['emTiles.fromRun'],
      tipVars: { area: num(s.demag?.area_derated_pct) != null ? _n2(num(s.demag?.area_derated_pct) as number) : '-' },
      unit: '%', d: 2, value: dmKept,
      level: dmLoss == null ? undefined : dmLoss < 1 ? 'ok' : dmLoss < 5 ? 'warn' : 'bad' }),
    tile({ id: 'brCorner', labelKey: 'configure.brCorner', tipKey: 'emTiles.brCornerTip', unit: '%', d: 1,
      tipExtraKeys: num(s.demag?.br_corner?.r_mm) != null && num(s.demag?.br_corner?.theta_deg) != null
        ? ['emTiles.brCornerAt'] : undefined,
      tipVars: { r: _n2(num(s.demag?.br_corner?.r_mm) ?? 0), th: _n2(num(s.demag?.br_corner?.theta_deg) ?? 0) },
      value: corner }),
    tile({ id: 'saturationKoef', labelKey: 'configure.saturationKoef', tipKey: 'configure.saturationKoefTip',
      tipExtraKeys: num(s.saturation?.T_linear_Nm) != null ? ['emTiles.fromRun', 'emTiles.satLinear'] : ['emTiles.fromRun'],
      tipVars: { tlin: num(s.saturation?.T_linear_Nm) != null ? _n2(num(s.saturation?.T_linear_Nm) as number) : '-' },
      unit: '%', d: 1, value: satKept,
      level: droop == null ? undefined : droop < 5 ? 'ok' : droop < 15 ? 'warn' : 'bad' }),
    tile({ id: 'totalKoef', labelKey: 'configure.totalKoef', tipKey: 'configure.totalKoefTip', tipExtraKeys: ['emTiles.fromRun'],
      unit: '%', d: 1,
      value: dmKept != null && satKept != null ? (dmKept * satKept) / 100 : null,
      level: dmKept == null || satKept == null ? undefined
        : (dmKept * satKept) / 100 > 95 ? 'ok' : (dmKept * satKept) / 100 > 85 ? 'warn' : 'bad' }),
    tile({ id: 'end3d', labelKey: 'configure.end3d', tipKey: 'emTiles.end3dTip',
      tipExtraKeys: [...(num(s.end3d?.T_corrected_Nm) != null ? ['emTiles.end3dCorrected'] : []),
        ...(k3dInh ? ['emTiles.end3dInherited'] : [])],
      tipVars: { k: k3dFlux != null ? k3dFlux.toFixed(3) : '-',
        t: num(s.end3d?.T_corrected_Nm) != null ? _n2(num(s.end3d?.T_corrected_Nm) as number) : '-' },
      unit: '', d: 3, value: k3dFlux,
      display: k3dFlux != null ? `×${k3dFlux.toFixed(3)}` : undefined,
      level: k3dFlux == null ? undefined : k3dInh ? 'warn' : k3dFlux > 0.97 ? 'ok' : k3dFlux > 0.93 ? 'warn' : 'bad' }),
  );

  // ── 9 the part masses, "<part> (<material>)  <kg> · <share>": the last row, the same tiles ──
  const total = num(s.mass_total_kg);
  const r9 = row(...(s.mass_components ?? []).map((c, i): EmTileSpec | null => {
    // a `reference` part is the customer's (a frameless motor's shaft): it is in the field and in the
    // losses but not in this mass, so the tile stays and says what it was modelled at, not "billed"
    if (c.state === 'reference') {
      return tile({ id: `part${i}`, label: c.name, tipKey: 'emTiles.partRefTip', tipText: c.note ?? undefined,
        unit: 'kg modelled', d: 3,
        value: num(c.mass_modelled_kg), width: EM_WIDE_TILE_W, labelColor: '#38bdf8' });
    }
    const m = num(c.mass_kg);
    if (m == null) return null;
    return tile({ id: `part${i}`, label: c.name, tipKey: 'emTiles.partTip', tipText: c.note ?? undefined,
      unit: total != null && total > 0 ? `kg · ${(100 * m / total).toFixed(1)} %` : 'kg', d: 3, value: m,
      width: EM_WIDE_TILE_W });
  }));

  return [r1, r2, r3, r4, r5, r6, r7, r8, r9].filter((r) => r.length > 0);
}

/** What the lead-cable tile needs (the renderer turns it into its own JSX). */
export const leadCableSection = (s: EmTileSummary): number | null => num(s.A_phase_mm2);

/** tempTileSpec, re-exported: the one judge both panels use. */
export type { TempTileSpec };
export { tempTileSpec };
