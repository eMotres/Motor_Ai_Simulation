/** The Tests section (owner 2026-10-26): three sub-menus (Parameters, Free-run, Loads); one compact table per test,
 *  measured | model | delta %, one short note per row and the explanation in tooltips.
 *
 *  Production: the factory adds tests, engineers / admins edit and delete them (every change audited on the server).
 *  Development (project motor view): read-only, behind the configuration's "visible to customer" flag for customers. */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, FormControlLabel, IconButton, Paper,
  Switch, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Tooltip, Typography,
} from '@mui/material';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import NotesIcon from '@mui/icons-material/Notes';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { useAuth } from '../../../contexts/AuthContext';
import { testsApi } from '../api';
import { columnsFor, summary, tableLines, testsOfKind, unitLabel, type TableLine } from '../tableModel';
import { TEST_KINDS, type CompareRow, type TestCompare, type TestKind, type TestRecord, type TestsPayload } from '../types';
import TestEditor from './TestEditor';

export type TestsSource = { kind: 'production' } | { kind: 'project'; projectId: string; showAll?: boolean };

const SUB_KEY = (die: string, config: string) => `tests.sub.v1:${die}|${config}`;
function readSub(die: string, config: string): TestKind {
  try {
    const v = window.localStorage.getItem(SUB_KEY(die, config));
    return v && (TEST_KINDS as readonly string[]).includes(v) ? (v as TestKind) : 'parameters';
  } catch { return 'parameters'; }
}
function writeSub(die: string, config: string, v: TestKind) {
  try { window.localStorage.setItem(SUB_KEY(die, config), v); } catch { /* storage off */ }
}

const LEVEL_COLOR: Record<string, string> = { ok: '#22c55e', warn: '#f59e0b', bad: '#f87171' };
const cell = { fontSize: 12, py: 0.35, px: 0.8, borderBottom: '1px solid var(--line-soft)' } as const;
const head = { ...cell, fontSize: 11, fontWeight: 700, color: 'var(--text-2)', bgcolor: 'var(--panel-2)' } as const;

function CompareTable({ cmp, tips, canCompute, onComputed }: {
  cmp: TestCompare | undefined; tips: Record<string, string>; canCompute: boolean; onComputed: (msg: string) => void;
}) {
  const { t } = useTranslation('production');
  const lines = tableLines(cmp);
  if (!lines.length) return null;
  const tipText = (key?: string | null) => (key ? t(`tests.tip.${key}`, { defaultValue: tips[key] ?? '' }) : '');
  const label = (l: { key: string; label: string; params: Record<string, unknown> }, ns: 'group' | 'row') =>
    t(`tests.${ns}.${l.key}`, { ...l.params, defaultValue: l.label });
  const compute = async (row: CompareRow) => {
    if (!row.action) return;
    try { await testsApi.createDuty(row.action); onComputed(t('tests.computeDone')); }
    catch (e) { onComputed(t('tests.errors.save', { reason: e instanceof Error ? e.message : String(e) })); }
  };
  return (
    <Table size="small" sx={{ '& td, & th': { whiteSpace: 'nowrap' } }}>
      <TableHead>
        <TableRow>
          <TableCell sx={head}>{t('tests.col.quantity')}</TableCell>
          <TableCell sx={head} align="right">{t('tests.col.measured')}</TableCell>
          <TableCell sx={head} align="right">{t('tests.col.model')}</TableCell>
          <TableCell sx={head} align="right">{t('tests.col.delta')}</TableCell>
          <TableCell sx={head} />
        </TableRow>
      </TableHead>
      <TableBody>
        {lines.map((l: TableLine, i) => l.type === 'group' ? (
          <TableRow key={`g${i}`}>
            <TableCell colSpan={5} sx={{ ...cell, fontWeight: 700, color: 'var(--text-1)', bgcolor: 'var(--panel)' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                {label(l, 'group')}{l.tip && <HelpTip title={tipText(l.tip)} />}
              </Box>
            </TableCell>
          </TableRow>
        ) : (
          <TableRow key={`r${i}`}>
            <TableCell sx={{ ...cell, pl: 2 }}>
              <Tooltip arrow placement="top-start" enterTouchDelay={0}
                title={[tipText(l.row.tip), l.row.detail].filter(Boolean).join(' · ')}>
                <span>{label(l.row, 'row')}{l.row.unit ? <span style={{ color: 'var(--text-3)' }}>, {unitLabel(l.row.unit)}</span> : null}</span>
              </Tooltip>
            </TableCell>
            <TableCell sx={cell} align="right">{l.measured}</TableCell>
            <TableCell sx={cell} align="right">{l.model}</TableCell>
            <TableCell sx={{ ...cell, fontWeight: 700, color: l.level ? LEVEL_COLOR[l.level] : 'var(--text-3)' }} align="right">{l.delta}</TableCell>
            <TableCell sx={{ ...cell, color: 'var(--text-3)' }}>
              {l.note && <span>{t(`tests.note.${l.note.key}`, l.note.params)}</span>}
              {canCompute && l.row.action && (
                <Tooltip title={t('tests.computeTip')}><Button size="small" sx={{ ml: 0.5, py: 0, fontSize: 11 }}
                  onClick={() => void compute(l.row)}>{t('tests.compute')}</Button></Tooltip>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
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
}> = ({ die, config, source, onOpenDocument }) => {
  const { t } = useTranslation('production');
  // "Compute it" adds a duty to the SHARED configuration: only an admin's catalog write lands there (anybody else's would
  // be a copy in their own workspace), so the button is the admin's
  const { isAdmin } = useAuth();
  const [data, setData] = useState<TestsPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sub, setSubState] = useState<TestKind>(() => readSub(die, config));
  const [editing, setEditing] = useState<{ rec: TestRecord | null } | null>(null);
  const [toDelete, setToDelete] = useState<TestRecord | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const setSub = (v: TestKind) => { setSubState(v); writeSub(die, config, v); };
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

  if (loading) return <CircularProgress size={18} />;
  if (err) return <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>;
  if (!data) return null;
  const can = data.can;
  const list = testsOfKind(data.tests, sub);
  const docName = new Map((data.documents ?? []).map((d) => [d.id, d]));

  const setVisible = async (v: boolean) => {
    try { await testsApi.setVisibleToCustomer(die, config, v); await reload(); }
    catch (e) { setMsg(t('tests.errors.save', { reason: e instanceof Error ? e.message : String(e) })); }
  };
  const doDelete = async () => {
    if (!toDelete) return;
    try { await testsApi.remove(die, config, toDelete.id); setToDelete(null); await reload(); }
    catch (e) { setMsg(t('tests.errors.save', { reason: e instanceof Error ? e.message : String(e) })); }
  };

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Tabs value={sub} onChange={(_, v) => setSub(v as TestKind)}
          sx={{ minHeight: 30, '& .MuiTab-root': { minHeight: 30, textTransform: 'none', fontSize: 12.5, py: 0.3 } }}>
          {TEST_KINDS.map((k) => (
            <Tab key={k} value={k} label={<Tooltip title={t(`tests.subTip.${k}`)}><span>
              {t(`tests.sub.${k}`)} ({testsOfKind(data.tests, k).length})</span></Tooltip>} />
          ))}
        </Tabs>
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
      {msg && <Alert severity="info" onClose={() => setMsg(null)} sx={{ mb: 1, fontSize: 12 }}>{msg}</Alert>}
      {list.length === 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('tests.none')}</Typography>}
      {list.map((rec) => {
        const cmp = data.compare[rec.id];
        const s = summary(cmp);
        const bits = [rec.date, rec.title, rec.instrument, rec.profile, rec.conditions?.state].filter(Boolean);
        const cond = [rec.conditions?.ambient_c !== undefined ? `${rec.conditions.ambient_c} °C` : '',
          rec.conditions?.humidity_pct !== undefined ? `${rec.conditions.humidity_pct} % RH` : ''].filter(Boolean);
        return (
          <Paper key={rec.id} variant="outlined" sx={{ p: 1, mb: 1.2, bgcolor: 'var(--panel)' }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8, flexWrap: 'wrap', mb: 0.5 }}>
              <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>{bits.join(' · ')}</Typography>
              {cond.length > 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{cond.join(' · ')}</Typography>}
              {s.worst !== null && <Chip size="small" label={`Δ ${s.worst > 0 ? '+' : ''}${s.worst.toFixed(1)} %`}
                sx={{ height: 18, fontSize: 10.5 }} />}
              <Tooltip title={[t('tests.by', { who: rec.operator || rec.created_by || '' }), rec.created_at, rec.updated_by ? `${rec.updated_by} ${rec.updated_at ?? ''}` : ''].filter(Boolean).join(' · ')}>
                <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{rec.operator || rec.created_by}</Typography>
              </Tooltip>
              {rec.note && <Tooltip title={rec.note}><NotesIcon sx={{ fontSize: 16, color: 'var(--text-3)' }} /></Tooltip>}
              {(rec.documents ?? []).map((id) => {
                const d = docName.get(id);
                return <Chip key={id} size="small" label={d?.name ?? id} sx={{ height: 18, fontSize: 10.5 }}
                  onClick={d && onOpenDocument ? () => onOpenDocument(d.file_id, d.name) : undefined} />;
              })}
              <Box sx={{ flex: 1 }} />
              {can.edit && (
                <>
                  <Tooltip title={t('tests.edit')}><IconButton size="small" onClick={() => setEditing({ rec })}><EditOutlinedIcon sx={{ fontSize: 16 }} /></IconButton></Tooltip>
                  <Tooltip title={t('tests.delete')}><IconButton size="small" onClick={() => setToDelete(rec)}><DeleteOutlineIcon sx={{ fontSize: 16 }} /></IconButton></Tooltip>
                </>
              )}
            </Box>
            <CompareTable cmp={cmp} tips={data.tips} canCompute={can.edit && isAdmin} onComputed={setMsg} />
            <RawRows rec={rec} />
          </Paper>
        );
      })}
      {editing && (
        <TestEditor open die={die} config={config} kind={editing.rec?.kind ?? sub} record={editing.rec}
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
