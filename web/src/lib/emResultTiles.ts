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

/** The run summary, as far as these tiles read it: every field optional, nothing invented. */
export interface EmTileSummary {
  T_em_avg_Nm?: number | null;
  T_ripple_pct?: number | null;
  torque_method?: string | null;
  P_mech_W?: number | null;
  P_loss_total_W?: number | null;
  P_core_W?: number | null;
  P_stranded_W?: number | null;
  P_mag_W?: number | null;
  P_loss_stator_W?: number | null;
  P_loss_rotor_W?: number | null;
  P_loss_split_measured?: boolean | null;
  loss_density_W_kg?: number | null;
  mass_total_kg?: number | null;
  mass_active_kg?: number | null;
  torque_per_mass_Nm_kg?: number | null;
  power_per_mass_W_kg?: number | null;
  V_phase_peak_V?: number | null;
  V_phase_rms_V?: number | null;
  V_line_peak_V?: number | null;
  V_line_rms_V?: number | null;
  V_line_peak_solved_V?: number | null;
  V_line_rms_solved_V?: number | null;
  star_delta?: string | null;
  J_coil_A_per_mm2?: number | null;
  A_phase_mm2?: number | null;
  slot_fill_pct?: number | null;
  R_phase_ohm?: number | null;
  R_line_line_ohm?: number | null;
  coil_temp_C?: number | null;
  I_phase_rms_A?: number | null;
  Ld_mH?: number | null;
  Lq_mH?: number | null;
  ldq_method?: string | null;
  ldq_tangent_clamped?: boolean | null;
  bench_ldq?: { Ld_mH: number; Lq_mH: number; Lq_over_Ld: number | null } | null;
  saliency_Lq_over_Ld?: number | null;
  psi_pm_Wb?: number | null;
  KV_rpm_per_V_line?: number | null;
  KV_noload_rpm_per_V_line?: number | null;
  Km_Nm_sqrtW?: number | null;
  Km_per_mass_Nm_sqrtW_kg?: number | null;
  demag?: { loss_pct?: number | null } | null;
  saturation?: { droop_pct?: number | null } | null;
  /** the coupled block, loosely typed: only the tiles' own fields are read */
  coupling?: {
    coil_temp_c?: number | null;
    magnet_temp_c?: number | null;
    controller?: {
      t_j_c?: number | null;
      losses?: { total_W?: number | null } | null;
      efficiency?: { inverter?: number | null; shaft?: number | null } | null;
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
  unit: string;
  d: number;
  value: number | null;
  /** printed instead of the number: "> 200" for a temperature over its limit */
  display?: string;
  level?: 'ok' | 'warn' | 'bad';
  labelColor?: string;
  valueColor?: string;
  /** the lead-cable tile renders its own JSX (gauge + conductor Ø) */
  kind?: 'leadCable';
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const _n = (v: number) => String(Number(v.toFixed(0)));
const _n2 = (v: number) => String(Number(v.toFixed(2)));

/** the tile only when the run has the value (no empty tiles, no zeros) */
const tile = (t: EmTileSpec | null): EmTileSpec | null => (t != null && (t.value != null || t.display != null) ? t : null);
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
  const pMech = num(s.P_mech_W), pLoss = num(s.P_loss_total_W);
  const pMechAbs = pMech != null ? Math.abs(pMech) : null;
  const isDelta = String(s.star_delta ?? 'star').toLowerCase().startsWith('d');

  // ── 1 torque · power · mass · efficiency · ripple · densities ─────────────
  // EFFICIENCY = POWER / (POWER + TOTAL LOSS), from the very numbers these tiles show
  // (Configure's identity, so the tiles add up in this panel too).
  const T = num(s.T_em_avg_Nm);
  const eff = pMechAbs != null && pLoss != null && pMechAbs > 0 ? (100 * pMechAbs) / (pMechAbs + pLoss) : null;
  const ripple = num(s.T_ripple_pct);
  const r1 = row(
    tile({ id: 'torque', labelKey: 'configure.torque', tipKey: 'emTiles.torqueTip',
      tipExtraKeys: torqueMethod ? [...k3d, torqueMethod] : [...k3d], unit: 'N·m',
      d: T != null && Math.abs(T) < 0.1 ? 4 : 2, value: T }),
    tile({ id: 'power', labelKey: 'configure.power', tipKey: 'emTiles.powerTip', unit: 'kW', d: 2,
      value: pMechAbs != null ? pMechAbs / 1000 : null }),
    tile({ id: 'mass', labelKey: 'configure.mass', tipKey: 'emTiles.massTip',
      tipVars: { active: num(s.mass_active_kg) != null ? _n2(num(s.mass_active_kg) as number) : '—' }, unit: 'kg', d: 2,
      value: num(s.mass_total_kg) ?? num(s.mass_active_kg) }),
    tile({ id: 'efficiency', labelKey: 'configure.efficiency', tipKey: 'emTiles.efficiencyTip', unit: '%', d: 1,
      value: eff, level: eff == null ? undefined : eff >= 92 ? 'ok' : eff >= 85 ? 'warn' : 'bad' }),
    tile({ id: 'tRipple', labelKey: 'configure.tRipple', tipKey: 'emTiles.tRippleTip',
      tipExtraKeys: torqueMethod ? [torqueMethod] : undefined, unit: '%', d: 1, value: ripple,
      level: ripple == null ? undefined : ripple <= 5 ? 'ok' : ripple <= 15 ? 'warn' : 'bad' }),
    tile({ id: 'tPerMass', labelKey: 'configure.tPerMass', tipKey: 'emTiles.tPerMassTip', unit: 'N·m/kg', d: 2,
      value: num(s.torque_per_mass_Nm_kg) }),
    tile({ id: 'pPerMass', labelKey: 'configure.pPerMass', tipKey: 'emTiles.pPerMassTip', unit: 'kW/kg', d: 2,
      value: num(s.power_per_mass_W_kg) != null ? (num(s.power_per_mass_W_kg) as number) / 1000 : null }),
  );

  // ── 2 total loss · iron · copper · magnet · stator/rotor heat · loss density · controller ──
  const ctl = s.coupling?.controller ?? null;
  const tj = num(ctl?.t_j_c), tjMax = num(ctl?.thermal?.t_j_max_c);
  const s1 = s.coupling?.continuous_rating ?? null;
  const s1p = num(s1?.power?.P_shaft_W);
  const s1Verified = s1 != null && s1.ok === true && s1.verified === true
    && s1.feasible !== false && s1.trustworthy !== false;
  const splitUnknown = s.P_loss_split_measured === false;
  const r2 = row(
    tile({ id: 'totalLoss', labelKey: 'configure.totalLoss', tipKey: 'emTiles.totalLossTip', unit: 'W', d: 0, value: pLoss }),
    tile({ id: 'ironLoss', labelKey: 'configure.ironLoss', tipKey: 'emTiles.ironLossTip', unit: 'W', d: 0, value: num(s.P_core_W) }),
    tile({ id: 'copperLoss', labelKey: 'configure.copperLoss', tipKey: 'emTiles.copperLossTip', unit: 'W', d: 0, value: num(s.P_stranded_W) }),
    tile({ id: 'magnetLoss', labelKey: 'configure.magnetLoss', tipKey: 'emTiles.magnetLossTip', unit: 'W', d: 0, value: num(s.P_mag_W) }),
    tile({ id: 'statorHeat', labelKey: 'configure.statorHeat', tipKey: 'emTiles.statorHeatTip',
      tipExtraKeys: splitUnknown ? ['emTiles.splitUnknown'] : undefined, unit: 'W', d: 0, value: num(s.P_loss_stator_W) }),
    tile({ id: 'rotorHeat', labelKey: 'configure.rotorHeat', tipKey: 'emTiles.rotorHeatTip',
      tipExtraKeys: splitUnknown ? ['emTiles.splitUnknown'] : undefined, unit: 'W', d: 0, value: num(s.P_loss_rotor_W),
      level: splitUnknown ? 'warn' : undefined }),
    tile({ id: 'lossDensity', labelKey: 'configure.lossDensity', tipKey: 'emTiles.lossDensityTip', unit: 'W/kg', d: 0,
      value: num(s.loss_density_W_kg) }),
    tile({ id: 'invLoss', labelKey: 'configureDrive.invLoss', tipKey: 'emTiles.ctrlLossTip', unit: 'W', d: 1,
      value: num(ctl?.losses?.total_W) }),
    tile({ id: 'tj', labelKey: 'configureDrive.tj', tipKey: 'emTiles.tjTip', unit: '°C', d: 0, value: tj,
      level: tj == null ? undefined : tjMax != null && tj > tjMax - 25 ? 'warn' : 'ok' }),
    tile({ id: 'motorEff', labelKey: 'configureDrive.motorEff', tipKey: 'emTiles.motorEffTip', unit: '%', d: 1,
      value: num(ctl?.efficiency?.shaft) != null ? (num(ctl?.efficiency?.shaft) as number) * 100 : null }),
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
  );

  // ── 6 Ld · Lq · ψ_PM · Lq/Ld — each tile carries its own method ───────────
  const ld = num(s.Ld_mH) ?? num(s.bench_ldq?.Ld_mH);
  const lq = num(s.Lq_mH) ?? num(s.bench_ldq?.Lq_mH);
  const sal = num(s.saliency_Lq_over_Ld) ?? num(s.bench_ldq?.Lq_over_Ld);
  const salMethod = num(s.saliency_Lq_over_Ld) != null ? ldqMethodKey(s, true) : 'emTiles.mBench';
  const r6 = row(
    tile({ id: 'ld', label: 'Ld', tipKey: 'emTiles.ldTip', tipExtraKeys: [ldqMethodKey(s, true)], unit: 'mH', d: 3, value: ld }),
    tile({ id: 'lq', label: 'Lq', tipKey: 'emTiles.lqTip', tipExtraKeys: [ldqMethodKey(s, false)], unit: 'mH', d: 3, value: lq }),
    tile({ id: 'psiPm', label: 'ψ_PM', tipKey: 'emTiles.psiTip', unit: 'mWb', d: 2,
      value: num(s.psi_pm_Wb) != null ? (num(s.psi_pm_Wb) as number) * 1000 : null }),
    tile({ id: 'lqLd', labelKey: 'configure.lqLd', tipKey: 'configure.lqLdTip', tipExtraKeys: [salMethod],
      unit: '', d: 2, value: sal }),
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
    tile({ id: 'kmPerMass', labelKey: 'configure.kmPerMass', tipKey: 'emTiles.kmPerMassTip', tipExtraKeys: k3d,
      unit: 'N·m/(√W·kg)', d: 3, value: num(s.Km_per_mass_Nm_sqrtW_kg), ...blue }),
  );

  // ── 8 demag · saturation · total (Configure's product convention) ─────────
  const dmLoss = num(s.demag?.loss_pct), droop = num(s.saturation?.droop_pct);
  const dmKept = dmLoss != null ? 100 - dmLoss : null;
  const satKept = droop != null ? Math.min(100, 100 - droop) : null;
  const r8 = row(
    tile({ id: 'demagKoef', labelKey: 'configure.demagKoef', tipKey: 'configure.demagKoefTip', tipExtraKeys: ['emTiles.fromRun'],
      unit: '%', d: 2, value: dmKept,
      level: dmLoss == null ? undefined : dmLoss < 1 ? 'ok' : dmLoss < 5 ? 'warn' : 'bad' }),
    tile({ id: 'saturationKoef', labelKey: 'configure.saturationKoef', tipKey: 'configure.saturationKoefTip', tipExtraKeys: ['emTiles.fromRun'],
      unit: '%', d: 1, value: satKept,
      level: droop == null ? undefined : droop < 5 ? 'ok' : droop < 15 ? 'warn' : 'bad' }),
    tile({ id: 'totalKoef', labelKey: 'configure.totalKoef', tipKey: 'configure.totalKoefTip', tipExtraKeys: ['emTiles.fromRun'],
      unit: '%', d: 1,
      value: dmKept != null && satKept != null ? (dmKept * satKept) / 100 : null,
      level: dmKept == null || satKept == null ? undefined
        : (dmKept * satKept) / 100 > 95 ? 'ok' : (dmKept * satKept) / 100 > 85 ? 'warn' : 'bad' }),
  );

  return [r1, r2, r3, r4, r5, r6, r7, r8].filter((r) => r.length > 0);
}

/** What the lead-cable tile needs (the renderer turns it into its own JSX). */
export const leadCableSection = (s: EmTileSummary): number | null => num(s.A_phase_mm2);

/** tempTileSpec, re-exported: the one judge both panels use. */
export type { TempTileSpec };
export { tempTileSpec };
