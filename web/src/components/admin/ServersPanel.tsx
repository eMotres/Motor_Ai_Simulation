// Admin · Servers — cluster load (node agents) + API/queue metrics. Admin only.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Paper, Typography, Button, Chip, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Dialog, DialogTitle, DialogContent, DialogActions,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RcTooltip, Legend,
} from 'recharts';
import HelpTip from '../common/HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const STATUS_COLOR: Record<string, string> = {
  online: '#4ade80', offline: '#f87171', never: 'var(--text-4)', revoked: 'var(--text-4)',
};

interface Proc { pid: number; name: string; user: string; nice: number; cpu: number; rss: number; container: string }
interface Sample {
  hostname?: string; uptime_s?: number; cores?: number;
  cpu?: { total: number; per_core: number[] }; load?: number[];
  mem?: { used: number; total: number }; swap?: { used: number; total: number };
  disks?: { mount: string; used: number; total: number }[];
  net?: { rx_bps: number; tx_bps: number };
  procs?: Proc[]; containers?: { name: string; cpu: number; mem: number }[];
}
interface Node { id: string; name: string; status: string; last_seen: number | null; sample: Sample | null }
interface Cluster { nodes: number; online: number; cores: number; cpu_used_cores: number; mem_used: number; mem_total: number }
interface Job {
  run_id: string; owner: string; kind: string; state: string; position?: number;
  elapsed_s?: number; waited_s?: number; eta_s?: number; frac?: number; agent?: string | null;
}
interface AppView {
  api: { requests: number; p50_ms: number; p95_ms: number };
  mcp: { calls_per_min: number; throttled_429: number };
  jobs: { running: number; queued: number; workers?: number; oldest_wait_s: number; items: Job[] };
}
interface Point { ts: number; cpu: number; mem: number; load1: number; disk: number; rx: number; tx: number }

const gb = (b?: number) => (b ? (b / 1024 ** 3).toFixed(1) : '0');
const pct = (u?: number, t?: number) => (t ? Math.round((100 * (u ?? 0)) / t) : 0);
const dur = (s?: number) => {
  if (!s || s <= 0) return '—';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${(s / 3600).toFixed(1)} h`;
  return `${Math.round(s / 86400)} d`;
};

const Bar: React.FC<{ v: number; label: string }> = ({ v, label }) => (
  <Box sx={{ mb: 0.5 }}>
    <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-3)' }}>
      <span>{label}</span><span>{Math.round(v)} %</span>
    </Box>
    <Box sx={{ height: 5, bgcolor: 'var(--line-soft)', borderRadius: 1 }}>
      <Box sx={{ height: 5, width: `${Math.min(100, v)}%`, borderRadius: 1,
        bgcolor: v > 90 ? '#f87171' : v > 70 ? '#fbbf24' : '#60a5fa' }} />
    </Box>
  </Box>
);

const NodeDetail: React.FC<{ node: Node }> = ({ node }) => {
  const [range, setRange] = useState<'24h' | '7d'>('24h');
  const [pts, setPts] = useState<Point[]>([]);
  useEffect(() => {
    fetch(`${API}/api/admin/nodes/${encodeURIComponent(node.id)}/history?range=${range}`)
      .then((r) => (r.ok ? r.json() : { points: [] })).then((d) => setPts(d.points || []))
      .catch(() => setPts([]));
  }, [node.id, range, node.last_seen]);
  const s = node.sample;
  const fmt = (ts: number) => new Date(ts * 1000).toLocaleString(undefined,
    range === '24h' ? { hour: '2-digit', minute: '2-digit' } : { day: '2-digit', hour: '2-digit' });
  return (
    <Paper elevation={0} sx={{ ...PANEL, p: 1.5, mt: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <Typography sx={{ fontWeight: 600, fontSize: 13 }}>{node.name}</Typography>
        <ToggleButtonGroup size="small" exclusive value={range} onChange={(_, v) => v && setRange(v)}>
          <ToggleButton value="24h" sx={{ py: 0 }}>24 h</ToggleButton>
          <ToggleButton value="7d" sx={{ py: 0 }}>7 d</ToggleButton>
        </ToggleButtonGroup>
        <HelpTip title="24 h at 1-min resolution, 7 d at 15-min averages." />
      </Box>
      <Box sx={{ height: 200 }}>
        <ResponsiveContainer>
          <LineChart data={pts}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" />
            <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fontSize: 10 }} minTickGap={40} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" width={40} />
            <RcTooltip labelFormatter={(v) => fmt(Number(v))} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Line type="monotone" dataKey="cpu" name="CPU %" stroke="#60a5fa" dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="mem" name="RAM %" stroke="#a78bfa" dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="disk" name="Disk / %" stroke="#fbbf24" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </Box>
      {s?.cpu?.per_core && (
        <Box sx={{ display: 'flex', gap: '2px', mt: 1, alignItems: 'flex-end', height: 30 }}>
          {s.cpu.per_core.map((c, i) => (
            <Box key={i} title={`core ${i}: ${c} %`} sx={{ flex: 1, height: `${Math.max(3, c)}%`,
              bgcolor: c > 90 ? '#f87171' : '#60a5fa', borderRadius: '1px' }} />
          ))}
        </Box>
      )}
      <Typography sx={{ fontSize: 12, fontWeight: 600, mt: 1.5 }}>Top processes</Typography>
      <Table size="small">
        <TableHead><TableRow>
          {['name', 'container', 'user', 'nice', 'CPU %', 'RSS GB'].map((h) => <TableCell key={h}>{h}</TableCell>)}
        </TableRow></TableHead>
        <TableBody>
          {(s?.procs || []).slice(0, 15).map((p) => (
            <TableRow key={p.pid}>
              <TableCell>{p.name}</TableCell><TableCell>{p.container || '—'}</TableCell>
              <TableCell>{p.user}</TableCell><TableCell>{p.nice}</TableCell>
              <TableCell>{p.cpu}</TableCell><TableCell>{gb(p.rss)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {!!s?.containers?.length && (
        <Box sx={{ mt: 1, display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
          {s.containers.map((c) => (
            <Chip key={c.name} size="small" label={`${c.name} · ${c.cpu.toFixed(0)} % · ${gb(c.mem)} GB`} />
          ))}
        </Box>
      )}
    </Paper>
  );
};

const ServersPanel: React.FC = () => {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [cluster, setCluster] = useState<Cluster | null>(null);
  const [app, setApp] = useState<AppView | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [minted, setMinted] = useState<{ id: string; token: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [n, a] = await Promise.all([
        fetch(`${API}/api/admin/nodes`).then((r) => { if (!r.ok) throw new Error(`nodes HTTP ${r.status}`); return r.json(); }),
        fetch(`${API}/api/admin/cluster/app`).then((r) => (r.ok ? r.json() : null)),
      ]);
      setNodes(n.nodes || []); setCluster(n.cluster || null); setApp(a); setErr(null);
    } catch (e) { setErr(String(e)); }
  }, []);
  useEffect(() => { void load(); const t = setInterval(() => void load(), 10000); return () => clearInterval(t); }, [load]);

  const addNode = async () => {
    const r = await fetch(`${API}/api/admin/nodes`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: newName }),
    });
    if (r.ok) { setMinted(await r.json()); setNewName(''); void load(); } else setErr(`add HTTP ${r.status}`);
  };
  const revoke = async (id: string) => {
    if (!window.confirm(`Revoke the token of ${id}? The agent there stops reporting.`)) return;
    await fetch(`${API}/api/admin/nodes/${encodeURIComponent(id)}/revoke`, { method: 'POST' }); void load();
  };
  const stopJob = async (rid: string) => {
    if (!window.confirm(`Stop job ${rid}?`)) return;
    await fetch(`${API}/api/admin/cluster/jobs/${encodeURIComponent(rid)}/stop`, { method: 'POST' }); void load();
  };
  const selNode = nodes.find((n) => n.id === sel);

  return (
    <Paper elevation={0} sx={{ ...PANEL, p: 2, mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <Typography sx={{ fontWeight: 700 }}>Servers</Typography>
        <HelpTip title="Load of every host running the node agent; refreshes every 10 s. Offline = no sample for 60 s." />
        <Box sx={{ flex: 1 }} />
        <Button size="small" onClick={() => setAddOpen(true)}>Add server</Button>
        <HelpTip title="Mints a node token (shown once) for deploy/node-agent/install.sh on the new host." />
        <Button size="small" startIcon={<RefreshIcon />} onClick={() => void load()}>Refresh</Button>
      </Box>
      {err && <Typography sx={{ color: '#f87171', fontSize: 12 }}>{err}</Typography>}

      {cluster && (
        <Box sx={{ display: 'flex', gap: 3, fontSize: 12, color: 'var(--text-2)', mb: 1.5, flexWrap: 'wrap' }}>
          <span>Nodes <b>{cluster.online}/{cluster.nodes}</b> online</span>
          <span>Cores <b>{cluster.cores}</b></span>
          <span>CPU used <b>{cluster.cpu_used_cores.toFixed(1)}</b> cores ({pct(cluster.cpu_used_cores, cluster.cores)} %)</span>
          <span>RAM <b>{gb(cluster.mem_used)}/{gb(cluster.mem_total)}</b> GB</span>
          {app && <span>Jobs <b>{app.jobs.running}</b> running · <b>{app.jobs.queued}</b> queued</span>}
          {app && <span>API p50/p95 <b>{app.api.p50_ms}/{app.api.p95_ms}</b> ms</span>}
          {app && <span>MCP <b>{app.mcp.calls_per_min}</b>/min · 429s <b>{app.mcp.throttled_429}</b></span>}
          <HelpTip title="Totals over online nodes; API latency and MCP counts over the last 5 min." />
        </Box>
      )}

      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 1 }}>
        {nodes.map((n) => {
          const s = n.sample;
          const root = s?.disks?.find((d) => d.mount === '/') ?? s?.disks?.[0];
          return (
            <Paper key={n.id} elevation={0} onClick={() => setSel(sel === n.id ? null : n.id)}
              sx={{ ...PANEL, p: 1.25, cursor: 'pointer', outline: sel === n.id ? '1px solid #60a5fa' : 'none' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.75 }}>
                <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: STATUS_COLOR[n.status] ?? 'var(--text-4)' }} />
                <Typography sx={{ fontWeight: 600, fontSize: 13, flex: 1 }}>{n.name}</Typography>
                <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{n.status}</Typography>
              </Box>
              {s ? (
                <>
                  <Bar label={`CPU · ${s.cores ?? s.cpu?.per_core?.length ?? '?'} cores`} v={s.cpu?.total ?? 0} />
                  <Bar label={`RAM · ${gb(s.mem?.used)}/${gb(s.mem?.total)} GB`} v={pct(s.mem?.used, s.mem?.total)} />
                  <Bar label={`Disk ${root?.mount ?? ''}`} v={pct(root?.used, root?.total)} />
                  <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                    load {s.load?.map((x) => x.toFixed(1)).join(' ')} · up {dur(s.uptime_s)}
                  </Typography>
                </>
              ) : <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>no samples yet</Typography>}
              {n.status !== 'revoked' && (
                <Button size="small" color="error" sx={{ mt: 0.5, p: 0, minWidth: 0, fontSize: 11 }}
                  onClick={(e) => { e.stopPropagation(); void revoke(n.id); }}>Revoke token</Button>
              )}
            </Paper>
          );
        })}
      </Box>
      {selNode && <NodeDetail node={selNode} />}

      {app && (
        <>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 2 }}>
            <Typography sx={{ fontWeight: 600, fontSize: 13 }}>Jobs</Typography>
            <HelpTip title="Every account's running and queued solves; agent = started through MCP." />
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>oldest wait {dur(app.jobs.oldest_wait_s)}</Typography>
          </Box>
          <Table size="small">
            <TableHead><TableRow>
              {['owner', 'kind', 'state', 'agent', 'elapsed', 'ETA', ''].map((h) => <TableCell key={h}>{h}</TableCell>)}
            </TableRow></TableHead>
            <TableBody>
              {app.jobs.items.length === 0 && (
                <TableRow><TableCell colSpan={7} sx={{ color: 'var(--text-4)' }}>idle</TableCell></TableRow>
              )}
              {app.jobs.items.map((j) => (
                <TableRow key={j.run_id}>
                  <TableCell>{j.owner}</TableCell><TableCell>{j.kind}</TableCell>
                  <TableCell>{j.state}{j.state === 'queued' && j.position ? ` #${j.position}` : ''}</TableCell>
                  <TableCell>{j.agent || '—'}</TableCell>
                  <TableCell>{dur(j.state === 'running' ? j.elapsed_s : j.waited_s)}</TableCell>
                  <TableCell>{dur(j.eta_s)}</TableCell>
                  <TableCell><Button size="small" color="error" onClick={() => void stopJob(j.run_id)}>Stop</Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}

      <Dialog open={addOpen} onClose={() => { setAddOpen(false); setMinted(null); }} maxWidth="sm" fullWidth>
        <DialogTitle>Add server</DialogTitle>
        <DialogContent>
          {!minted ? (
            <TextField autoFocus fullWidth size="small" label="Node name" value={newName}
              onChange={(e) => setNewName(e.target.value)} sx={{ mt: 1 }}
              InputProps={{ endAdornment: <HelpTip title="Re-using a name re-keys that node; the old token stops working." /> }} />
          ) : (
            <Box>
              <Typography sx={{ fontSize: 12, mb: 1 }}>Token for <b>{minted.id}</b> — shown once:</Typography>
              <TextField fullWidth size="small" value={minted.token} InputProps={{ readOnly: true }}
                onFocus={(e) => e.target.select()} />
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mt: 1, fontFamily: 'monospace' }}>
                sudo bash deploy/node-agent/install.sh --url https://eu1.emotres.com --token &lt;token&gt;
              </Typography>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => { setAddOpen(false); setMinted(null); }}>Close</Button>
          {!minted && <Button variant="contained" disabled={!newName.trim()} onClick={() => void addNode()}>Create token</Button>}
        </DialogActions>
      </Dialog>
    </Paper>
  );
};

export default ServersPanel;
