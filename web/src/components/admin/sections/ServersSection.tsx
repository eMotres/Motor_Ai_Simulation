// Admin · Servers — cluster load (node agents): compute hosts only. Job queue
// and MCP traffic live in Agents; usage/cost lives in Usage & pricing.
// Below: every USER-OWNED compute node (docs/BYO_COMPUTE.md), read-only.
import React, { useEffect, useState } from 'react';
import { Box, Typography, Table, TableHead, TableRow, TableCell, TableBody } from '@mui/material';
import ServersPanel from '../ServersPanel';
import { getStoredToken } from '../../../lib/localAuth';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

interface UserNode {
  id: string; name: string; owner: string; status: string; version: string;
  version_ok: boolean; cores: number; cpu_pct: number | null; last_seen: number | null;
}

const UserNodesTable: React.FC = () => {
  const [rows, setRows] = useState<UserNode[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const token = getStoredToken();
    fetch(`${API}/api/admin/user-nodes`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((j: { nodes: UserNode[] }) => setRows(j.nodes))
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);
  return (
    <Box sx={{ mt: 3 }}>
      <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)', mb: 0.5 }}>User-owned compute nodes</Typography>
      {err && <Typography variant="caption" color="error">{err}</Typography>}
      <Table size="small" sx={{ '& td, & th': { fontSize: 12 } }}>
        <TableHead><TableRow>
          <TableCell>Node</TableCell><TableCell>Owner</TableCell><TableCell>Status</TableCell>
          <TableCell>Cores</TableCell><TableCell>CPU</TableCell><TableCell>Version</TableCell>
          <TableCell>Last seen</TableCell>
        </TableRow></TableHead>
        <TableBody>
          {rows.map((n) => (
            <TableRow key={n.id}>
              <TableCell>{n.name}</TableCell><TableCell>{n.owner}</TableCell><TableCell>{n.status}</TableCell>
              <TableCell>{n.cores || '—'}</TableCell>
              <TableCell>{n.cpu_pct != null ? `${Math.round(n.cpu_pct)} %` : '—'}</TableCell>
              <TableCell sx={{ color: n.version && !n.version_ok ? '#f87171' : undefined }}>{n.version || '—'}</TableCell>
              <TableCell>{n.last_seen ? new Date(n.last_seen * 1000).toLocaleString() : '—'}</TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && !err && (
            <TableRow><TableCell colSpan={7} sx={{ color: 'var(--text-4)' }}>No user nodes.</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </Box>
  );
};

const ServersSection: React.FC = () => (
  <Box>
    <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>Servers</Typography>
    <ServersPanel />
    <UserNodesTable />
  </Box>
);

export default ServersSection;
