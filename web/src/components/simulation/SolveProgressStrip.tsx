/**
 * The Simulation tab's solve-progress strip — the transient endpoint's preset.
 *
 * The bar itself moved to `common/SolveProgressStrip` on 2026-09-07, when
 * Mechanical and Thermal asked for the same one ("нужно сделать прогресс бар в
 * Mechanical и Thermal так же, как в Simulation").  This file stays so
 * SimulationPanel's import keeps working, and so the two things only this tab
 * knows stay with this tab: the transient progress URL, and the PWM composition
 * fallback (it describes the frame schedule of a voltage run and nothing else).
 *
 * `unit="steps"` (changed 2026-09-21 from "points"): the coupled orchestrator's
 * own strip (`common/SolveProgressStrip` against `/api/coupled/progress`, see
 * SimulationPanel) sits right above this one and says "steps" for the very same
 * kind of count — when SimulationPanel lets both render in the same render pass
 * (e.g. the instant the orchestrator hands off between its own step and the
 * transient's), the word must not flip.  One wording everywhere this counts
 * FEM frames.
 */
import React from 'react';

import Strip from '../common/SolveProgressStrip';

<<<<<<< Updated upstream
/** `runId` is the panel's `runNonce` — the SAME token the Run and the Stop
 *  button already send as `run_id` (TransientCharts, FemAnimationViewer).
 *  Passing it here makes the bar follow this run rather than "whatever the
 *  server's transient route is doing", which with several accounts is somebody
 *  else's solve.  Omitted, the poll keeps its old no-argument form. */
const SolveProgressStrip: React.FC<{ runId?: string }> = ({ runId }) => (
  <Strip endpoint="/api/simulation/physics/fem_transient/progress"
    unit="steps" pwmFallback runId={runId} />
);
=======
interface ProgressInfo {
  running: boolean;
  step: number;
  total: number;
  elapsed_s: number;
  eta_s: number;
  per_step_s?: number;
  phase: string;
  // Backend-authored breakdown of `total` ("2×36 settle + 8 pre-roll + 144
  // reported").  Only the code that built the frame schedule can state it —
  // see the fallback comment at the render site.
  composition?: string;
}

const SolveProgressStrip: React.FC = () => {
  const [p, setP] = useState<ProgressInfo | null>(null);
  // Local ticking clock between polls so "elapsed" advances smoothly.
  const [, force] = useState(0);
  const seenStart = useRef(0);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      if (!alive) return;
      try {
        const r = await fetch(`${API}/api/simulation/physics/fem_transient/progress`);
        if (r.ok) {
          const j: ProgressInfo = await r.json();
          if (alive) setP(j);
        }
      } catch { /* polling errors are not user-facing */ }
      if (alive) timer = window.setTimeout(tick, (p?.running ? 350 : 1500));
    };
    tick();
    return () => { alive = false; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p?.running]);

  useEffect(() => {           // 200 ms repaint while running, for the clock
    if (!p?.running) return;
    const t = window.setInterval(() => force(x => x + 1), 200);
    return () => window.clearInterval(t);
  }, [p?.running]);

  if (!p?.running) { seenStart.current = 0; return null; }

  const total = Math.max(1, p.total);
  const step = Math.min(p.step, total);
  const pct = Math.min(100, (100 * step) / total);
  const perPt = p.per_step_s ?? 0;
  const eta = p.eta_s ?? 0;

  return (
    <Box sx={{ position: 'sticky', top: 0, zIndex: 20,
      display: 'flex', flexDirection: 'column', gap: 0.5,
      px: 1.25, py: 0.75, mb: 1,
      bgcolor: 'var(--panel-2)', border: '1px solid var(--line-accent)',
      borderRadius: 1, fontFamily: 'monospace',
      boxShadow: '0 2px 12px rgba(37,99,235,0.25)' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between',
        alignItems: 'baseline', fontSize: 12, color: 'var(--text-2)' }}>
        <span>
          <CircularProgress size={11} thickness={6}
            sx={{ color: 'var(--brand)', mr: 0.8, verticalAlign: 'middle' }} />
          Computing&nbsp;<b style={{ color: 'var(--text-0)' }}>{step}</b>
          &nbsp;/&nbsp;<b>{total}</b>&nbsp;points
          {(() => {
            // A voltage/PWM run marches settling periods before the reported
            // one, so its total is bigger than the panel's steps/period — a
            // bare "432 points" over a 144-step panel reads as a bug ("что за
            // хрень", 2026-08-31).  The BACKEND now says how the total is made
            // up, because it is the only thing that knows: under the PWM
            // mixed-resolution schedule the settle marches coarse and the
            // reported window fine, so the old "total / 3" arithmetic here was
            // simply wrong (2×36 + 8 + 144 = 224, not 3 × anything).  The
            // client math survives only as the fallback for a backend that
            // does not send a composition yet.
            if (p.composition) {
              return <span style={{ color: 'var(--text-4)' }}>
                &nbsp;= {p.composition}</span>;
            }
            try {
              const drv = JSON.parse(localStorage.getItem('sim.drive') || '"current"');
              if ((drv === 'pwm_voltage' || drv === 'voltage') && total % 3 === 0) {
                return <span style={{ color: 'var(--text-4)' }}>
                  &nbsp;= {total / 3}/period × 3 (2 settling + 1 reported)</span>;
              }
            } catch { /* label only */ }
            return null;
          })()}
          {perPt ? `   ·   ${perPt.toFixed(2)} s/pt` : ''}
          <span style={{ color: 'var(--text-4)', marginLeft: 10 }}>{p.phase}</span>
        </span>
        <span style={{ color: 'var(--text-3)' }}>
          elapsed&nbsp;<b style={{ color: 'var(--text-0)' }}>{(p.elapsed_s ?? 0).toFixed(1)} s</b>
          {eta ? `   ·   ETA ${eta.toFixed(0)} s` : ''}
        </span>
      </Box>
      <Box sx={{ width: '100%', height: 6, bgcolor: 'var(--line-soft)',
        borderRadius: 3, overflow: 'hidden' }}>
        <Box sx={{ width: `${pct}%`, height: '100%', bgcolor: 'var(--brand)',
          transition: 'width 300ms linear' }} />
      </Box>
    </Box>
  );
};
>>>>>>> Stashed changes

export default SolveProgressStrip;
