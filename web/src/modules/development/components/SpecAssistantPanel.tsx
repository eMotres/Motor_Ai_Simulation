/**
 * Specification assistant (owner 2026-10-27): a collapsible panel on the right of Development -> Specification.
 * The in-app support assistant, in spec mode, asks about the missing design-critical fields and PROPOSES values with a
 * one-line reason.  A proposal is a card (field, value with unit, reason, the recalculations it triggers); nothing is
 * written to the specification until a spec editor (the project owner or an admin) presses Apply.  Everybody else sees
 * the card with "the project owner applies".  The chat lives in this browser tab (the server is stateless).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Button, CircularProgress, IconButton, Paper, TextField, Tooltip, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import SendIcon from '@mui/icons-material/Send';
import SmartToyOutlinedIcon from '@mui/icons-material/SmartToyOutlined';
import { useTranslation } from 'react-i18next';
import type { DevelopmentSchema } from '../types';
import { applyFailure, cardAction, cardView, historyForServer, langOf, openProposals, type ChatMessage, type Proposal } from '../specAssistant';
import { specAssistantApi, SpecAssistantError, type AssistantStatus } from '../specAssistantApi';

interface Props {
  projectId: string;
  schema: DevelopmentSchema | null;
  /** UI language (i18n) - the assistant answers in it */
  lang: string;
  /** called before Apply: save the pending edits so the revision the server checks is current */
  beforeApply?: () => Promise<void>;
  /** called after a proposal was applied: reload the specification form */
  onApplied: () => void;
  /** the right column of the wizard (wide screens): in the page flow and sticky instead of floating at the screen edge */
  docked?: boolean;
}

const OPEN_KEY = 'specAssistant.open';
const chatKey = (id: string) => `specAssistant.chat.${id}`;
const readStore = (k: string): string | null => { try { return window.localStorage.getItem(k); } catch { return null; } };
const writeStore = (k: string, v: string) => { try { window.localStorage.setItem(k, v); } catch { /* storage may be blocked */ } };
const readChat = (id: string): ChatMessage[] => {
  try { const raw = window.sessionStorage.getItem(chatKey(id)); const v = raw ? JSON.parse(raw) : []; return Array.isArray(v) ? v : []; } catch { return []; }
};
const writeChat = (id: string, m: ChatMessage[]) => { try { window.sessionStorage.setItem(chatKey(id), JSON.stringify(m.slice(-40))); } catch { /* ignore */ } };

export default function SpecAssistantPanel({ projectId, schema, lang, beforeApply, onApplied, docked = false }: Props) {
  const { t } = useTranslation('development');
  const { t: tc } = useTranslation('common');
  const ui = langOf(lang);
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [open, setOpen] = useState(() => readStore(OPEN_KEY) === '1');
  const [messages, setMessages] = useState<ChatMessage[]>(() => readChat(projectId));
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [canApply, setCanApply] = useState(false);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const bottom = useRef<HTMLDivElement | null>(null);
  const token = useRef(0);

  const refresh = useCallback(async () => {
    const mine = token.current;
    try {
      const list = await specAssistantApi.proposals(projectId);
      if (mine === token.current) { setProposals(list.proposals); setCanApply(list.can_apply); }
    } catch { /* not open for this account, or offline: no cards */ }
  }, [projectId]);

  useEffect(() => {
    token.current += 1;
    setStatus(null); setProposals([]); setError(''); setMessages(readChat(projectId));
    let live = true;
    specAssistantApi.status(projectId).then((s) => { if (live) { setStatus(s); setCanApply(s.can_apply); } }).catch(() => { if (live) setStatus({ allowed: false, can_apply: false, configured: false }); });
    return () => { live = false; };
  }, [projectId]);
  useEffect(() => { if (status?.allowed && open) void refresh(); }, [status?.allowed, open, refresh]);
  useEffect(() => { bottom.current?.scrollIntoView?.({ block: 'end' }); }, [messages.length, sending, proposals.length, open]);

  const toggle = (next: boolean) => { setOpen(next); writeStore(OPEN_KEY, next ? '1' : '0'); };

  const send = async (typed: string) => {
    if (sending) return;
    const content = typed.trim();
    const next = content ? [...messages, { role: 'user' as const, content }] : messages;
    if (content) { setMessages(next); writeChat(projectId, next); setText(''); }
    setSending(true); setError('');
    try {
      const turn = await specAssistantApi.turn(projectId, historyForServer(next), ui);
      const after = [...next, { role: 'assistant' as const, content: turn.reply }];
      setMessages(after); writeChat(projectId, after);
      await refresh();
    } catch (e) {
      setError(e instanceof SpecAssistantError && e.status === 403 ? t('specAssistant.closed') : t('specAssistant.failed'));
    }
    setSending(false);
  };

  const apply = async (p: Proposal) => {
    setBusyId(p.id); setError('');
    try {
      await beforeApply?.();
      const fresh = await specAssistantApi.proposals(projectId);
      await specAssistantApi.apply(projectId, p.id, fresh.revision);
      onApplied();
    } catch (e) {
      const f = e instanceof SpecAssistantError ? applyFailure(e.status, e.detail) : { key: 'specAssistant.applyFailed', reload: false };
      setError(t(f.key));
    }
    await refresh();
    setBusyId(null);
  };
  const dismiss = async (p: Proposal) => {
    setBusyId(p.id); setError('');
    try { await specAssistantApi.dismiss(projectId, p.id); } catch (e) {
      setError(t(e instanceof SpecAssistantError && e.status === 409 ? 'specAssistant.alreadyClosed' : 'specAssistant.applyFailed'));
    }
    await refresh();
    setBusyId(null);
  };

  if (!status?.allowed) return null;
  if (!open) {
    return (
      <Tooltip title={t('specAssistant.tip')} placement="left">
        <Button size="small" variant="contained" startIcon={<SmartToyOutlinedIcon />} onClick={() => toggle(true)}
          sx={docked ? { position: 'sticky', top: 8, alignSelf: 'flex-start', flex: '0 0 auto', minHeight: 44, textTransform: 'none' } : { position: 'fixed', right: 0, top: '38%', zIndex: 1190, textTransform: 'none', borderRadius: '8px 0 0 8px', minWidth: 0 }}>
          {t('specAssistant.title')}{proposals.length > 0 ? ` (${proposals.length})` : ''}
        </Button>
      </Tooltip>
    );
  }
  const cards = openProposals(proposals);
  return (
    <Paper elevation={docked ? 0 : 6} data-testid="spec-assistant" sx={docked
      ? { position: 'sticky', top: 8, alignSelf: 'flex-start', flex: '0 0 360px', width: 360, height: 'calc(100vh - 120px)', minHeight: 420, display: 'flex', flexDirection: 'column', bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px' }
      : { position: 'fixed', right: 0, top: 64, bottom: 8, width: { xs: '100vw', sm: 380 }, zIndex: 1190, display: 'flex', flexDirection: 'column', bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '8px 0 0 8px' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, px: 1.5, py: 0.8, borderBottom: '1px solid var(--line-soft)' }}>
        <SmartToyOutlinedIcon sx={{ fontSize: 18 }} />
        <Tooltip title={t('specAssistant.tip')}><Typography sx={{ flex: 1, fontWeight: 700, fontSize: 14 }}>{t('specAssistant.title')}</Typography></Tooltip>
        <IconButton size="small" aria-label={t('specAssistant.close')} onClick={() => toggle(false)}><CloseIcon sx={{ fontSize: 18 }} /></IconButton>
      </Box>
      {cards.length > 0 && (
        <Box sx={{ maxHeight: '45%', overflowY: 'auto', p: 1, display: 'flex', flexDirection: 'column', gap: 1, borderBottom: '1px solid var(--line-soft)' }}>
          {cards.map((p) => {
            const c = cardView(p, schema, ui);
            const action = cardAction(p, canApply);
            return (
              <Box key={c.id} data-testid="spec-proposal" sx={{ border: '1px solid var(--line)', borderLeft: '3px solid #3b82f6', borderRadius: 1, p: 1, bgcolor: 'var(--panel-2)' }}>
                <Typography sx={{ fontWeight: 700, fontSize: 13 }}>{c.title}</Typography>
                <Typography sx={{ fontSize: 13 }}>{c.proposed}</Typography>
                {c.current && <Typography sx={{ fontSize: 11.5, color: 'text.secondary' }}>{t('specAssistant.now', { value: c.current })}</Typography>}
                <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{c.reason}</Typography>
                <Typography sx={{ fontSize: 12, color: c.recalcNeeded ? 'warning.main' : 'text.secondary' }}>{t('specAssistant.recalc', { what: c.recalc })}</Typography>
                {action === 'apply' ? (
                  <Box sx={{ display: 'flex', gap: 1, mt: 0.8 }}>
                    <Button size="small" variant="contained" disabled={busyId !== null} onClick={() => void apply(p)} sx={{ textTransform: 'none' }}>
                      {busyId === c.id ? <CircularProgress size={14} /> : t('specAssistant.apply')}
                    </Button>
                    <Button size="small" disabled={busyId !== null} onClick={() => void dismiss(p)} sx={{ textTransform: 'none' }}>{t('specAssistant.dismiss')}</Button>
                  </Box>
                ) : <Typography sx={{ fontSize: 11.5, mt: 0.6, fontStyle: 'italic' }}>{t('specAssistant.ownerApplies')}</Typography>}
              </Box>
            );
          })}
        </Box>
      )}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 1.2, minHeight: 0 }}>
        {messages.length === 0 && (
          <Box>
            <Typography sx={{ fontSize: 12.5, color: 'text.secondary', mb: 1 }}>{t('specAssistant.intro')}</Typography>
            <Button size="small" variant="outlined" disabled={sending || status.configured === false} onClick={() => void send('')} sx={{ textTransform: 'none' }}>{t('specAssistant.start')}</Button>
            {status.configured === false && <Typography sx={{ fontSize: 11.5, mt: 0.6 }}>{t('specAssistant.notConfigured')}</Typography>}
          </Box>
        )}
        {messages.map((m, i) => (
          <Box key={i} sx={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', my: 0.8 }}>
            <Box sx={{ bgcolor: m.role === 'user' ? 'action.selected' : 'transparent', borderRadius: 2, px: m.role === 'user' ? 1.2 : 0, py: m.role === 'user' ? 0.7 : 0,
              maxWidth: '92%', fontSize: 13, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.content}</Box>
          </Box>
        ))}
        {sending && <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, my: 1 }}><CircularProgress size={13} /><Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{t('specAssistant.working')}</Typography></Box>}
        {error && <Typography role="alert" sx={{ fontSize: 12, color: 'error.main', my: 0.8 }}>{error}</Typography>}
        <div ref={bottom} />
      </Box>
      <Typography sx={{ fontSize: 12, color: 'text.secondary', px: 1.25, pt: 0.75, borderTop: '1px solid var(--line-soft)' }} data-testid="ai-disclosure">
        {tc('legal.aiDisclosure')}
      </Typography>
      <Box sx={{ display: 'flex', gap: 0.8, p: 1 }}>
        <TextField size="small" fullWidth multiline maxRows={4} value={text} placeholder={t('specAssistant.placeholder')} disabled={sending}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(text); } }} />
        <IconButton aria-label={t('specAssistant.send')} disabled={sending || !text.trim()} onClick={() => void send(text)}><SendIcon sx={{ fontSize: 18 }} /></IconButton>
      </Box>
    </Paper>
  );
}
