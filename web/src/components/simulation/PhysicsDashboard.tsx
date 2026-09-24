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
import StoredRunSelector   from './StoredRunSelector';
import { useMotorStore }   from '../../stores/motorStore';
import { geoSignature }    from '../common/geoSig';
import { applyS1AsOperatingPoint, s1AutoSetPlan, s1AutoSetNoticeText }
                            from './coupledApi';
import type { TransientSummary } from './SummaryTable';
import type { FemPayload } from './fem-types';

interface Props {
  gamma_deg:      number;
  I_phase_rms?:   number;
  // Winding connection currently SELECTED in the left panel.  Part of the
  // operating point (it divides the coil current), so the summary card has to
  // be able to tell it apart from the one its numbers were solved with.
  connection?:    string;
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
  // Transient EXCITATION SOURCE — sinusoidal current, sinusoidal voltage (FOC
  // verification), PWM inverter, 120° block, or an imposed waveform.  Threaded
  // to TransientCharts only: the static / animation views stay current-driven
  // (illustrative).
  drive?:         'current' | 'voltage' | 'pwm_voltage' | 'custom_current'
                  | 'bldc_current';
  vPeak?:         number;
  vDelta?:        number;
  // (no vBus / fSwitch: the PWM carrier and DC link are the Controller's,
  //  resolved by the backend — 2026-09-24)
  iBlock?:        number;   // bldc_current: flat-top block amplitude [A]
  waveform?:      string;   // custom_current: JSON [[θe_deg, i_A], …]
  // ── GENERATOR → BATTERY ─────────────────────────────────────────────
  // The pack on the DC link and the two loops that use it.  Threaded to
  // TransientCharts only, like every other drive-side field: the static and
  // animation views stay current-driven and have no bridge.
  battery?:       Record<string, unknown> | null;
  busCouple?:     boolean;
  chargeMax?:     boolean;
  /** The panel's speed — the third coordinate of the operating point the
   *  summary card checks itself against (2026-09-13: a card of the 22 900
   *  rpm peak sat unflagged under a panel set to 20 900). */
  rpm?:           number;
}


// ── main component ────────────────────────────────────────────────────────────
const PhysicsDashboard: React.FC<Props> = ({ gamma_deg, I_phase_rms, connection = '', runNonce = 0, onBusyChange, steps = 12, fresh = false, onSummary, fieldLosses = true, demag = false, torqueFilter = false, eddyCoupled = true, drive = 'current', vPeak = 0, vDelta = 0, iBlock = 0, waveform = '', battery = null, busCouple = false, chargeMax = false, rpm }) => {
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
  // ── MACHINE IDENTITY of the shown numbers ──────────────────────────────
  // The summary card is the LAST thing the user reads, and it kept presenting
  // the previous machine's physics as current after another motor was loaded
  // (user 2026-08-31: "при загрузке нового мотора остались данные со старого
  // — этот косяк давным давно не исправляется").  The charts already carry
  // two witnesses on every summary (_geoSig — this client's stamp of the run,
  // _geoStaleBackend — the server's own fingerprint verdict on the restored
  // last transient); the card just never looked at them.  An applied duty
  // summary belongs to the machine that was just applied, so only the
  // transient path is checked.
  const liveGeometry = useMotorStore(st => st.geometry);
  const liveGeoSig = React.useMemo(
    () => geoSignature(liveGeometry as Record<string, unknown>), [liveGeometry]);
  const summaryStale = !appliedSummary && !!transientSummary && (
    ((transientSummary as any)._geoSig != null
      && (transientSummary as any)._geoSig !== liveGeoSig)
    || (transientSummary as any)._geoStaleBackend === true);
  // "vs applied point" delta chip removed (user request 2026-08-20): the
  // header line had no room for it and the η delta was routinely nonsense
  // when the applied point carried no recorded results.

  // ── AUTO-SET the operating point from a VERIFIED S1 run (owner 2026-09-21,
  // fourth round, screenshot: the dashboard DIMMED after a `continuous` run
  // and the panel still read the setpoint 63.64 A under tiles at 48.6 A —
  // *«почему замыленный экран … опять токи не совпадают»*).  The dimming
  // IS SummaryTable's own operating-point staleness guard (`opStale`, current
  // vs `liveOp.current`) — this is the current mismatch, not the geometry
  // fingerprint (the "3D ×0.951 ⚠ recompute" chip is the unrelated inherited
  // passport).  Fixing the mismatch clears the dim by itself: writing the S1
  // current through `applyS1AsOperatingPoint` brings `liveOp.current` back
  // within the guard's own 0.05 A tolerance.  A manual "Use N A…" button once
  // did this on click, but sat unnoticed at the end of a row — removed (owner,
  // fourth round: *«ты что не можешь сам записать этот ток и прогнать солвер
  // с ним автоматом?»*): the panel now moves BY ITSELF.  Only for a REAL
  // verification pass (`s1AutoSetPlan` — record_is_s1 AND verified === true);
  // an estimate or a contradiction never moves the setpoint (rule 2 of the
  // brief), and the S1 line already says why.  Never silent (project rule):
  // one visible line under the header, with an undo back to the previous
  // setpoint.
  const [s1Notice, setS1Notice] =
    React.useState<{ from: number; to: number } | null>(null);
  const s1AppliedForRef = React.useRef<TransientSummary | null>(null);
  React.useEffect(() => {
    if (!transientSummary || s1AppliedForRef.current === transientSummary) return;
    s1AppliedForRef.current = transientSummary;      // once per NEW run only
    const plan = s1AutoSetPlan(transientSummary.coupling, I_phase_rms ?? null);
    if (!plan) return;
    applyS1AsOperatingPoint(plan.to);
    setS1Notice(plan);
  }, [transientSummary, I_phase_rms]);
  const undoS1Notice = React.useCallback(() => {
    if (!s1Notice) return;
    applyS1AsOperatingPoint(s1Notice.from);
    setS1Notice(null);
  }, [s1Notice]);
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
        {/* Which of this duty's SAVED runs is on screen (sine / PWM / BLDC).
            Renders nothing until the loaded duty has more than one. */}
        <StoredRunSelector />
        <Box sx={{ flex: 1 }}/>
      </Box>

      {/* ── S1 auto-set notice (owner 2026-09-21, fourth round) — never silent:
           the setpoint just moved on its own, so this says to what, from what,
           and offers the one click back.  `undo` restores the PREVIOUS
           setpoint through the same setter, exactly like a typed value. ── */}
      {s1Notice && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
          px: 1.25, py: 0.75, borderRadius: 1, bgcolor: 'rgba(74,222,128,0.10)',
          border: '1px solid #16a34a', color: '#4ade80', fontSize: 12,
          fontWeight: 600, alignSelf: 'flex-start' }}>
          <span>{s1AutoSetNoticeText(s1Notice).slice(0, -'undo'.length)}</span>
          <Box component="span" onClick={undoS1Notice}
            sx={{ textDecoration: 'underline', cursor: 'pointer', fontWeight: 700 }}>
            undo
          </Box>
        </Box>
      )}

      {/* ── Top-of-tab summary card — populated by TransientCharts, or by a
           design applied from the Sweep tab (numbers reused, no re-run) ── */}
      {summaryStale && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
          px: 1.25, py: 0.75, borderRadius: 1, bgcolor: 'rgba(239,68,68,0.10)',
          border: '1px solid #b91c1c', color: '#f87171', fontSize: 12,
          fontWeight: 700, alignSelf: 'flex-start' }}>
          ⚠ STALE — DIFFERENT MACHINE · Run Simulation
        </Box>
      )}
      <Box sx={summaryStale ? { opacity: 0.35, pointerEvents: 'none' } : undefined}>
        <SummaryTable summary={shownSummary} fromSweep={!!appliedSummary}
          liveOp={{ current: I_phase_rms, gamma: gamma_deg, connection, rpm }}/>
      </Box>

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
        iBlock={iBlock} waveform={waveform}
        battery={battery as never} busCouple={busCouple} chargeMax={chargeMax}
        steps={steps} runNonce={runNonce} fresh={fresh} onBusyChange={onBusyChange}
        appliedFromSweep={!!appliedSummary}
        onSummary={setTransientSummary}/>
    </Box>
  );
};

export default PhysicsDashboard;
