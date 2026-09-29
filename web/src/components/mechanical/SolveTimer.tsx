/** How long a mechanical solve is taking, and how long the last one took.
 *
 * User 2026-09-06: "нужно добавить ещё индикатор времени расчёта".  A contact
 * solve on a fine mesh is a minute of silence with a spinner on it, and the two
 * questions that silence raises are "is it still going" and "how much longer".
 *
 * Two components, one line each (the tab's standing no-walls-of-text rule):
 *
 *   • `SolveTimer` — WHILE a solve runs: a clock started by the store when the
 *     press happened, plus the last measured duration of the same kind of solve
 *     as an estimate.  The clock is client-side on purpose: it has to move
 *     while the request is in flight and the backend has said nothing yet.
 *   • `solvedIn` — AFTER it: the BACKEND's own `elapsed_s`, because a client
 *     stopwatch also times the network and the JSON of a 40 MB field payload.
 *     A cache hit says so and still names the seconds the answer once cost.
 */
import React, { useEffect, useState } from 'react';
import { Tooltip, Typography } from '@mui/material';

import { fmtClock, fmtSecs } from './api';

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;

/** The running clock beside a Solve button. Renders nothing when idle. */
export const SolveTimer: React.FC<{
  busy: boolean;
  /** `Date.now()` of the press, from the store slice */
  startedAt: number | null;
  /** last measured seconds for this kind of solve, or null on the first ever */
  est: number | null;
  /** what the estimate is an estimate OF, for the tooltip */
  what?: string;
}> = ({ busy, startedAt, est, what = 'this solve' }) => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!busy || !startedAt) return;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [busy, startedAt]);

  if (!busy || !startedAt) return null;
  const ms = Math.max(0, now - startedAt);
  const over = est !== null && ms / 1000 > est * 1.5;
  return (
    <Tooltip title={est === null
      ? `Time since the press. Nothing of this kind has been solved yet on this browser, so there is no estimate to compare it against — the next run will have one.`
      : `Time since the press, against the last measured ${what}: ${fmtSecs(est)}. The estimate is that one measurement and nothing more — a finer mesh, more modes or a contact set that fights will all take longer.`}>
      <Typography sx={{ ...lbl, color: over ? '#fbbf24' : 'var(--text-3)',
                        cursor: 'help', fontFamily: 'monospace' }}>
        solving… {fmtClock(ms)}{est !== null && ` · ~${fmtSecs(est)}`}
      </Typography>
    </Tooltip>
  );
};

/** "solved in 48 s", or "cached · solved in 48 s earlier". */
export function solvedIn(r?: { elapsed_s?: number; solve_time_s?: number;
                               cached?: boolean } | null): string {
  if (!r) return '';
  const s = r.elapsed_s ?? r.solve_time_s;
  if (s === undefined || s === null) return r.cached ? 'cached' : '';
  return r.cached ? `cached · solved in ${fmtSecs(s)} earlier`
                  : `solved in ${fmtSecs(s)}`;
}

export default SolveTimer;
