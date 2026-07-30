/**
 * SolveProgressStrip — the live "Computing X / N points" bar, pinned to the
 * VERY TOP of the Simulation tab (user request 2026-07-30: the strip used to
 * live inside the Transient panel far down the page; a running solve must be
 * visible without scrolling).
 *
 * Self-contained: polls /physics/fem_transient/progress on its own (350 ms
 * while a solve runs, 1.5 s when idle) and renders nothing at all when no
 * solve is in flight — so mounting it costs an idle page one cheap poll.
 * The backend's `running` flag is the single source of truth, which also
 * covers solves launched by other panels (field viewer, animation).
 */
import React, { useEffect, useRef, useState } from 'react';
import { Box, CircularProgress } from '@mui/material';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface ProgressInfo {
  running: boolean;
  step: number;
  total: number;
  elapsed_s: number;
  eta_s: number;
  per_step_s?: number;
  phase: string;
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

export default SolveProgressStrip;
