/**
 * SolveProgressStrip — the live "Computing X / N points" bar, pinned to the
 * VERY TOP of a tab (user request 2026-07-30: the strip used to live inside the
 * Transient panel far down the page; a running solve must be visible without
 * scrolling).
 *
 * Generalised out of `simulation/SolveProgressStrip` on 2026-09-07 — "нужно
 * сделать прогресс бар в Mechanical и Thermal так же, как в Simulation".  Same
 * bar, same look, different endpoint: every progress route returns the same
 * shape, so the only per-tab knowledge is the URL and the words.
 *
 * Self-contained: polls its endpoint on its own (350 ms while a solve runs,
 * 1.5 s when idle) and renders nothing at all when no solve is in flight — so
 * mounting it costs an idle page one cheap poll, and unmounting the panel stops
 * the poll.  The backend's `running` flag is the single source of truth, which
 * also covers solves launched by other panels (field viewer, animation).
 *
 * The fetch carries auth for free: `lib/apiAuth` replaces window.fetch and
 * attaches the Bearer token to every URL under the API base.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Box, CircularProgress } from '@mui/material';

import { formatProgressLine, formatQueueLine } from './progressLine';
import type { ProgressInfo } from './progressLine';
import { whenVisible } from '../../lib/pageVisible';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export interface SolveProgressStripProps {
  /** Path after the API base, e.g. '/api/mechanical/progress'. */
  endpoint: string;
  /** The word after the counter.  Points for the transient's frame schedule,
   *  steps for the load cases / iterations Mechanical and Thermal march. */
  unit?: string;
  /** Names the running solve: kind → label, e.g. `{ modes: 'Modal analysis' }`. */
  kindLabels?: Record<string, string>;
  /** Full override of the leading text, when a tab needs its own sentence. */
  label?: (p: ProgressInfo) => string;
  /** Simulation only: guess the PWM composition when the backend omits it. */
  pwmFallback?: boolean;
  /** The run this strip is about (backend Stage 4).  Sent as `?run_id=`, so the
   *  bar follows THIS run rather than "whatever this router is doing" — which
   *  with several accounts on one server is somebody else's solve.  Omitted, the
   *  poll keeps its old no-argument form and the backend answers the caller's
   *  newest run in that router: that fall-back is why the tabs that have no run
   *  id yet need no change at all. */
  runId?: string;
}

const SolveProgressStrip: React.FC<SolveProgressStripProps> = ({
  endpoint, unit = 'points', kindLabels, label, pwmFallback = false, runId,
}) => {
  const [p, setP] = useState<ProgressInfo | null>(null);
  // Local ticking clock between polls so "elapsed" advances smoothly.
  const [, force] = useState(0);
  const seenStart = useRef(0);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      if (!alive) return;
      await whenVisible();              // a hidden tab polls nothing (lib/pageVisible)
      if (!alive) return;
      try {
        const url = runId
          ? `${API}${endpoint}${endpoint.includes('?') ? '&' : '?'}run_id=${encodeURIComponent(runId)}`
          : `${API}${endpoint}`;
        const r = await fetch(url);
        if (r.ok) {
          const j: ProgressInfo = await r.json();
          // Same object while nothing changed → no re-render of the strip's
          // parent tree every 1.5 s on an idle backend (see TransientCharts).
          if (alive) setP(prev => (prev
            && prev.running === j.running && prev.step === j.step
            && prev.total === j.total && prev.phase === j.phase
            && prev.elapsed_s === j.elapsed_s && prev.eta_s === j.eta_s
            && prev.queued === j.queued && prev.position === j.position)
            ? prev : j);
        }
      } catch { /* polling errors are not user-facing */ }
      // A QUEUED run polls at the running rate: its position is the only thing
      // moving, and it is what the user is waiting to see move.
      if (alive) timer = window.setTimeout(tick,
        (p?.running || p?.queued ? 350 : 1500));
    };
    tick();
    return () => { alive = false; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p?.running, p?.queued, endpoint, runId]);

  useEffect(() => {           // 200 ms repaint while running, for the clock
    if (!p?.running) return;
    const t = window.setInterval(() => force(x => x + 1), 200);
    return () => window.clearInterval(t);
  }, [p?.running]);

  // WAITING FOR A SLOT.  Not running, but very much not nothing: on a shared
  // server the gap between pressing Run and the first frame is the queue, and a
  // strip that renders nothing there is the "hung server" this component
  // exists to prevent.  One short line and a tooltip — the standing UI rule.
  const queueLine = formatQueueLine(p ?? {});
  if (!p?.running && queueLine) {
    return (
      <Box title="waiting for a solver slot on this server"
        sx={{ position: 'sticky', top: 0, zIndex: 20,
        display: 'flex', alignItems: 'center', gap: 0.8,
        px: 1.25, py: 0.75, mb: 1,
        bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
        borderRadius: 1, fontFamily: 'monospace', fontSize: 12,
        color: 'var(--text-3)' }}>
        <CircularProgress size={11} thickness={6}
          sx={{ color: 'var(--text-4)', verticalAlign: 'middle' }} />
        <span>{queueLine}</span>
      </Box>
    );
  }

  if (!p?.running) { seenStart.current = 0; return null; }

  const line = formatProgressLine(p, unit, kindLabels);
  const { step, total, pct } = line;
  const prefix = label ? label(p) : line.prefix;
  const perPt = p.per_step_s ?? 0;
  const eta = p.eta_s ?? 0;
  // Native tooltip, not a second line of text: the UI rule is one short line,
  // and the phase/composition words are already the long part of it.  Only for
  // a kind-reporting endpoint — the transient sends no kind, so its strip keeps
  // exactly the behaviour it had before this component was generalised.
  const tip = p.kind
    ? [line.prefix.replace(/\s·$/, ''), p.phase, p.composition].filter(Boolean).join(' · ')
    : undefined;

  return (
    <Box title={tip}
      sx={{ position: 'sticky', top: 0, zIndex: 20,
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
          {prefix}&nbsp;<b style={{ color: 'var(--text-0)' }}>{step}</b>
          &nbsp;/&nbsp;<b>{total}</b>&nbsp;{unit}
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
            // does not send a composition yet — and only on the transient
            // endpoint, which is the only schedule it describes.
            if (p.composition) {
              return <span style={{ color: 'var(--text-4)' }}>
                &nbsp;= {p.composition}</span>;
            }
            if (!pwmFallback) return null;
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

export default SolveProgressStrip;
