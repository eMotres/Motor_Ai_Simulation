// "My compute nodes" — servers the account owns and attaches (docs/BYO_COMPUTE.md).
// Token shown ONCE on creation; the server keeps only its hash.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, Table, TableBody,
  TableCell, TableHead, TableRow, Typography, TextField, Box, Chip, Alert,
  CircularProgress, Tooltip, MenuItem, Select,
} from '@mui/material';
import { getStoredToken } from '../../lib/localAuth';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

interface NodeRow {
  id: string; name: string; status: string; created: number; last_seen: number | null;
  version: string; version_ok: boolean; cores: number; cpu_pct: number | null;
  mem_used: number | null; mem_total: number | null;
}
interface RemoteJob {
  run_id: string; kind: string; state: string; where: string; queued_at: number;
  error: string; provenance?: { cpu_s?: number };
}
interface Created { id: string; token: string; install: { script: string; docker: string } }

const PREF_LABEL: Record<string, string> = {
  platform_only: 'Platform only',
  own_first: 'My nodes first, then platform',
  own_only: 'My nodes only',
};
const when = (s?: number | null) =>
  (s ? new Date(s * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—');
const gb = (b?: number | null) => (b ? `${(b / 1e9).toFixed(1)} GB` : '—');

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredToken();
  const r = await fetch(`${API}/api/nodes${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = ((await r.json()) as { detail?: string }).detail ?? msg; } catch { /* keep */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const statusColor = (s: string) => (s === 'online' ? 'success' : s === 'revoked' ? 'default' : 'warning');

const ComputeNodesDialog: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const [nodes, setNodes] = useState<NodeRow[]>([]);
  const [jobs, setJobs] = useState<RemoteJob[]>([]);
  const [pref, setPref] = useState('platform_only');
  const [version, setVersion] = useState('');
  const [name, setName] = useState('');
  const [created, setCreated] = useState<Created | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true); setErr(null);
    try {
      const [n, j] = await Promise.all([
        call<{ nodes: NodeRow[]; pref: string; platform_version: string }>(''),
        call<{ jobs: RemoteJob[] }>('/jobs/mine?limit=20'),
      ]);
      setNodes(n.nodes); setPref(n.pref); setVersion(n.platform_version); setJobs(j.jobs);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }, []);
  useEffect(() => { if (open) { setCreated(null); void load(); } }, [open, load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); await load(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const add = () => act(async () => {
    const c = await call<Created>('', { method: 'POST', body: JSON.stringify({ name }) });
    setCreated(c); setName('');
  });

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem' }}>
        My compute nodes
        <Tooltip arrow title="Your own servers run your jobs with the same solver version as the platform. They connect out over HTTPS; no ports to open. CPU on your nodes does not count against fair use.">
          <Typography component="span" sx={{ ml: 1, fontSize: 11, color: 'var(--text-3)', cursor: 'help' }}>
            what is this?
          </Typography>
        </Tooltip>
      </DialogTitle>
      <DialogContent sx={{ pt: '8px !important' }}>
        {err && <Alert severity="error" sx={{ mb: 1, py: 0 }}>{err}</Alert>}
        {created && (
          <Alert severity="success" sx={{ mb: 1.5, '& code': { fontSize: 11, wordBreak: 'break-all' } }}
            onClose={() => setCreated(null)}>
            Node <b>{created.id}</b> added. Copy the token now; it is not shown again. Run on the server:
            <Box sx={{ mt: 0.5 }}><code>{created.install.script}</code></Box>
            <Box sx={{ mt: 0.5, opacity: 0.8 }}>or with Docker: <code>{created.install.docker}</code></Box>
          </Alert>
        )}
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 1.5, flexWrap: 'wrap' }}>
          <TextField size="small" label="New node name" value={name}
            onChange={(e) => setName(e.target.value)} sx={{ minWidth: 220 }} />
          <Button variant="outlined" size="small" disabled={busy || !name.trim()} onClick={() => void add()}
            sx={{ textTransform: 'none' }}>Add node</Button>
          <Box sx={{ flex: 1 }} />
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>Run my jobs on</Typography>
          <Select size="small" value={pref} sx={{ fontSize: 12, minWidth: 230 }}
            onChange={(e) => void act(() => call('/prefs', { method: 'PUT', body: JSON.stringify({ pref: e.target.value }) }))}>
            {Object.entries(PREF_LABEL).map(([k, v]) => <MenuItem key={k} value={k} sx={{ fontSize: 12 }}>{v}</MenuItem>)}
          </Select>
          {busy && <CircularProgress size={16} />}
        </Box>
        <Table size="small" sx={{ '& td, & th': { fontSize: 12 } }}>
          <TableHead>
            <TableRow>
              <TableCell>Node</TableCell><TableCell>Status</TableCell><TableCell>Cores</TableCell>
              <TableCell>CPU</TableCell><TableCell>RAM</TableCell><TableCell>Version</TableCell>
              <TableCell>Last seen</TableCell><TableCell align="right" />
            </TableRow>
          </TableHead>
          <TableBody>
            {nodes.map((n) => (
              <TableRow key={n.id} hover sx={{ opacity: n.status === 'revoked' ? 0.45 : 1 }}>
                <TableCell><Tooltip title={n.id} arrow><span>{n.name}</span></Tooltip></TableCell>
                <TableCell><Chip size="small" label={n.status} color={statusColor(n.status)} sx={{ height: 18, fontSize: 10 }} /></TableCell>
                <TableCell>{n.cores || '—'}</TableCell>
                <TableCell>{n.cpu_pct != null ? `${Math.round(n.cpu_pct)} %` : '—'}</TableCell>
                <TableCell>{gb(n.mem_used)} / {gb(n.mem_total)}</TableCell>
                <TableCell>
                  <Tooltip title={n.version_ok ? 'matches the platform' : `platform is ${version}; update the worker`} arrow>
                    <span style={{ color: n.version && !n.version_ok ? '#f87171' : undefined }}>{n.version || '—'}</span>
                  </Tooltip>
                </TableCell>
                <TableCell>{when(n.last_seen)}</TableCell>
                <TableCell align="right">
                  {n.status !== 'revoked'
                    ? <Button size="small" disabled={busy} onClick={() => void act(() => call(`/${n.id}/revoke`, { method: 'POST' }))}
                        sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>Revoke</Button>
                    : <Button size="small" disabled={busy} onClick={() => void act(() => call(`/${n.id}`, { method: 'DELETE' }))}
                        sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>Remove</Button>}
                </TableCell>
              </TableRow>
            ))}
            {nodes.length === 0 && !busy && (
              <TableRow><TableCell colSpan={8} sx={{ textAlign: 'center', color: 'var(--text-4)', py: 2 }}>
                No nodes yet. Add one, then run the install command on your server.
              </TableCell></TableRow>
            )}
          </TableBody>
        </Table>
        {jobs.length > 0 && (
          <>
            <Typography sx={{ fontSize: 12, fontWeight: 600, mt: 2, mb: 0.5 }}>Jobs on my nodes</Typography>
            <Table size="small" sx={{ '& td, & th': { fontSize: 12 } }}>
              <TableHead>
                <TableRow><TableCell>Job</TableCell><TableCell>State</TableCell><TableCell>Where</TableCell>
                  <TableCell>CPU</TableCell><TableCell>Queued</TableCell><TableCell align="right" /></TableRow>
              </TableHead>
              <TableBody>
                {jobs.map((j) => (
                  <TableRow key={j.run_id} hover>
                    <TableCell><Tooltip title={j.run_id} arrow><span>{j.kind}</span></Tooltip></TableCell>
                    <TableCell><Tooltip title={j.error || ''} arrow><span>{j.state}</span></Tooltip></TableCell>
                    <TableCell>{j.where || '—'}</TableCell>
                    <TableCell>{j.provenance?.cpu_s != null ? `${Math.round(j.provenance.cpu_s)} s` : '—'}</TableCell>
                    <TableCell>{when(j.queued_at)}</TableCell>
                    <TableCell align="right">
                      {(j.state === 'queued' || j.state === 'leased') && (
                        <Button size="small" color="error" disabled={busy}
                          onClick={() => void act(async () => {
                            const token = getStoredToken();
                            const r = await fetch(`${API}/api/jobs/${encodeURIComponent(j.run_id)}/cancel`, {
                              method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {},
                            });
                            if (!r.ok) throw new Error(`HTTP ${r.status}`);
                          })}
                          sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>Stop</Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)', ml: 2 }}>Platform version {version}</Typography>
        <Box sx={{ flex: 1 }} />
        <Button onClick={() => void load()} disabled={busy} sx={{ textTransform: 'none' }}>Refresh</Button>
        <Button onClick={onClose} sx={{ textTransform: 'none' }}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

export default ComputeNodesDialog;
