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
  P_stranded_W:        number;
  P_solid_W:           number;
  efficiency:          number;
  mass_total_kg:       number;
  mass_components:     Array<{ name: string; mass_kg: number; volume_cm3?: number; material?: string }>;
  torque_per_mass_Nm_kg: number;
  power_per_mass_W_kg:   number;
  loss_density_W_kg:     number;
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
  if (!summary) {
    return (
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
        textAlign: 'center', fontSize: 11, color: 'var(--text-3)' }}>
        {loading
          ? 'Computing transient FEM — summary will appear when the run completes…'
          : 'No transient data yet — press “Run Simulation” in the left panel.'}
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
  const stale = dI > 0.05 || dG > 0.05;

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2,
        flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
          Simulation summary — real FEM results
        </Typography>
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
        <Cell label="Active mass" value={fmt(s.mass_total_kg, 2)} unit="kg"
          tooltip="Sum of stator iron + copper + magnets + rotor iron + shaft (active section, no housing)"/>
        <Cell label="Torque density" value={fmt(s.torque_per_mass_Nm_kg, 2)} unit="N·m/kg"
          tooltip="T_em / mass_active — figure of merit for motor compactness"/>
        <Cell label="Power density" value={fmt(s.power_per_mass_W_kg / 1000, 2)} unit="kW/kg"
          tooltip="P_mech / mass_active"/>
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
          tooltip="Bertotti loss in stator + rotor steel — kh·f·B² + kc·(f·B)² + ke·(f·B)^1.5, period mean"/>
        <Cell label="Stranded (copper)" value={fmtK(s.P_stranded_W)} unit="W"
          tooltip="I²R (DC) + AC eddy/proximity share in the coil windings, incl. end-winding resistance (k_end) and ρ_Cu(T)"/>
        <Cell label="Solid (magnets)" value={fmtK(s.P_solid_W)} unit="W"
          tooltip="Magnet + shaft eddy from the coupled conducting-rotor field solve (with per-magnet ∫J=0 and lamination factor); the d²/12 slab estimate is used only when field losses are off"/>
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
