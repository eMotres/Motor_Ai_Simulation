/**
 * SupportWidget — floating "Help & feedback" launcher available to every user.
 *
 * Every question and every problem goes through the assistant (owner 2026-10-05):
 *  - Ask:     chat with the in-app assistant. It answers, asks for what is missing
 *             and, for a bug / feature request / account issue / something it
 *             cannot answer, proposes a TICKET DRAFT. The draft is shown as a card
 *             the user can edit and send; the conversation and a snapshot of the
 *             screen (tab, motor, Configure values, build, browser, recent failed
 *             calls — never a token) are attached.
 *  - Tickets: the signed-in user's own tickets and their status.
 *
 * There is no separate report form. The rules of the conversation are pure and
 * live in lib/supportFlow.ts; answers are rendered as markdown by
 * SupportMarkdown (sanitised: no raw HTML from the model).
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  Box, Paper, IconButton, Tabs, Tab, TextField, Chip, CircularProgress, Typography, Tooltip,
} from '@mui/material';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import CloseIcon from '@mui/icons-material/Close';
import SendIcon from '@mui/icons-material/Send';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../contexts/AuthContext';
import { useUIStore } from '../../stores/motorStore';
import { APP_VERSION, APP_GIT_SHA } from '../../lib/version';
import { askAssistant, submitTicket, listMyTickets, type Ticket } from '../../lib/support';
import {
  initialFlow, addUserMessage, receiveReply, editDraft, dismissDraft, runConfirm,
  type FlowState,
} from '../../lib/supportFlow';
import { buildSupportContext, installFailedCallRecorder } from '../../lib/supportContext';
import SupportMarkdown from './SupportMarkdown';
import TicketDraftCard from './TicketDraftCard';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
// Remember failed calls from the first request on, not from the moment the widget opens.
installFailedCallRecorder(API.replace(/\/$/, ''));

const STATUS_COLOR: Record<string, string> = {
  open: '#60a5fa', in_progress: '#fbbf24', resolved: '#4ade80', closed: 'var(--text-3)',
};
const TYPE_COLOR: Record<string, string> = {
  bug: '#f87171', feature: '#a78bfa', question: 'var(--text-2)', account: '#38bdf8',
};
const BODY = { fontSize: 13, lineHeight: 1.45 } as const;

const SupportWidget: React.FC = () => {
  const { user } = useAuth();
  const { t, i18n } = useTranslation('support');
  const activeTab = useUIStore((s) => s.activeTab) as string;
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'ask' | 'tickets'>('ask');

  // chat + the ticket draft: one pure state (lib/supportFlow.ts)
  const [flow, setFlow] = useState<FlowState>(initialFlow);
  const flowRef = useRef(flow);
  flowRef.current = flow;
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);
  const [demo, setDemo] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // tickets
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loadingTickets, setLoadingTickets] = useState(false);
  const [ticketsFailed, setTicketsFailed] = useState(false);

  // the snapshot of the screen that rides with a message and a ticket
  const context = () => buildSupportContext({
    tab: activeTab, lang: i18n.resolvedLanguage ?? i18n.language ?? 'en',
    appVersion: APP_VERSION, appGitSha: APP_GIT_SHA,
    userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
  });

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [flow, sending, open, tab]);

  useEffect(() => {
    if (!(open && tab === 'tickets' && user)) return;
    setLoadingTickets(true); setTicketsFailed(false);
    listMyTickets().then(setTickets).catch(() => { setTickets([]); setTicketsFailed(true); })
      .finally(() => setLoadingTickets(false));
  }, [open, tab, user, flow.draft?.status]);

  // a visitor has no tickets tab
  useEffect(() => { if (!user && tab === 'tickets') setTab('ask'); }, [user, tab]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    const next = addUserMessage(flowRef.current, text);
    setFlow(next); setInput(''); setSending(true); setFailed(false);
    try {
      const reply = await askAssistant(next.msgs, user ? context() : undefined);
      setDemo(reply.source === 'mock');
      setFlow((f) => receiveReply(f, reply));
    } catch {
      setFailed(true);
    } finally {
      setSending(false);
    }
  };

  const confirm = () => { void runConfirm(flowRef.current, submitTicket, context(), setFlow); };

  if (!open) {
    return (
      <Tooltip title={t('title')} placement="left">
        <IconButton
          aria-label={t('title')}
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

  const draftOpen = !!flow.draft;

  return (
    <Paper sx={{
      position: 'fixed', bottom: 20, right: 20, zIndex: 1300,
      width: 'min(420px, calc(100vw - 32px))', height: 'min(640px, calc(100vh - 40px))',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2,
      boxShadow: '0 10px 40px rgba(0,0,0,0.5)',
    }}>
      {/* header */}
      <Box sx={{ display: 'flex', alignItems: 'center', px: 1.5, py: 1, bgcolor: 'var(--panel-2)', borderBottom: '1px solid var(--line-soft)' }}>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)', flex: 1 }}>{t('title')}</Typography>
        <IconButton size="small" aria-label={t('close')} onClick={() => setOpen(false)} sx={{ color: 'var(--text-3)' }}>
          <CloseIcon sx={{ fontSize: 20 }} />
        </IconButton>
      </Box>

      {user && (
        <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="fullWidth"
          sx={{ minHeight: 40, borderBottom: '1px solid var(--line-soft)', '& .MuiTab-root': { minHeight: 40, fontSize: 13, textTransform: 'none' } }}>
          <Tab label={t('tabAsk')} value="ask" />
          <Tab label={t('tabTickets')} value="tickets" />
        </Tabs>
      )}

      {/* ASK */}
      {tab === 'ask' && (
        <>
          <Box ref={scrollRef} sx={{ flex: 1, overflowY: 'auto', p: 1.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
            <Box sx={{ alignSelf: 'flex-start', maxWidth: '88%', bgcolor: 'var(--line-soft)', color: 'var(--text-0)', px: 1.25, py: 0.85, borderRadius: 1.5, ...BODY }}>
              {user ? t('greeting') : t('greetingVisitor')}
            </Box>
            {flow.msgs.map((m, i) => (
              <Box key={i} sx={{
                alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '88%',
                bgcolor: m.role === 'user' ? 'var(--line-accent)' : 'var(--line-soft)', color: 'var(--text-0)',
                px: 1.25, py: 0.85, borderRadius: 1.5, ...BODY,
                ...(m.role === 'user' ? { whiteSpace: 'pre-wrap', wordBreak: 'break-word' } : {}),
              }}>
                {m.role === 'assistant' ? <SupportMarkdown text={m.content} /> : m.content}
              </Box>
            ))}
            {draftOpen && (
              <TicketDraftCard flow={flow} signedIn={!!user}
                onEdit={(p) => setFlow((f) => editDraft(f, p))}
                onSend={confirm}
                onDismiss={() => setFlow((f) => dismissDraft(f))} />
            )}
            {sending && (
              <Box sx={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: 1, color: 'var(--text-2)', px: 1 }}>
                <CircularProgress size={14} /> <Typography sx={BODY}>{t('thinking')}</Typography>
              </Box>
            )}
            {failed && (
              <Typography sx={{ ...BODY, color: '#f87171' }} role="alert">{t('failed')}</Typography>
            )}
            {demo && (
              <Typography sx={{ ...BODY, color: '#fbbf24', textAlign: 'center', mt: 0.5 }}>{t('demo')}</Typography>
            )}
          </Box>
          <Box sx={{ display: 'flex', gap: 0.75, p: 1, borderTop: '1px solid var(--line-soft)' }}>
            <TextField
              value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }}
              placeholder={t('placeholder')} size="small" fullWidth multiline maxRows={4}
              sx={{ '& .MuiInputBase-root': { fontSize: 14, bgcolor: 'var(--panel-2)' } }}
            />
            <IconButton aria-label={t('send')} onClick={() => void send()} disabled={!input.trim() || sending} sx={{ color: '#60a5fa' }}>
              <SendIcon sx={{ fontSize: 22 }} />
            </IconButton>
          </Box>
        </>
      )}

      {/* MY TICKETS */}
      {tab === 'tickets' && user && (
        <Box sx={{ flex: 1, overflowY: 'auto', p: 1.5 }}>
          {loadingTickets ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}><CircularProgress size={22} /></Box>
          ) : ticketsFailed ? (
            <Typography sx={{ ...BODY, color: '#f87171', textAlign: 'center', mt: 4 }}>{t('tickets.loadFailed')}</Typography>
          ) : tickets.length === 0 ? (
            <Typography sx={{ ...BODY, color: 'var(--text-2)', textAlign: 'center', mt: 4 }}>{t('tickets.empty')}</Typography>
          ) : tickets.map((k) => (
            <Box key={k.id} sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, p: 1.25, mb: 1 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.5 }}>
                <Chip label={t(`type.${k.type}`, { defaultValue: k.type })} size="small"
                  sx={{ height: 22, fontSize: 12, bgcolor: 'var(--panel-2)', color: TYPE_COLOR[k.type] ?? 'var(--text-2)' }} />
                <Box sx={{ flex: 1 }} />
                <Chip label={t(`status.${k.status || 'open'}`, { defaultValue: (k.status || 'open').replace('_', ' ') })} size="small"
                  sx={{ height: 22, fontSize: 12, bgcolor: 'var(--panel-2)', color: STATUS_COLOR[k.status] ?? 'var(--text-2)' }} />
              </Box>
              <Typography sx={{ ...BODY, color: 'var(--text-0)', fontWeight: 600 }}>{k.title}</Typography>
              {k.description && <Typography sx={{ ...BODY, color: 'var(--text-2)', mt: 0.25 }}>{k.description}</Typography>}
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  );
};

export default SupportWidget;
