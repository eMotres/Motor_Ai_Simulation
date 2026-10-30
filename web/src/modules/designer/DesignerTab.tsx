/**
 * Designer (pilot 2026-10-18): a chat with the AeroStator design assistant.  The customer describes the drone or
 * robot; the assistant searches THEIR catalogue, rates motor + propeller pairs (propeller S1) and recommends up to
 * three motors as cards that open in Configure.  The conversation runs on the server (routes/design_agent.py); this
 * page posts the message and polls the conversation while the assistant works, showing each tool step.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Chip, CircularProgress, IconButton, TextField, Tooltip, Typography } from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import SendIcon from '@mui/icons-material/Send';
import { MiniMarkdown } from './miniMarkdown';
import { useAuth } from '../../contexts/AuthContext';
import { useUIStore } from '../../stores/motorStore';
import { applyDutyEverywhere } from '../../lib/dutyApply';
import { guardLoad } from '../../lib/loadGuard';
import { beginFamilyLoad, failFamilyLoad, finishFamilyLoad } from '../../lib/familyLoad';
import { loadTargetTab } from '../../lib/catalogRows';
import { isStandardUser, uiCanWrite } from '../../lib/accessUi';
import { canWriteServer } from '../../lib/localAuth';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API}/api/design-agent`;

interface Step { tool: string; args: Record<string, any>; ok: boolean; error?: string }
interface Card { die: string; config: string; duty: string | null; propeller_id: string | null; headline: string }
interface Entry { role: 'user' | 'assistant'; text: string; steps?: Step[]; cards?: Card[]; ts?: number }
interface Conversation {
  id: string; title: string; state: 'idle' | 'running' | 'error'; error: string | null;
  display: Entry[]; pending: { steps: Step[] } | null; cost_usd: number;
  limits: { conversation_usd: number; user_day_usd: number; service_day_usd: number };
}
interface Row { id: string; title: string; updated: number; cost_usd: number }
interface Status { allowed: boolean; configured: boolean; model?: string; spent_today_usd?: number;
  limits?: { conversation_usd: number; user_day_usd: number } }

/** One line per tool step, in plain words. */
export function stepLabel(s: Step): string {
  const a = s.args || {};
  switch (s.tool) {
    case 'list_motors': return `Catalogue${a.application && a.application !== 'any' ? ` (${a.application})` : ''}`;
    case 'list_propellers': return `Propellers${a.query ? ` "${a.query}"` : ''}`;
    case 'propeller_s1': return `S1: ${a.die} / ${a.config} + ${a.propeller_id}${a.required_thrust_g ? ` @ ${a.required_thrust_g} g` : ''}`;
    case 'check_fit': return `Fit: ${a.torque_nm} N·m @ ${a.speed_rpm} rpm`;
    case 'get_machine_performance': return `Duty: ${a.die} / ${a.config} / ${a.duty}`;
    case 'present_motor': return `Card: ${a.die} / ${a.config}`;
    default: return s.tool;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } });
  if (!r.ok) {
    let why = `HTTP ${r.status}`;
    try { const b = await r.json(); if (b?.detail) why = String(b.detail); } catch { /* keep the status */ }
    throw new Error(why);
  }
  return r.json() as Promise<T>;
}

function Steps({ steps, live }: { steps: Step[]; live?: boolean }) {
  if (!steps.length && !live) return null;
  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, my: 0.5 }}>
      {steps.filter((s) => s.tool !== 'present_motor').map((s, i) => (
        <Tooltip key={i} title={s.ok ? JSON.stringify(s.args) : (s.error ?? 'failed')}>
          <Chip size="small" variant="outlined" label={stepLabel(s)}
            sx={{ fontSize: 11, height: 22, borderColor: s.ok ? 'divider' : '#f87171', color: s.ok ? 'text.secondary' : '#fca5a5' }} />
        </Tooltip>
      ))}
      {live && <Chip size="small" icon={<CircularProgress size={11} />} label="working…" sx={{ fontSize: 11, height: 22 }} />}
    </Box>
  );
}

function MotorCard({ c }: { c: Card }) {
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const { enforced, isAdmin, role } = useAuth();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const open = async () => {
    if (!c.duty) return;
    setBusy(true); setErr(null);
    const canWrite = uiCanWrite(isAdmin, canWriteServer());
    // Ask before the view switches (lib/loadGuard): a Cancel leaves the screen as it was.
    const permit = await guardLoad({ die: c.die, config: c.config, duty: c.duty });
    if (!permit) { setBusy(false); return; }
    const token = beginFamilyLoad(c.die, c.config, c.duty);
    setActiveTab(loadTargetTab(canWrite, isStandardUser(enforced, isAdmin, role)) as never);
    try { await applyDutyEverywhere(c.die, c.config, c.duty, canWrite, permit); finishFamilyLoad(token); }
    catch (e: any) { const why = String(e?.message ?? e); setErr(why); failFamilyLoad(token, why); }
    setBusy(false);
  };
  return (
    <Box sx={{ border: '1px solid', borderColor: 'divider', borderLeft: '3px solid #3b82f6', borderRadius: 1,
      p: 1, display: 'flex', alignItems: 'center', gap: 1.5, bgcolor: 'var(--panel-2)' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ fontWeight: 700, fontSize: 13 }}>{c.die} / {c.config}
          {c.propeller_id && <Typography component="span" sx={{ fontSize: 12, color: 'text.secondary', ml: 1 }}>+ {c.propeller_id}</Typography>}
        </Typography>
        <Typography sx={{ fontSize: 12.5, color: 'text.secondary' }}>{c.headline}</Typography>
        {err && <Typography sx={{ fontSize: 11, color: '#fca5a5' }}>{err}</Typography>}
      </Box>
      <Button size="small" variant="contained" disabled={busy || !c.duty} onClick={open} sx={{ textTransform: 'none', flexShrink: 0 }}>
        Open in Configure
      </Button>
    </Box>
  );
}

const EXAMPLES = [
  'Quadcopter, 2.5 kg take-off mass, 4 motors, 6S 5000 mAh, props up to 13". Long hover. Which motor and propeller?',
  'Квадрокоптер 4 кг, 4 мотора, 12S, винты до 15 дюймов. Что подойдёт для долгого висения?',
  'Robot joint: 3 N·m continuous at 1500 rpm, 48 V, diameter under 90 mm.',
];

export default function DesignerTab() {
  const { t: tc } = useTranslation('common');
  const [status, setStatus] = useState<Status | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [conv, setConv] = useState<Conversation | null>(null);
  const [text, setText] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const bottom = useRef<HTMLDivElement | null>(null);

  const refreshList = useCallback(() => {
    call<{ conversations: Row[] }>('/conversations').then((d) => setRows(d.conversations)).catch(() => {});
    call<Status>('/status').then(setStatus).catch(() => {});
  }, []);
  useEffect(() => { call<Status>('/status').then(setStatus).catch((e) => setErr(String(e.message ?? e))); refreshList(); }, [refreshList]);

  // poll while the assistant works
  useEffect(() => {
    if (!conv || conv.state !== 'running') return;
    const t = setTimeout(() => {
      call<Conversation>(`/conversations/${conv.id}`).then((c) => {
        setConv(c);
        if (c.state !== 'running') refreshList();
      }).catch((e) => setErr(String(e.message ?? e)));
    }, 1500);
    return () => clearTimeout(t);
  }, [conv, refreshList]);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); },
    [conv?.display.length, conv?.pending?.steps.length, conv?.state]);

  const send = async () => {
    const t = text.trim();
    if (!t || sending) return;
    setSending(true); setErr(null);
    try {
      const c = conv
        ? await call<Conversation>(`/conversations/${conv.id}/messages`, { method: 'POST', body: JSON.stringify({ text: t }) })
        : await call<Conversation>('/conversations', { method: 'POST', body: JSON.stringify({ text: t }) });
      setConv(c); setText(''); refreshList();
    } catch (e: any) { setErr(String(e?.message ?? e)); }
    setSending(false);
  };
  const openConv = (id: string) => call<Conversation>(`/conversations/${id}`).then(setConv).catch((e) => setErr(String(e.message ?? e)));
  const remove = async (id: string) => {
    try { await call(`/conversations/${id}`, { method: 'DELETE' }); if (conv?.id === id) setConv(null); refreshList(); }
    catch (e: any) { setErr(String(e?.message ?? e)); }
  };

  if (status && !status.allowed) {
    return <Box sx={{ p: 3 }}><Typography sx={{ fontSize: 13 }}>The design assistant is not open for this account yet.</Typography></Box>;
  }
  const running = conv?.state === 'running';
  return (
    <Box sx={{ display: 'flex', height: '100%', minHeight: 0 }}>
      <Box sx={{ width: 240, flexShrink: 0, borderRight: '1px solid', borderColor: 'divider', p: 1, overflowY: 'auto',
        display: { xs: 'none', md: 'block' } }}>
        <Button fullWidth size="small" variant="outlined" onClick={() => { setConv(null); setErr(null); }}
          sx={{ textTransform: 'none', mb: 1 }}>New conversation</Button>
        {rows.map((r) => (
          <Box key={r.id} onClick={() => openConv(r.id)} sx={{ display: 'flex', alignItems: 'center', gap: 0.5, px: 1, py: 0.5,
            borderRadius: 1, cursor: 'pointer', bgcolor: conv?.id === r.id ? 'action.selected' : 'transparent',
            '&:hover': { bgcolor: 'action.hover' } }}>
            <Typography sx={{ fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.title}</Typography>
            <IconButton size="small" onClick={(e) => { e.stopPropagation(); remove(r.id); }} aria-label="delete conversation">
              <DeleteOutlineIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </Box>
        ))}
      </Box>
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Box sx={{ flex: 1, overflowY: 'auto', px: { xs: 2, md: 4 }, py: 2 }}>
          <Box sx={{ maxWidth: 860, mx: 'auto' }}>
            {!conv && (
              <Box sx={{ mt: 2 }}>
                <Typography sx={{ fontWeight: 700, fontSize: 18, mb: 0.5 }}>Design assistant</Typography>
                <Typography sx={{ fontSize: 13, color: 'text.secondary', mb: 2 }}>
                  Describe what you are building. The assistant picks motors and propellers from your catalogue using our
                  simulation results only, and opens the one you choose in Configure.
                </Typography>
                {EXAMPLES.map((e) => (
                  <Box key={e} onClick={() => setText(e)} sx={{ border: '1px dashed', borderColor: 'divider', borderRadius: 1, p: 1, mb: 1,
                    cursor: 'pointer', fontSize: 12.5, color: 'text.secondary', '&:hover': { borderColor: 'primary.main' } }}>{e}</Box>
                ))}
              </Box>
            )}
            {conv?.display.map((m, i) => (
              m.role === 'user' ? (
                <Box key={i} sx={{ display: 'flex', justifyContent: 'flex-end', my: 1.5 }}>
                  <Box sx={{ bgcolor: 'action.selected', borderRadius: 2, px: 1.5, py: 1, maxWidth: '80%', fontSize: 13.5, whiteSpace: 'pre-wrap' }}>{m.text}</Box>
                </Box>
              ) : (
                <Box key={i} sx={{ my: 1.5 }}>
                  <Steps steps={m.steps ?? []} />
                  {m.text && <MiniMarkdown text={m.text} />}
                  {(m.cards ?? []).length > 0 && (
                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, mt: 1 }}>
                      {(m.cards ?? []).map((c, j) => <MotorCard key={j} c={c} />)}
                    </Box>
                  )}
                </Box>
              )
            ))}
            {running && <Steps steps={conv?.pending?.steps ?? []} live />}
            {conv?.state === 'error' && conv.error && (
              <Typography sx={{ fontSize: 12.5, color: '#fca5a5', my: 1 }}>{conv.error}</Typography>
            )}
            <div ref={bottom} />
          </Box>
        </Box>
        <Box sx={{ borderTop: '1px solid', borderColor: 'divider', px: { xs: 2, md: 4 }, py: 1.5 }}>
          <Box sx={{ maxWidth: 860, mx: 'auto' }}>
            {err && <Typography sx={{ fontSize: 12, color: '#fca5a5', mb: 0.5 }}>{err}</Typography>}
            <Typography sx={{ fontSize: 12, color: 'text.secondary', mb: 0.5 }} data-testid="ai-disclosure">{tc('legal.aiDisclosure')}</Typography>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-end' }}>
              <TextField fullWidth multiline maxRows={6} size="small" value={text} disabled={running}
                placeholder="Describe your drone or robot: mass, number of motors, battery, propeller size…"
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} />
              <IconButton color="primary" onClick={send} disabled={running || sending || !text.trim()} aria-label="send">
                {sending ? <CircularProgress size={18} /> : <SendIcon />}
              </IconButton>
            </Box>
            {status?.limits && (
              <Typography sx={{ fontSize: 10.5, color: 'text.secondary', mt: 0.5 }}>
                Pilot · {status.model} · this conversation ${(conv?.cost_usd ?? 0).toFixed(2)} of ${status.limits.conversation_usd.toFixed(2)}
                {' · '}today ${(status.spent_today_usd ?? 0).toFixed(2)} of ${status.limits.user_day_usd.toFixed(2)}
              </Typography>
            )}
            {status && !status.configured && (
              <Typography sx={{ fontSize: 11, color: '#fbbf24', mt: 0.5 }}>The server has no API key for the assistant yet.</Typography>
            )}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
