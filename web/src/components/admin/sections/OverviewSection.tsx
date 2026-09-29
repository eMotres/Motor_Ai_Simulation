// Admin · Overview — one row of KPI tiles, nothing else long on this page.
// Each tile is its own honest number: no invented "today" totals where the
// backend only ever gives a live rate.
import React, { useEffect, useState } from 'react';
import { Box, Typography, CircularProgress } from '@mui/material';
import HelpTip from '../../common/HelpTip';
import type { AdminSectionId } from '../AdminNav';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface Kpi {
  label: string; value: React.ReactNode; sub?: string; color?: string;
  help?: string; goto?: AdminSectionId;
}

const TILE = {
  bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5,
  px: 2, py: 1.5, flex: '1 1 150px', minWidth: 150, cursor: 'pointer',
  '&:hover': { borderColor: 'var(--text-4)' },
} as const;

const Tile: React.FC<{ k: Kpi; onGoto: (s: AdminSectionId) => void }> = ({ k, onGoto }) => (
  <Box sx={TILE} onClick={() => k.goto && onGoto(k.goto)}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <Typography sx={{ fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {k.label}
      </Typography>
      {k.help && <HelpTip title={k.help} />}
    </Box>
    <Typography sx={{ fontSize: 24, fontWeight: 800, color: k.color ?? 'var(--text-0)', lineHeight: 1.25 }}>{k.value}</Typography>
    {k.sub && <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{k.sub}</Typography>}
  </Box>
);

interface RegistryUser { email: string; role: string; created?: string | null }
interface AdminSession { email: string; lastSeen: number; revoked: boolean }
interface AuthEvent { ts: string; event: string }
interface Cluster { cores: number; cpu_used_cores: number; mem_used: number; mem_total: number }
interface AppView { jobs: { running: number; queued: number }; mcp: { calls_per_min: number; throttled_429: number } }

const OverviewSection: React.FC<{ onGoto: (s: AdminSectionId) => void }> = ({ onGoto }) => {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [users, setUsers] = useState<RegistryUser[]>([]);
  const [pending, setPending] = useState(0);
  const [activeSessions, setActiveSessions] = useState<AdminSession[]>([]);
  const [rejects24h, setRejects24h] = useState(0);
  const [cluster, setCluster] = useState<Cluster | null>(null);
  const [app, setApp] = useState<AppView | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true); setErr(null);
      try {
        const [u, p, s, ev, n, a] = await Promise.all([
          fetch(`${API}/api/auth/users`).then((r) => (r.ok ? r.json() : { users: [] })),
          fetch(`${API}/api/auth/pending`).then((r) => (r.ok ? r.json() : { pending: [] })).catch(() => ({ pending: [] })),
          fetch(`${API}/api/admin/sessions`).then((r) => (r.ok ? r.json() : { sessions: [] })).catch(() => ({ sessions: [] })),
          fetch(`${API}/api/admin/auth_events?limit=200`).then((r) => (r.ok ? r.json() : { events: [] })).catch(() => ({ events: [] })),
          fetch(`${API}/api/admin/nodes`).then((r) => (r.ok ? r.json() : { cluster: null })).catch(() => ({ cluster: null })),
          fetch(`${API}/api/admin/cluster/app`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        ]);
        if (!alive) return;
        setUsers(u.users ?? []);
        setPending((p.pending ?? []).length);
        setActiveSessions(s.sessions ?? []);
        const cutoff = Date.now() / 1000 - 86400;
        setRejects24h((ev.events ?? []).filter((e: AuthEvent) => e.event === 'reject' && Date.parse(e.ts) / 1000 >= cutoff).length);
        setCluster(n.cluster ?? null);
        setApp(a);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, color: 'var(--text-3)', py: 4 }}>
        <CircularProgress size={18} /> <Typography sx={{ fontSize: 13 }}>Loading overview…</Typography>
      </Box>
    );
  }

  const weekAgo = Date.now() - 7 * 86400_000;
  const activeThisWeek = new Set(activeSessions.filter((s) => !s.revoked && s.lastSeen * 1000 >= weekAgo).map((s) => s.email)).size;
  const newThisWeek = users.filter((u) => u.created && Date.parse(u.created) >= weekAgo).length;
  const cpuPct = cluster?.cores ? Math.round((100 * cluster.cpu_used_cores) / cluster.cores) : 0;
  const ramPct = cluster?.mem_total ? Math.round((100 * cluster.mem_used) / cluster.mem_total) : 0;

  const kpis: Kpi[] = [
    { label: 'Users total', value: users.length, sub: `${newThisWeek} new this week`, goto: 'users' },
    { label: 'Active 7 d', value: activeThisWeek, sub: 'distinct signed-in accounts', goto: 'logs',
      help: 'Accounts with a live, non-revoked session seen in the last 7 days.' },
    { label: 'Jobs', value: `${app?.jobs.running ?? 0} / ${app?.jobs.queued ?? 0}`, sub: 'running / queued', color: '#60a5fa', goto: 'agents' },
    { label: 'Cluster load', value: `${cpuPct}%`, sub: `RAM ${ramPct}% · ${cluster?.cores ?? 0} cores`, color: cpuPct > 85 ? '#f87171' : '#4ade80', goto: 'servers' },
    { label: 'Pending sign-ups', value: pending, color: pending ? '#fbbf24' : undefined, goto: 'signups' },
    { label: 'MCP calls', value: app?.mcp.calls_per_min ?? 0, sub: `per min · ${app?.mcp.throttled_429 ?? 0} throttled (429)`, goto: 'agents' },
    { label: 'Auth errors', value: rejects24h, sub: 'rejected tokens, 24 h', color: rejects24h ? '#f87171' : undefined, goto: 'logs',
      help: 'Rejected auth tokens in the last 24 h (expired, revoked, bad signature, disabled, …). Not a general error count — see Logs / Events for reasons.' },
  ];

  return (
    <Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171', mb: 1 }}>{err}</Typography>}
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5 }}>
        {kpis.map((k) => <Tile key={k.label} k={k} onGoto={onGoto} />)}
      </Box>
    </Box>
  );
};

export default OverviewSection;
