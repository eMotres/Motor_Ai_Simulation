/** The project's Timeline tab (owner 2026-11-03): one Gantt picture, a row per lane grouped by iteration, the plan bar (light)
 *  under the forecast bar (solid; red when late, green when done), a today line.  Read-only for the project's members; an
 *  admin also gets the edit table (iteration start date, plan weeks, done date, propeller lane, public note), "Create from
 *  cost estimate" and "Freeze baseline".  No captions under fields: one tooltip at most per element (timeline.ts). */
import { useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, CircularProgress, IconButton, MenuItem, Select, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';
import HelpTip from '../../../components/common/HelpTip';
import { developmentApi, DevelopmentApiError } from '../api';
import {
  addIteration, barGeometry, ganttRange, iterationDated, laneTip, monthTicks, putBody, removeLastIteration, setIterationStart, setLaneField,
  setPropeller, statusColor, timelineWeeks, TIMELINE_LANES, dayNumber,
  type AdminTimeline, type ProjectTimeline as View, type TimelineDoc, type TimelineLaneDoc, type TimelineLaneId,
} from '../timeline';

interface Props { projectId: string; isAdmin: boolean; language: string; t: (key: string, args?: Record<string, unknown>) => string; onReady?: (ready: boolean) => void }

const ROW = 26;
const HEAD = 30;
const LABEL_W = 190;
const PX_PER_DAY = 6;

function Gantt({ view, t }: { view: View; t: Props['t'] }) {
  const range = useMemo(() => ganttRange(view), [view]);
  const rows = view.iterations.flatMap((it) => [{ kind: 'iteration' as const, it }, ...it.lanes.map((lane) => ({ kind: 'lane' as const, it, lane }))]);
  const chartW = range.days * PX_PER_DAY;
  const height = HEAD + rows.length * ROW + 6;
  const x = (iso: string) => LABEL_W + (dayNumber(iso) - range.start) * PX_PER_DAY;
  return <Box sx={{ overflowX: 'auto', border: '1px solid var(--line-soft)', borderRadius: '10px', bgcolor: 'var(--panel-2)' }}>
    <svg data-testid="timeline-gantt" width={LABEL_W + chartW} height={height} style={{ display: 'block' }}>
      {monthTicks(range).map((iso) => <g key={iso}>
        <line x1={x(iso)} x2={x(iso)} y1={HEAD - 6} y2={height} stroke="var(--line-soft)" />
        <text x={x(iso) + 3} y={HEAD - 10} fontSize={11} fill="var(--text-3)">{iso.slice(0, 7)}</text>
      </g>)}
      {rows.map((row, i) => {
        const y = HEAD + i * ROW;
        if (row.kind === 'iteration') {
          return <g key={`it-${row.it.index}`}>
            <rect x={0} y={y} width={LABEL_W + chartW} height={ROW} fill="var(--panel)" />
            <text x={8} y={y + 17} fontSize={12.5} fontWeight={600} fill="var(--text-0)">
              {t('timeline.iteration', { n: row.it.index })}{row.it.start_date ? ` · ${row.it.start_date}` : ''}
            </text>
          </g>;
        }
        const lane = row.lane;
        const tip = laneTip(lane, t);
        const plan = barGeometry(lane.plan_start, lane.plan_end, range.start, PX_PER_DAY);
        const fc = barGeometry(lane.forecast_start, lane.forecast_end, range.start, PX_PER_DAY);
        return <g key={`${row.it.index}-${lane.lane}`} data-lane={`${row.it.index}-${lane.lane}`} data-status={lane.status}>
          <title>{tip}</title>
          <text x={20} y={y + 17} fontSize={12} fill={lane.status === 'late' ? '#ef4444' : 'var(--text-1)'}>{t(`timeline.lanes.${lane.lane}`)}</text>
          {!iterationDated(row.it) && <text x={LABEL_W + 8} y={y + 17} fontSize={12} fill="var(--text-2)">{t('timeline.weeksN', { n: timelineWeeks(lane.weeks) })}</text>}
          {plan && <rect x={LABEL_W + plan.x} y={y + 5} width={plan.w} height={ROW - 10} rx={3} fill="rgba(96,165,250,0.18)" stroke="rgba(96,165,250,0.55)" strokeDasharray="3 2" />}
          {fc && <rect x={LABEL_W + fc.x} y={y + 9} width={fc.w} height={ROW - 18} rx={2} fill={statusColor(lane.status)} />}
          {fc && lane.readiness_pct !== null && lane.status !== 'done' && <rect x={LABEL_W + fc.x} y={y + 9} width={(fc.w * lane.readiness_pct) / 100} height={ROW - 18} rx={2} fill="rgba(255,255,255,0.45)" />}
        </g>;
      })}
      <line data-testid="timeline-today" x1={x(view.today)} x2={x(view.today)} y1={HEAD - 6} y2={height} stroke="#f43f5e" strokeWidth={1.5}>
        <title>{t('timeline.today', { date: view.today })}</title>
      </line>
    </svg>
  </Box>;
}

const LANE_FIELDS: (keyof TimelineLaneDoc)[] = ['plan_weeks', 'start', 'end', 'done_date', 'note'];

function Editor({ doc, data, busy, t, onChange }: { doc: TimelineDoc; data: AdminTimeline; busy: boolean; t: Props['t']; onChange: (d: TimelineDoc) => void }) {
  const cell = { px: 0.5, py: 0.25 } as const;
  const input = (value: string, onCommit: (v: string) => void, type: 'date' | 'number' | 'text', width: number, label: string) =>
    <TextField size="small" type={type} value={value} disabled={busy} onChange={(e) => onCommit(e.target.value)}
      inputProps={{ 'aria-label': label, ...(type === 'number' ? { min: 0, max: 104, step: 0.5 } : {}), ...(type === 'text' ? { maxLength: 120 } : {}) }}
      sx={{ width, '& .MuiInputBase-input': { fontSize: 12, py: 0.4, px: 0.75 } }} />;
  const pick = doc.production ? `${doc.production.die}\u0000${doc.production.config}` : '';
  return <Stack spacing={1.5} sx={{ mt: 2 }} data-testid="timeline-editor">
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
      <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('timeline.admin.motor')}</Typography>
      <Select size="small" value={pick} displayEmpty disabled={busy} sx={{ minWidth: 220, fontSize: 12.5, '& .MuiSelect-select': { py: 0.5 } }}
        onChange={(e) => { const v = String(e.target.value); const [die, config] = v.split('\u0000'); onChange({ ...doc, production: v ? { die, config } : null }); }}>
        <MenuItem value="">{t('timeline.admin.motorAuto')}</MenuItem>
        {data.linked_motors.map((m) => <MenuItem key={`${m.die}/${m.config}`} value={`${m.die}\u0000${m.config}`}>{`${m.die} · ${m.config}`}</MenuItem>)}
      </Select>
      <HelpTip title={t('timeline.admin.motorTip')} />
    </Stack>
    {doc.iterations.map((it) => <Box key={it.index} sx={{ border: '1px solid var(--line-soft)', borderRadius: '10px', p: 1 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 0.5 }} flexWrap="wrap">
        <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{t('timeline.iteration', { n: it.index })}</Typography>
        <Tooltip title={t('timeline.admin.startTip')}><span>{input(it.start_date ?? '', (v) => onChange(setIterationStart(doc, it.index, v)), 'date', 150, t('timeline.admin.startDate'))}</span></Tooltip>
        <Stack direction="row" alignItems="center">
          <Switch size="small" checked={!!it.lanes.propeller} disabled={busy} onChange={(e) => onChange(setPropeller(doc, it.index, e.target.checked))}
            inputProps={{ 'aria-label': t('timeline.admin.propeller') }} />
          <Typography sx={{ fontSize: 12 }}>{t('timeline.admin.propeller')}</Typography>
        </Stack>
      </Stack>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 720, '& td, & th': { borderColor: 'var(--line-soft)' } }}>
          <TableHead><TableRow>
            <TableCell sx={{ ...cell, fontSize: 12, fontWeight: 600 }}>{t('timeline.admin.lane')}</TableCell>
            {LANE_FIELDS.map((f) => <TableCell key={f} sx={{ ...cell, fontSize: 12, fontWeight: 600 }}>{t(`timeline.admin.${f}`)}</TableCell>)}
          </TableRow></TableHead>
          <TableBody>
            {TIMELINE_LANES.filter((lid) => it.lanes[lid]).map((lid: TimelineLaneId) => {
              const lane = it.lanes[lid] as TimelineLaneDoc;
              const set = (f: keyof TimelineLaneDoc) => (v: string) => onChange(setLaneField(doc, it.index, lid, f, v));
              return <TableRow key={lid} data-edit={`${it.index}-${lid}`}>
                <TableCell sx={{ ...cell, fontSize: 12.5, whiteSpace: 'nowrap' }}>{t(`timeline.lanes.${lid}`)}</TableCell>
                <TableCell sx={cell}>{input(lane.plan_weeks === null ? '' : String(lane.plan_weeks), set('plan_weeks'), 'number', 80, t('timeline.admin.plan_weeks'))}</TableCell>
                <TableCell sx={cell}>{input(lane.start ?? '', set('start'), 'date', 145, t('timeline.admin.start'))}</TableCell>
                <TableCell sx={cell}>{input(lane.end ?? '', set('end'), 'date', 145, t('timeline.admin.end'))}</TableCell>
                <TableCell sx={cell}>{input(lane.done_date ?? '', set('done_date'), 'date', 145, t('timeline.admin.done_date'))}</TableCell>
                <TableCell sx={cell}>{input(lane.note, set('note'), 'text', 220, t('timeline.admin.note'))}</TableCell>
              </TableRow>;
            })}
          </TableBody>
        </Table>
      </Box>
    </Box>)}
    <Stack direction="row" spacing={0.5}>
      <Tooltip title={t('timeline.admin.addIteration')}><span><IconButton size="small" disabled={busy || doc.iterations.length >= 10} onClick={() => onChange(addIteration(doc))}><AddIcon fontSize="small" /></IconButton></span></Tooltip>
      <Tooltip title={t('timeline.admin.removeIteration')}><span><IconButton size="small" disabled={busy || !doc.iterations.length} onClick={() => onChange(removeLastIteration(doc))}><RemoveIcon fontSize="small" /></IconButton></span></Tooltip>
    </Stack>
  </Stack>;
}

export default function ProjectTimeline({ projectId, isAdmin, language, t, onReady }: Props) {
  const [view, setView] = useState<View | null>(null);
  const [admin, setAdmin] = useState<AdminTimeline | null>(null);
  const [doc, setDoc] = useState<TimelineDoc | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'none' | 'error'>('loading');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);

  const adopt = (a: AdminTimeline) => {
    setAdmin(a); setDoc(a.timeline); setView(a.view); setDirty(false); setState('ready');
    onReady?.(a.timeline.iterations.length > 0);
  };
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    let live = true;
    setState('loading'); setView(null); setAdmin(null); setDoc(null); setError('');
    const load = isAdmin ? developmentApi.adminTimeline(projectId).then((a) => { if (live) adopt(a); })
      : developmentApi.projectTimeline(projectId).then((v) => { if (live) { setView(v); setState('ready'); } });
    load.catch((e) => {
      if (!live) return;
      if (e instanceof DevelopmentApiError && e.status === 404) setState('none');
      else { setError(e instanceof Error ? e.message : String(e)); setState('error'); }
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, isAdmin]);

  const run = async (op: () => Promise<AdminTimeline>) => {
    setBusy(true); setError('');
    try { adopt(await op()); } catch (e) { fail(e); } finally { setBusy(false); }
  };

  if (state === 'loading') return <CircularProgress size={18} />;
  if (state === 'error') return <Alert severity="error">{error}</Alert>;
  if (state === 'none' || !view) return <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('timeline.none')}</Typography>;
  const motor = view.motor ? (language.startsWith('zh') ? view.motor.zh || view.motor.en : view.motor.en || view.motor.zh) : '';

  return <Box data-testid="project-timeline" sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', p: { xs: 1, md: 2 }, minWidth: 0 }}>
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }} flexWrap="wrap" useFlexGap>
      <Typography component="h2" sx={{ fontSize: 16, fontWeight: 600, color: 'var(--text-0)' }}>{t('tabs.timeline')}</Typography>
      {motor && <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>{motor}</Typography>}
      <HelpTip title={view.baseline_on ? `${t('timeline.tip')} ${t('timeline.baselineOn', { date: view.baseline_on })}` : t('timeline.tip')} />
      <Box sx={{ flex: 1 }} />
      {isAdmin && admin && doc && <>
        <Tooltip title={t('timeline.admin.seedTip')}><span>
          <Button size="small" variant="outlined" disabled={busy || !admin.estimate_available} onClick={() => void run(() => developmentApi.seedTimeline(projectId, doc.revision))}>{t('timeline.admin.seed')}</Button>
        </span></Tooltip>
        <Tooltip title={doc.baseline ? t('timeline.baselineOn', { date: doc.baseline.frozen_at.slice(0, 10) }) : t('timeline.admin.baselineTip')}><span>
          <Button size="small" variant="outlined" disabled={busy || dirty || !doc.iterations.some((it) => it.start_date)} onClick={() => void run(() => developmentApi.timelineBaseline(projectId, doc.revision))}>{t('timeline.admin.baseline')}</Button>
        </span></Tooltip>
        <Button size="small" variant="contained" disabled={busy || !dirty} onClick={() => void run(() => developmentApi.saveTimeline(projectId, putBody(doc)))}>{t('timeline.admin.save')}</Button>
      </>}
    </Stack>
    {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
    {view.iterations.length ? <Gantt view={view} t={t} /> : <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('timeline.none')}</Typography>}
    {isAdmin && admin && doc && <Editor doc={doc} data={admin} busy={busy} t={t} onChange={(d) => { setDoc(d); setDirty(true); }} />}
  </Box>;
}
