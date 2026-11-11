// Admin · Logs: "Assistant conversations" - the conversations of the specification assistant and the help assistant that are kept
// for service improvement (GET /api/admin/assistant-conversations*).  Read-only; every read and export is an admin-audit entry.
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Button, MenuItem, Paper, Select, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const BASE = `${API}/api/admin/assistant-conversations`;

interface Conv { conv_id: string; assistant: string; account: string; project_id: string; first: string; last: string; turns: number; filled: number; proposed: number; errors: number }
interface Counters { conversations: number; turns: number; filled: number; proposed: number; undone: number; refusals: number; errors: number }
interface Rec { iso: string; kind: string; user_text?: string; reply?: string; event?: string; field_id?: string; model?: string; source?: string; error?: string; applied?: { field_id: string; basis: string }[]; proposed?: { field_id: string }[]; refused?: string[] }

const AssistantConversationsSection: React.FC = () => {
  const [assistant, setAssistant] = useState('');
  const [project, setProject] = useState('');
  const [account, setAccount] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [convs, setConvs] = useState<Conv[]>([]);
  const [days, setDays] = useState<(Counters & { day: string })[]>([]);
  const [total, setTotal] = useState<Counters | null>(null);
  const [open, setOpen] = useState<{ id: string; records: Rec[] } | null>(null);

  const query = useCallback(() => {
    const q = new URLSearchParams();
    if (assistant) q.set('assistant', assistant);
    if (project.trim()) q.set('project', project.trim());
    if (account.trim()) q.set('account', account.trim());
    if (from) q.set('date_from', from);
    if (to) q.set('date_to', to);
    return q.toString();
  }, [assistant, project, account, from, to]);

  const load = useCallback(async () => {
    const q = query();
    const list = await fetch(`${BASE}?${q}`).then((r) => (r.ok ? r.json() : { conversations: [] })).catch(() => ({ conversations: [] }));
    setConvs(list.conversations || []);
    const st = await fetch(`${BASE}/stats?${q}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    setDays(st?.days || []); setTotal(st?.total || null);
  }, [query]);
  useEffect(() => { void load(); }, [load]);

  const openConv = async (id: string) => {
    const j = await fetch(`${BASE}/${encodeURIComponent(id)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    setOpen(j ? { id, records: j.records || [] } : null);
  };
  const exportJsonl = async () => {
    const r = await fetch(`${BASE}/export?${query()}`);
    if (!r.ok) return;
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url; a.download = `assistant_conversations_${from || 'start'}_${to || 'now'}.jsonl`; a.click();
    URL.revokeObjectURL(url);
  };

  const head = { '& td, & th': { borderColor: 'var(--panel)', fontSize: 12 }, '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, bgcolor: 'var(--panel-2)' } } as const;
  return (
    <Box sx={{ mt: 3 }} data-testid="assistant-conversations">
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>Assistant conversations</Typography>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>read-only; every read and export is logged in the admin audit</Typography>
      </Box>
      <Box sx={{ display: 'flex', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Select size="small" displayEmpty value={assistant} onChange={(e) => setAssistant(e.target.value)} sx={{ fontSize: 12, minWidth: 140 }}>
          <MenuItem value="">All assistants</MenuItem><MenuItem value="spec">Specification</MenuItem><MenuItem value="support">Help</MenuItem>
        </Select>
        <TextField size="small" placeholder="project id" value={project} onChange={(e) => setProject(e.target.value)} sx={{ '& input': { fontSize: 12, py: 0.5 } }} />
        <TextField size="small" placeholder="account" value={account} onChange={(e) => setAccount(e.target.value)} sx={{ '& input': { fontSize: 12, py: 0.5 } }} />
        <TextField size="small" type="date" value={from} onChange={(e) => setFrom(e.target.value)} sx={{ '& input': { fontSize: 12, py: 0.5 } }} />
        <TextField size="small" type="date" value={to} onChange={(e) => setTo(e.target.value)} sx={{ '& input': { fontSize: 12, py: 0.5 } }} />
        <Button size="small" variant="outlined" onClick={() => void exportJsonl()}>Export JSONL</Button>
      </Box>
      {total && (
        <Paper sx={{ ...PANEL, p: 0, overflow: 'auto', maxHeight: 220, mb: 1 }}>
          <Table size="small" stickyHeader sx={head}>
            <TableHead><TableRow><TableCell>Day</TableCell><TableCell>Conversations</TableCell><TableCell>Turns</TableCell><TableCell>Filled</TableCell><TableCell>Proposed</TableCell><TableCell>Undone</TableCell><TableCell>Refusals</TableCell><TableCell>Errors</TableCell></TableRow></TableHead>
            <TableBody>
              {[...days].reverse().map((d) => <TableRow key={d.day}><TableCell>{d.day}</TableCell><TableCell>{d.conversations}</TableCell><TableCell>{d.turns}</TableCell><TableCell>{d.filled}</TableCell><TableCell>{d.proposed}</TableCell><TableCell>{d.undone}</TableCell><TableCell>{d.refusals}</TableCell><TableCell>{d.errors}</TableCell></TableRow>)}
              <TableRow><TableCell><b>Total</b></TableCell><TableCell>{total.conversations}</TableCell><TableCell>{total.turns}</TableCell><TableCell>{total.filled}</TableCell><TableCell>{total.proposed}</TableCell><TableCell>{total.undone}</TableCell><TableCell>{total.refusals}</TableCell><TableCell>{total.errors}</TableCell></TableRow>
            </TableBody>
          </Table>
        </Paper>
      )}
      <Paper sx={{ ...PANEL, p: 0, overflow: 'auto', maxHeight: 320 }}>
        <Table size="small" stickyHeader sx={head}>
          <TableHead><TableRow><TableCell>Last (UTC)</TableCell><TableCell>Assistant</TableCell><TableCell>Account</TableCell><TableCell>Project</TableCell><TableCell>Turns</TableCell><TableCell>Filled</TableCell><TableCell>Errors</TableCell></TableRow></TableHead>
          <TableBody>
            {convs.map((c) => (
              <TableRow key={c.conv_id} hover onClick={() => void openConv(c.conv_id)} sx={{ cursor: 'pointer' }}>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>{c.last.replace('T', ' ').replace('Z', '')}</TableCell><TableCell>{c.assistant}</TableCell>
                <TableCell>{c.account || 'visitor'}</TableCell><TableCell>{c.project_id}</TableCell><TableCell>{c.turns}</TableCell><TableCell>{c.filled}</TableCell><TableCell>{c.errors}</TableCell>
              </TableRow>
            ))}
            {convs.length === 0 && <TableRow><TableCell colSpan={7} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>No conversations recorded.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </Paper>
      {open && (
        <Paper sx={{ ...PANEL, p: 1.5, mt: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700, flex: 1 }}>Conversation {open.id}</Typography>
            <Button size="small" onClick={() => setOpen(null)}>Close</Button>
          </Box>
          {open.records.map((r, i) => r.kind === 'event'
            ? <Typography key={i} sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{r.iso} - {r.event} {r.field_id}</Typography>
            : <Box key={i} sx={{ mb: 1 }}>
              <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)' }}>{r.iso} - {r.model || r.source}{r.error ? ` - error: ${r.error}` : ''}</Typography>
              <Typography sx={{ fontSize: 12.5, whiteSpace: 'pre-wrap' }}><b>User:</b> {r.user_text}</Typography>
              <Typography sx={{ fontSize: 12.5, whiteSpace: 'pre-wrap' }}><b>Assistant:</b> {r.reply}</Typography>
              {(r.applied?.length || r.proposed?.length || r.refused?.length) ? <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                filled: {(r.applied ?? []).map((a) => `${a.field_id} (${a.basis})`).join(', ') || '-'}; proposed: {(r.proposed ?? []).map((a) => a.field_id).join(', ') || '-'}; refused: {(r.refused ?? []).join(', ') || '-'}</Typography> : null}
            </Box>)}
        </Paper>
      )}
    </Box>
  );
};

export default AssistantConversationsSection;
