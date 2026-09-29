// Admin · Overview — live load: per-server CPU/RAM, CPU by user, and a "now"
// table of running/queued jobs. Auto-refreshes every 5-10 s, pauses when the
// tab is hidden. Range: 15 min / 1 h / 24 h / 7 d.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Paper, Typography, ToggleButton, ToggleButtonGroup, Table, TableBody,
  TableCell, TableHead, TableRow, Button } from '@mui/material';
import {
  ResponsiveContainer, LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RcTooltip, Legend,
} from 'recharts';
import HelpTip from '../common/HelpTip';
import { mergeNodeSeries } from './liveLoadSeries';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const REFRESH_MS = 7000;
const NODE_COLORS = ['#60a5fa', '#a78bfa', '#4ade80', '#fbbf24', '#f87171', '#22d3ee'];
const USER_COLORS = ['#60a5fa', '#a78bfa', '#4ade80', '#fbbf24', '#f87171', '#22d3ee', '#f472b6', '#94a3b8', '#71717a'];

type Range = '15m' | '1h' | '24h' | '7d';
interface NodePoint { ts: number; cpu: number; mem: number }
interface UserLoad { users: string[]; bucket_s: number; series: Record<string, number>[] }
interface JobItem {
  run_id: string; owner: string; kind: string; state: string; elapsed_s?: number;
  cpu_s?: number; node?: string; agent?: string | null;
}
interface Snapshot { running: number; queued: number; items: JobItem[] }
interface LoadLive {
  range: Range; nodes: Record<string, NodePoint[]>;
  cluster: { cores: number; cpu_used_cores: number; mem_used: number; mem_total: number };
  user_load: UserLoad; snapshot: Snapshot;
}

const dur = (s?: number) => {
  if (!s || s <= 0) return '—';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
};

const RANGES: { v: Range; label: string }[] = [
  { v: '15m', label: '15 min' }, { v: '1h', label: '1 h' },
  { v: '24h', label: '24 h' }, { v: '7d', label: '7 d' },
];

const LiveLoadPanel: React.FC = () => {
  const [range, setRange] = useState<Range>('1h');
  const [data, setData] = useState<LoadLive | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const visibleRef = useRef(true);

  const load = useCallback(async (r: Range) => {
    if (!visibleRef.current) return;
    try {
      const res = await fetch(`${API}/api/admin/load/live?range=${r}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    const onVis = () => { visibleRef.current = document.visibilityState === 'visible'; if (visibleRef.current) void load(range); };
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, [load, range]);

  useEffect(() => {
    void load(range);
    const t = setInterval(() => void load(range), REFRESH_MS);
    return () => clearInterval(t);
  }, [load, range]);

  const fmt = (ts: number) => new Date(ts * 1000).toLocaleString(undefined,
    range === '24h' || range === '7d' ? { day: '2-digit', hour: '2-digit' } : { hour: '2-digit', minute: '2-digit' });

  const stop = async (runId: string) => {
    if (!window.confirm(`Stop job ${runId}?`)) return;
    await fetch(`${API}/api/admin/cluster/jobs/${encodeURIComponent(runId)}/stop`, { method: 'POST' });
    void load(range);
  };

  const nodeIds = data ? Object.keys(data.nodes) : [];
  const cpuRam = mergeNodeSeries(data?.nodes ?? {});

  return (
    <Paper elevation={0} sx={{ ...PANEL, p: 2, mt: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontWeight: 700 }}>Live load</Typography>
        <HelpTip title="Server CPU/RAM and who is using them, refreshed every 7 s. Pauses while this tab is hidden." />
        <Box sx={{ flex: 1 }} />
        <ToggleButtonGroup size="small" exclusive value={range} onChange={(_, v) => v && setRange(v)}>
          {RANGES.map((r) => <ToggleButton key={r.v} value={r.v} sx={{ py: 0, fontSize: 11 }}>{r.label}</ToggleButton>)}
        </ToggleButtonGroup>
      </Box>
      {err && <Typography sx={{ color: '#f87171', fontSize: 12, mb: 1 }}>{err}</Typography>}

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        <Box sx={{ flex: '1 1 380px', minWidth: 320 }}>
          <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)', mb: 0.5 }}>CPU % / RAM % per server</Typography>
          <Box sx={{ height: 190 }}>
            <ResponsiveContainer>
              <LineChart data={cpuRam}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" />
                <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" width={36} />
                <RcTooltip labelFormatter={(v) => fmt(Number(v))} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {nodeIds.map((id, i) => (
                  <Line key={id} type="monotone" dataKey={`${id}_cpu`} name={`${id} CPU`}
                    stroke={NODE_COLORS[i % NODE_COLORS.length]} dot={false} isAnimationActive={false} strokeWidth={1.5} />
                ))}
                <Line type="monotone" dataKey="cluster_cpu" name="cluster mean" stroke="var(--text-4)"
                  strokeDasharray="4 3" dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </Box>
        </Box>

        <Box sx={{ flex: '1 1 380px', minWidth: 320 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
            <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>CPU % by user (top {data?.user_load.users.length ?? 0} + other)</Typography>
            <HelpTip title="Measured CPU-seconds per (minute, user), not estimated. Agent jobs are attributed to the account that submitted them; hover a job in the table below to see client." />
          </Box>
          <Box sx={{ height: 190 }}>
            <ResponsiveContainer>
              <AreaChart data={data?.user_load.series ?? []}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" />
                <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10 }} unit="%" width={36} />
                <RcTooltip labelFormatter={(v) => fmt(Number(v))} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {[...(data?.user_load.users ?? []), 'other'].map((u, i) => (
                  <Area key={u} type="monotone" dataKey={u} name={u} stackId="cpu"
                    stroke={USER_COLORS[i % USER_COLORS.length]} fill={USER_COLORS[i % USER_COLORS.length]}
                    fillOpacity={0.55} isAnimationActive={false} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </Box>
        </Box>
      </Box>

      <Typography sx={{ fontSize: 12, fontWeight: 600, mt: 1.5 }}>Now</Typography>
      <Table size="small">
        <TableHead><TableRow>
          {['user', 'job', 'state', 'client', 'node', 'elapsed', 'CPU-s', ''].map((h) => <TableCell key={h}>{h}</TableCell>)}
        </TableRow></TableHead>
        <TableBody>
          {(data?.snapshot.items ?? []).map((j) => (
            <TableRow key={j.run_id}>
              <TableCell>{j.owner}</TableCell>
              <TableCell>{j.kind}</TableCell>
              <TableCell>{j.state}</TableCell>
              <TableCell>{j.agent ? `🤖 ${j.agent}` : 'web'}</TableCell>
              <TableCell>{j.node ?? '—'}</TableCell>
              <TableCell>{dur(j.elapsed_s)}</TableCell>
              <TableCell>{j.cpu_s?.toFixed(1) ?? '—'}</TableCell>
              <TableCell>
                {j.state === 'running' && (
                  <Button size="small" color="error" sx={{ p: 0, minWidth: 0, fontSize: 11 }}
                    onClick={() => void stop(j.run_id)}>Stop</Button>
                )}
              </TableCell>
            </TableRow>
          ))}
          {!data?.snapshot.items.length && (
            <TableRow><TableCell colSpan={8} sx={{ color: 'var(--text-4)', fontSize: 12 }}>nothing running</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </Paper>
  );
};

export default LiveLoadPanel;
