// Admin · Usage — machine time per account and per agent client (job_usage).
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Paper, Typography, Button, Table, TableBody, TableCell, TableHead, TableRow,
  ToggleButton, ToggleButtonGroup, TextField,
} from '@mui/material';
import HelpTip from '../common/HelpTip';
import PricingData from './PricingData';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

interface Row { key: string; jobs: number; cpu_h: number; wall_h: number; peak_rss: number; share_cluster_pct: number; share_jobs_pct: number }
interface JobRow {
  run_id: string; ts_end: number; user: string; client: string; kind: string; machine: string;
  node: string; wall_s: number; cpu_s: number; peak_rss: number; status: string; cpu_method: string;
}
type Period = '1' | '7' | '30' | 'custom';

const gb = (b: number) => (b / 1024 ** 3).toFixed(1);
const toEpoch = (d: string) => (d ? new Date(d).getTime() / 1000 : undefined);

const UsagePanel: React.FC = () => {
  const [by, setBy] = useState<'user' | 'client'>('user');
  const [period, setPeriod] = useState<Period>('1');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [drill, setDrill] = useState<string | null>(null);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const range = useCallback(() => {
    if (period !== 'custom') return `days=${period}`;
    const s = toEpoch(from); const e = toEpoch(to);
    return [s ? `start=${s}` : '', e ? `end=${e + 86400}` : ''].filter(Boolean).join('&') || 'days=1';
  }, [period, from, to]);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/admin/usage?by=${by}&${range()}`);
      if (!r.ok) throw new Error(`usage HTTP ${r.status}`);
      const d = await r.json(); setRows(d.rows || []); setTotal(d.total_cpu_h || 0); setErr(null);
    } catch (e) { setErr(String(e)); }
  }, [by, range]);
  useEffect(() => { void load(); setDrill(null); }, [load]);

  useEffect(() => {
    if (!drill) { setJobs([]); return; }
    fetch(`${API}/api/admin/usage/jobs?${by}=${encodeURIComponent(drill)}&${range()}`)
      .then((r) => (r.ok ? r.json() : { jobs: [] })).then((d) => setJobs(d.jobs || [])).catch(() => setJobs([]));
  }, [drill, by, range]);

  const csv = async (detail: 'summary' | 'jobs') => {
    const q = detail === 'jobs' && drill ? `&${by}=${encodeURIComponent(drill)}` : '';
    const r = await fetch(`${API}/api/admin/usage.csv?by=${by}&detail=${detail}&${range()}${q}`);
    if (!r.ok) { setErr(`csv HTTP ${r.status}`); return; }
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a'); a.href = url; a.download = `usage_${by}_${detail}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Paper elevation={0} sx={{ ...PANEL, p: 2, mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontWeight: 700 }}>Usage</Typography>
        <HelpTip title="Measured CPU and wall time of finished jobs. Click a row for its jobs." />
        <ToggleButtonGroup size="small" exclusive value={by} onChange={(_, v) => v && setBy(v)}>
          <ToggleButton value="user" sx={{ py: 0 }}>per user</ToggleButton>
          <ToggleButton value="client" sx={{ py: 0 }}>per client</ToggleButton>
        </ToggleButtonGroup>
        <ToggleButtonGroup size="small" exclusive value={period} onChange={(_, v) => v && setPeriod(v)}>
          <ToggleButton value="1" sx={{ py: 0 }}>today</ToggleButton>
          <ToggleButton value="7" sx={{ py: 0 }}>7 d</ToggleButton>
          <ToggleButton value="30" sx={{ py: 0 }}>30 d</ToggleButton>
          <ToggleButton value="custom" sx={{ py: 0 }}>custom</ToggleButton>
        </ToggleButtonGroup>
        {period === 'custom' && (
          <>
            <TextField type="date" size="small" value={from} onChange={(e) => setFrom(e.target.value)} sx={{ width: 150 }} />
            <TextField type="date" size="small" value={to} onChange={(e) => setTo(e.target.value)} sx={{ width: 150 }} />
          </>
        )}
        <Box sx={{ flex: 1 }} />
        <Button size="small" onClick={() => void csv('summary')}>CSV</Button>
        <HelpTip title="Download this table; with a row open, 'Jobs CSV' exports its job list." />
      </Box>
      {err && <Typography sx={{ color: '#f87171', fontSize: 12 }}>{err}</Typography>}
      <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 0.5 }}>total {total.toFixed(2)} CPU-h</Typography>
      <Table size="small">
        <TableHead><TableRow>
          {[by, 'jobs', 'CPU-h', 'wall-h', 'peak RAM GB', '% of jobs', '% of cluster'].map((h) => <TableCell key={h}>{h}</TableCell>)}
        </TableRow></TableHead>
        <TableBody>
          {rows.length === 0 && <TableRow><TableCell colSpan={7} sx={{ color: 'var(--text-4)' }}>no finished jobs in this period</TableCell></TableRow>}
          {rows.map((r) => (
            <TableRow key={r.key} hover selected={drill === r.key} sx={{ cursor: 'pointer' }}
              onClick={() => setDrill(drill === r.key ? null : r.key)}>
              <TableCell>{r.key}</TableCell><TableCell>{r.jobs}</TableCell>
              <TableCell>{r.cpu_h.toFixed(2)}</TableCell><TableCell>{r.wall_h.toFixed(2)}</TableCell>
              <TableCell>{gb(r.peak_rss)}</TableCell><TableCell>{r.share_jobs_pct.toFixed(1)}</TableCell>
              <TableCell>{r.share_cluster_pct.toFixed(2)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {drill && (
        <Box sx={{ mt: 1.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography sx={{ fontWeight: 600, fontSize: 13 }}>Jobs · {drill}</Typography>
            <HelpTip title="apportioned = CPU split with jobs that ran at the same time; exclusive = ran alone." />
            <Button size="small" onClick={() => void csv('jobs')}>Jobs CSV</Button>
          </Box>
          <Table size="small">
            <TableHead><TableRow>
              {['finished', 'user', 'client', 'kind', 'machine', 'node', 'CPU s', 'wall s', 'RAM GB', 'status', 'CPU'].map((h) => <TableCell key={h}>{h}</TableCell>)}
            </TableRow></TableHead>
            <TableBody>
              {jobs.map((j) => (
                <TableRow key={j.run_id}>
                  <TableCell>{new Date(j.ts_end * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })}</TableCell>
                  <TableCell>{j.user}</TableCell><TableCell>{j.client}</TableCell><TableCell>{j.kind}</TableCell>
                  <TableCell>{j.machine || '—'}</TableCell><TableCell>{j.node}</TableCell>
                  <TableCell>{j.cpu_s.toFixed(0)}</TableCell><TableCell>{j.wall_s.toFixed(0)}</TableCell>
                  <TableCell>{gb(j.peak_rss)}</TableCell><TableCell>{j.status}</TableCell><TableCell>{j.cpu_method}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
      <PricingData />
    </Paper>
  );
};

export default UsagePanel;
