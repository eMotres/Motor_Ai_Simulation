/**
 * The two rows of chips of the Electromagnetic Simulation tab, as PURE presentation: the computed POINTS of a motor
 * (SimPointSwitcher) and the SOURCES one point was run on — Sine / PWM (StoredRunSelector).
 *
 * They hold no state and fetch nothing.  The live tab drives them from the catalog tree and the workbench stores; the
 * read-only project view (ReadOnlySimulationView, owner 2026-11-04) drives the very same chips from a read-only answer, so a
 * member sees the same switcher without any of the workbench behind it.
 */
import React from 'react';
import { Box, Tooltip, CircularProgress } from '@mui/material';
import { driveLabel, type StoredRunMeta } from '../../lib/dutyRuns';
import type { Point } from '../../lib/simPoints';

interface PointChipsProps {
  points: readonly Point[];
  /** the point on screen */
  loaded: string;
  /** the point (or action) in flight; the chip shows a spinner and clicks are ignored */
  busy: string | null;
  onPick: (name: string) => void;
}

/** One chip per point: green, amber when computed on an older build; the loaded one is filled; the default carries ★. */
export const PointChips: React.FC<PointChipsProps> = ({ points, loaded, busy, onPick }) => (
  <>
    {points.map((p) => {
      const on = p.name === loaded;
      return (
        <Tooltip key={p.name} placement="top" title={p.tip}>
          <Box component="span" onClick={() => { if (!on) onPick(p.name); }}
            sx={{ fontSize: 10.5, fontWeight: 600, px: 0.7, py: '1px', borderRadius: '3px',
                  cursor: busy ? 'wait' : (on ? 'default' : 'pointer'), lineHeight: 1.6,
                  color: on ? '#0b1220' : (p.stale ? '#f59e0b' : '#34d399'),
                  bgcolor: on ? (p.stale ? '#f59e0b' : '#34d399') : 'transparent',
                  border: `1px solid ${p.stale ? '#f59e0b88' : '#34d39966'}`,
                  '&:hover': { borderColor: p.stale ? '#f59e0b' : '#34d399' } }}>
            {busy === p.name
              ? <CircularProgress size={9} sx={{ color: 'inherit' }} />
              : <>{p.stale ? '⚠ ' : ''}{p.name}{p.isDefault ? ' ★' : ''}</>}
          </Box>
        </Tooltip>
      );
    })}
  </>
);

interface RunChipsProps {
  runs: readonly StoredRunMeta[];
  pick: string | null;
  busy: string | null;
  onPick: (drive: string) => void;
}

/** One chip per stored source of the point (Sine, PWM, …); the shown one is filled. */
export const RunChips: React.FC<RunChipsProps> = ({ runs, pick, busy, onPick }) => (
  <>
    {runs.map((r) => {
      const on = r.drive === pick;
      return (
        <Tooltip key={r.drive} placement="top"
          title={`${driveLabel(r.drive)}${r.primary ? ' — the duty’s primary result' : ''}`
            + (r.recorded_at ? ` · ${r.recorded_at}` : '')
            + (r.f_switch_hz ? ` · carrier ${(Number(r.f_switch_hz) / 1000).toFixed(1)} kHz` : '')
            + (r.steps ? ` · ${r.steps} steps/period` : '')
            + (r.ripple_pct != null ? ` · ripple ${Number(r.ripple_pct).toFixed(1)} %` : '')
            + (r.stale ? ' · ⚠ solved on a different build/materials' : '')}>
          <Box component="span" onClick={() => onPick(r.drive)}
            sx={{ fontSize: 10.5, fontWeight: 600, px: 0.7, py: '1px',
                  borderRadius: '3px', cursor: busy ? 'wait' : 'pointer',
                  lineHeight: 1.6,
                  color: on ? '#0b1220' : (r.stale ? '#f59e0b' : '#38bdf8'),
                  bgcolor: on ? (r.stale ? '#f59e0b' : '#38bdf8') : 'transparent',
                  border: `1px solid ${r.stale ? '#f59e0b88' : '#38bdf866'}`,
                  '&:hover': { borderColor: r.stale ? '#f59e0b' : '#38bdf8' } }}>
            {busy === r.drive
              ? <CircularProgress size={9} sx={{ color: 'inherit' }} />
              : <>{r.stale ? '⚠' : ''}{driveLabel(r.drive)}</>}
          </Box>
        </Tooltip>
      );
    })}
  </>
);
