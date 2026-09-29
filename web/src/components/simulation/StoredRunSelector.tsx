/**
 * StoredRunSelector — which of this duty's stored runs is on screen.
 *
 * One duty is one operating point, and the same point can be solved from
 * several sources: sinusoidal current, the PWM inverter, a 120° block.  Each is
 * saved separately (routes/family.py `runs:`), ▶ brings them all, and this row
 * switches between them INSTANTLY — the run's settings go back on the panel and
 * its waveforms/summary go where a finished solve would have put them, so the
 * charts, the summary card and the charging card show it with nothing solved.
 *
 * Run still solves whatever source the panel is set to, as it always has.
 */
import React, { useEffect, useState } from 'react';
import { Box, Tooltip, CircularProgress } from '@mui/material';
import { activeDuty, dutyKey } from '../../lib/dutySettings';
import {
  RUNS_EVENT, applyStoredRun, driveLabel, dutyRuns, getDutyRun, pickedRun,
  setPickedRun, type StoredRunMeta,
} from '../../lib/dutyRuns';

const StoredRunSelector: React.FC = () => {
  const [runs, setRuns] = useState<StoredRunMeta[]>([]);
  const [pick, setPick] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err,  setErr]  = useState<string | null>(null);
  const [duty, setDuty] = useState(() => activeDuty());

  const reread = () => {
    const a = activeDuty();
    setDuty(a);
    const k = dutyKey(a?.die, a?.config, a?.duty);
    setRuns(dutyRuns(k));
    setPick(pickedRun(k));
  };
  useEffect(() => {
    reread();
    // The list moves when ▶ loads a duty (duty-runs-changed) and the duty
    // itself moves on every catalog load (sim-settings-restored is the LAST
    // thing that fires there, so the row is never a step behind).
    const evs = [RUNS_EVENT, 'sim-settings-restored', 'family-changed'];
    for (const e of evs) window.addEventListener(e, reread);
    return () => { for (const e of evs) window.removeEventListener(e, reread); };
  }, []);

  // One source is not a choice — the row would be a label pretending to be a
  // control.  It appears the moment a second excitation of this point exists.
  if (!duty || runs.length < 2) return null;

  const show = async (drive: string) => {
    if (busy) return;
    setBusy(drive); setErr(null);
    try {
      const k = dutyKey(duty.die, duty.config, duty.duty);
      const run = await getDutyRun(k, duty.die, duty.config, duty.duty, drive);
      if (!run?.payload) throw new Error('its waveforms are gone from disk — re-run and save');
      applyStoredRun(run);
      setPickedRun(k, drive);
      setPick(drive);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
    setBusy(null);
  };

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.6, flexWrap: 'wrap' }}>
      <Tooltip title={`Saved runs of duty '${duty.duty}' — the same operating point solved from different sources. Click to show one: settings, charts and summary switch with nothing recomputed. Run still solves the source the panel is set to.`}>
        <Box component="span" sx={{ fontSize: 10, color: 'var(--text-4)', cursor: 'help' }}>
          saved runs
        </Box>
      </Tooltip>
      {runs.map(r => {
        const on = r.drive === pick;
        return (
          <Tooltip key={r.drive} placement="top"
            title={`${driveLabel(r.drive)}${r.primary ? ' — the duty’s primary result' : ''}`
              + (r.recorded_at ? ` · ${r.recorded_at}` : '')
              + (r.f_switch_hz ? ` · carrier ${(Number(r.f_switch_hz) / 1000).toFixed(1)} kHz` : '')
              + (r.steps ? ` · ${r.steps} steps/period` : '')
              + (r.ripple_pct != null ? ` · ripple ${Number(r.ripple_pct).toFixed(1)} %` : '')
              + (r.stale ? ' · ⚠ solved on a different build/materials' : '')}>
            <Box component="span" onClick={() => void show(r.drive)}
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
      {err && (
        <Box component="span" sx={{ fontSize: 10, color: '#fca5a5' }}>✗ {err}</Box>
      )}
    </Box>
  );
};

export default StoredRunSelector;
