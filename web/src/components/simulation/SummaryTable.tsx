/**
 * SummaryTable — single overview card at the top of the Simulation tab.
 *
 * Pulls all key metrics from the most recent transient run (T, P, masses,
 * loss breakdown, efficiency, KV).  Numbers come straight from
 * /fem_transient → summary, so every value here is a REAL FEM result
 * (not analytical).
 */
import React from 'react';
import { Box, Paper, Typography, Tooltip } from '@mui/material';
import AddToCompareButton from '../compare/AddToCompareButton';
import HelpTip from '../common/HelpTip';
import { geoSignature } from '../common/geoSig';
import { useMotorStore } from '../../stores/motorStore';

export interface TransientSummary {
  rpm:                 number;
  I_phase_rms_A:       number;
  gamma_deg:           number;
  T_em_avg_Nm:         number;
  T_ripple_pct:        number;
  T_ripple_raw_pct?:   number;
  T_ripple_filt_pct?:  number;
  P_mech_W:            number;
  V_phase_peak_V:      number;
  V_phase_rms_V:       number;
  V_line_peak_V:       number;
  V_line_rms_V:        number;
  KV_rpm_per_V_phase:  number;
  KV_rpm_per_V_line:   number;
  V1_phase_V?:         number;
  THD_pct?:            number;
  THD_LL_pct?:         number;
  I1_A?:               number;   // fundamental current amplitude (branch, from FFT)
  THD_I_pct?:          number;   // ≈0 in current drive; real parasitics in voltage drive
  V1_LL_V?:            number;   // fundamental of the ACTUAL line-to-line waveform
  Kt_Nm_per_Arms?:     number;
  J_coil_A_per_mm2?:   number;   // coil current density = I_rms/parallel over one strand's copper section
  P_loss_total_W:      number;
  P_core_W:            number;
  // Split behind P_core_W, per half — what the core loss is MADE of.  `model`
  // says WHICH loss model produced it: 'measured_surface' (the manufacturer's
  // P(B, f) table interpolated directly, summed over the locus's harmonics) or
  // 'bertotti' (the three fitted coefficients).  Absent on runs that predate
  // the split.
  P_core_terms?: Record<string, { hysteresis_W: number; eddy_W: number;
                                  excess_W: number; k_f: number;
                                  model?: string }>;
  P_stranded_W:        number;
  P_solid_W:           number;
  // Discarded frames at θ<0 the coupled eddy solve needed before its σ·∂A/∂t
  // start-up transient was quiet — 0 when the solve did not run.
  eddy_warmup_frames?: number;
  efficiency:          number;
  // TOTAL = iron + copper + magnets + shaft (the divisor of every density below);
  // ACTIVE = the EM-active mass without the shaft — the basis an ANSYS active-mass
  // expression quotes, so it is the tile a user cross-checks against Ansys.
  mass_total_kg:       number;
  mass_active_kg?:     number;
  mass_area_source?:   string;
  mass_components:     Array<{ name: string; mass_kg: number; volume_cm3?: number; material?: string }>;
  torque_per_mass_Nm_kg: number;
  power_per_mass_W_kg:   number;
  loss_density_W_kg:     number;
  // Nonlinear-solve honesty: false = at least one frame of the reported window
  // was accepted without meeting its solver's convergence test, so everything
  // above is an average that includes an unconverged field.
  nonlinear_converged?:  boolean;
  // Time resolution the run ACTUALLY used vs the one that was asked for: the
  // solver snaps steps/period onto the slip-node divisor grid.
  steps_snapped?:              boolean;
  n_steps_per_period?:         number;
  n_steps_per_period_requested?: number;
  slip_nodes_per_period?:      number;
  nonlinear_resid_max?:  number;
  nonlinear_tol?:        number;
  nonlinear_unconverged_frames?: number[];
  // WHICH MACHINE these numbers describe.  Stamped by TransientCharts from the
  // run that produced them and carried through PhysicsDashboard into
  // `sim.lastSummary`, so a summary that outlives its motor (preset switch,
  // page reload, applied Sweep design) can be recognised as belonging to
  // another machine instead of being read as the current one.  Absent = a run
  // from before the stamp existed: unknown, and reported as unknown.
  _geoSig?:              string;
  // The backend's independent verdict on a RESTORED run (its own fingerprint of
  // the machine it solved vs the machine loaded now).  Either witness is enough.
  _geoStaleBackend?:     boolean;
}

interface Props {
  summary: TransientSummary | null;
  loading?: boolean;
  fromSweep?: boolean;   // numbers reused from an applied Sweep design (no re-run)
  // Currently-set operating point (the Simulation inputs). If the shown summary was
  // computed at a DIFFERENT current / γ (e.g. the user changed it after the run, and
  // the Optimize tab already uses the new point), flag the result as stale so the
  // Sim numbers aren't mistaken for the current point.
  liveOp?: { current?: number; gamma?: number };
}

/** "  stator 67.3 W (hyst 33.9 / eddy 17.6 / excess 15.8, k_f 0.92); rotor …"
 *  — the split behind the Core tile, appended to its HelpTip.  Empty string
 *  when the run predates the split, so old stored summaries still read. */
function coreSplit(t?: TransientSummary['P_core_terms']): string {
  const parts = Object.entries(t ?? {}).map(([half, v]) => {
    const tot = v.hysteresis_W + v.eddy_W + v.excess_W;
    return `${half} ${tot.toFixed(1)} W (hyst ${v.hysteresis_W.toFixed(1)}`
         + ` / eddy ${v.eddy_W.toFixed(1)} / excess ${v.excess_W.toFixed(1)},`
         + ` k_f ${v.k_f.toFixed(3)})`;
  });
  return parts.length ? `  Split: ${parts.join('; ')}.` : '';
}

/** True when any half of this run went through the measured P(B, f) surface.
 *  The tile must not describe the model it did not use. */
function usesSurface(t?: TransientSummary['P_core_terms']): boolean {
  return Object.values(t ?? {}).some(v => v.model === 'measured_surface');
}

/** The Core tile's HelpTip — one text per loss model, because the two differ in
 *  what they claim: the surface path IS the manufacturer's measurement inside
 *  the measured (B, f) envelope, the Bertotti path is three coefficients fitted
 *  to it.  Both carry the same stated under-reads (alternating-axis basis,
 *  no manufacturing degradation, no correction coefficient anywhere). */
function coreTooltip(t?: TransientSummary['P_core_terms']): string {
  const head = usesSurface(t)
    ? 'Core loss in stator + rotor steel from the manufacturer\'s MEASURED '
      + 'P(B, f) table, interpolated directly and summed over the harmonics of '
      + 'each element\'s flux locus — no B² law, so the saturation upturn is '
      + 'followed.  Past the table\'s edge it blends into the fitted Bertotti '
      + 'extrapolation; the run log says how much of the answer came from '
      + 'there.  The steel carries B/k_f over k_f of the section (lamination).  '
      + 'In the split below only eddy is separately computed (from the true '
      + 'dB/dt series) — hysteresis/excess are the fit\'s proportion of the '
      + 'measured total.'
    : 'Bertotti loss in stator + rotor steel — kh·f·B² + kc·(f·B)² + '
      + 'ke·(f·B)^1.5, period mean.  Coefficients are fitted to the material\'s '
      + 'measured loss curves at every frequency; the steel carries B/k_f over '
      + 'k_f of the section (lamination).';
  return head + coreSplit(t)
       + '  Alternating-axis basis: a rotating B locus (tooth roots) reads low, '
       + 'and manufacturing degradation (punching, stacking, welding — '
       + 'typically 1.5-2×) is NOT included.  This is the raw calculation, with '
       + 'no correction coefficient.';
}

const Cell: React.FC<{
  label: string; value: string; unit?: string;
  tooltip?: string; accent?: 'green' | 'amber' | 'red' | 'blue' | 'default';
}> = ({ label, value, unit, tooltip, accent = 'default' }) => {
  const colour = {
    green:   '#4ade80',
    amber:   '#fbbf24',
    red:     '#f87171',
    blue:    '#60a5fa',
    default: 'var(--text-0)',
  }[accent];
  const cell = (
    <Box sx={{
      display: 'flex', flexDirection: 'column',
      px: 1.25, py: 0.8,
      bgcolor: 'var(--panel-2)', border: '1px solid var(--app-bg)',
      borderRadius: 1, minWidth: 0,
    }}>
      <Typography sx={{ fontSize: 9, color: 'var(--text-3)',
        letterSpacing: '0.04em', textTransform: 'uppercase',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {label}
      </Typography>
      <Typography sx={{ fontSize: 16, fontWeight: 700, color: colour,
        fontFamily: 'monospace', lineHeight: 1.2 }}>
        {value}
        {unit && (
          <Typography component="span" sx={{ fontSize: 10, color: 'var(--text-4)',
            ml: 0.5, fontWeight: 400 }}>{unit}</Typography>
        )}
      </Typography>
    </Box>
  );
  return tooltip ? <Tooltip title={tooltip} placement="top">{cell}</Tooltip> : cell;
};

const SummaryTable: React.FC<Props> = ({ summary, loading, fromSweep, liveOp }) => {
  // Hook FIRST — the empty-state early-return below must not sit between the
  // component entry and a hook call.
  const geometry = useMotorStore(s => s.geometry);
  const liveSig = React.useMemo(
    () => geoSignature(geometry as Record<string, unknown>), [geometry]);

  if (!summary) {
    // Keep the header (and the disabled "+ Compare") in place even with nothing to
    // show: the button vanishing entirely before the first run made it impossible
    // to find. Its tooltip explains what is missing.
    return (
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
        display: 'flex', flexDirection: 'column', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
          <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
            Simulation summary — real FEM results
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 'auto' }}>
            <AddToCompareButton />
          </Box>
        </Box>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          {loading
            ? 'Computing transient FEM — summary will appear when the run completes…'
            : 'No transient data yet — press “Run Simulation” in the left panel.'}
        </Typography>
      </Paper>
    );
  }

  const s = summary;
  const fmt = (n: number, d = 2) => Number(n).toFixed(d);
  const fmtK = (n: number) => n >= 1000 ? `${fmt(n / 1000, 2)}k` : fmt(n, 0);

  const accentEff: 'green' | 'amber' | 'red' = s.efficiency >= 0.92 ? 'green'
                                              : s.efficiency >= 0.85 ? 'amber'
                                              : 'red';
  const accentRipple: 'green' | 'amber' | 'red' = s.T_ripple_pct <= 5 ? 'green'
                                                : s.T_ripple_pct <= 15 ? 'amber'
                                                : 'red';

  // Stale-operating-point guard: these numbers were computed at the summary's own
  // current / γ.  If the inputs have since changed (the optimizer already uses the
  // new point), the Sim shows an OLD point — flag it so it isn't compared 1:1.
  const liveI = liveOp?.current, liveG = liveOp?.gamma;
  const dI = Number.isFinite(liveI as number) ? Math.abs((liveI as number) - s.I_phase_rms_A) : 0;
  const dG = Number.isFinite(liveG as number) ? Math.abs((liveG as number) - s.gamma_deg)     : 0;
  const opStale = dI > 0.05 || dG > 0.05;
  // …and the OTHER way a card goes stale, which used to slip through entirely:
  // the machine changed under it.  Loading another preset (or reloading with a
  // persisted `sim.lastSummary` from the previous session) leaves I and γ
  // untouched, so the operating-point check above says "current" about torque
  // and mass measured on a motor that is no longer loaded — precisely the
  // "0.02 N·m from a previous machine" reading this banner exists to stop.
  // Two independent witnesses: this client's stamp, and the backend's own
  // fingerprint verdict on a restored run.  Unknown (unstamped legacy run) is
  // NOT treated as fresh — it simply cannot raise this flag on its own.
  const geoStale = (!!s._geoSig && !!liveSig && s._geoSig !== liveSig)
                   || s._geoStaleBackend === true;
  const stale = opStale || geoStale;

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      {/* A stale headline number is worse than none: the user read a saved
          0.02 N·m from a previous (broken) machine as "the solver is garbage".
          The tiny gray ⚠ did exist — it whispered. This banner shouts. */}
      {stale && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
          px: 1.25, py: 0.75, borderRadius: 1, bgcolor: 'rgba(239,68,68,0.10)',
          border: '1px solid #b91c1c', color: '#f87171', fontSize: 12, fontWeight: 700 }}>
          {/* One-line verdict; the "why" lives in the ⓘ (UI rule). */}
          {geoStale
            ? <>⚠ STALE — DIFFERENT MACHINE · Re-run Simulation</>
            : <>⚠ STALE — I = {s.I_phase_rms_A} A, γ = {s.gamma_deg}° · Re-run Simulation</>}
          <HelpTip title={geoStale
            ? ('These numbers were computed on a different machine than the one now loaded. Torque, mass and '
              + 'efficiency below belong to the previous geometry'
              + (opStale ? ` (and to I = ${s.I_phase_rms_A} A, γ = ${s.gamma_deg}°)` : '')
              + '. Press Re-run Simulation.')
            : (`These numbers were computed at I = ${s.I_phase_rms_A} A, γ = ${s.gamma_deg}°, not the current `
              + 'panel settings (possibly a previous machine). Press Re-run Simulation.')} />
        </Box>
      )}
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2,
        flexWrap: 'wrap', opacity: stale ? 0.55 : 1 }}>
        <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
          Simulation summary — real FEM results
        </Typography>
        {/* Snapshot THIS design as a comparison point (Compare tab). Sits next to
            the numbers it captures, so it is obvious what gets saved. */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 'auto' }}>
          <AddToCompareButton />
        </Box>
        <Typography sx={{ fontSize: 10, color: stale ? '#f59e0b' : 'var(--text-4)' }}>
          @ {s.rpm} rpm · I_ph = {s.I_phase_rms_A} A_rms · γ = {s.gamma_deg}°
          {stale && (
            <Tooltip title={`Computed at I = ${s.I_phase_rms_A} A, γ = ${s.gamma_deg}° — the panel is now set to `
              + `${Number.isFinite(liveI as number) ? `I = ${fmt(liveI as number, 2)} A` : ''}`
              + `${dG > 0.05 && Number.isFinite(liveG as number) ? `, γ = ${fmt(liveG as number, 0)}°` : ''}. `
              + 'Run Simulation to recompute at the current point.'} placement="top">
              <span style={{ marginLeft: 6, cursor: 'help' }}>⚠</span>
            </Tooltip>
          )}
          {s.nonlinear_converged === false && (
            <Tooltip title={
              'Nonlinear solve did NOT converge on '
              + `${s.nonlinear_unconverged_frames?.length
                    ? `frame(s) ${s.nonlinear_unconverged_frames.join(', ')}`
                    : 'at least one frame'}`
              + ` — worst residual ${(s.nonlinear_resid_max ?? 0).toExponential(2)}`
              + ` against tol ${(s.nonlinear_tol ?? 0).toExponential(0)}. `
              + 'Every number on this card is an average that includes that frame, '
              + 'so treat it as indicative, not as a result. Torque ripple and the '
              + 'dB/dt losses (core, AC copper) are the most affected.'} placement="top">
              <span style={{ marginLeft: 6, color: '#f87171', cursor: 'help',
                fontWeight: 700 }}>⚠ not converged</span>
            </Tooltip>
          )}
          {s.steps_snapped && (
            <Tooltip title={
              `Requested ${s.n_steps_per_period_requested} steps per electrical period; `
              + `the rotor has to land on whole slip-ring nodes `
              + `(${s.slip_nodes_per_period ?? '?'} per period), so the solver snapped to the `
              + `nearest divisor and ran ${s.n_steps_per_period}. Every number on this card is `
              + 'at the SNAPPED resolution. Pick a divisor of the slip-node count to get '
              + 'exactly what you asked for.'} placement="top">
              <span style={{ marginLeft: 6, color: '#fbbf24', cursor: 'help' }}>
                ⚠ steps {s.n_steps_per_period_requested} → {s.n_steps_per_period}
              </span>
            </Tooltip>
          )}
          {fromSweep && (
            <Tooltip title="Numbers reused from the applied Sweep design (no re-run). Run Simulation for waveforms and field maps." placement="top">
              <span style={{ marginLeft: 6, color: '#fbbf24', cursor: 'help' }}>← Sweep</span>
            </Tooltip>
          )}
        </Typography>
      </Box>

      {/* ── Row 1 — primary outputs ───────────────────────────────────── */}
      <Box sx={{ display: 'grid',
        gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', gap: 1 }}>
        <Cell label="Torque T_em" value={fmt(s.T_em_avg_Nm, 2)} unit="N·m"
          accent="blue"
          tooltip="Average electromagnetic torque from Maxwell stress integral over one electrical period"/>
        <Cell label="Mech power" value={`${fmt(s.P_mech_W / 1000, 2)}`} unit="kW"
          accent="blue"
          tooltip="Shaft power from the energy balance P_elec_in − P_loss (≈ T_em × ω_mech)"/>
        {/* 3 decimals: a 30 mm machine weighs ~0.05 kg, so 2 decimals showed a
            single significant digit and hid every change during optimization.
            Matches the per-component breakdown below, which already uses 3. */}
        <Cell label="EM-active mass"
          value={fmt(s.mass_active_kg ?? s.mass_total_kg, 3)} unit="kg"
          tooltip={"IN: stator iron + rotor iron (× lamination k_f) + copper (× k_end) + magnets. "
                 + "OUT: shaft, housing, bearings. CAD sections × stack × the assigned material's density "
                 + `— the same basis an Ansys active-mass expression uses. With shaft: ${fmt(s.mass_total_kg, 3)} kg.`}/>
        <Cell label="Torque density" value={fmt(s.torque_per_mass_Nm_kg, 2)} unit="N·m/kg"
          tooltip="T_em / total mass (EM-active + shaft) — figure of merit for motor compactness"/>
        <Cell label="Power density" value={fmt(s.power_per_mass_W_kg / 1000, 2)} unit="kW/kg"
          tooltip="P_mech / total mass (EM-active + shaft)"/>
        <Cell label="T ripple" value={fmt(s.T_ripple_pct, 1)} unit="%"
          accent={accentRipple}
          tooltip={`Physical torque ripple (T_max − T_min)/|T_avg| over one electrical period, ` +
                   `reconstructed from the 6·k electrical orders a balanced 3-phase machine can produce ` +
                   `(6th/12th ripple + cogging).` +
                   (s.T_ripple_raw_pct != null
                     ? `  Raw FEM pk-pk = ${s.T_ripple_raw_pct.toFixed(1)}% — the difference is sliding-band ` +
                       `stair-step noise (forbidden orders), not real ripple.`
                     : '')}/>
      </Box>

      {/* ── Row 2 — losses ─────────────────────────────────────────────── */}
      <Box sx={{ display: 'grid',
        gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', gap: 1 }}>
        <Cell label="Total loss" value={fmtK(s.P_loss_total_W)} unit="W"
          accent="amber"
          tooltip="Cu + Fe (Bertotti) + magnet eddy + shaft eddy — period means"/>
        <Cell label="Core (lamination)" value={fmtK(s.P_core_W)} unit="W"
          tooltip={coreTooltip(s.P_core_terms)}/>
        <Cell label="Stranded (copper)" value={fmtK(s.P_stranded_W)} unit="W"
          tooltip="I²R (DC) + AC eddy/proximity share in the coil windings, incl. end-winding resistance (k_end) and ρ_Cu(T)"/>
        <Cell label="Solid (magnets)" value={fmtK(s.P_solid_W)} unit="W"
          tooltip={"Magnet + shaft eddy from the coupled conducting-rotor field solve "
                 + "(with per-magnet ∫J=0 and lamination factor); the d²/12 slab estimate "
                 + "is used only when field losses are off."
                 + (s.eddy_warmup_frames
                    ? `  Cycle mean over a SETTLED window: ${s.eddy_warmup_frames} warm-up `
                      + `frame(s) at θ<0 were solved and discarded first, so no σ·∂A/∂t `
                      + `start-up transient is averaged into it.`
                    : '')}/>
        <Cell label="Loss density" value={fmt(s.loss_density_W_kg, 1)} unit="W/kg"
          tooltip="P_loss / mass — thermal stress indicator"/>
        <Cell label="Efficiency η" value={fmt(s.efficiency * 100, 2)} unit="%"
          accent={accentEff}
          tooltip="P_mech / P_elec_in (energy balance; equals P_mech/(P_mech+P_loss) when the input power isn't reported)"/>
      </Box>

      {/* ── Row 3 — voltage + KV ───────────────────────────────────────── */}
      <Box sx={{ display: 'grid',
        gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', gap: 1 }}>
        <Cell label="V_phase peak" value={fmt(s.V_phase_peak_V, 1)} unit="V"
          tooltip="Max |V_A|, |V_B|, |V_C| of the actual waveform (R·I + dψ/dt) — includes harmonics"/>
        <Cell label="V_line peak" value={fmt(s.V_line_peak_V, 1)} unit="V"
          tooltip="Max of the ACTUAL |V_A−V_B|, |V_B−V_C|, |V_C−V_A| waveforms (triplens cancel line-to-line, so this is LESS than √3×phase peak). This is what the DC bus / battery must cover."/>
        <Cell label="V_phase RMS" value={fmt(s.V_phase_rms_V, 1)} unit="V"
          tooltip="True RMS of the phase waveform (not peak/√2 — harmonics included)"/>
        <Cell label="V_line RMS" value={fmt(s.V_line_rms_V, 1)} unit="V"
          tooltip="True RMS of the line-to-line waveforms (mean of the 3 pairs)"/>
        <Cell label="J coil" value={s.J_coil_A_per_mm2 != null ? fmt(s.J_coil_A_per_mm2, 1) : '—'} unit="A/mm²"
          accent={s.J_coil_A_per_mm2 == null ? 'default'
                  : s.J_coil_A_per_mm2 <= 20 ? 'green'
                  : s.J_coil_A_per_mm2 <= 40 ? 'amber' : 'red'}
          tooltip="Coil current density = I_phase RMS / (a_parallel × strand copper section, wire_width × wire_height). The thermal-loading figure of merit: ~5–15 A/mm² continuous (natural/liquid cooling), 20–40+ for short peak / forced cooling."/>
        <Cell label="KV (line)" value={fmt(s.KV_rpm_per_V_line, 1)} unit="rpm/V"
          tooltip="rpm / (V₁_LL RMS) — fundamental line-to-line at this load point"/>
      </Box>

      {/* ── Mass component breakdown ───────────────────────────────────── */}
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 0.5 }}>
        {s.mass_components.map((c, i) => {
          const pct = (c.mass_kg / s.mass_total_kg * 100);
          return (
            <Box key={i} sx={{
              flex: '1 1 0', minWidth: 0,
              px: 1, py: 0.4,
              bgcolor: 'var(--panel-2)', border: '1px solid var(--app-bg)', borderRadius: 1 }}>
              <Typography sx={{ fontSize: 9, color: 'var(--text-3)',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {c.name}
              </Typography>
              <Typography sx={{ fontSize: 11, fontFamily: 'monospace',
                color: 'var(--text-1)' }}>
                <b>{fmt(c.mass_kg, 3)} kg</b> · {fmt(pct, 1)}%
              </Typography>
            </Box>
          );
        })}
      </Box>
    </Paper>
  );
};

export default SummaryTable;
