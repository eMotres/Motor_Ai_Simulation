// Admin · Agent activity — every account's AGENT DRAFTS (MCP Stage 3
// start_design) and agent-submitted job runs, in one place.
//
// Moved here from the Motors catalog page 2026-09-30 (owner: that page is not
// the right place for every AI agent's draft designs and job queue — move it
// into Admin). A regular user still sees their OWN drafts in Configure
// ("My agent drafts"); this section
// is the admin's cross-account view — GET /api/admin/agent_designs (new) for
// drafts, GET /api/jobs?all=true (routes/jobs_api.py, already admin-aware)
// for runs, filtered here to the ones an agent submitted.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Paper, Typography, Button, Table, TableBody, TableCell, TableHead, TableRow,
  Select, MenuItem, FormControl, InputLabel, Tooltip,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import HelpTip from '../../common/HelpTip';
import { bestDraftResult, type AgentDraft } from '../../../lib/agentDrafts';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

type AdminDraft = AgentDraft & { owner: string };

interface RunRow {
  run_id: string; owner: string; kind: string; state: string;
  queued_at?: number; started_at?: number; finished_at?: number;
  elapsed_s?: number; waited_s?: number; error?: string;
  body?: { agent?: { client_name?: string; credential_id?: string; kind?: string };
          design_id?: string; what?: string };
}

const STATES = ['all', 'queued', 'running', 'done', 'failed', 'cancelled', 'interrupted'] as const;
const fmt = (v: unknown, d = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—');
const when = (t?: number) => (t ? new Date(t * 1000).toLocaleString() : '—');

const AgentActivitySection: React.FC = () => {
  const [drafts, setDrafts] = useState<AdminDraft[]>([]);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [userFilter, setUserFilter] = useState('all');
  const [stateFilter, setStateFilter] = useState<(typeof STATES)[number]>('all');

  const load = useCallback(async () => {
    try {
      const [dRes, jRes] = await Promise.all([
        fetch(`${API}/api/admin/agent_designs`),
        fetch(`${API}/api/jobs?all=true&limit=200`),
      ]);
      if (!dRes.ok) throw new Error(`agent_designs HTTP ${dRes.status}`);
      const dJson = await dRes.json();
      setDrafts(dJson.designs || []);
      if (jRes.ok) {
        const jJson = await jRes.json();
        setRuns((jJson.jobs || []).filter((j: RunRow) => !!j.body?.agent));
      } else {
        setRuns([]);
      }
      setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffect(() => { void load(); const t = setInterval(() => void load(), 15000); return () => clearInterval(t); }, [load]);

  const users = useMemo(() => Array.from(new Set([
    ...drafts.map((d) => d.owner), ...runs.map((r) => r.owner),
  ].filter(Boolean))).sort(), [drafts, runs]);

  const shownDrafts = useMemo(() => drafts.filter((d) => userFilter === 'all' || d.owner === userFilter), [drafts, userFilter]);
  const shownRuns = useMemo(() => runs.filter((r) =>
    (userFilter === 'all' || r.owner === userFilter)
    && (stateFilter === 'all' || r.state === stateFilter)), [runs, userFilter, stateFilter]);

  const deleteDraft = async (d: AdminDraft) => {
    if (!window.confirm(`Delete draft "${d.name}" (${d.owner})? This cannot be undone.`)) return;
    try {
      const r = await fetch(`${API}/api/admin/agent_designs/${encodeURIComponent(d.design_id)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      void load();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>Agent activity</Typography>
        <HelpTip title="Machines and simulation runs every account's AI agents created through MCP (start_design / simulate). A draft never changes that account's saved catalog or open machine." />
        <Box sx={{ flex: 1 }} />
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel id="aa-user">User</InputLabel>
          <Select labelId="aa-user" label="User" value={userFilter} onChange={(e) => setUserFilter(e.target.value)}>
            <MenuItem value="all">All users</MenuItem>
            {users.map((u) => <MenuItem key={u} value={u}>{u}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel id="aa-state">State (runs)</InputLabel>
          <Select labelId="aa-state" label="State (runs)" value={stateFilter} onChange={(e) => setStateFilter(e.target.value as typeof stateFilter)}>
            {STATES.map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </Select>
        </FormControl>
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} onClick={() => void load()}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>Refresh</Button>
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171', mb: 1 }}>{err}</Typography>}

      <Typography sx={{ fontSize: 13, fontWeight: 700, mb: 1, color: 'var(--text-1)' }}>
        Drafts ({shownDrafts.length})
      </Typography>
      <Paper sx={{ ...PANEL, p: 2, mb: 2 }}>
        <Table size="small" sx={{
          '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
          '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
        }}>
          <TableHead><TableRow>
            {['user', 'client', 'credential', 'draft', 'base machine', 'key params', 'FEM headline', 'created', ''].map((h) => <TableCell key={h}>{h}</TableCell>)}
          </TableRow></TableHead>
          <TableBody>
            {shownDrafts.length === 0 && (
              <TableRow><TableCell colSpan={9} sx={{ color: 'var(--text-4)' }}>none</TableCell></TableRow>
            )}
            {shownDrafts.map((d) => {
              const res = bestDraftResult(d);
              return (
                <TableRow key={d.design_id}>
                  <TableCell>{d.owner}</TableCell>
                  <TableCell>{d.created_by.client_name}</TableCell>
                  <TableCell>{d.created_by.credential_kind}</TableCell>
                  <TableCell>
                    <Tooltip arrow title={(d.why || []).join(' · ')}>
                      <span>{d.name}</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell>{d.starting_point.die} / {d.starting_point.config}</TableCell>
                  <TableCell>
                    L {fmt(d.params.stack_mm)} mm · {fmt(d.params.current_a_rms)} A · {fmt(d.params.speed_rpm, 0)} rpm
                  </TableCell>
                  <TableCell>{res ? `${fmt(res.torque_nm, 2)} N·m` : 'not simulated'}</TableCell>
                  <TableCell>{new Date(d.created_at).toLocaleString()}</TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>
                    <Button size="small" component="a" href={d.open_in_configure} target="_blank" rel="noopener">Open</Button>
                    <Button size="small" color="error" onClick={() => void deleteDraft(d)}>Delete</Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Paper>

      <Typography sx={{ fontSize: 13, fontWeight: 700, mb: 1, color: 'var(--text-1)' }}>
        Runs ({shownRuns.length})
      </Typography>
      <Paper sx={{ ...PANEL, p: 2 }}>
        <Table size="small" sx={{
          '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
          '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
        }}>
          <TableHead><TableRow>
            {['user', 'client', 'credential', 'kind', 'state', 'draft', 'queued', 'error'].map((h) => <TableCell key={h}>{h}</TableCell>)}
          </TableRow></TableHead>
          <TableBody>
            {shownRuns.length === 0 && (
              <TableRow><TableCell colSpan={8} sx={{ color: 'var(--text-4)' }}>none</TableCell></TableRow>
            )}
            {shownRuns.map((r) => (
              <TableRow key={r.run_id}>
                <TableCell>{r.owner}</TableCell>
                <TableCell>{r.body?.agent?.client_name || '—'}</TableCell>
                <TableCell>{r.body?.agent?.kind || '—'}</TableCell>
                <TableCell>{r.kind}</TableCell>
                <TableCell>{r.state}</TableCell>
                <TableCell>{r.body?.design_id || '—'}</TableCell>
                <TableCell>{when(r.queued_at)}</TableCell>
                <TableCell sx={{ color: r.error ? '#f87171' : undefined }}>{r.error ? r.error.slice(0, 80) : '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
};

export default AgentActivitySection;
