/** The Tests section (owner 2026-10-26): three sub-menus (Parameters, Free-run, Loads); one compact table per test,
 *  measured | model | delta %, one short note per row and the explanation in tooltips; a group whose rows are a series over
 *  speed gets a measured-vs-model chart to its right (stacked below ~900 px).
 *
 *  Sub-menus (owner 2026-11-05): Power lists the load tests by name and highest power; a row opens the test in Loads.
 *  Sub-menus (owner 2026-11-04): Resistance, Inductance, Constants (Kv, Kt, EMF), Bearings, Free-run, Loads, and
 *  Parameters for the rest (insulation, hipot, surge, cogging, LCR, weight, dimensions).  A tab is only a view of the
 *  comparison groups (tableModel.tabOfGroup); the stored records are unchanged.
 *
 *  Production: the factory adds tests, engineers / admins edit and delete them (every change audited on the server).
 *  Development (project motor view): read-only, behind the configuration's "visible to customer" flag for customers. */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, FormControlLabel, IconButton, MenuItem,
  Paper, Select, Switch, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography,
} from '@mui/material';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import NotesIcon from '@mui/icons-material/Notes';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { useAuth } from '../../../contexts/AuthContext';
import { testsApi } from '../api';
import TestVideos from './TestVideos';
import {
  cmpForTab, columnsFor, computeProgress, kindOfTab, loadPlotOptions, loadPlotRows, loadTable, otherBlocks, foldedHeight, shortHead, commonNotes, ownNotes, pointNotes, LOAD_TABLE_MAX_PX, TABLE_ROW_PX,
  loadSeriesOptions, missingActions, perSpeedSeries, pickQuantity, simulationCurves, usesSpeedPlots,
  isVideoName, normalizeTab, powerSummary, quantitySeries, summary, tableBlocks, testsOfTab, unitLabel, type LoadPlotKey, type TableBlock,
} from '../tableModel';
import {
  TEST_TABS, type CompareRow, type CreateDutyAction, type TestCompare, type TestRecord, type TestsPayload, type TestTab,
} from '../types';
import SubTabs from '../../../components/common/SubTabs';
import SeriesChart, { SpeedChart } from './SeriesChart';
import TestEditor from './TestEditor';

export type TestsSource = { kind: 'production' } | { kind: 'project'; projectId: string; showAll?: boolean };

const SUB_KEY = (die: string, config: string) => `tests.sub.v1:${die}|${config}`;
function readSub(die: string, config: string): TestTab {
  try {
    const v = window.localStorage.getItem(SUB_KEY(die, config));
    return normalizeTab(v);                                 // also maps the retired `constants` and `load` values
  } catch { return 'resistance'; }
}
function writeSub(die: string, config: string, v: TestTab) {
  try { window.localStorage.setItem(SUB_KEY(die, config), v); } catch { /* storage off */ }
}

const LEVEL_COLOR: Record<string, string> = { ok: '#22c55e', warn: '#f59e0b', bad: '#f87171' };
// compact table (owner 2026-10-27, tighter 2026-11-05): 20 px rows, small font, numbers right-aligned in tabular figures
const cell = { fontSize: 11, py: 0, px: 0.5, height: TABLE_ROW_PX, lineHeight: `${TABLE_ROW_PX}px`, borderBottom: '1px solid var(--line-soft)' } as const;
const num = { ...cell, fontVariantNumeric: 'tabular-nums' } as const;
const head = { ...cell, fontSize: 10, fontWeight: 700, color: 'var(--text-2)', bgcolor: 'var(--panel-2)' } as const;

/** Wide = the box is at least this many px: table and chart side by side; below it they stack. */
export const SIDE_BY_SIDE_MIN_PX = 900;

function useWide(): [React.RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [wide, setWide] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => setWide(el.getBoundingClientRect().width >= SIDE_BY_SIDE_MIN_PX);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, wide];
}

/** A load sweep with two or more rows at some speed (owner 2026-11-04): one line per speed against torque or current.
 *  A plot selector (torque vs current by default); the model values, where the model has the point, as dashed lines. */
function SpeedLoadChart({ cmp, height }: { cmp: TestCompare | undefined; height: number }) {
  const { t } = useTranslation('production');
  const rows = loadPlotRows(cmp);
  const curves = simulationCurves(cmp);
  const note = cmp?.series?.simulation_note ?? '';
  const options = loadPlotOptions(rows, curves);
  const [sel, setSel] = useState<string>('torque_current');
  const key = (options.find((k) => k === sel) ?? options[0]) as LoadPlotKey | undefined;
  const plot = key ? perSpeedSeries(rows, key, curves) : null;
  if (!key || !plot) return null;
  return (
    <Box data-testid="tests-load-speed-series">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.6, height: 24, mb: 0.5 }}>
        <Typography sx={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-2)' }}>{t('tests.series.label')}</Typography>
        <HelpTip title={t('tests.series.tipSpeeds')} />
        {note && <HelpTip title={note} />}
        <Select size="small" value={key} onChange={(e) => setSel(String(e.target.value))}
          sx={{ height: 24, fontSize: 11.5, minWidth: 200, '& .MuiSelect-select': { py: 0, pl: 1 } }}
          inputProps={{ 'aria-label': t('tests.series.label') }}>
          {options.map((o) => (
            <MenuItem key={o} value={o} sx={{ fontSize: 12, minHeight: 28 }}>{t(`tests.series.plot.${o}`)}</MenuItem>
          ))}
        </Select>
      </Box>
      <SpeedChart key={key} plot={plot} height={height}
        xLabel={t(key === 'torque_current' ? 'tests.series.plot.x_current' : 'tests.series.plot.x_torque')} />
    </Box>
  );
}

/** The chart beside a load sweep's per-point tables (owner 2026-10-27): x = rpm, a compact selector above it picks the
 *  plotted quantity (torque, efficiencies, DC current / power, shaft power, temperatures), measured dots plus the model
 *  curve where the model has the point.  Full height of the tables beside it (capped), sticky while they scroll. */
function LoadSeriesChart({ cmp, tips, height }: { cmp: TestCompare | undefined; tips: Record<string, string>; height: number }) {
  const { t } = useTranslation('production');
  const options = loadSeriesOptions(cmp);
  const [sel, setSel] = useState<string>('torque');
  const q = pickQuantity(options, sel);
  if (!q) return null;
  const name = (k: string, fallback: string) => t(`tests.series.q.${k}`, { defaultValue: fallback });
  return (
    <Box data-testid="tests-load-series">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.6, height: 24, mb: 0.5 }}>
        <Typography sx={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-2)' }}>{t('tests.series.label')}</Typography>
        <HelpTip title={t('tests.series.tip')} />
        <Select size="small" value={q.key} onChange={(e) => setSel(String(e.target.value))}
          sx={{ height: 24, fontSize: 11.5, minWidth: 150, '& .MuiSelect-select': { py: 0, pl: 1 } }}
          inputProps={{ 'aria-label': t('tests.series.label') }}>
          {options.map((o) => (
            <MenuItem key={o.key} value={o.key} sx={{ fontSize: 12, minHeight: 28 }}>
              {name(o.key, o.label)}, {unitLabel(o.unit)}
            </MenuItem>
          ))}
        </Select>
        {q.tip && <HelpTip title={t(`tests.tip.${q.tip}`, { defaultValue: tips[q.tip] ?? '' })} />}
      </Box>
      <SeriesChart key={q.key} series={quantitySeries(q)} height={height} />
    </Box>
  );
}

function CompareTable({ cmp, tips, canCompute, busy, onCompute }: {
  cmp: TestCompare | undefined; tips: Record<string, string>; canCompute: boolean; busy: boolean;
  onCompute: (actions: CreateDutyAction[]) => void;
}) {
  const { t } = useTranslation('production');
  const [boxRef, wide] = useWide();
  const blocks = tableBlocks(cmp);
  if (!blocks.length) return null;
  const tipText = (key?: string | null) => (key ? t(`tests.tip.${key}`, { defaultValue: tips[key] ?? '' }) : '');
  const label = (l: { key: string; label: string; params: Record<string, unknown> }, ns: 'group' | 'row') =>
    t(`tests.${ns}.${l.key}`, { ...l.params, defaultValue: l.label });
  const basisText = (b: TableBlock) => {
    if (!b.basisKey) return b.basis ?? '';
    const params = Object.fromEntries(Object.entries(b.basisParams).filter(([, v]) => v !== null && v !== undefined));
    return t(`tests.basis.${b.basisKey}`, { ...params, defaultValue: b.basis ?? '' });
  };
  const table = loadTable(blocks);
  const tableBasis = table ? Array.from(new Set(table.points.map((p) => basisText(p.block)).filter(Boolean))).join(' · ') : '';
  const tableExtra = !!table && table.points.some((p) => pointNotes(p.cells).length > 0
    || (canCompute && Object.values(p.cells).some((l) => l.row.action)));
  const plotRows = loadPlotRows(cmp);
  const plotCurves = simulationCurves(cmp);
  const perSpeed = usesSpeedPlots(plotRows, plotCurves) && loadPlotOptions(plotRows, plotCurves).length > 0;
  const hasLoadChart = perSpeed || loadSeriesOptions(cmp).length > 0;
  const computeTip = (row: CompareRow) => {
    const a = row.action;
    const g = a?.body.duty.gamma_deg;
    return [t('tests.computeTip'), g === undefined ? '' : t('tests.computeGamma', { gamma: g, source: a?.gamma_source ?? '' })]
      .filter(Boolean).join(' · ');
  };
  return (
    <Box ref={boxRef} sx={hasLoadChart ? { display: 'flex', flexDirection: wide ? 'row' : 'column', gap: 1.2, alignItems: 'flex-start' } : undefined}>
      <Box sx={hasLoadChart ? { flex: wide ? '0 0 48%' : '1 1 auto', minWidth: 0, width: wide ? undefined : '100%' } : undefined}>
      {table && (
        <Box sx={{ mb: 1, overflow: 'auto', maxHeight: LOAD_TABLE_MAX_PX }} data-testid="tests-load-table">
          <Table size="small" stickyHeader sx={{ '& td, & th': { whiteSpace: 'nowrap' } }}>
            <TableHead>
              <TableRow>
                <TableCell sx={head}>
                  <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.4 }}>
                    {t('tests.col.point')}
                    {tableBasis && <HelpTip title={tableBasis} />}
                  </Box>
                </TableCell>
                {table.columns.map((c) => (
                  <React.Fragment key={c.key}>
                    <TableCell sx={head} align="right">
                      <Tooltip arrow placement="top" enterTouchDelay={0} title={[label(c.row, 'row'), tipText(c.row.tip), c.row.detail].filter(Boolean).join(' · ')}>
                        <span>{shortHead(label(c.row, 'row'))}{c.row.unit ? `, ${unitLabel(c.row.unit)}` : ''}</span>
                      </Tooltip>
                    </TableCell>
                    {c.hasModel && <TableCell sx={head} align="right">{t('tests.col.model')}</TableCell>}
                    {c.hasModel && <TableCell sx={head} align="right">{t('tests.col.delta')}</TableCell>}
                  </React.Fragment>
                ))}
                {tableExtra && <TableCell sx={{ ...head, fontWeight: 400, color: 'var(--text-3)' }}>{commonNotes(table).map((n) => t(`tests.note.${n.key}`, n.params)).join(' · ')}</TableCell>}
              </TableRow>
            </TableHead>
            <TableBody>
              {table.points.map(({ block, cells }, pointIndex) => {
                const action = Object.values(cells).find((l) => l.row.action)?.row;
                return (
                  <TableRow key={pointIndex}>
                    <TableCell sx={{ ...cell, color: 'var(--text-1)' }}>{label(block, 'group')}</TableCell>
                    {table.columns.map((c) => {
                      const l = cells[c.key];
                      return (
                        <React.Fragment key={c.key}>
                          <TableCell sx={num} align="right">{l?.measured ?? '—'}</TableCell>
                          {c.hasModel && <TableCell sx={num} align="right">{l?.model ?? '—'}</TableCell>}
                          {c.hasModel && (
                            <TableCell sx={{ ...num, fontWeight: 700, color: l?.level ? LEVEL_COLOR[l.level] : 'var(--text-3)' }} align="right">{l?.delta ?? '—'}</TableCell>
                          )}
                        </React.Fragment>
                      );
                    })}
                    {tableExtra && (
                      <TableCell sx={{ ...cell, color: 'var(--text-3)' }}>
                        {ownNotes(table, cells).map((n) => t(`tests.note.${n.key}`, n.params)).join(' · ')}
                        {canCompute && action?.action && (
                          <Tooltip title={computeTip(action)}><span><Button size="small" disabled={busy}
                            sx={{ ml: 0.5, py: 0, minHeight: 0, lineHeight: `${TABLE_ROW_PX - 2}px`, fontSize: 10.5 }}
                            onClick={() => onCompute([action.action as CreateDutyAction])}>{t('tests.compute')}</Button></span></Tooltip>
                        )}
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Box>
      )}
      {otherBlocks(blocks).map((b) => {
        const basis = basisText(b);
        return (
          <Box key={b.key} sx={{ mb: 0.6 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.6, minWidth: 0, height: TABLE_ROW_PX, bgcolor: 'var(--panel-2)', px: 0.5 }}>
              <Typography noWrap sx={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-1)', flexShrink: 0, maxWidth: '70%' }}>
                {label(b, 'group')}
              </Typography>
              {b.tip && <HelpTip title={tipText(b.tip)} />}
              {basis && (
                <Tooltip arrow placement="top-start" enterTouchDelay={0} title={basis}>
                  <Typography noWrap sx={{ fontSize: 11, color: 'var(--text-3)', minWidth: 0 }}>{basis}</Typography>
                </Tooltip>
              )}
            </Box>
            <Box sx={{ display: 'flex', flexDirection: wide ? 'row' : 'column', gap: 1.2, alignItems: wide ? 'flex-start' : 'stretch' }}>
              <Box sx={{ flex: b.series && wide ? '0 0 45%' : '1 1 auto', maxWidth: b.series ? undefined : 640, minWidth: 0, overflowX: 'auto' }}>
                <Table size="small" sx={{ '& td, & th': { whiteSpace: 'nowrap' } }}>
                  <TableHead>
                    <TableRow>
                      <TableCell sx={head}>{t('tests.col.quantity')}</TableCell>
                      <TableCell sx={head} align="right">{t('tests.col.measured')}</TableCell>
                      <TableCell sx={head} align="right">{t('tests.col.model')}</TableCell>
                      <TableCell sx={{ ...head, width: 48 }} align="right">{t('tests.col.delta')}</TableCell>
                      <TableCell sx={head} />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {b.lines.map((l, i) => (
                      <TableRow key={i}>
                        <TableCell sx={cell}>
                          <Tooltip arrow placement="top-start" enterTouchDelay={0}
                            title={[tipText(l.row.tip), l.row.detail].filter(Boolean).join(' · ')}>
                            <span>{label(l.row, 'row')}{l.row.unit ? <span style={{ color: 'var(--text-3)' }}>, {unitLabel(l.row.unit)}</span> : null}</span>
                          </Tooltip>
                        </TableCell>
                        <TableCell sx={num} align="right">{l.measured}</TableCell>
                        <TableCell sx={num} align="right">{l.model}</TableCell>
                        <TableCell sx={{ ...num, width: 48, fontWeight: 700, color: l.level ? LEVEL_COLOR[l.level] : 'var(--text-3)' }} align="right">{l.delta}</TableCell>
                        <TableCell sx={{ ...cell, color: 'var(--text-3)' }}>
                          {l.note && <span>{t(`tests.note.${l.note.key}`, l.note.params)}</span>}
                          {canCompute && l.row.action && (
                            <Tooltip title={computeTip(l.row)}><span><Button size="small" disabled={busy}
                              sx={{ ml: 0.5, py: 0, minHeight: 0, lineHeight: `${TABLE_ROW_PX - 2}px`, fontSize: 10.5 }}
                              onClick={() => onCompute([l.row.action as CreateDutyAction])}>{t('tests.compute')}</Button></span></Tooltip>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
              {b.series && (
                <Box sx={{ flex: wide ? '1 1 0' : '1 1 auto', minWidth: 0 }}>
                  {/* the chart runs the full height of its table (owner 2026-10-27): one row per line + the header */}
                  <SeriesChart series={b.series} height={wide ? Math.max(190, (b.lines.length + 1) * TABLE_ROW_PX) : 190} />
                </Box>
              )}
            </Box>
          </Box>
        );
      })}
      </Box>
      {hasLoadChart && (
        <Box sx={{ flex: wide ? '1 1 0' : '1 1 auto', minWidth: 0, width: wide ? undefined : '100%',
          position: wide ? 'sticky' : 'static', top: 8 }}>
          {perSpeed
            ? <SpeedLoadChart cmp={cmp} height={wide ? Math.min(520, Math.max(220, foldedHeight(blocks))) : 240} />
            : <LoadSeriesChart cmp={cmp} tips={tips}
              height={wide ? Math.min(520, Math.max(220, foldedHeight(blocks))) : 240} />}
        </Box>
      )}
    </Box>
  );
}

function RawRows({ rec }: { rec: TestRecord }) {
  const { t } = useTranslation('production');
  const [open, setOpen] = useState(false);
  if (!rec.rows?.length) return null;
  const cols = Array.from(new Set([...columnsFor(rec.kind), ...rec.rows.flatMap((r) => Object.keys(r))]))
    .filter((c) => rec.rows!.some((r) => r[c] !== undefined));
  return (
    <Box sx={{ mt: 0.5 }}>
      <Button size="small" onClick={() => setOpen((v) => !v)} sx={{ fontSize: 11, textTransform: 'none' }}>
        {t('tests.rawData')} ({rec.rows.length}) {open ? '▴' : '▾'}
      </Button>
      {open && (
        <Table size="small">
          <TableHead><TableRow>{cols.map((c) => <TableCell key={c} sx={head} align="right">{t(`tests.cols.${c}`, { defaultValue: c })}</TableCell>)}</TableRow></TableHead>
          <TableBody>
            {rec.rows.map((r, i) => (
              <TableRow key={i}>{cols.map((c) => <TableCell key={c} sx={cell} align="right">{r[c] ?? '—'}</TableCell>)}</TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Box>
  );
}

export const TestsPanel: React.FC<{
  die: string; config: string; source: TestsSource;
  /** production document download (the panel itself never fetches files) */
  onOpenDocument?: (fileId: string, name: string) => void;
  onLoadDocument?: (fileId: string, onProgress: (fraction: number) => void) => Promise<Blob>;
}> = ({ die, config, source, onOpenDocument, onLoadDocument }) => {
  const { t } = useTranslation('production');
  // "Compute it" adds a duty to the SHARED configuration: only an admin's catalog write lands there (anybody else's would
  // be a copy in their own workspace), so the button is the admin's
  const { isAdmin } = useAuth();
  const [data, setData] = useState<TestsPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sub, setSubState] = useState<TestTab>(() => readSub(die, config));
  const [editing, setEditing] = useState<{ rec: TestRecord | null } | null>(null);
  const [toDelete, setToDelete] = useState<TestRecord | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [msgError, setMsgError] = useState(false);
  // run ids of the solves "Compute it" queued; while any is open the panel polls the job list and refreshes when they end
  const [pending, setPending] = useState<string[]>([]);
  const unlisted = useRef(0);
  const note = (text: string | null, isError = false) => { setMsg(text); setMsgError(isError); };
  const setSub = (v: TestTab) => { setSubState(v); writeSub(die, config, v); };
  const projectId = source.kind === 'project' ? source.projectId : '';
  const showAll = source.kind === 'project' ? !!source.showAll : false;

  const reload = useCallback(async () => {
    try {
      const d = source.kind === 'project' ? await testsApi.project(projectId, die, config, showAll) : await testsApi.get(die, config);
      setData(d); setErr(null);
    } catch (e) {
      setErr(t('tests.errors.load', { reason: e instanceof Error ? e.message : String(e) }));
    } finally { setLoading(false); }
  }, [die, config, source.kind, projectId, showAll, t]);
  useEffect(() => { setLoading(true); void reload(); }, [reload]);

  const reason = (e: unknown) => (e instanceof Error ? e.message : String(e));
  // "Compute it": the server creates the TEST duties and queues their solve (admin only); the server's own words on failure
  const startCompute = async (actions: CreateDutyAction[]) => {
    if (!actions.length || pending.length) return;
    try {
      const res = await testsApi.compute(die, config, actions);
      const ids = res.jobs.filter((j) => j.state === 'queued' && j.run_id).map((j) => j.run_id as string);
      const refused = res.jobs.find((j) => j.state === 'refused');
      if (ids.length) { unlisted.current = 0; setPending(ids); }
      if (refused) note(t('tests.errors.compute', { reason: refused.error ?? '' }), true);
      else if (ids.length) note(t('tests.computing', { count: ids.length }));
      else note(t('tests.computeNothing'));
    } catch (e) { note(t('tests.errors.compute', { reason: reason(e) }), true); }
  };
  useEffect(() => {
    if (!pending.length) return undefined;
    let stop = false;
    const tick = async () => {
      try {
        const p = computeProgress(pending, await testsApi.jobs());
        if (stop) return;
        // a run the list no longer names (evicted) is not waited for for ever
        unlisted.current = p.unlisted > 0 && p.unlisted === p.running ? unlisted.current + 1 : 0;
        if (p.finished || unlisted.current >= 5) {
          setPending([]);
          await reload();
          if (!stop) note(p.failed ? t('tests.errors.compute', { reason: p.error ?? '' }) : t('tests.computeFinished', { count: p.done }), p.failed > 0);
        } else note(t('tests.computingProgress', { done: p.done, total: p.total }));
      } catch { /* the next tick tries again */ }
    };
    const id = window.setInterval(() => void tick(), 4000);
    return () => { stop = true; window.clearInterval(id); };
  }, [pending, reload, t]);

  if (loading) return <CircularProgress size={18} />;
  if (err) return <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>;
  if (!data) return null;
  const can = data.can;
  const list = testsOfTab(data.tests, data.compare, sub);
  const docName = new Map((data.documents ?? []).map((d) => [d.id, d]));
  const power = powerSummary(data.tests);
  const openTest = (id: string) => {
    setSub('loads');
    window.setTimeout(() => document.getElementById(`test-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  };

  const setVisible = async (v: boolean) => {
    try { await testsApi.setVisibleToCustomer(die, config, v); await reload(); }
    catch (e) { note(t('tests.errors.save', { reason: reason(e) }), true); }
  };
  const doDelete = async () => {
    if (!toDelete) return;
    try { await testsApi.remove(die, config, toDelete.id); setToDelete(null); await reload(); }
    catch (e) { note(t('tests.errors.save', { reason: reason(e) }), true); }
  };
  const canCompute = can.edit && isAdmin;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <SubTabs value={sub} onChange={setSub} ariaLabel={t('tests.tab')}
          items={TEST_TABS.map((k) => ({ id: k, tip: t(`tests.tabsTip.${k}`),
            label: `${t(`tests.tabs.${k}`)} (${k === 'power' ? power.length : testsOfTab(data.tests, data.compare, k).length})` }))} />
        <Box sx={{ flex: 1 }} />
        {!data.model.available && (
          <Tooltip title={t('tests.modelMissingTip')}><Chip size="small" label={t('tests.modelMissing')} sx={{ fontSize: 11 }} /></Tooltip>
        )}
        {can.settings && (
          <Tooltip title={t('tests.visibleTip')}>
            <FormControlLabel sx={{ m: 0, '& .MuiFormControlLabel-label': { fontSize: 12 } }} label={t('tests.visible')}
              control={<Switch size="small" checked={!!data.settings.tests_visible_to_customer}
                onChange={(e) => void setVisible(e.target.checked)} />} />
          </Tooltip>
        )}
        {can.add && <Button size="small" variant="outlined" onClick={() => setEditing({ rec: null })}>{t('tests.add')}</Button>}
      </Box>
      {msg && (
        <Alert severity={msgError ? 'error' : 'info'} onClose={() => note(null)} sx={{ mb: 1, fontSize: 12 }}
          icon={!msgError && pending.length ? <CircularProgress size={14} /> : undefined}>{msg}</Alert>
      )}
      {sub === 'power' && (power.length === 0
        ? <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('tests.none')}</Typography>
        : (
          <Paper variant="outlined" sx={{ bgcolor: 'var(--panel)', overflowX: 'auto' }} data-testid="tests-power">
            <Table size="small" sx={{ '& td, & th': { whiteSpace: 'nowrap' } }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={head}>{t('tests.power.test')}</TableCell>
                  <TableCell sx={head} align="right">
                    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>{t('tests.power.max')}<HelpTip title={t('tests.power.maxTip')} /></Box>
                  </TableCell>
                  <TableCell sx={head} align="right">{t('tests.power.torque')}</TableCell>
                  <TableCell sx={head} align="right">{t('tests.power.speed')}</TableCell>
                  <TableCell sx={head} align="right">{t('tests.power.efficiency')}</TableCell>
                  <TableCell sx={head} align="right">{t('tests.power.coil')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {power.map((l) => (
                  <TableRow key={l.id} hover onClick={() => openTest(l.id)} sx={{ cursor: 'pointer' }}>
                    <TableCell sx={{ ...cell, color: 'var(--text-0)' }}>{[l.date, l.title].filter(Boolean).join(' · ')}</TableCell>
                    <TableCell sx={{ ...num, fontWeight: 700 }} align="right">{l.powerKw.toFixed(1)}</TableCell>
                    <TableCell sx={num} align="right">{l.torque.toFixed(1)}</TableCell>
                    <TableCell sx={num} align="right">{Math.round(l.rpm)}</TableCell>
                    <TableCell sx={num} align="right">{l.effPct === null ? '—' : l.effPct.toFixed(1)}</TableCell>
                    <TableCell sx={num} align="right">{l.tempC === null ? '—' : Math.round(l.tempC)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Paper>
        ))}
      {sub !== 'power' && list.length === 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('tests.none')}</Typography>}
      {list.map((rec) => {
        const cmp = cmpForTab(data.compare[rec.id], rec.kind, sub);       // only this tab's groups
        const s = summary(cmp);
        const missing = canCompute ? missingActions(cmp) : [];
        const bits = [rec.date, rec.title, rec.instrument, rec.profile, rec.conditions?.state].filter(Boolean);
        // what the caller may see: an internal document of a test is not listed for the factory or a project viewer
        const docs = (rec.documents ?? []).flatMap((id) => { const d = docName.get(id); return d ? [d] : []; });
        const videos = onLoadDocument ? docs.filter((d) => isVideoName(d.name)) : [];
        const cond = [rec.conditions?.ambient_c !== undefined ? `${rec.conditions.ambient_c} °C` : '',
          rec.conditions?.humidity_pct !== undefined ? `${rec.conditions.humidity_pct} % RH` : ''].filter(Boolean);
        return (
          <Paper key={rec.id} id={`test-${rec.id}`} variant="outlined" sx={{ p: 1, mb: 1.2, bgcolor: 'var(--panel)', scrollMarginTop: 60 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8, flexWrap: 'wrap', mb: 0.5 }}>
              <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>{bits.join(' · ')}</Typography>
              {cond.length > 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{cond.join(' · ')}</Typography>}
              {s.worst !== null && <Chip size="small" label={`Δ ${s.worst > 0 ? '+' : ''}${s.worst.toFixed(1)} %`}
                sx={{ height: 18, fontSize: 10.5 }} />}
              <Tooltip title={[t('tests.by', { who: rec.operator || rec.created_by || '' }), rec.created_at, rec.updated_by ? `${rec.updated_by} ${rec.updated_at ?? ''}` : ''].filter(Boolean).join(' · ')}>
                <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{rec.operator || rec.created_by}</Typography>
              </Tooltip>
              {rec.note && <Tooltip title={rec.note}><NotesIcon sx={{ fontSize: 16, color: 'var(--text-3)' }} /></Tooltip>}
              {docs.filter((d) => !videos.includes(d)).map((d) => (
                <Chip key={d.id} size="small" label={d.name} sx={{ height: 18, fontSize: 10.5 }}
                  onClick={onOpenDocument ? () => onOpenDocument(d.file_id, d.name) : undefined} />
              ))}
              <Box sx={{ flex: 1 }} />
              {missing.length > 0 && (
                <Tooltip title={t('tests.computeAllTip', { count: missing.length })}>
                  <span><Button size="small" variant="outlined" disabled={pending.length > 0} sx={{ fontSize: 11, py: 0, minHeight: 22 }}
                    onClick={() => void startCompute(missing)}>{t('tests.computeAll', { count: missing.length })}</Button></span>
                </Tooltip>
              )}
              {can.edit && (
                <>
                  <Tooltip title={t('tests.edit')}><IconButton size="small" onClick={() => setEditing({ rec })}><EditOutlinedIcon sx={{ fontSize: 16 }} /></IconButton></Tooltip>
                  <Tooltip title={t('tests.delete')}><IconButton size="small" onClick={() => setToDelete(rec)}><DeleteOutlineIcon sx={{ fontSize: 16 }} /></IconButton></Tooltip>
                </>
              )}
            </Box>
            {videos.length > 0 && onLoadDocument && <TestVideos videos={videos} load={onLoadDocument} onDownload={onOpenDocument} />}
            <CompareTable cmp={cmp} tips={data.tips} canCompute={canCompute} busy={pending.length > 0} onCompute={(a) => void startCompute(a)} />
            <RawRows rec={rec} />
          </Paper>
        );
      })}
      {editing && (
        <TestEditor open die={die} config={config} kind={editing.rec?.kind ?? kindOfTab(sub)} record={editing.rec}
          documents={(data.documents ?? []).map((d) => ({ id: d.id, name: d.name }))}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void reload(); }} />
      )}
      <Dialog open={!!toDelete} onClose={() => setToDelete(null)}>
        <DialogContent sx={{ fontSize: 13 }}>{t('tests.deleteConfirm')}</DialogContent>
        <DialogActions>
          <Button onClick={() => setToDelete(null)}>{t('tests.editor.cancel')}</Button>
          <Button color="error" onClick={() => void doDelete()}>{t('tests.delete')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default TestsPanel;
