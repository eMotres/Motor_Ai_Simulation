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
// "app (idle/overhead)". RAM keeps each server's identity hue but is styled
// (dashed, hollower fill) and chrome-accented (violet grid/mean-line) to
// read as visually distinct from CPU at a glance — see liveLoadColors.ts's
// RAM_ACCENT comment for why that's a chrome accent and not a second
// per-server hue family. Every colour comes from liveLoadColors.ts; nothing
// here is a hand-picked hex.
//
// Axis alignment: all three charts share ONE [now-range, now] domain and
// tick set (computeXAxis), an identical margin/Y-axis width, and an
// identical fixed plot-area height. Legends are custom HTML rendered BELOW
// each fixed-height chart box (not recharts' built-in <Legend>, which lives
// INSIDE the chart's SVG and would shrink the plot area by however many
// legend rows a chart happens to have — chart 3 has far more series than
// charts 1/2, so its recharts-managed legend used to push its plot shorter
// than the other two, misaligning the x-axes vertically).
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Paper, Typography, ToggleButton, ToggleButtonGroup, Table, TableBody,
  TableCell, TableHead, TableRow, TableSortLabel, Button, useTheme } from '@mui/material';
import {
  ResponsiveContainer, ComposedChart, AreaChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RcTooltip,
} from 'recharts';
import HelpTip from '../common/HelpTip';
import {
  mergeNodeSeries, mergeLoadSeries, isCollectingHistory, serverLabel, threadsUsed,
  cpuTooltipValue, memTooltipValue, ramNowLabel, parseServerSeriesKey, clusterLine,
  computeXAxis, sortByField, levelColor, pctCell, computeNodeTotals,
  type XAxisSpec, type SortDir,
} from './liveLoadSeries';
import {
  serverColor, userColor, containerColor, hostColor, overheadColor, RAM_ACCENT, RAM_GRID,
  type Mode,
} from './liveLoadColors';

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
const NEUTRAL_GRID = 'var(--line-soft)';

// Shared chart geometry -- identical on all three charts so their plot areas
// (and therefore their x-axis lines and ticks) sit at the same pixel
// position. See the file-header comment for why the legend lives outside
// this box instead of inside the chart.
const CHART_BOX_HEIGHT = 200;
const CHART_MARGIN = { top: 8, right: 10, bottom: 4, left: 4 } as const;
const Y_AXIS_WIDTH = 40;
const X_TICK_COUNT = 5;

type Range = '15m' | '1h' | '24h' | '7d';
interface NodePoint { ts: number; cpu: number; mem: number }
interface NodeNow {
  id: string; name: string; status: string; cores?: number; cores_physical?: number;
  cpu?: number; mem_total?: number; mem_used?: number;
}
interface UserLoad { users: string[]; bucket_s: number; series: Record<string, number>[] }
interface OutsideNow {
  node: string; name: string; cpu: number; mem: number; uptime_s: number | null;
  cpu_pct_server?: number; mem_pct_server?: number;
}
interface OutsideApp {
  items: string[]; app_overhead_key: string | null; bucket_s: number;
  series: Record<string, number>[]; now: OutsideNow[];
}
interface JobItem {
  run_id: string; owner: string; kind: string; state: string; elapsed_s?: number;
  cpu_s?: number; node?: string; agent?: string | null;
  cpu_rate?: number; rss?: number; cpu_pct_server?: number; mem_pct_server?: number;
}
interface Snapshot { running: number; queued: number; items: JobItem[] }
interface LoadLive {
  range: Range; nodes: Record<string, NodePoint[]>; nodes_now: NodeNow[];
  cluster: { cores: number; cpu_used_cores: number; mem_used: number; mem_total: number };
  user_load: UserLoad; outside_app: OutsideApp; snapshot: Snapshot;
  monitoring_since: number | null;
}
interface LegendItem { itemKey: string; label: string; color: string; dashed?: boolean; opacity?: number }

// ── "Now" process-monitor table: one row per job OR outside-app container/
// host, so the whole server's load is visible in one place (owner: "чтобы
// было как у людей мониторы" -- htop / Task Manager / Grafana style). ──────
interface ProcRow {
  procKey: string; isJob: boolean; user: string; job: string; state: string;
  client: string; node: string; cpuPct: number | null; memPct: number | null;
  rss: number | null; threads: number | null; elapsedS: number | null;
  cpuS: number | null; color: string; runId?: string;
}
type SortKey = 'user' | 'job' | 'state' | 'client' | 'node' | 'cpuPct' | 'memPct'
  | 'rss' | 'threads' | 'elapsedS' | 'cpuS';
const SORT_GETTERS: Record<SortKey, (r: ProcRow) => number | string | null | undefined> = {
  user: (r) => r.user, job: (r) => r.job, state: (r) => r.state, client: (r) => r.client,
  node: (r) => r.node, cpuPct: (r) => r.cpuPct, memPct: (r) => r.memPct, rss: (r) => r.rss,
  threads: (r) => r.threads, elapsedS: (r) => r.elapsedS, cpuS: (r) => r.cpuS,
};
const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: 'user', label: 'user' }, { key: 'job', label: 'job' }, { key: 'state', label: 'state' },
  { key: 'client', label: 'client' }, { key: 'node', label: 'node' },
  { key: 'cpuPct', label: 'CPU %', numeric: true }, { key: 'memPct', label: 'MEM %', numeric: true },
  { key: 'rss', label: 'RSS GB', numeric: true }, { key: 'threads', label: 'threads', numeric: true },
  { key: 'elapsedS', label: 'elapsed', numeric: true }, { key: 'cpuS', label: 'CPU-s', numeric: true },
];

/** Task-Manager-style mini bar: a coloured fill inside a track, width = pct
 *  clamped to the cell. Colour is the LEVEL (green/yellow/red), independent
 *  of the row's identity colour (the dot next to "user"/"job"). */
const MiniBar: React.FC<{ pct: number | null }> = ({ pct }) => (
  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
    <Box sx={{ width: 34, height: 6, borderRadius: 1, bgcolor: 'var(--line-soft)', overflow: 'hidden', flexShrink: 0 }}>
      {pct != null && (
        <Box sx={{ width: `${Math.max(0, Math.min(100, pct))}%`, height: '100%', bgcolor: levelColor(pct) }} />
      )}
    </Box>
    <Typography component="span" sx={{ fontSize: 10.5, color: 'var(--text-2)', minWidth: 26, textAlign: 'right' }}>
      {pctCell(pct)}
    </Typography>
  </Box>
);

const ColorDot: React.FC<{ color: string }> = ({ color }) => (
  <Box component="span" sx={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
    bgcolor: color, mr: 0.75, verticalAlign: 'middle' }} />
);

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

/** Vertical fill gradient. CPU: full-strength colour at the top fading to
 *  the surface at the bottom (a solid, filled look). RAM: a visibly
 *  DIFFERENT, hollower shape -- lower peak opacity, stops pulled in from
 *  the edges -- so the two charts don't read as the same fill style even
 *  before a viewer clocks the dashed stroke or the violet chrome. */
const GradientDef: React.FC<{ id: string; color: string; kind: 'cpu' | 'mem' }> = ({ id, color, kind }) => (
  <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
    {kind === 'cpu' ? (
      <>
        <stop offset="5%" stopColor={color} stopOpacity={0.75} />
        <stop offset="95%" stopColor={color} stopOpacity={0.06} />
      </>
    ) : (
      <>
        <stop offset="15%" stopColor={color} stopOpacity={0.42} />
        <stop offset="90%" stopColor={color} stopOpacity={0.04} />
      </>
    )}
  </linearGradient>
);

const ChartHead: React.FC<{ title: string; help: string }> = ({ title, help }) => (
  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5, flexWrap: 'wrap' }}>
    <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{title}</Typography>
    <HelpTip title={help} />
  </Box>
);

/** Custom legend, rendered BELOW the chart's fixed-height box instead of
 *  recharts' built-in <Legend> (which lives inside the SVG and would eat
 *  into the plot area — see the file-header comment). */
const ChartLegend: React.FC<{ items: LegendItem[] }> = ({ items }) => (
  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mt: 0.5 }}>
    {items.map((it) => (
      <Box key={it.itemKey} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        {it.dashed ? (
          <Box sx={{ width: 12, height: 0, borderTop: `2px dashed ${it.color}`, opacity: it.opacity ?? 1 }} />
        ) : (
          <Box sx={{ width: 9, height: 9, borderRadius: '2px', bgcolor: it.color, opacity: it.opacity ?? 1 }} />
        )}
        <Typography component="span" sx={{ fontSize: 10, color: 'var(--text-2)' }}>{it.label}</Typography>
      </Box>
    ))}
  </Box>
);

const LiveLoadPanel: React.FC = () => {
  const [range, setRange] = useState<Range>('1h');
  const [data, setData] = useState<LoadLive | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // "Now" table sort -- default CPU % descending (owner's rule).
  const [sortKey, setSortKey] = useState<SortKey>('cpuPct');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const onSort = (key: SortKey) => {
    if (key === sortKey) setSortDir(sortDir === 'desc' ? 'asc' : 'desc');
    else { setSortKey(key); setSortDir('desc'); }
  };
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

  // ONE shared time domain/tick set for all three charts (owner: "the same
  // time domain must be shared by all three... the same time ticks").
  const xSpec: XAxisSpec = computeXAxis(range, Date.now() / 1000, X_TICK_COUNT);

  const nodeIds = data ? Object.keys(data.nodes) : [];
  const cpuRam = mergeNodeSeries(data?.nodes ?? {});
  const nodesNow = data?.nodes_now ?? [];
  const nodeMeta: Record<string, NodeNow> = Object.fromEntries(nodesNow.map((n) => [n.id, n]));
  const collectingServers = isCollectingHistory(cpuRam.length, data?.monitoring_since ?? null);

  const serverLegendLabel = (id: string) =>
    serverLabel(id, { threads: nodeMeta[id]?.cores, cores: nodeMeta[id]?.cores_physical });

  // Regression note (see parseServerSeriesKey's own doc comment): a prior
  // version looked nodeMeta up by the raw dataKey ("eu1_cpu") instead of the
  // parsed server id ("eu1"), so this lookup was always a miss -- every
  // per-server tooltip silently fell back to a bare "%" with no GB/threads.
  const serverTooltip = (kind: 'cpu' | 'mem') => (value: unknown, _name: unknown, entry: { dataKey?: unknown }) => {
    const key = String(entry?.dataKey ?? '');
    if (key === `cluster_${kind}`) return [pctFmt(value), `cluster ${kind === 'cpu' ? 'CPU' : 'RAM'} (mean)`];
    const parsed = parseServerSeriesKey(key);
    const meta = parsed ? nodeMeta[parsed.id] : undefined;
    const label = `${parsed ? serverLegendLabel(parsed.id) : key} ${kind === 'cpu' ? 'CPU' : 'RAM'}`;
    const v = kind === 'cpu'
      ? cpuTooltipValue(Number(value), meta?.cores)
      : memTooltipValue(Number(value), meta?.mem_total);
    return [v, label];
  };
  const cpuTooltip = serverTooltip('cpu');
  const memTooltip = serverTooltip('mem');

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

  // ── "Now" process-monitor table: jobs + outside-app containers/host, one
  // unified sortable table, grouped by node with a totals row per node. ────
  const jobRows: ProcRow[] = (data?.snapshot.items ?? []).map((j) => ({
    procKey: `job:${j.run_id}`, isJob: true, user: j.owner, job: j.kind, state: j.state,
    client: j.agent ? `🤖 ${j.agent}` : 'web', node: j.node ?? '—',
    cpuPct: j.cpu_pct_server ?? null, memPct: j.mem_pct_server ?? null,
    rss: j.rss ?? null, threads: j.cpu_rate ?? null,
    elapsedS: j.elapsed_s ?? null, cpuS: j.cpu_s ?? null,
    color: userColor(j.owner, mode), runId: j.run_id,
  }));
  const outsideRows: ProcRow[] = outsideNow.map((c) => ({
    procKey: `out:${c.node}:${c.name}`, isJob: false, user: '—',
    job: outsideLabel(c.name, overheadKey), state: 'running',
    client: c.name === HOST_KEY ? 'host' : 'container', node: c.node,
    cpuPct: c.cpu_pct_server ?? null, memPct: c.mem_pct_server ?? null,
    rss: c.mem ?? null, threads: c.cpu != null ? c.cpu / 100 : null,
    elapsedS: c.uptime_s, cpuS: null,
    color: c.name === HOST_KEY ? hostColor(mode) : containerColor(c.name, mode).stroke,
  }));
  const rowsByNode = new Map<string, ProcRow[]>();
  for (const r of [...jobRows, ...outsideRows]) {
    const arr = rowsByNode.get(r.node) ?? [];
    arr.push(r);
    rowsByNode.set(r.node, arr);
  }
  type FlatEntry = { flatKey: string } & (
    { kind: 'totals'; node: string; totals: ReturnType<typeof computeNodeTotals> } | { kind: 'row'; row: ProcRow });
  const flatRows: FlatEntry[] = [];
  for (const n of nodesNow) {
    const rows = rowsByNode.get(n.id) ?? [];
    rowsByNode.delete(n.id);
    const jobPcts = rows.filter((r) => r.isJob);
    const nodeMemPct = n.mem_total ? (100 * (n.mem_used ?? 0)) / n.mem_total : 0;
    const totals = computeNodeTotals(n.cpu ?? 0, nodeMemPct,
      jobPcts.map((r) => r.cpuPct ?? 0), jobPcts.map((r) => r.memPct ?? 0));
    flatRows.push({ flatKey: `totals:${n.id}`, kind: 'totals', node: n.id, totals });
    for (const row of sortByField(rows, SORT_GETTERS[sortKey], sortDir)) {
      flatRows.push({ flatKey: row.procKey, kind: 'row', row });
    }
  }
  // a row whose `node` matched no registered node (no node agent there yet)
  // still shows, ungrouped, rather than silently vanishing.
  for (const rows of rowsByNode.values()) {
    for (const row of sortByField(rows, SORT_GETTERS[sortKey], sortDir)) {
      flatRows.push({ flatKey: row.procKey, kind: 'row', row });
    }
  }
  const hasAnyRow = flatRows.some((e) => e.kind === 'row');

  const loadTooltip = (value: unknown, _name: unknown, entry: { dataKey?: unknown }) => {
    const key = String(entry?.dataKey ?? '');
    if (key === 'other') return [pctFmt(value), 'other'];
    if (key === overheadKey) return [pctFmt(value), 'app (idle/overhead)'];
    if (key === OUTSIDE_OTHER_KEY) return [pctFmt(value), 'outside app: other'];
    if (outsideKeys.includes(key)) return [pctFmt(value), outsideLabel(key, overheadKey)];
    return [pctFmt(value), userLegendLabel(key)];
  };

  const cpuLegend: LegendItem[] = [
    ...nodeIds.map((id) => ({ itemKey: id, label: `${serverLegendLabel(id)} CPU`, color: serverColor(id, mode) })),
    { itemKey: 'cluster_cpu', label: 'cluster CPU (mean)', color: 'var(--text-2)', dashed: true },
  ];
  const ramLegend: LegendItem[] = [
    ...nodeIds.map((id) => ({ itemKey: id, label: `${serverLegendLabel(id)} RAM`, color: serverColor(id, mode), opacity: 0.85 })),
    { itemKey: 'cluster_mem', label: 'cluster RAM (mean)', color: RAM_ACCENT[mode], dashed: true },
  ];
  const loadLegend: LegendItem[] = [
    ...users.map((u) => ({ itemKey: u, label: userLegendLabel(u), color: userColor(u, mode) })),
    { itemKey: 'other', label: 'other', color: MUTED, opacity: 0.6 },
    ...outsideKeys.map((k) => (k === HOST_KEY
      ? { itemKey: k, label: outsideLabel(k, overheadKey), color: hostColor(mode) }
      : { itemKey: k, label: outsideLabel(k, overheadKey), color: containerColor(k, mode).stroke })),
    { itemKey: OUTSIDE_OTHER_KEY, label: 'outside app: other', color: MUTED, opacity: 0.5 },
    ...(overheadKey ? [{ itemKey: overheadKey, label: 'app (idle/overhead)', color: overheadColor(mode), dashed: true }] : []),
  ];

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
              <span>{n.cpu != null ? `${n.cpu.toFixed(0)} %` : '—'}</span>
              {threadsUsed(n.cores, n.cpu ?? 0) != null && (
                <span>({threadsUsed(n.cores, n.cpu ?? 0)!.toFixed(1)} thr used)</span>
              )}
              <span>·</span>
              <span>{ramNowLabel(n.mem_used, n.mem_total)}</span>
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
            help="One colour per server (same colour as its dot in the strip above and its RAM chart), solid gradient fill. Dashed grey = cluster mean." />
          {collectingServers && data?.monitoring_since != null && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mb: 0.5 }}>collecting since {fmtHHMM(data.monitoring_since)}</Typography>
          )}
          <Box sx={{ height: CHART_BOX_HEIGHT }}>
            <ResponsiveContainer>
              <ComposedChart data={cpuRam} margin={CHART_MARGIN}>
                <defs>
                  {nodeIds.map((id) => <GradientDef key={id} id={`llp-cpu-grad-${id}`} color={serverColor(id, mode)} kind="cpu" />)}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={NEUTRAL_GRID} />
                <XAxis dataKey="ts" type="number" domain={xSpec.domain} ticks={xSpec.ticks}
                  tickFormatter={fmt} tick={{ fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" width={Y_AXIS_WIDTH} />
                <RcTooltip labelFormatter={(v) => fmt(Number(v))} formatter={cpuTooltip} />
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
          <ChartLegend items={cpuLegend} />
        </Box>

        <Box>
          <ChartHead title="RAM % per server"
            help="Same per-server colours as the CPU chart (so you can match a server across both), but styled differently on purpose: dashed outline, a hollower fill, and a violet grid/mean-line — so RAM never reads as 'the same chart' as CPU at a glance." />
          {collectingServers && data?.monitoring_since != null && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mb: 0.5 }}>collecting since {fmtHHMM(data.monitoring_since)}</Typography>
          )}
          <Box sx={{ height: CHART_BOX_HEIGHT }}>
            <ResponsiveContainer>
              <ComposedChart data={cpuRam} margin={CHART_MARGIN}>
                <defs>
                  {nodeIds.map((id) => <GradientDef key={id} id={`llp-ram-grad-${id}`} color={serverColor(id, mode)} kind="mem" />)}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={RAM_GRID[mode]} />
                <XAxis dataKey="ts" type="number" domain={xSpec.domain} ticks={xSpec.ticks}
                  tickFormatter={fmt} tick={{ fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" width={Y_AXIS_WIDTH} />
                <RcTooltip labelFormatter={(v) => fmt(Number(v))} formatter={memTooltip} />
                {nodeIds.map((id) => (
                  <Area key={id} type="monotone" dataKey={`${id}_mem`} name={`${serverLegendLabel(id)} RAM`}
                    stroke={serverColor(id, mode)} strokeDasharray="5 3" fill={`url(#llp-ram-grad-${id})`} strokeWidth={1.5}
                    dot={false} isAnimationActive={false} />
                ))}
                <Line type="monotone" dataKey="cluster_mem" name="cluster RAM (mean)" stroke={RAM_ACCENT[mode]}
                  strokeDasharray="4 3" dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </Box>
          <ChartLegend items={ramLegend} />
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
          <Box sx={{ height: CHART_BOX_HEIGHT }}>
            <ResponsiveContainer>
              <AreaChart data={loadSeries} margin={CHART_MARGIN}>
                <CartesianGrid strokeDasharray="3 3" stroke={NEUTRAL_GRID} />
                <XAxis dataKey="ts" type="number" domain={xSpec.domain} ticks={xSpec.ticks}
                  tickFormatter={fmt} tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} unit="%" width={Y_AXIS_WIDTH} />
                <RcTooltip labelFormatter={(v) => fmt(Number(v))} formatter={loadTooltip} />
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
          <ChartLegend items={loadLegend} />
        </Box>
      </Box>

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1.5 }}>
        <Typography sx={{ fontSize: 12, fontWeight: 600 }}>Now — all processes</Typography>
        <HelpTip title={'Every running job AND everything outside the job queue (docker containers, "host" for '
          + 'processes outside any container), one row each, one table. CPU % = job_usage’s current rate '
          + '(ΔCPU-s / Δt over the last ≈ 2 s sample; docker CPUPerc for outside-app rows) ÷ that '
          + 'server’s thread count. MEM % = current RSS ÷ that server’s total RAM — for a job this is '
          + 'the WHOLE app process tree’s memory (Python doesn’t split heap between concurrent jobs), so '
          + 'concurrent jobs show the same RSS; the totals row below takes the max of them, not the sum, for '
          + 'exactly that reason. “threads” = cores currently in use (same number CPU % is built from), not a '
          + 'literal OS thread count. Sort by clicking a column header.'} />
      </Box>
      <Table size="small" sx={{ '& td, & th': { whiteSpace: 'nowrap' } }}>
        <TableHead><TableRow>
          {COLUMNS.map((col) => (
            <TableCell key={col.key} sortDirection={sortKey === col.key ? sortDir : false}>
              <TableSortLabel active={sortKey === col.key} direction={sortKey === col.key ? sortDir : 'desc'}
                onClick={() => onSort(col.key)}>{col.label}</TableSortLabel>
            </TableCell>
          ))}
          <TableCell />
        </TableRow></TableHead>
        <TableBody>
          {flatRows.map((entry) => entry.kind === 'totals' ? (
            <TableRow key={entry.flatKey} sx={{ bgcolor: 'var(--panel-3, rgba(255,255,255,0.05))' }}>
              <TableCell colSpan={5} sx={{ fontSize: 11, fontWeight: 700 }}>{entry.node} — total</TableCell>
              <TableCell><MiniBar pct={entry.totals.cpuTotal} /></TableCell>
              <TableCell><MiniBar pct={entry.totals.memTotal} /></TableCell>
              <TableCell colSpan={3} sx={{ fontSize: 10.5, color: 'var(--text-3)' }}>
                app {Math.round(entry.totals.cpuApp)} % CPU / {Math.round(entry.totals.memApp)} % MEM
                {' · outside '}{Math.round(entry.totals.cpuOutside)} % / {Math.round(entry.totals.memOutside)} %
                {' · idle '}{Math.round(entry.totals.cpuIdle)} % / {Math.round(entry.totals.memIdle)} %
              </TableCell>
              <TableCell />
              <TableCell />
            </TableRow>
          ) : (
            <TableRow key={entry.flatKey}>
              <TableCell><ColorDot color={entry.row.color} />{entry.row.user}</TableCell>
              <TableCell>{entry.row.job}</TableCell>
              <TableCell>{entry.row.state}</TableCell>
              <TableCell>{entry.row.client}</TableCell>
              <TableCell>{entry.row.node}</TableCell>
              <TableCell><MiniBar pct={entry.row.cpuPct} /></TableCell>
              <TableCell><MiniBar pct={entry.row.memPct} /></TableCell>
              <TableCell>{entry.row.rss != null ? gb(entry.row.rss) : '—'}</TableCell>
              <TableCell>{entry.row.threads != null ? entry.row.threads.toFixed(1) : '—'}</TableCell>
              <TableCell>{dur(entry.row.elapsedS)}</TableCell>
              <TableCell>{entry.row.cpuS != null ? entry.row.cpuS.toFixed(1) : '—'}</TableCell>
              <TableCell>
                {entry.row.isJob && entry.row.state === 'running' && (
                  <Button size="small" color="error" sx={{ p: 0, minWidth: 0, fontSize: 11 }}
                    onClick={() => void stop(entry.row.runId!)}>Stop</Button>
                )}
              </TableCell>
            </TableRow>
          ))}
          {!hasAnyRow && (
            <TableRow><TableCell colSpan={COLUMNS.length + 1} sx={{ color: 'var(--text-4)', fontSize: 12 }}>
              nothing running
            </TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </Paper>
  );
};

export default LiveLoadPanel;
