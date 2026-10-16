/**
 * MyAgentDraftsBlock — Configure tab's compact list of the signed-in
 * account's OWN agent drafts (MCP Stage 3).
 *
 * Used to live on the Motors catalog page (AgentWorkPanel); moved out
 * 2026-09-30 (owner: that page is not the right place for every AI agent's
 * draft designs and job queue — move it into Admin). Cross-account drafts +
 * runs now live in
 * Admin -> Agent activity; a regular user cannot see Admin, so this
 * collapsible block keeps their OWN drafts reachable, next to the
 * single-draft banner this same tab already shows for a
 * ?tab=configure&design=… link. Renders nothing while the account has none.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Typography, Button, Collapse, Tooltip } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import { listDrafts, bestDraftResult, showDraftInConfigure, type AgentDraft } from '../../lib/agentDrafts';
import { nsT } from '../../i18n/nsT';

const tx = nsT('controller');   // EN source, ZH mirror (docs/I18N.md)

const fmt = (v: unknown, d = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—');
const LS_KEY = 'configure.myAgentDrafts.open';

const MyAgentDraftsBlock: React.FC = () => {
  const [drafts, setDrafts] = useState<AgentDraft[]>([]);
  const [open, setOpen] = useState<boolean>(() => {
    try { return localStorage.getItem(LS_KEY) === '1'; } catch { return false; }
  });

  const load = useCallback(async () => {
    try { setDrafts(await listDrafts()); } catch { /* signed out / older server: show nothing */ }
  }, []);
  useEffect(() => {
    void load();
    // A draft just opened via the banner above (or deleted elsewhere) should
    // update this count without a full reload.
    const onChanged = () => { void load(); };
    window.addEventListener('agent-draft', onChanged);
    return () => window.removeEventListener('agent-draft', onChanged);
  }, [load]);

  const toggle = () => setOpen((v) => {
    const next = !v;
    try { localStorage.setItem(LS_KEY, next ? '1' : '0'); } catch { /* storage blocked */ }
    return next;
  });

  if (!drafts.length) return null;

  return (
    <Box sx={{ mx: 1, mt: 1, borderRadius: 2, border: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)' }}>
      <Box onClick={toggle} role="button" aria-expanded={open}
           sx={{ display: 'flex', alignItems: 'center', gap: 0.75, px: 1.5, py: 1, cursor: 'pointer', userSelect: 'none' }}>
        {open ? <ExpandMoreIcon sx={{ fontSize: 18, color: 'var(--text-3)' }} />
              : <ChevronRightIcon sx={{ fontSize: 18, color: 'var(--text-3)' }} />}
        <Typography sx={{ fontWeight: 700, fontSize: 12.5 }}>{tx('configure.draftsTitle', { n: drafts.length })}</Typography>
      </Box>
      <Collapse in={open}>
        <Box sx={{ px: 1.5, pb: 1 }}>
          {drafts.map((d) => {
            const res = bestDraftResult(d);
            return (
              <Box key={d.design_id} sx={{
                display: 'flex', alignItems: 'center', gap: 1, py: 0.6,
                borderTop: '1px solid var(--line-soft)', fontSize: 12,
              }}>
                <Tooltip arrow title={(d.why || []).join(' · ')}>
                  <Typography sx={{ fontSize: 12, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {d.name} <Box component="span" sx={{ color: 'var(--text-3)' }}>
                      {tx('configure.draftRow', { die: d.starting_point.die, config: d.starting_point.config, L: fmt(d.params.stack_mm), I: fmt(d.params.current_a_rms), rpm: fmt(d.params.speed_rpm, 0) })}
                      {res ? tx('configure.draftFem', { T: fmt(res.torque_nm, 2) }) : tx('configure.draftNotSimulated')}
                    </Box>
                  </Typography>
                </Tooltip>
                <Button size="small" onClick={() => showDraftInConfigure(d.design_id)}>{tx('configure.draftOpen')}</Button>
              </Box>
            );
          })}
        </Box>
      </Collapse>
    </Box>
  );
};

export default MyAgentDraftsBlock;
