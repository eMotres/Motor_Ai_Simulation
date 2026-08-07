/**
 * PhysicsDashboard — the FEM view of the Simulation tab.
 *
 * Every panel here is a finite-element solve:
 *   SummaryTable        — the last transient's summary card
 *   FemAnimationViewer  — field frames across one electrical period
 *   FemFieldChart       — magnetostatic / coupled-eddy field inspector
 *   TransientCharts     — T(t), P(t), V(t) from the sliding-band transient
 *
 * It used to ALSO fetch /api/simulation/physics — an analytic Green's-function
 * MMF / B_r / Steinmetz-loss estimate — and render all of it into Papers with
 * `display: 'none'`. Nothing of that payload had been visible for a long time,
 * yet it was recomputed on every Run, and `if (!data) return null` meant a
 * failure of that analytic endpoint blanked the FEM panels below it. Endpoint
 * and panels are gone; the "real FEM" chip in the header is now literal.
 */
import React from 'react';
import { Box, Typography, Chip } from '@mui/material';
import FemFieldChart       from './FemFieldChart';
import FemAnimationViewer  from './FemAnimationViewer';
import TransientCharts     from './TransientCharts';
import SummaryTable        from './SummaryTable';
import type { TransientSummary } from './SummaryTable';
import type { FemPayload } from './fem-types';

interface Props {
  gamma_deg:      number;
  I_phase_rms?:   number;
  // Ticks on each "Run Simulation" press; forwarded to the FEM panels
  // which only (re)solve on that signal.
  runNonce?:      number;
  onBusyChange?:  (busy: boolean) => void;
  // Steps per electrical period (set in the left panel).  Drives both the
  // transient n_steps_per_period and the animation n_frames so they share
  // one backend cache key.
  steps?:         number;
  // When true the next run tells the backend to discard cached frames and
  // recompute the whole period ("Start fresh"); false resumes them.
  fresh?:         boolean;
  // Forwards the latest transient-run summary up to the parent (SimulationPanel)
  // so it can be snapshotted by the "Save simulation" card.
  onSummary?:     (s: TransientSummary | null) => void;
  // Field-based magnet/shaft eddy losses (vs slab estimate) — see TransientCharts.
  fieldLosses?:   boolean;
  // Per-element irreversible demagnetisation — de-rates Br → torque/EMF + %-map.
  demag?:         boolean;
  // Coupled σ·∂A/∂t eddy-current solve in the run — see TransientCharts.
  eddyCoupled?:   boolean;
  // Band-limit T(t) to the physical 6·k orders (default ON; off = raw torque).
  torqueFilter?:  boolean;
  // Transient drive mode: imposed sinusoidal current vs imposed sinusoidal
  // voltage (FOC verification).  Threaded to TransientCharts only — the
  // static/animation views stay current-driven (illustrative).
  drive?:         'current' | 'voltage';
  vPeak?:         number;
  vDelta?:        number;
}


// ── main component ────────────────────────────────────────────────────────────
const PhysicsDashboard: React.FC<Props> = ({ gamma_deg, I_phase_rms, runNonce = 0, onBusyChange, steps = 12, fresh = false, onSummary, fieldLosses = true, demag = false, torqueFilter = false, eddyCoupled = true, drive = 'current', vPeak = 0, vDelta = 0 }) => {
  // Latest FEM solve payload — kept around so future siblings can reuse it.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [_femPayload, setFemPayload] = React.useState<FemPayload | null>(null);
  // Most recent transient-run summary — drives the top-of-tab overview card.
  const [transientSummary, setTransientSummary] =
    React.useState<TransientSummary | null>(null);
  // A design applied from the Sweep/Optimization tab pushes its already-computed
  // FEM summary here so the card shows those exact numbers WITHOUT a re-run.
  // A fresh Run (runNonce increments) supersedes it.
  const [appliedSummary, setAppliedSummary] = React.useState<TransientSummary | null>(null);
  // Kept even after a Run supersedes it, so the Run can be REPORTED against the
  // point it was supposed to reproduce instead of silently replacing it.
  const appliedRef = React.useRef<TransientSummary | null>(null);
  React.useEffect(() => {
    const onApply = (e: Event) => {
      const s = (e as CustomEvent).detail?.summary;
      if (s) { setAppliedSummary(s as TransientSummary); appliedRef.current = s as TransientSummary; }
    };
    window.addEventListener('sim-apply-summary', onApply as EventListener);
    return () => window.removeEventListener('sim-apply-summary', onApply as EventListener);
  }, []);
  // An applied design REPLACES the geometry, so everything on the card belongs to the
  // previous one.  Drop it instead of presenting it as current while the recompute
  // this apply kicked off is still running.
  React.useEffect(() => {
    const onApplied = () => setTransientSummary(null);
    window.addEventListener('sim-design-applied', onApplied);
    return () => window.removeEventListener('sim-design-applied', onApplied);
  }, []);
  // A fresh Run supersedes an applied summary — but only once ITS OWN result is in.
  // (Was: cleared the moment runNonce ticked, which made an auto-recompute fall back
  // to the PREVIOUS design's numbers for the whole solve.)
  const appliedNonceRef = React.useRef(runNonce);
  const [runPending, setRunPending] = React.useState(false);
  React.useEffect(() => {
    if (runNonce !== appliedNonceRef.current) { appliedNonceRef.current = runNonce; setRunPending(true); }
  }, [runNonce]);
  React.useEffect(() => {
    if (runPending && transientSummary) { setAppliedSummary(null); setRunPending(false); }
  }, [runPending, transientSummary]);
  const shownSummary = appliedSummary ?? transientSummary;
  // A solve that lands on different numbers than the applied point is a FINDING,
  // not a screen update: on 2026-08-07 an auto-recompute replaced a swept 27.33
  // N·m with 12.65 and nothing said the two disagreed.  Report the gap.
  const deltaVsApplied = React.useMemo(() => {
    const a = appliedRef.current, t = transientSummary;
    if (!a || !t || appliedSummary) return null;
    const rel = (x?: number, y?: number) =>
      (Number.isFinite(x as number) && Number.isFinite(y as number) && (y as number) !== 0)
        ? 100 * ((x as number) - (y as number)) / (y as number) : null;
    const dT = rel(t.T_em_avg_Nm, a.T_em_avg_Nm);
    const dEff = (Number.isFinite(t.efficiency as number) && Number.isFinite(a.efficiency as number))
      ? 100 * ((t.efficiency as number) - (a.efficiency as number)) : null;
    if (dT == null && dEff == null) return null;
    return { dT, dEff };
  }, [transientSummary, appliedSummary]);
  // Forward the shown summary to the parent (for the Save-simulation snapshot) and
  // persist it, so "Save as new motor" stamps the card with the numbers the user
  // actually SEES here — not a stale .last_transient on disk.
  React.useEffect(() => {
    onSummary?.(shownSummary);
    try {
      if (shownSummary) localStorage.setItem('sim.lastSummary', JSON.stringify(shownSummary));
    } catch { /* quota */ }
  }, [shownSummary]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>

      {/* ── Header ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="h6" sx={{ color: 'var(--text-0)', fontWeight: 700 }}>
          Physics Dashboard
        </Typography>
        {/* Now literally true: every panel below is a FEM solve.  It used to sit
            above a /physics fetch whose analytic MMF / B_r / loss estimates were
            all rendered into display:none Papers. */}
        <Chip label="real FEM" size="small" sx={{ fontSize: 10, bgcolor: 'var(--ok-bg)', color: '#4ade80' }}/>
        <Box sx={{ flex: 1 }}/>
      </Box>

      {/* ── Top-of-tab summary card — populated by TransientCharts, or by a
           design applied from the Sweep tab (numbers reused, no re-run) ── */}
      <SummaryTable summary={shownSummary} fromSweep={!!appliedSummary}
        deltaVsApplied={deltaVsApplied}
        liveOp={{ current: I_phase_rms, gamma: gamma_deg }}/>

      {/* ── Field viewer / animation — one widget covers both the static
            initial snapshot (frame[0] at rotor_angle = 0) AND the full
            playback across one electrical period.  Slider lets you scrub
            through the rotor positions; play button auto-advances. ── */}
      <FemAnimationViewer gamma_deg={gamma_deg} I_phase_rms={I_phase_rms}
        n_frames={Math.min(steps, 24)} runNonce={runNonce} fresh={fresh} onPayload={setFemPayload}/>

      {/* ── Field inspector: Magnetostatic ↔ Eddy-current solve ──────────
          Standalone FemFieldChart (fetches its own data, no payloadOverride)
          with the Magneto/Eddy toggle — switch to "Eddy" to run the σ·∂A/∂t
          solve and inspect A_z / |B| / J (real current density in the copper). */}
      <FemFieldChart gamma_deg={gamma_deg} I_phase_rms={I_phase_rms}/>

      {/* ── Transient: T(t), P(t), V(t) — one FEM solve per time step ── */}
      <TransientCharts gamma_deg={gamma_deg} I_phase_rms={I_phase_rms} fieldLosses={fieldLosses}
        demag={demag} torqueFilter={torqueFilter} eddyCoupled={eddyCoupled}
        drive={drive} vPeak={vPeak} vDelta={vDelta}
        steps={steps} runNonce={runNonce} fresh={fresh} onBusyChange={onBusyChange}
        appliedFromSweep={!!appliedSummary}
        onSummary={setTransientSummary}/>
    </Box>
  );
};

export default PhysicsDashboard;
