/** Propellers > Designer: stored optimisation runs (the workspace's _runs folder, also runs imported from server jobs).
 *  Owner 2026-11-22: see the optimisation in the application - the best value against rpm per diameter, the run list and,
 *  for one run, how the optimiser converged.  Read-only; "Load" puts the run's best design into the Designer. */
import { useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, MenuItem, Select, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip as RcTooltip, XAxis, YAxis } from 'recharts';
import { useTranslation } from 'react-i18next';

import { convergence, fmt, runMap } from '../../lib/propellerDesign';
import type { Vector } from '../../lib/propellerDesign';
import * as api from '../../lib/propellerDesignApi';
import { Panel } from './DesignerParts';

const TXT = { fontSize: 10, fill: 'var(--text-3)' };
const SERIES = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e', '#17becf', '#8c564b', '#e377c2', '#7f7f7f'];
const OBJ = ['flight_thrust_N', 'thrust_per_shaft_W', 'cooling_air_axial_ms'];

export function StoredRunsPanel({ op, onLoad }: { op: string; onLoad: (v: Vector) => void }) {
  const { t } = useTranslation('simulation');
  const [runs, setRuns] = useState<api.StoredRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [objective, setObjective] = useState('flight_thrust_N');
  const [blades, setBlades] = useState('2');
  const [open, setOpen] = useState<(api.StoredRun & { evaluations: api.StoredEval[] }) | null>(null);

  const refresh = () => { api.listRuns().then((r) => { setRuns(r.runs); setError(null); }).catch((e) => setError(String(e?.message ?? e))); };
  useEffect(refresh, []);

  const mine = useMemo(() => (runs ?? []).filter((r) => r.operating_point === op && r.objectives.includes(objective)
    && (blades === 'all' || r.blades === Number(blades))), [runs, op, objective, blades]);
  const lines = useMemo(() => runMap(mine, objective), [mine, objective]);
  const conv = useMemo(() => (open ? convergence(open.evaluations, objective) : []), [open, objective]);
  const scatter = useMemo(() => (open ? open.evaluations.filter((e) => e.objectives[objective] != null)
    .map((e) => ({ g: e.generation, v: e.objectives[objective] as number, ok: e.feasible })) : []), [open, objective]);

  const openRun = (id: string) => { api.getRun(id).then((r) => { setOpen(r); setError(null); }).catch((e) => setError(String(e?.message ?? e))); };
  const loadBest = () => {
    const b = open?.best?.[objective];
    const e = b ? open?.evaluations.find((x) => x.index === b.index) : undefined;
    if (e) onLoad(e.vector);
  };

  return (
    <Panel title={t('propStoredRuns')} tip={t('propStoredRunsTip')}>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center', mb: 0.5 }} data-testid="prop-stored-runs">
        <Select size="small" value={objective} onChange={(e) => setObjective(String(e.target.value))} sx={{ fontSize: 12 }} inputProps={{ 'aria-label': t('propOptObjectives') }}>
          {OBJ.map((o) => <MenuItem key={o} value={o} sx={{ fontSize: 12 }}>{t(`propOptObj_${o}`)}</MenuItem>)}
        </Select>
        <Select size="small" value={blades} onChange={(e) => setBlades(String(e.target.value))} sx={{ fontSize: 12 }} renderValue={(v) => `${t('propDesignBlades')}: ${v === 'all' ? t('propRunsAll') : v}`}>
          {['2', '3', 'all'].map((b) => <MenuItem key={b} value={b} sx={{ fontSize: 12 }}>{b === 'all' ? t('propRunsAll') : b}</MenuItem>)}
        </Select>
        <Button size="small" onClick={refresh}>{t('propRunsRefresh')}</Button>
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('propRunsCount', { n: mine.length })}</Typography>
      </Box>
      {error && <Alert severity="error">{error}</Alert>}
      {runs !== null && mine.length === 0 ? <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('propRunsNone')}</Typography> : (
        <>
          <Typography sx={{ fontSize: 12, fontWeight: 600, mt: 0.5 }}>{t('propRunsMap')}</Typography>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart margin={{ top: 6, right: 12, bottom: 16, left: 0 }}>
              <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
              <XAxis dataKey="rpm" type="number" domain={['dataMin', 'dataMax']} tick={TXT} tickFormatter={(v) => String(Math.round(Number(v)))}
                label={{ value: 'rpm', position: 'insideBottom', offset: -6, fontSize: 10, fill: 'var(--text-3)' }} allowDuplicatedCategory={false} />
              <YAxis tick={TXT} width={44} domain={['auto', 'auto']} tickFormatter={(v) => fmt(Number(v), 1)} />
              <RcTooltip contentStyle={{ background: 'var(--panel)', fontSize: 12 }} formatter={(v) => fmt(Number(v), 2)} labelFormatter={(v) => `${Math.round(Number(v))} rpm`} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {lines.map((l, k) => (
                <Line key={l.diameter_in} data={l.points} dataKey="value" name={`${l.diameter_in}″`} stroke={SERIES[k % SERIES.length]} isAnimationActive={false} dot />
              ))}
            </LineChart>
          </ResponsiveContainer>
          <Box sx={{ maxHeight: 260, overflow: 'auto', mt: 0.5 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  {['propRunsD', 'propDesignBlades', 'propRunsRpm', 'propRunsPower', 'propRunsFeasible', 'propRunsBest', 'propRunsMach', ''].map((k) => (
                    <TableCell key={k} sx={{ fontSize: 11, py: 0.25 }}>{k ? t(k) : ''}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {mine.map((r) => {
                  const b = r.best?.[objective];
                  return (
                    <TableRow key={r.run_id} selected={open?.run_id === r.run_id}>
                      <TableCell sx={{ fontSize: 11, py: 0.25 }}>{r.diameter_in}″</TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.25 }}>{r.blades}</TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.25 }}>{r.operating ? Math.round(r.operating.rpm) : '-'}</TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.25 }}>{r.operating ? Math.round(r.operating.shaft_power_W) : '-'}</TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.25 }}>{`${r.n_feasible}/${r.n_evaluations}`}</TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.25 }}>{b ? fmt(b.value, 2) : '-'}</TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.25 }}>{b?.tip_mach != null ? fmt(b.tip_mach, 2) : '-'}</TableCell>
                      <TableCell sx={{ py: 0.25 }}><Button size="small" onClick={() => openRun(r.run_id)}>{t('propRunsOpen')}</Button></TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Box>
        </>
      )}
      {open && (
        <Box sx={{ mt: 1 }} data-testid="prop-stored-run">
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            <Typography sx={{ fontSize: 12, fontWeight: 600, flex: 1 }}>
              {`${open.run_id} · ${open.diameter_in}″ · ${open.blades} · ${open.operating ? Math.round(open.operating.rpm) + ' rpm · ' + Math.round(open.operating.shaft_power_W) + ' W' : ''}`}
            </Typography>
            <Button size="small" variant="outlined" onClick={loadBest} disabled={!open.best?.[objective]}>{t('propRunsLoad')}</Button>
          </Box>
          {open.not_checked?.length ? <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{t('propRunsNotChecked', { list: open.not_checked.join(', ') })}</Typography> : null}
          <ResponsiveContainer width="100%" height={170}>
            <LineChart data={conv} margin={{ top: 6, right: 12, bottom: 16, left: 0 }}>
              <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
              <XAxis dataKey="generation" tick={TXT} label={{ value: t('propRunsGeneration'), position: 'insideBottom', offset: -6, fontSize: 10, fill: 'var(--text-3)' }} />
              <YAxis tick={TXT} width={44} domain={['auto', 'auto']} tickFormatter={(v) => fmt(Number(v), 1)} />
              <RcTooltip contentStyle={{ background: 'var(--panel)', fontSize: 12 }} />
              <Line dataKey="best" name={t('propRunsBestSoFar')} stroke="var(--brand)" isAnimationActive={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
          <ResponsiveContainer width="100%" height={170}>
            <ScatterChart margin={{ top: 6, right: 12, bottom: 16, left: 0 }}>
              <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
              <XAxis dataKey="g" type="number" tick={TXT} name={t('propRunsGeneration')} allowDecimals={false} />
              <YAxis dataKey="v" type="number" tick={TXT} width={44} domain={['auto', 'auto']} tickFormatter={(v) => fmt(Number(v), 1)} />
              <RcTooltip contentStyle={{ background: 'var(--panel)', fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Scatter name={t('propRunsFeasibleDots')} data={scatter.filter((p) => p.ok)} fill="var(--brand)" isAnimationActive={false} />
              <Scatter name={t('propRunsInfeasibleDots')} data={scatter.filter((p) => !p.ok)} fill="var(--text-3)" isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
        </Box>
      )}
    </Panel>
  );
}
