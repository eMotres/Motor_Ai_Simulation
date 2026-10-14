/**
 * Admin · 3D downloads — every STEP download, newest first, with the NDA it rests on, filters (user, company, motor, NDA
 * version), the signed NDA PDF of an online signature and the file hash; plus "trace a file": paste a STEP header line or a
 * download id, or pick a file (hashed in the browser, nothing is uploaded) and get the matching download.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Button, Link, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import HelpTip from '../common/HelpTip';
import { openNdaPdf } from './ndaPdf';
import { ndaLabel, sha256Hex, shortHash, traceText, type DownloadRow } from './downloads3d';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5, mb: 2 } as const;
const HEAD = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', py: 0.5 } as const;
const CELL = { fontSize: 12, color: 'var(--text-1)', py: 0.5, verticalAlign: 'top' } as const;

const Rows: React.FC<{ rows: DownloadRow[] }> = ({ rows }) => (
  <Table size="small">
    <TableHead>
      <TableRow>
        {['When (UTC)', 'User', 'Company', 'Motor', 'NDA', 'File hash', 'Download id'].map((h) => <TableCell key={h} sx={HEAD}>{h}</TableCell>)}
      </TableRow>
    </TableHead>
    <TableBody>
      {rows.map((r, i) => (
        <TableRow key={r.download_id ?? `${r.iso}-${i}`}>
          <TableCell sx={CELL}>{r.iso.replace('T', ' ').replace('Z', '')}</TableCell>
          <TableCell sx={CELL}>{r.email ?? r.user}{r.name && r.name !== r.user ? ` (${r.name})` : ''}</TableCell>
          <TableCell sx={CELL}>{r.company || r.nda_basis?.company || '—'}</TableCell>
          <TableCell sx={CELL}>{r.motor_id}</TableCell>
          <TableCell sx={CELL}>
            {r.nda_basis?.type === 'online' && r.nda_basis.record_id
              ? <Link component="button" underline="hover" sx={{ fontSize: 12 }} onClick={() => void openNdaPdf(r.nda_basis!.record_id!)}>{ndaLabel(r)} · PDF</Link>
              : ndaLabel(r)}
          </TableCell>
          <TableCell sx={{ ...CELL, fontFamily: 'monospace' }} title={r.sha256}>{shortHash(r.sha256)}</TableCell>
          <TableCell sx={{ ...CELL, fontFamily: 'monospace' }}>{r.download_id ?? '—'}</TableCell>
        </TableRow>
      ))}
    </TableBody>
  </Table>
);

const Downloads3dPanel: React.FC = () => {
  const [rows, setRows] = useState<DownloadRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ user: '', company: '', motor: '', nda_version: '' });
  const [text, setText] = useState('');
  const [found, setFound] = useState<DownloadRow[] | null>(null);

  const load = useCallback(async () => {
    try {
      const q = new URLSearchParams({ limit: '500' });
      (Object.keys(f) as (keyof typeof f)[]).forEach((k) => { if (f[k].trim()) q.set(k, f[k].trim()); });
      const r = await fetch(`${API}/api/model3d/downloads?${q}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows(((await r.json()) as { downloads: DownloadRow[] }).downloads); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [f]);
  useEffect(() => { const t = setTimeout(() => void load(), 250); return () => clearTimeout(t); }, [load]);

  const trace = useCallback(async (t: string) => {
    try {
      const r = await fetch(`${API}/api/model3d/trace`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setFound(((await r.json()) as { matches: DownloadRow[] }).matches); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, []);

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    const head = new TextDecoder('latin1').decode(await file.slice(0, 262144).arrayBuffer());
    const t = traceText(head, await sha256Hex(await file.arrayBuffer()));
    setText(t); await trace(t);
  };

  return (
    <Box>
      <Paper sx={PANEL}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1 }}>
          <Typography sx={{ ...HEAD }}>Trace a file</Typography>
          <HelpTip title="Paste a STEP header line, a download id or a file hash, or pick the file: it is hashed in your browser and its header is read here, nothing is uploaded." />
        </Box>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField size="small" value={text} onChange={(e) => setText(e.target.value)} placeholder="Header line, download id or sha256"
            sx={{ flex: 1, minWidth: 260 }} slotProps={{ htmlInput: { style: { fontSize: 12 } } }} />
          <Button size="small" variant="contained" disabled={!text.trim()} onClick={() => void trace(text)} sx={{ textTransform: 'none' }}>Trace</Button>
          <Button size="small" component="label" sx={{ textTransform: 'none' }}>
            Pick a file<input hidden type="file" onChange={(e) => { void onFile(e.target.files?.[0]); e.target.value = ''; }} />
          </Button>
        </Box>
        {found && (found.length ? <Rows rows={found} /> : <Typography sx={{ fontSize: 12, color: 'var(--text-4)', mt: 1 }}>No download matches.</Typography>)}
      </Paper>

      <Paper sx={PANEL}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1 }}>
          <Typography sx={{ ...HEAD }}>3D downloads ({rows?.length ?? 0})</Typography>
          <HelpTip title="Every STEP download, newest first. The NDA column is the basis the file was released on; an online signature links to its signed PDF." />
        </Box>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1 }}>
          {([['user', 'User'], ['company', 'Company'], ['motor', 'Motor'], ['nda_version', 'NDA version']] as const).map(([k, label]) => (
            <TextField key={k} size="small" label={label} value={f[k]} onChange={(e) => setF((s) => ({ ...s, [k]: e.target.value }))}
              sx={{ width: 170 }} slotProps={{ htmlInput: { style: { fontSize: 12 } } }} />
          ))}
        </Box>
        {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
        {rows && !rows.length && <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>No downloads.</Typography>}
        {rows && rows.length > 0 && <Rows rows={rows} />}
      </Paper>
    </Box>
  );
};

export default Downloads3dPanel;
