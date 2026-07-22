/**
 * SupportWidget — floating "Help & feedback" launcher available to every user.
 *
 * Three modes in one panel:
 *  - Ask:    chat with the in-app AI assistant (backend proxies to Claude).
 *  - Report: file a bug / feature request / question → ticket in Firestore.
 *  - Tickets: the signed-in user's own tickets and their status.
 *
 * Reporting needs a signed-in account (Firestore is per-user); the AI chat works
 * for anyone the backend lets through.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  Box, Paper, IconButton, Tabs, Tab, TextField, Button, Chip, CircularProgress,
  Typography, ToggleButtonGroup, ToggleButton, Tooltip,
} from '@mui/material';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import CloseIcon from '@mui/icons-material/Close';
import SendIcon from '@mui/icons-material/Send';
import { useAuth } from '../../contexts/AuthContext';
import {
  askAssistant, submitTicket, listMyTickets, type ChatMsg, type Ticket, type TicketType,
} from '../../lib/support';

const STATUS_COLOR: Record<string, string> = {
  open: '#60a5fa', in_progress: '#fbbf24', resolved: '#4ade80', closed: 'var(--text-3)',
};
const TYPE_COLOR: Record<string, string> = { bug: '#f87171', feature: '#a78bfa', question: 'var(--text-3)' };
const GREETING: ChatMsg = {
  role: 'assistant',
  content: 'Hi! I can help with the Configurator, motor parameters, plans, and how the app works. Ask away — or use **Report** to send a bug or feature request.',
};

const SupportWidget: React.FC = () => {
  const { user, signIn } = useAuth();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'ask' | 'report' | 'tickets'>('ask');

  // chat
  const [msgs, setMsgs] = useState<ChatMsg[]>([GREETING]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [demo, setDemo] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // report
  const [rtype, setRtype] = useState<TicketType>('bug');
  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  // tickets
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loadingTickets, setLoadingTickets] = useState(false);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, sending, open, tab]);

  useEffect(() => {
    if (open && tab === 'tickets' && user) {
      setLoadingTickets(true);
      listMyTickets(user.uid).then(setTickets).catch(() => setTickets([])).finally(() => setLoadingTickets(false));
    }
  }, [open, tab, user]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    const next = [...msgs, { role: 'user', content: text } as ChatMsg];
    setMsgs(next); setInput(''); setSending(true);
    try {
      const { reply, source } = await askAssistant(next);
      setDemo(source === 'mock');
      setMsgs((m) => [...m, { role: 'assistant', content: reply }]);
    } catch {
      setMsgs((m) => [...m, { role: 'assistant', content: "Sorry — I couldn't answer just now. Please try again, or use the Report tab." }]);
    } finally {
      setSending(false);
    }
  };

  const file = async () => {
    if (!user || !title.trim() || submitting) return;
    setSubmitting(true);
    try {
      await submitTicket(user.uid, user.email, { type: rtype, title, description: desc });
      setSubmitted(true); setTitle(''); setDesc('');
    } catch { /* surface nothing destructive */ } finally {
      setSubmitting(false);
    }
  };

  if (!open) {
    return (
      <Tooltip title="Help & feedback" placement="left">
        <IconButton
          onClick={() => setOpen(true)}
          sx={{
            position: 'fixed', bottom: 20, right: 20, zIndex: 1300,
            width: 52, height: 52, bgcolor: '#2563eb', color: '#fff',
            boxShadow: '0 4px 16px rgba(0,0,0,0.4)', '&:hover': { bgcolor: '#1d4ed8' },
          }}
        >
          <ChatBubbleOutlineIcon />
        </IconButton>
      </Tooltip>
    );
  }

  return (
    <Paper sx={{
      position: 'fixed', bottom: 20, right: 20, zIndex: 1300,
      width: 'min(380px, calc(100vw - 32px))', height: 'min(560px, calc(100vh - 40px))',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2,
      boxShadow: '0 10px 40px rgba(0,0,0,0.5)',
    }}>
      {/* header */}
      <Box sx={{ display: 'flex', alignItems: 'center', px: 1.5, py: 1, bgcolor: 'var(--panel-2)', borderBottom: '1px solid var(--line-soft)' }}>
        <Typography sx={{ fontSize: 14, fontWeight: 800, color: 'var(--text-0)', flex: 1 }}>Help &amp; feedback</Typography>
        <IconButton size="small" onClick={() => setOpen(false)} sx={{ color: 'var(--text-3)' }}><CloseIcon sx={{ fontSize: 18 }} /></IconButton>
      </Box>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="fullWidth"
        sx={{ minHeight: 36, borderBottom: '1px solid var(--line-soft)', '& .MuiTab-root': { minHeight: 36, fontSize: 12, textTransform: 'none' } }}>
        <Tab label="Ask" value="ask" />
        <Tab label="Report" value="report" />
        {user && <Tab label="My tickets" value="tickets" />}
      </Tabs>

      {/* ASK */}
      {tab === 'ask' && (
        <>
          <Box ref={scrollRef} sx={{ flex: 1, overflowY: 'auto', p: 1.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
            {msgs.map((m, i) => (
              <Box key={i} sx={{
                alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '85%',
                bgcolor: m.role === 'user' ? 'var(--line-accent)' : 'var(--line-soft)', color: 'var(--text-0)',
                px: 1.25, py: 0.85, borderRadius: 1.5, fontSize: 13, lineHeight: 1.45, whiteSpace: 'pre-wrap',
              }}>
                {m.content}
              </Box>
            ))}
            {sending && (
              <Box sx={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: 1, color: 'var(--text-3)', px: 1 }}>
                <CircularProgress size={12} /> <Typography sx={{ fontSize: 12 }}>thinking…</Typography>
              </Box>
            )}
            {demo && (
              <Typography sx={{ fontSize: 10.5, color: '#fbbf24', textAlign: 'center', mt: 0.5 }}>
                demo mode — AI assistant not configured on this server
              </Typography>
            )}
          </Box>
          <Box sx={{ display: 'flex', gap: 0.75, p: 1, borderTop: '1px solid var(--line-soft)' }}>
            <TextField
              value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }}
              placeholder="Ask about the app…" size="small" fullWidth multiline maxRows={3}
              sx={{ '& .MuiInputBase-root': { fontSize: 13, bgcolor: 'var(--panel-2)' } }}
            />
            <IconButton onClick={() => void send()} disabled={!input.trim() || sending} sx={{ color: '#60a5fa' }}>
              <SendIcon sx={{ fontSize: 20 }} />
            </IconButton>
          </Box>
        </>
      )}

      {/* REPORT */}
      {tab === 'report' && (
        <Box sx={{ flex: 1, overflowY: 'auto', p: 2 }}>
          {!user ? (
            <Box sx={{ textAlign: 'center', mt: 4 }}>
              <Typography sx={{ fontSize: 13, color: 'var(--text-2)', mb: 2 }}>Sign in to send a bug report or feature request — so we can follow up with you.</Typography>
              <Button variant="contained" size="small" onClick={() => void signIn()} sx={{ textTransform: 'none' }}>Sign in with Google</Button>
            </Box>
          ) : submitted ? (
            <Box sx={{ textAlign: 'center', mt: 4 }}>
              <Typography sx={{ fontSize: 14, color: '#4ade80', fontWeight: 700, mb: 1 }}>Thanks — sent! ✓</Typography>
              <Typography sx={{ fontSize: 12, color: 'var(--text-2)', mb: 2 }}>The team will see your ticket. You can track it under “My tickets”.</Typography>
              <Button size="small" onClick={() => setSubmitted(false)} sx={{ textTransform: 'none', color: '#60a5fa' }}>Send another</Button>
            </Box>
          ) : (
            <>
              <ToggleButtonGroup exclusive value={rtype} onChange={(_, v) => v && setRtype(v)} size="small" fullWidth sx={{ mb: 1.5 }}>
                <ToggleButton value="bug" sx={{ textTransform: 'none', fontSize: 12 }}>🐞 Bug</ToggleButton>
                <ToggleButton value="feature" sx={{ textTransform: 'none', fontSize: 12 }}>💡 Feature</ToggleButton>
                <ToggleButton value="question" sx={{ textTransform: 'none', fontSize: 12 }}>❔ Question</ToggleButton>
              </ToggleButtonGroup>
              <TextField label="Title" value={title} onChange={(e) => setTitle(e.target.value)} size="small" fullWidth sx={{ mb: 1.5 }}
                InputLabelProps={{ sx: { fontSize: 13 } }} inputProps={{ maxLength: 120 }} />
              <TextField label="Describe it (what you did, what happened)" value={desc} onChange={(e) => setDesc(e.target.value)}
                size="small" fullWidth multiline minRows={4} sx={{ mb: 1.5 }} InputLabelProps={{ sx: { fontSize: 13 } }} inputProps={{ maxLength: 2000 }} />
              <Button variant="contained" fullWidth disabled={!title.trim() || submitting} onClick={() => void file()} sx={{ textTransform: 'none' }}>
                {submitting ? <CircularProgress size={18} /> : 'Send to the team'}
              </Button>
            </>
          )}
        </Box>
      )}

      {/* MY TICKETS */}
      {tab === 'tickets' && user && (
        <Box sx={{ flex: 1, overflowY: 'auto', p: 1.5 }}>
          {loadingTickets ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}><CircularProgress size={20} /></Box>
          ) : tickets.length === 0 ? (
            <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', mt: 4 }}>No tickets yet. Use the Report tab to file one.</Typography>
          ) : tickets.map((t) => (
            <Box key={t.id} sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, p: 1.25, mb: 1 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.5 }}>
                <Chip label={t.type} size="small" sx={{ height: 18, fontSize: 9.5, bgcolor: 'var(--panel-2)', color: TYPE_COLOR[t.type] ?? 'var(--text-3)' }} />
                <Box sx={{ flex: 1 }} />
                <Chip label={(t.status || 'open').replace('_', ' ')} size="small" sx={{ height: 18, fontSize: 9.5, bgcolor: 'var(--panel-2)', color: STATUS_COLOR[t.status] ?? 'var(--text-3)' }} />
              </Box>
              <Typography sx={{ fontSize: 13, color: 'var(--text-0)', fontWeight: 600 }}>{t.title}</Typography>
              {t.description && <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)', mt: 0.25 }}>{t.description}</Typography>}
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  );
};

export default SupportWidget;
