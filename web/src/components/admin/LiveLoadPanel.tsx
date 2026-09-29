// Admin · Overview — live load, as three charts: (1) CPU % per server, (2)
// RAM % per server, (3) CPU by user and process (job-queue work by account,
// plus everything that runs outside the queue — docker containers and host
// processes) — plus a per-server strip (cores/threads) and "now" tables for
// jobs and outside-app containers. Auto-refreshes every 5-10 s, pauses when
// the tab is hidden. Range: 15 min / 1 h / 24 h / 7 d.
//
// Colour: see liveLoadColors.ts for the full derivation. Short version — a
// validated categorical eight (dataviz skill), split so users (6 slots,
// hash-stable) can never collide with the reserved outside-app family
// (orange = containers, aqua = host), plus one new muted-warm pair for
// "app (idle/overhead)". Every colour comes from that module; nothing here
// is a hand-picked hex.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Paper, Typography, ToggleButton, ToggleButtonGroup, Table, TableBody,
  TableCell, TableHead, TableRow, Button, useTheme } from '@mui/material';
import {
  ResponsiveContainer, ComposedChart, AreaChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RcTooltip, Legend,
} from 'recharts';
import HelpTip from '../common/HelpTip';
import {
  mergeNodeSeries, mergeLoadSeries, isCollectingHistory, serverLabel, threadsUsed,
  cpuTooltipValue, memTooltipValue, clusterLine,
} from './liveLoadSeries';
import { serverColor, userColor, containerColor, hostColor, overheadColor, type Mode } from './liveLoadColors';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const REFRESH_MS = 7000;
const STATUS_DOT: Record<string, string> = {
  online: '#4ade80', offline: '#f87171', never: 'var(--text-4)', revoked: 'var(--text-4)',
};
// cluster_monitor.HOST_KEY / OTHER_KEY: literal strings the backend uses for
// the "processes outside any container" bucket and the outside-app overflow
// bucket (kept distinct from job_usage's own "other" -- see mergeLoadSeries).
const HOST_KEY = 'host';
const OUTSIDE_OTHER_KEY = 'outside-other';
const MUTED = 'var(--text-4)';

type Range = '15m' | '1h' | '24h' | '7d';
interface NodePoint { ts: number; cpu: number; mem: number }
interface NodeNow {
  id: string; name: string; status: string; cores?: number; cores_physical?: number;
  cpu?: number; mem_total?: number;
}
interface UserLoad { users: string[]; bucket_s: number; series: Record<string, number>[] }
interface OutsideNow { node: string; name: string; cpu: number; mem: number; uptime_s: number | null }
interface OutsideApp {
  items: string[]; app_overhead_key: string | null; bucket_s: number;
  series: Record<string, number>[]; now: OutsideNow[];
}
interface JobItem {
  run_id: string; owner: string; kind: string; state: string; elapsed_s?: number;
  cpu_s?: number; node?: string; agent?: string | null;
}
interface Snapshot { running: number; queued: number; items: JobItem[] }
interface LoadLive {
  range: Range; nodes: Record<string, NodePoint[]>; nodes_now: NodeNow[];
  cluster: { cores: number; cpu_used_cores: number; mem_used: number; mem_total: number };
  user_load: UserLoad; outside_app: OutsideApp; snapshot: Snapshot;
  monitoring_since: number | null;
}

const dur = (s?: number | null) => {
  if (!s || s <= 0) return '—';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${(s / 3600).toFixed(1)} h`;
  return `${Math.round(s / 86400)} d`;
};
const gb = (b?: number) => (b ? (b / 1024 ** 3).toFixed(1) : '0');
const pctFmt = (v: unknown) => `${Number(v).toFixed(1)} %`;

/** "outside app: mesher_1" for a real container/host bucket, "app (idle/overhead)"
 *  for the app's own containers' unattributed CPU, "outside app: other" for the
 *  overflow past the top-N containers/host. */
const outsideLabel = (key: string, overheadKey: string | null) => {
  if (key === overheadKey) return 'app (idle/overhead)';
  if (key === OUTSIDE_OTHER_KEY) return 'outside app: other';
  if (key === HOST_KEY) return 'outside app: host (no container)';
  return `outside app: ${key}`;
};

const RANGES: { v: Range; label: string }[] = [
  { v: '15m', label: '15 min' }, { v: '1h', label: '1 h' },
  { v: '24h', label: '24 h' }, { v: '7d', label: '7 d' },
];

/** Vertical fill gradient, full-strength colour at the top fading toward the
 *  surface at the bottom -- the "filled with colour gradient" area look,
 *  shared by the CPU and RAM per-server charts. */
const GradientDef: React.FC<{ id: string; color: string }> = ({ id, color }) => (
  <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
    <stop offset="5%" stopColor={color} stopOpacity={0.75} />
    <stop offset="95%" stopColor={color} stopOpacity={0.06} />
  </linearGradient>
);

const ChartHead: React.FC<{ title: string; help: string }> = ({ title, help }) => (
  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5, flexWrap: 'wrap' }}>
    <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{title}</Typography>
    <HelpTip title={help} />
  </Box>
);

const LiveLoadPanel: React.FC = () => {
  const [range, setRange] = useState<Range>('1h');
  const [data, setData] = useState<LoadLive | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const visibleRef = useRef(true);
  const mode: Mode = useTheme().palette.mode === 'dark' ? 'dark' : 'light';

  const load = useCallback(async (r: Range) => {
    if (!visibleRef.current) return;
    try {
      // top=6: matches the 6-slot user palette exactly, so "top N users" never
      // outnumbers the distinct hash-stable colours available for them.
      const res = await fetch(`${API}/api/admin/load/live?range=${r}&top=6`);
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
  const fmtHHMM = (ts: number) => new Date(ts * 1000).toLocaleTimeString(undefined,
    { hour: '2-digit', minute: '2-digit' });

  const stop = async (runId: string) => {
    if (!window.confirm(`Stop job ${runId}?`)) return;
    await fetch(`${API}/api/admin/cluster/jobs/${encodeURIComponent(runId)}/stop`, { method: 'POST' });
    void load(range);
  };

  const nodeIds = data ? Object.keys(data.nodes) : [];
  const cpuRam = mergeNodeSeries(data?.nodes ?? {});
  const nodesNow = data?.nodes_now ?? [];
  const nodeMeta: Record<string, NodeNow> = Object.fromEntries(nodesNow.map((n) => [n.id, n]));
  const collectingServers = isCollectingHistory(cpuRam.length, data?.monitoring_since ?? null);

  const serverLegendLabel = (id: string) =>
    serverLabel(id, { threads: nodeMeta[id]?.cores, cores: nodeMeta[id]?.cores_physical });

  const cpuTooltip = (value: unknown, _name: unknown, entry: { dataKey?: unknown }) => {
    const key = String(entry?.dataKey ?? '');
    if (key === 'cluster_cpu') return [pctFmt(value), 'cluster CPU (mean)'];
    const meta = nodeMeta[key];
    return [cpuTooltipValue(Number(value), meta?.cores), `${serverLegendLabel(key)} CPU`];
  };
  const memTooltip = (value: unknown, _name: unknown, entry: { dataKey?: unknown }) => {
    const key = String(entry?.dataKey ?? '');
    if (key === 'cluster_mem') return [pctFmt(value), 'cluster RAM (mean)'];
    const meta = nodeMeta[key];
    return [memTooltipValue(Number(value), meta?.mem_total), `${serverLegendLabel(key)} RAM`];
  };

  const loadSeries = mergeLoadSeries(data?.user_load.series ?? [], data?.outside_app.series ?? []);
  const users = data?.user_load.users ?? [];
  const outsideKeys = data?.outside_app.items ?? [];
  const overheadKey = data?.outside_app.app_overhead_key ?? null;
  const collecting = isCollectingHistory(loadSeries.length, data?.monitoring_since ?? null);
  const outsideNow = data?.outside_app.now ?? [];
  // "agent/MCP clients marked": a user currently shown with a running agent-
  // submitted job (per the Now table's own convention, 🤖 <client>) gets that
  // marker in the chart legend/tooltip too. Snapshot-only (the live queue),
  // not a per-minute history fact -- an honest limitation, noted for anyone
  // wondering why a user's marker can flicker as their job finishes.
  const agentUsers = new Set(
    (data?.snapshot.items ?? []).filter((j) => j.agent && j.state === 'running').map((j) => j.owner));
  const userLegendLabel = (u: string) => (u === 'other' ? 'other' : (agentUsers.has(u) ? `🤖 ${u}` : u));
  const loadTooltip = (value: unknown, _name: unknown, entry: { dataKey?: unknown }) => {
    const key = String(entry?.dataKey ?? '');
    if (key === 'other') return [pctFmt(value), 'other'];
    if (key === overheadKey) return [pctFmt(value), 'app (idle/overhead)'];
    if (key === OUTSIDE_OTHER_KEY) return [pctFmt(value), 'outside app: other'];
    if (outsideKeys.includes(key)) return [pctFmt(value), outsideLabel(key, overheadKey)];
    return [pctFmt(value), userLegendLabel(key)];
  };

  return (
    <Paper elevation={0} sx={{ ...PANEL, p: 2, mt: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontWeight: 700 }}>Live load</Typography>
        <HelpTip title="Server CPU/RAM and who — or what — is using them, refreshed every 7 s. Pauses while this tab is hidden." />
        <Box sx={{ flex: 1 }} />
        <ToggleButtonGroup size="small" exclusive value={range} onChange={(_, v) => v && setRange(v)}>
          {RANGES.map((r) => <ToggleButton key={r.v} value={r.v} sx={{ py: 0, fontSize: 11 }}>{r.label}</ToggleButton>)}
        </ToggleButtonGroup>
      </Box>
      {err && <Typography sx={{ color: '#f87171', fontSize: 12, mb: 1 }}>{err}</Typography>}

      {!!nodesNow.length && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
          {nodesNow.map((n) => (
            <Box key={n.id} sx={{ display: 'flex', alignItems: 'center', gap: 0.5, fontSize: 10.5,
              color: 'var(--text-3)', bgcolor: 'var(--panel-3, rgba(255,255,255,0.04))', borderRadius: 1, px: 0.75, py: 0.25 }}>
              <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: serverColor(n.id, mode) }} title="series colour" />
              <Box sx={{ width: 6, height: 6, borderRadius: '50%', bgcolor: STATUS_DOT[n.status] ?? 'var(--text-4)' }} title={n.status} />
              <span>{n.name}</span>
              <span>·</span>
              <span>{n.cores_physical ? `${n.cores_physical}c/${n.cores ?? '?'}t` : n.cores ? `${n.cores} threads` : '—'}</span>
              <span>·</span>
              <span>{gb(n.mem_total)} GB</span>
              <span>·</span>
              <span>{n.cpu != null ? `${n.cpu.toFixed(0)} %` : '—'}</span>
              {threadsUsed(n.cores, n.cpu ?? 0) != null && (
                <span>({threadsUsed(n.cores, n.cpu ?? 0)!.toFixed(1)} thr used)</span>
              )}
            </Box>
          ))}
          <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)', ml: 'auto' }}>
            {clusterLine(data?.cluster.cores ?? 0, data?.cluster.cpu_used_cores ?? 0)}
          </Typography>
        </Box>
      )}

      {/* responsive grid: 3 across on wide screens, stacked on narrow */}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(3, 1fr)' }, gap: 2 }}>
        <Box>
          <ChartHead title="CPU % per server"
            help="One colour per server (same colour as its dot in the strip above and its RAM chart), filled with a top-to-bottom gradient. Dashed grey = cluster mean." />
          {collectingServers && data?.monitoring_since != null && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mb: 0.5 }}>collecting since {fmtHHMM(data.monitoring_since)}</Typography>
          )}
          <Box sx={{ height: 220 }}>
            <ResponsiveContainer>
              <ComposedChart data={cpuRam}>
                <defs>
                  {nodeIds.map((id) => <GradientDef key={id} id={`llp-cpu-grad-${id}`} color={serverColor(id, mode)} />)}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" />
                <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" width={36} />
                <RcTooltip labelFormatter={(v) => fmt(Number(v))} formatter={cpuTooltip} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {nodeIds.map((id) => (
                  <Area key={id} type="monotone" dataKey={`${id}_cpu`} name={`${serverLegendLabel(id)} CPU`}
                    stroke={serverColor(id, mode)} fill={`url(#llp-cpu-grad-${id})`} strokeWidth={1.5}
                    dot={false} isAnimationActive={false} />
                ))}
                <Line type="monotone" dataKey="cluster_cpu" name="cluster CPU (mean)" stroke="var(--text-2)"
                  strokeDasharray="4 3" dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </Box>
        </Box>

        <Box>
          <ChartHead title="RAM % per server"
            help="Same server colours as the CPU chart, filled with a top-to-bottom gradient. Dashed grey = cluster mean." />
          {collectingServers && data?.monitoring_since != null && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mb: 0.5 }}>collecting since {fmtHHMM(data.monitoring_since)}</Typography>
          )}
          <Box sx={{ height: 220 }}>
            <ResponsiveContainer>
              <ComposedChart data={cpuRam}>
                <defs>
                  {nodeIds.map((id) => <GradientDef key={id} id={`llp-ram-grad-${id}`} color={serverColor(id, mode)} />)}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" />
                <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" width={36} />
                <RcTooltip labelFormatter={(v) => fmt(Number(v))} formatter={memTooltip} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {nodeIds.map((id) => (
                  <Area key={id} type="monotone" dataKey={`${id}_mem`} name={`${serverLegendLabel(id)} RAM`}
                    stroke={serverColor(id, mode)} fill={`url(#llp-ram-grad-${id})`} strokeWidth={1.5}
                    dot={false} isAnimationActive={false} />
                ))}
                <Line type="monotone" dataKey="cluster_mem" name="cluster RAM (mean)" stroke="var(--text-2)"
                  strokeDasharray="4 3" dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </Box>
        </Box>

        <Box>
          <ChartHead title={`CPU by user and process (top ${users.length} users + top ${outsideKeys.length} outside)`}
            help={'Measured CPU-seconds per minute, not estimated. Bright colours are job-queue work, attributed to the '
              + 'account that submitted it (🤖 = currently running via an agent/MCP client). Warm orange shades are '
              + 'docker containers outside the job queue (mesher_*, prof_*, stagea_* studies, …), one shared hue so '
              + 'they read as one family; cool teal is "host" (processes outside any container). "app (idle/overhead)" '
              + "is the app's own containers' CPU left over once their job-queue share is subtracted (nginx, uvicorn, "
              + "GC — not a user's job). \"other\" (either family) is muted grey — see the legend/tooltip for exact names."} />
          {collecting && data?.monitoring_since != null && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mb: 0.5 }}>collecting since {fmtHHMM(data.monitoring_since)}</Typography>
          )}
          <Box sx={{ height: 220 }}>
            <ResponsiveContainer>
              <AreaChart data={loadSeries}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" />
                <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10 }} unit="%" width={36} />
                <RcTooltip labelFormatter={(v) => fmt(Number(v))} formatter={loadTooltip} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {[...users, 'other'].map((u) => {
                  const color = u === 'other' ? MUTED : userColor(u, mode);
                  return (
                    <Area key={u} type="monotone" dataKey={u} name={userLegendLabel(u)} stackId="cpu"
                      stroke={color} fill={color} fillOpacity={u === 'other' ? 0.35 : 0.75} isAnimationActive={false} />
                  );
                })}
                {outsideKeys.map((k) => {
                  if (k === HOST_KEY) {
                    const color = hostColor(mode);
                    return (
                      <Area key={k} type="monotone" dataKey={k} name={outsideLabel(k, overheadKey)} stackId="cpu"
                        stroke={color} fill={color} fillOpacity={0.6} isAnimationActive={false} />
                    );
                  }
                  const c = containerColor(k, mode);
                  return (
                    <Area key={k} type="monotone" dataKey={k} name={outsideLabel(k, overheadKey)} stackId="cpu"
                      stroke={c.stroke} fill={c.stroke} fillOpacity={c.fillOpacity} isAnimationActive={false} />
                  );
                })}
                <Area type="monotone" dataKey={OUTSIDE_OTHER_KEY} name={outsideLabel(OUTSIDE_OTHER_KEY, overheadKey)}
                  stackId="cpu" stroke={MUTED} fill={MUTED} fillOpacity={0.3} isAnimationActive={false} />
                {overheadKey && (
                  <Area type="monotone" dataKey={overheadKey} name={outsideLabel(overheadKey, overheadKey)} stackId="cpu"
                    stroke={overheadColor(mode)} fill={overheadColor(mode)} fillOpacity={0.5}
                    strokeDasharray="3 2" isAnimationActive={false} />
                )}
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

      <Typography sx={{ fontSize: 12, fontWeight: 600, mt: 1.5 }}>Outside app</Typography>
      <Table size="small">
        <TableHead><TableRow>
          {['container', 'node', 'CPU %', 'RAM', 'uptime'].map((h) => <TableCell key={h}>{h}</TableCell>)}
        </TableRow></TableHead>
        <TableBody>
          {outsideNow.map((c) => (
            <TableRow key={`${c.node}:${c.name}`}>
              <TableCell>{c.name}</TableCell>
              <TableCell>{c.node}</TableCell>
              <TableCell>{c.cpu.toFixed(1)}</TableCell>
              <TableCell>{gb(c.mem)} GB</TableCell>
              <TableCell>{dur(c.uptime_s)}</TableCell>
            </TableRow>
          ))}
          {!outsideNow.length && (
            <TableRow><TableCell colSpan={5} sx={{ color: 'var(--text-4)', fontSize: 12 }}>nothing outside the app running</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </Paper>
  );
};

export default LiveLoadPanel;
