// The records under the results of the Controller sim tab (owner 2026-11-23; section 2 of the controller-project proposal): the board
// revisions with their status and date, the test results as they arrive, and the paired motor.  MOTRES staff enter them (the server
// decides: `can_edit`), every project member reads them.  Saved with the records' own revision check (409 keeps the typed rows).
import { useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, CircularProgress, IconButton, MenuItem, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import HelpTip from '../../../components/common/HelpTip';
import { controllerSimApi, isConflict, type SimRecords, type SimRevision, type SimTest } from '../controllerSpec';

type T = (key: string, args?: Record<string, unknown>) => string;
interface Props { projectId: string; ct: T; pairedMotor: string; linkedMotors: ReadonlyArray<{ die: string; config: string }> }

const NEW_REVISION: SimRevision = { rev: '', status: 'draft', date: '', documents: '', note: '' };
const NEW_TEST: SimTest = { date: '', revision: '', name: '', value: '', result: '', ref: '' };
const cell = { fontSize: 12, py: 0.5, verticalAlign: 'top' } as const;

export default function ControllerSimRecords({ projectId, ct, pairedMotor, linkedMotors }: Props) {
  const [doc, setDoc] = useState<SimRecords | null>(null);
  const [revisions, setRevisions] = useState<SimRevision[]>([]);
  const [tests, setTests] = useState<SimTest[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const edits = useRef(0);

  const adopt = (d: SimRecords) => { setDoc(d); setRevisions(d.revisions ?? []); setTests(d.tests ?? []); setDirty(false); setConflict(false); };
  useEffect(() => {
    let live = true;
    setDoc(null); setError('');
    controllerSimApi.get(projectId).then((d) => { if (live) adopt(d); }).catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [projectId]);

  if (error && !doc) return <Alert severity="error">{error}</Alert>;
  if (!doc) return <CircularProgress size={18} />;
  const edit = doc.can_edit;
  const touch = () => { edits.current += 1; setDirty(true); };
  const putRevision = (i: number, patch: Partial<SimRevision>) => { setRevisions((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r))); touch(); };
  const putTest = (i: number, patch: Partial<SimTest>) => { setTests((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r))); touch(); };
  const save = async () => {
    setBusy(true); setError(''); setConflict(false);
    const startedAt = edits.current;
    try {
      const saved = await controllerSimApi.save(projectId, { revisions, tests }, doc.revision);      // revision AND rows from one answer
      if (edits.current === startedAt) adopt(saved); else setDoc(saved);                              // typed while saving: kept, still unsaved
    } catch (e) { setConflict(isConflict(e)); setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const reload = async () => {
    setBusy(true);
    try { adopt(await controllerSimApi.get(projectId)); setError(''); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const text = (value: string, onChange: (v: string) => void, label: string, max: number, width?: number) => edit
    ? <TextField size="small" fullWidth={!width} sx={width ? { width } : undefined} value={value} onChange={(e) => onChange(e.target.value)} inputProps={{ maxLength: max, 'aria-label': label }} />
    : <>{value}</>;
  const date = (value: string, onChange: (v: string) => void, label: string) => edit
    ? <TextField size="small" type="date" sx={{ width: 150 }} value={value} onChange={(e) => onChange(e.target.value)} inputProps={{ 'aria-label': label }} />
    : <>{value}</>;
  const select = <V extends string>(value: V, options: readonly V[], onChange: (v: V) => void, label: string, prefix: string) => edit
    ? <TextField select size="small" sx={{ width: 130 }} value={value} onChange={(e) => onChange(e.target.value as V)} inputProps={{ 'aria-label': label }}>{options.map((o) => <MenuItem key={o} value={o}>{o ? ct(`${prefix}.${o}`) : ct('spec.notSet')}</MenuItem>)}</TextField>
    : <>{value ? ct(`${prefix}.${value}`) : ''}</>;
  const remove = (onClick: () => void) => edit && <IconButton size="small" aria-label={ct('spec.removeRow')} onClick={onClick}><DeleteOutlineIcon sx={{ fontSize: 16 }} /></IconButton>;

  return <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, mt: 1.5 }} data-testid="controller-sim-records">
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <Box sx={{ flex: 1 }} />
      {doc.revision > 0 && <Tooltip title={ct('spec.revisionTip', { by: doc.updated_by ?? '', at: doc.updated_at ? new Date(doc.updated_at).toLocaleString() : '' })}><Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{ct('spec.revision', { n: doc.revision })}</Typography></Tooltip>}
      {edit && <Button size="small" variant="contained" disabled={busy || !dirty} data-testid="controller-sim-save" onClick={() => void save()}>{busy ? <CircularProgress size={14} /> : ct('spec.save')}</Button>}
    </Box>
    {error && <Alert severity="error" action={conflict ? <Button color="inherit" size="small" disabled={busy} onClick={() => void reload()}>{ct('spec.reload')}</Button> : undefined}>{conflict ? ct('sim.conflict') : error}</Alert>}

    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}><Typography sx={{ fontSize: 13, fontWeight: 700 }}>{ct('sim.revisions')}</Typography><HelpTip title={ct('sim.revisionsTip')} /></Box>
      <Box sx={{ overflowX: 'auto' }}><Table size="small" data-testid="controller-sim-revisions">
        <TableHead><TableRow>{['rev', 'status', 'date', 'documents', 'note'].map((k) => <TableCell key={k} sx={cell}>{ct(`sim.col.${k}`)}</TableCell>)}<TableCell sx={cell} /></TableRow></TableHead>
        <TableBody>{revisions.map((r, i) => <TableRow key={i}>
          <TableCell sx={cell}>{text(r.rev, (v) => putRevision(i, { rev: v }), ct('sim.col.rev'), 40, 110)}</TableCell>
          <TableCell sx={cell}>{select(r.status, ['', 'draft', 'frozen', 'released'] as const, (v) => putRevision(i, { status: v }), ct('sim.col.status'), 'sim.status')}</TableCell>
          <TableCell sx={cell}>{date(r.date, (v) => putRevision(i, { date: v }), ct('sim.col.date'))}</TableCell>
          <TableCell sx={cell}>{text(r.documents, (v) => putRevision(i, { documents: v }), ct('sim.col.documents'), 200)}</TableCell>
          <TableCell sx={cell}>{text(r.note, (v) => putRevision(i, { note: v }), ct('sim.col.note'), 500)}</TableCell>
          <TableCell sx={cell}>{remove(() => { setRevisions((rows) => rows.filter((_r, j) => j !== i)); touch(); })}</TableCell>
        </TableRow>)}</TableBody>
      </Table></Box>
      {edit && <Button size="small" startIcon={<AddIcon />} onClick={() => { setRevisions((rows) => [...rows, { ...NEW_REVISION }]); touch(); }}>{ct('spec.addRow')}</Button>}
    </Paper>

    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}><Typography sx={{ fontSize: 13, fontWeight: 700 }}>{ct('sim.tests')}</Typography><HelpTip title={ct('sim.testsTip')} /></Box>
      <Box sx={{ overflowX: 'auto' }}><Table size="small" data-testid="controller-sim-tests">
        <TableHead><TableRow>{['date', 'revision', 'name', 'value', 'result', 'ref'].map((k) => <TableCell key={k} sx={cell}>{ct(`sim.col.${k}`)}</TableCell>)}<TableCell sx={cell} /></TableRow></TableHead>
        <TableBody>{tests.map((r, i) => <TableRow key={i}>
          <TableCell sx={cell}>{date(r.date, (v) => putTest(i, { date: v }), ct('sim.col.date'))}</TableCell>
          <TableCell sx={cell}>{text(r.revision, (v) => putTest(i, { revision: v }), ct('sim.col.revision'), 40, 100)}</TableCell>
          <TableCell sx={cell}>{text(r.name, (v) => putTest(i, { name: v }), ct('sim.col.name'), 120)}</TableCell>
          <TableCell sx={cell}>{text(r.value, (v) => putTest(i, { value: v }), ct('sim.col.value'), 120)}</TableCell>
          <TableCell sx={{ ...cell, color: r.result === 'fail' ? 'error.main' : undefined }}>{select(r.result, ['', 'pass', 'fail'] as const, (v) => putTest(i, { result: v }), ct('sim.col.result'), 'sim.result')}</TableCell>
          <TableCell sx={cell}>{text(r.ref, (v) => putTest(i, { ref: v }), ct('sim.col.ref'), 200)}</TableCell>
          <TableCell sx={cell}>{remove(() => { setTests((rows) => rows.filter((_r, j) => j !== i)); touch(); })}</TableCell>
        </TableRow>)}</TableBody>
      </Table></Box>
      {edit && <Button size="small" startIcon={<AddIcon />} onClick={() => { setTests((rows) => [...rows, { ...NEW_TEST }]); touch(); }}>{ct('spec.addRow')}</Button>}
    </Paper>

    <Paper variant="outlined" sx={{ p: 1.5 }} data-testid="controller-sim-motor">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}><Typography sx={{ fontSize: 13, fontWeight: 700 }}>{ct('sim.pairedMotor')}</Typography><HelpTip title={ct('sim.pairedMotorTip')} /></Box>
      {linkedMotors.length
        ? linkedMotors.map((m) => <Typography key={`${m.die}/${m.config}`} sx={{ fontSize: 12.5 }}>{m.die} / {m.config}</Typography>)
        : <Typography sx={{ fontSize: 12.5, color: pairedMotor ? undefined : 'var(--text-3)' }}>{pairedMotor || ct('sim.standAlone')}</Typography>}
    </Paper>
  </Box>;
}
