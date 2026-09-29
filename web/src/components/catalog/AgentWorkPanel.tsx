/**
 * AgentWorkPanel — the Motors tab's view of what the user's AI agents did
 * (MCP Stage 3): their DRAFT machines (🤖, open in Configure / revert / delete)
 * and the account's job queue with agent runs badged and a Stop button.
 * Renders nothing while there are no drafts and no jobs.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Typography, Button, Chip, Tooltip } from '@mui/material';
import {
  listDrafts, listJobs, revertDraft, deleteDraft, cancelJob, showDraftInConfigure,
  type AgentDraft, type JobRow,
} from '../../lib/agentDrafts';
import { useUIStore } from '../../stores/motorStore';

const ROW = { display: 'flex', alignItems: 'center', gap: 1, py: 0.5,
  borderBottom: '1px solid var(--line-soft)', fontSize: 12 } as const;
const fmt = (v: unknown, d = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—');

const AgentWorkPanel: React.FC = () => {
  const [drafts, setDrafts] = useState<AgentDraft[]>([]);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const setActiveTab = useUIStore((s) => s.setActiveTab);

  const load = useCallback(async () => {
    try {
      const [d, j] = await Promise.all([listDrafts(), listJobs().catch(() => [] as JobRow[])]);
      setDrafts(d); setJobs(j); setErr(null);
    } catch { /* signed out / older server: show nothing */ }
  }, []);
  useEffect(() => {
    void load();
    const t = setInterval(() => { void load(); }, 10000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); await load(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const live = jobs.filter((j) => j.state === 'queued' || j.state === 'running' || j.state === 'leased' || j.body?.agent);
  if (!drafts.length && !live.length) return null;

  return (
    <Box sx={{ p: 2, mb: 2, borderRadius: 2, border: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)' }}>
      {err && <Typography sx={{ fontSize: 11, color: '#fca5a5', mb: 1 }}>{err}</Typography>}
      {drafts.length > 0 && (
        <>
          <Typography sx={{ fontWeight: 700, fontSize: 13, mb: 0.5 }}>
            🤖 Agent drafts
            <Tooltip arrow title="Machines your AI agents started through MCP. They never change your saved motors or the machine you have open.">
              <Box component="span" sx={{ ml: 1, fontSize: 12, color: 'var(--text-2)', cursor: 'help' }}>ⓘ</Box>
            </Tooltip>
          </Typography>
          {drafts.map((d) => {
            const res = d.results.coupled ?? d.results.thermal ?? d.results.em;
            return (
              <Box key={d.design_id} sx={ROW}>
                <Chip size="small" label={`🤖 ${d.created_by.client_name}`} sx={{ fontSize: 10 }} />
                <Tooltip arrow title={(d.why || []).join(' · ')}>
                  <Typography sx={{ fontSize: 12, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {d.name} <Box component="span" sx={{ color: 'var(--text-3)' }}>
                      · {d.starting_point.die} / {d.starting_point.config} · L {fmt(d.params.stack_mm)} mm
                      · {fmt(d.params.current_a_rms)} A · {fmt(d.params.speed_rpm, 0)} rpm
                      {res ? ` · FEM ${fmt(res.torque_nm, 2)} N·m` : ' · not simulated'}
                      {d.edited_by_user ? ' · edited' : ''}
                    </Box>
                  </Typography>
                </Tooltip>
                <Button size="small" onClick={() => { setActiveTab('compare'); showDraftInConfigure(d.design_id); }}>Configure</Button>
                <Tooltip title="Back to what the agent created">
                  <span><Button size="small" disabled={!d.edited_by_user} onClick={() => { void act(() => revertDraft(d.design_id)); }}>Revert</Button></span>
                </Tooltip>
                <Button size="small" color="error" onClick={() => { void act(() => deleteDraft(d.design_id)); }}>Delete</Button>
              </Box>
            );
          })}
        </>
      )}
      {live.length > 0 && (
        <>
          <Typography sx={{ fontWeight: 700, fontSize: 13, mt: drafts.length ? 1.5 : 0, mb: 0.5 }}>My runs</Typography>
          {live.map((j) => (
            <Box key={j.run_id} sx={ROW}>
              {j.body?.agent
                ? <Chip size="small" label={`🤖 ${j.body.agent.client_name ?? 'agent'}`} sx={{ fontSize: 10 }} />
                : <Chip size="small" label="you" sx={{ fontSize: 10 }} />}
              <Typography sx={{ fontSize: 12, flex: 1 }}>
                {j.kind} · {j.state}
                {j.where && j.where !== 'platform' ? ` · ${j.where}` : ''}
                {j.state === 'queued' && j.position ? ` · #${j.position} in queue` : ''}
                {j.state === 'running' && j.progress?.total ? ` · ${j.progress.step}/${j.progress.total}` : ''}
                {j.error ? ` · ${j.error.slice(0, 80)}` : ''}
              </Typography>
              {(j.state === 'queued' || j.state === 'running' || j.state === 'leased') && (
                <Button size="small" color="error" onClick={() => { void act(() => cancelJob(j.run_id)); }}>Stop</Button>
              )}
            </Box>
          ))}
        </>
      )}
    </Box>
  );
};

export default AgentWorkPanel;
