/**
 * ReadOnlySimulationView — the Electromagnetic Simulation tab for people who may LOOK but not run (owner 2026-11-04:
 * «эти точки показываем всем, кто работает над проектом … без возможности запуска для всех, кроме меня пока»).
 *
 * Opened from a project's motor row.  It shows what the live tab shows for a point — the point switcher, the Sine / PWM chips,
 * the summary card and every waveform chart — drawn by the SAME components (SimChips, SummaryTable, TransientCharts), fed from
 * two plain GET answers of the project (development routes `/motors/sim` and `/motors/sim/run`).
 *
 * Why not the live panel: SimulationPanel restores a point into the workbench's stores and localStorage and calls
 * `/api/family/activate`, which writes the server's context — the owner's.  This view therefore holds its own state, writes
 * nothing to the workbench (no `sim.*` keys, no stores), calls no solver route and no write route, and offers no Run, Save,
 * Recompute, new / rename / delete point, mesh or settings control.  The server enforces the same (GET only, project
 * membership, motor grant); hiding is not the guard.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogContent, DialogTitle, Tooltip, Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import { developmentApi } from '../../modules/development/api';
import { driveLabel } from '../../lib/dutyRuns';
import { pointsOf, switcherLayout, type Point } from '../../lib/simPoints';
import { driveFor, initialPoint, runMetasOf, type SimStoredPoints, type SimStoredRun } from '../../lib/simReadOnly';
import { PointChips, RunChips } from './SimChips';
import SummaryTable, { type TransientSummary } from './SummaryTable';
import TransientCharts from './TransientCharts';

interface Props {
  projectId: string;
  die: string;
  config: string;
  /** the point to open first (the row's own); falls back to the motor's default point */
  duty?: string | null;
  showAll?: boolean;
}

const errText = (e: unknown, fallback: string): string => {
  const m = String((e as Error)?.message ?? '').trim();
  return m && !/^HTTP \d+$/.test(m) ? m : fallback;
};

export const ReadOnlySimulationView: React.FC<Props> = ({ projectId, die, config, duty = null, showAll = false }) => {
  const { t } = useTranslation('development');
  const [cfg, setCfg] = useState<SimStoredPoints | null>(null);
  const [point, setPoint] = useState<string | null>(null);
  const [drive, setDrive] = useState<string | null>(null);
  const [run, setRun] = useState<SimStoredRun | null>(null);
  // the card TransientCharts derived from THIS run (it adds the raw-curve ripple); never a previous run's
  const [derived, setDerived] = useState<{ run: SimStoredRun; s: TransientSummary } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const cache = useRef(new Map<string, SimStoredRun>());
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const show = useCallback(async (c: SimStoredPoints, name: string, want: string | null) => {
    const d = c.duties.find((x) => x.name === name) ?? null;
    const dr = driveFor(d, want);
    setPoint(name); setDrive(dr); setErr(null);
    if (!dr) { setRun(null); return; }
    const key = `${name}\u0000${dr}`;
    const hit = cache.current.get(key);
    if (hit) { setRun(hit); return; }
    setBusy(`${name}\u0000${dr}`);
    try {
      const r = await developmentApi.motorSimRun(projectId, die, config, name, dr, showAll);
      cache.current.set(key, r);
      if (alive.current) setRun(r);
    } catch (e) {
      if (alive.current) { setRun(null); setErr(errText(e, t('simView.loadFailed'))); }
    }
    if (alive.current) setBusy(null);
  }, [projectId, die, config, showAll, t]);

  useEffect(() => {
    let cancelled = false;
    cache.current.clear();
    setCfg(null); setRun(null); setDerived(null); setErr(null); setPoint(null); setDrive(null);
    void (async () => {
      try {
        const c = await developmentApi.motorSimPoints(projectId, die, config, showAll);
        if (cancelled) return;
        setCfg(c);
        const first = initialPoint(c, duty);
        if (first) await show(c, first, null);
      } catch (e) {
        if (!cancelled) setErr(errText(e, t('simView.loadFailed')));
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, die, config, showAll]);

  const points: Point[] = useMemo(() => pointsOf(cfg, false, driveLabel), [cfg]);
  const layout = switcherLayout(points, false, point);
  const here = cfg?.duties.find((d) => d.name === point) ?? null;
  const runs = useMemo(() => runMetasOf(here), [here]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="h6" sx={{ color: 'var(--text-0)', fontWeight: 700 }}>{t('simView.title')}</Typography>
        <Tooltip title={t('simView.viewOnlyTip')}>
          <Chip label={t('simView.viewOnly')} size="small" sx={{ fontSize: 10, bgcolor: 'var(--panel-2)', color: 'var(--text-2)' }} />
        </Tooltip>
        {layout.chips && point && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.6, flexWrap: 'wrap' }}>
            <Tooltip title={t('simView.pointsTip')}>
              <Box component="span" sx={{ fontSize: 10, color: 'var(--text-4)', cursor: 'help' }}>{t('simView.points')}</Box>
            </Tooltip>
            <PointChips points={points} loaded={point} busy={busy ? busy.split('\u0000')[0] : null}
              onPick={(n) => { if (cfg && !busy) void show(cfg, n, drive); }} />
          </Box>
        )}
        {runs.length >= 2 && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.6, flexWrap: 'wrap' }}>
            <Tooltip title={t('simView.runsTip')}>
              <Box component="span" sx={{ fontSize: 10, color: 'var(--text-4)', cursor: 'help' }}>{t('simView.runs')}</Box>
            </Tooltip>
            <RunChips runs={runs} pick={drive} busy={busy ? busy.split('\u0000')[1] : null}
              onPick={(dr) => { if (cfg && point && !busy && dr !== drive) void show(cfg, point, dr); }} />
          </Box>
        )}
        {busy && <CircularProgress size={12} />}
      </Box>

      {err && <Alert severity="warning" sx={{ py: 0 }}>{err}</Alert>}
      {cfg && !cfg.duties.length && !err && (
        <Typography sx={{ color: 'var(--text-3)', fontSize: 12 }}>{t('simView.noPoints')}</Typography>
      )}
      {cfg && point && !busy && !err && !run && drive == null && cfg.duties.length > 0 && (
        <Typography sx={{ color: 'var(--text-3)', fontSize: 12 }}>{t('simView.noRun')}</Typography>
      )}

      {run && (
        <>
          <SummaryTable summary={derived?.run === run ? derived.s : ((run.summary as unknown as TransientSummary) ?? null)} readOnly />
          <TransientCharts readOnly payloadOverride={run.payload ?? null} onSummary={(s) => setDerived({ run, s })} />
        </>
      )}
    </Box>
  );
};

interface DialogProps extends Props {
  open: boolean;
  onClose: () => void;
}

/** The view in a full-width dialog: opened from a project-motor row, closed with one click, leaves nothing behind. */
export const ReadOnlySimulationDialog: React.FC<DialogProps> = ({ open, onClose, ...rest }) => {
  const { t } = useTranslation('development');
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xl" scroll="paper">
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Box component="span" sx={{ flex: 1, fontSize: 15, fontWeight: 700 }}>{rest.die} / {rest.config}</Box>
        <Button size="small" onClick={onClose}>{t('simView.close')}</Button>
      </DialogTitle>
      <DialogContent dividers>
        {open && <ReadOnlySimulationView {...rest} />}
      </DialogContent>
    </Dialog>
  );
};

export default ReadOnlySimulationView;
