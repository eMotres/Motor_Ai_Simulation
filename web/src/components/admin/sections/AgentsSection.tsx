// Admin · Agents — MCP traffic (calls/min, 429 throttling) and the job queue,
// which names the agent that started each run. Split out of the old Servers
// panel: compute-host load stays in Servers, per-account cost stays in Usage.
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Paper, Typography, Button, Table, TableBody, TableCell, TableHead, TableRow } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import HelpTip from '../../common/HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const TILE = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, px: 2, py: 1.25, flex: 1, minWidth: 130 } as const;
const LABEL = { fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' } as const;

interface Job {
  run_id: string; owner: string; kind: string; state: string; position?: number;
  elapsed_s?: number; waited_s?: number; eta_s?: number; agent?: string | null; cpu_s?: number;
}
interface AppView {
  mcp: { calls_per_min: number; throttled_429: number };
  jobs: { running: number; queued: number; oldest_wait_s: number; items: Job[] };
}

const dur = (s?: number) => {
  if (!s || s <= 0) return '—';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${(s / 3600).toFixed(1)} h`;
  return `${Math.round(s / 86400)} d`;
};

const Tile: React.FC<{ label: string; value: React.ReactNode; color?: string }> = ({ label, value, color }) => (
  <Box sx={TILE}>
    <Typography sx={LABEL}>{label}</Typography>
    <Typography sx={{ fontSize: 22, fontWeight: 800, color: color ?? 'var(--text-0)' }}>{value}</Typography>
  </Box>
);

const AgentsSection: React.FC = () => {
  const [app, setApp] = useState<AppView | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/admin/cluster/app`);
      setApp(r.ok ? await r.json() : null); setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffect(() => { void load(); const t = setInterval(() => void load(), 10000); return () => clearInterval(t); }, [load]);

  const stopJob = async (rid: string) => {
    if (!window.confirm(`Stop job ${rid}?`)) return;
    await fetch(`${API}/api/admin/cluster/jobs/${encodeURIComponent(rid)}/stop`, { method: 'POST' }); void load();
  };

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>Agents</Typography>
        <HelpTip title="MCP client traffic and the solve queue — which agent (if any) started each running/queued job." />
        <Box sx={{ flex: 1 }} />
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} onClick={() => void load()}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>Refresh</Button>
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171', mb: 1 }}>{err}</Typography>}

      <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', mb: 2 }}>
        <Tile label="MCP calls" value={`${app?.mcp.calls_per_min ?? 0}/min`} />
        <Tile label="Throttled (429)" value={app?.mcp.throttled_429 ?? 0} color={app?.mcp.throttled_429 ? '#fbbf24' : undefined} />
        <Tile label="Jobs running" value={app?.jobs.running ?? 0} color="#60a5fa" />
        <Tile label="Jobs queued" value={app?.jobs.queued ?? 0} />
        <Tile label="Oldest wait" value={dur(app?.jobs.oldest_wait_s)} />
      </Box>

      <Paper sx={{ ...PANEL, p: 2 }}>
        <Table size="small" sx={{
          '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
          '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
        }}>
          <TableHead><TableRow>
            {['owner', 'kind', 'state', 'agent', 'elapsed', 'CPU s', 'ETA', ''].map((h) => <TableCell key={h}>{h}</TableCell>)}
          </TableRow></TableHead>
          <TableBody>
            {(app?.jobs.items.length ?? 0) === 0 && (
              <TableRow><TableCell colSpan={8} sx={{ color: 'var(--text-4)' }}>idle</TableCell></TableRow>
            )}
            {app?.jobs.items.map((j) => (
              <TableRow key={j.run_id}>
                <TableCell>{j.owner}</TableCell><TableCell>{j.kind}</TableCell>
                <TableCell>{j.state}{j.state === 'queued' && j.position ? ` #${j.position}` : ''}</TableCell>
                <TableCell>{j.agent || '—'}</TableCell>
                <TableCell>{dur(j.state === 'running' ? j.elapsed_s : j.waited_s)}</TableCell>
                <TableCell>{j.cpu_s != null ? Math.round(j.cpu_s) : '—'}</TableCell>
                <TableCell>{dur(j.eta_s)}</TableCell>
                <TableCell><Button size="small" color="error" onClick={() => void stopJob(j.run_id)}>Stop</Button></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
};

export default AgentsSection;
