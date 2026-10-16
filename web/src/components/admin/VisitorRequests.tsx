/**
 * Admin · Visitor requests — the inbox of people who asked for access, and the
 * raw daily log of what signed-out visitors asked the assistant.
 *
 * Since 2026-09-17 the in-app assistant answers anonymous callers on the landing
 * page, and the one question a visitor has is "how do I get in". When they leave
 * contact details the backend files a structured request; this is where those
 * land. The chats below them are the evidence behind each one — and the record
 * of every visitor who asked something and did NOT leave an address.
 *
 * Read-only except for the status select, the Invite button and a cleanup delete.
 * The bearer is attached by the global fetch interceptor (lib/apiAuth), so these
 * are plain `fetch` calls exactly like the rest of the admin panel.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box, Paper, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  Button, Chip, Tooltip, Select, MenuItem, Collapse, IconButton, CircularProgress,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight';
import MailOutlineIcon from '@mui/icons-material/MailOutline';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import { ConfirmDialog, type ConfirmState } from '../common/PromptDialogs';
import { shortUA } from '../auth/SessionsDialog';
import {
  REQUEST_STATUSES, newRequestCount, requestSummary, contactLine, fmtWhen,
  type VisitorRequest, type VisitorConversation,
} from '../../lib/visitorRequests';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const LABEL = { fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' } as const;
const TABLE_SX = {
  '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
  '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
} as const;

/** Colour by what the line MEANS: blue = waiting on us, green = they are in. */
const STATUS_COLOR: Record<string, string> = {
  new: '#60a5fa', contacted: '#fbbf24', invited: '#4ade80', declined: 'var(--text-3)',
};
const LIMIT_LABEL: Record<string, string> = {
  ip_burst: 'burst limit', ip_day: 'daily limit (this IP)', global_day: 'daily limit (all visitors)',
};

/** The only setup this feature needs, said once, in a tooltip rather than on the page. */
const PUSH_HELP =
  'Optional e-mail alert, one per request: turn on 2-step verification for vadim@motresres.com, '
  + 'create an app password at myaccount.google.com/apppasswords, then set SMTP_USER and SMTP_PASSWORD '
  + 'in /etc/motres/api.env and restart the API (port 587 is open on the host; 25 and 465 are not). '
  + 'Reply to the alert and the visitor gets the answer. Unset = no mail, this inbox still fills.';

/** A transcript that never scrolls sideways — an admin reads it, they do not pan it. */
const TurnBubble: React.FC<{ who: string; text: string; mine: boolean }> = ({ who, text, mine }) => (
  <Box sx={{ mb: 0.75 }}>
    <Typography sx={{ ...LABEL, color: mine ? '#60a5fa' : 'var(--text-4)' }}>{who}</Typography>
    <Typography sx={{
      fontSize: 12.5, color: mine ? 'var(--text-0)' : 'var(--text-2)',
      whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', userSelect: 'text',
      borderLeft: '2px solid', borderColor: mine ? '#60a5fa' : 'var(--line-soft)', pl: 1,
    }}>
      {text}
    </Typography>
  </Box>
);

// ── the inbox ───────────────────────────────────────────────────────────────

const RequestRow: React.FC<{
  r: VisitorRequest; busy: boolean;
  onStatus: (status: string) => void; onInvite: () => void; onDelete: () => void;
}> = ({ r, busy, onStatus, onInvite, onDelete }) => {
  const [open, setOpen] = useState(false);
  const turns = Array.isArray(r.transcript) ? r.transcript : [];
  return (
    <>
      <TableRow hover sx={{ '& td': { borderBottom: open ? 'none' : undefined } }}>
        <TableCell sx={{ width: 32, pr: 0 }}>
          <Tooltip arrow title={turns.length ? `${turns.length} messages — click to read` : 'no transcript'}>
            <span>
              <IconButton size="small" disabled={!turns.length} onClick={() => setOpen((v) => !v)}
                sx={{ color: 'var(--text-3)', p: 0.25 }}>
                {open ? <KeyboardArrowDownIcon sx={{ fontSize: 18 }} /> : <KeyboardArrowRightIcon sx={{ fontSize: 18 }} />}
              </IconButton>
            </span>
          </Tooltip>
        </TableCell>
        <TableCell sx={{ cursor: turns.length ? 'pointer' : 'default' }} onClick={() => turns.length && setOpen((v) => !v)}>
          <Typography sx={{ fontSize: 13, color: 'var(--text-0)', fontWeight: 600 }}>{contactLine(r)}</Typography>
          <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{r.email || '—'}</Typography>
        </TableCell>
        <TableCell sx={{ color: 'var(--text-2)', maxWidth: 420 }}>
          <Tooltip arrow title={r.note || requestSummary(r)}>
            <span>{requestSummary(r)}</span>
          </Tooltip>
        </TableCell>
        <TableCell sx={{ color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
          {fmtWhen(r.updated)}
          {r.merged > 1 && (
            <Tooltip arrow title={`${r.merged} requests from this address were merged`}>
              <Chip label={`×${r.merged}`} size="small"
                sx={{ ml: 0.75, height: 17, fontSize: 9.5, bgcolor: 'var(--panel)', color: 'var(--text-3)' }} />
            </Tooltip>
          )}
        </TableCell>
        <TableCell>
          <Tooltip arrow title={`${r.ip_hash || 'no ip'} · ${shortUA(r.ua)}`}>
            <Typography sx={{ fontSize: 10.5, fontFamily: 'ui-monospace, monospace', color: 'var(--text-4)' }}>
              {r.ip_hash || '—'}
            </Typography>
          </Tooltip>
        </TableCell>
        <TableCell>
          <Select
            value={r.status} variant="standard" disableUnderline disabled={busy}
            onChange={(e) => onStatus(e.target.value)}
            sx={{
              fontSize: 12, fontWeight: 700, color: STATUS_COLOR[r.status] ?? 'var(--text-2)',
              '& .MuiSelect-select': { py: 0.25, pr: '20px !important' }, '& svg': { color: 'var(--text-4)' },
            }}
          >
            {REQUEST_STATUSES.map((s) => (
              <MenuItem key={s} value={s} sx={{ fontSize: 12, color: STATUS_COLOR[s] }}>{s}</MenuItem>
            ))}
          </Select>
        </TableCell>
        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
          <Tooltip arrow title="Open the invite dialog with this address filled in">
            <span>
              <Button size="small" disabled={busy || !r.email} onClick={onInvite}
                startIcon={<MailOutlineIcon sx={{ fontSize: 14 }} />}
                sx={{ textTransform: 'none', fontSize: 11, color: 'var(--text-2)' }}>
                Invite
              </Button>
            </span>
          </Tooltip>
          <Tooltip arrow title="Delete this request">
            <span>
              <Button size="small" disabled={busy} onClick={onDelete}
                sx={{ color: 'var(--text-3)', minWidth: 0, px: 0.5, '&:hover': { color: '#f87171' } }}>
                <DeleteOutlineIcon sx={{ fontSize: 15 }} />
              </Button>
            </span>
          </Tooltip>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={7} sx={{ py: 0, borderBottom: open ? undefined : 'none' }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ py: 1.25, pl: 4, pr: 2 }}>
              {turns.map((t, i) => (
                <TurnBubble key={i} who={t.role === 'user' ? 'Visitor' : 'Assistant'}
                  text={t.content} mine={t.role === 'user'} />
              ))}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
};

// ── the daily log ───────────────────────────────────────────────────────────

const ConversationBlock: React.FC<{ c: VisitorConversation }> = ({ c }) => {
  const [open, setOpen] = useState(false);
  const turns = Array.isArray(c.turns) ? c.turns : [];
  return (
    <Box sx={{ borderTop: '1px solid var(--panel)' }}>
      <Box onClick={() => setOpen((v) => !v)}
        sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.75, cursor: 'pointer', '&:hover': { bgcolor: 'var(--panel)' } }}>
        {open ? <KeyboardArrowDownIcon sx={{ fontSize: 18, color: 'var(--text-3)' }} />
          : <KeyboardArrowRightIcon sx={{ fontSize: 18, color: 'var(--text-3)' }} />}
        <Typography sx={{ fontSize: 11, fontFamily: 'ui-monospace, monospace', color: 'var(--text-4)' }}>
          {c.ip_hash || '—'}
        </Typography>
        <Tooltip arrow title={c.ua || 'no user agent'}>
          <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{shortUA(c.ua)}</Typography>
        </Tooltip>
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          {turns.length} {turns.length === 1 ? 'question' : 'questions'}
        </Typography>
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)', whiteSpace: 'nowrap' }}>
          {fmtWhen(c.started)} → {fmtWhen(c.last)}
        </Typography>
      </Box>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ px: 2, pb: 1.5, pl: 4 }}>
          {turns.map((t, i) => (
            <Box key={i} sx={{ mb: 1 }}>
              <TurnBubble who="Visitor" text={t.user} mine />
              <TurnBubble who={t.source ? `Assistant · ${t.source}` : 'Assistant'} text={t.reply} mine={false} />
              {t.limit && (
                <Tooltip arrow title="This answer was the rate limiter's canned notice — no provider call was made.">
                  <Chip label={LIMIT_LABEL[t.limit] ?? t.limit} size="small"
                    sx={{ height: 17, fontSize: 9.5, bgcolor: 'var(--panel)', color: '#fbbf24' }} />
                </Tooltip>
              )}
            </Box>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
};

// ── section ─────────────────────────────────────────────────────────────────

const VisitorRequests: React.FC<{ onInvite: (email: string) => void; onCount?: (n: number) => void }> =
  ({ onInvite, onCount }) => {
    const [requests, setRequests] = useState<VisitorRequest[]>([]);
    const [convs, setConvs] = useState<VisitorConversation[]>([]);
    const [days, setDays] = useState<string[]>([]);
    const [day, setDay] = useState('');
    const [busy, setBusy] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [confirm, setConfirm] = useState<ConfirmState | null>(null);

    // `onCount` goes through a ref on purpose: a parent that passes an inline
    // arrow would otherwise change `load`'s identity on every one of its own
    // renders, and the mount effect below would re-fetch in a loop.
    const countCb = useRef(onCount);
    countCb.current = onCount;
    const report = useCallback((list: VisitorRequest[]) => { countCb.current?.(newRequestCount(list)); }, []);

    const loadChats = useCallback(async (which: string) => {
      const q = which ? `?day=${encodeURIComponent(which)}` : '';
      const j = await fetch(`${API}/api/admin/support/visitor_chats${q}`)
        .then((r) => (r.ok ? r.json() : null)).catch(() => null);
      if (!j) { setConvs([]); return; }
      setConvs(j.conversations ?? []);
      setDays(j.days ?? []);
      if (j.day) setDay(j.day);
    }, []);

    const load = useCallback(async (which: string) => {
      setLoading(true); setErr(null);
      try {
        const j = await fetch(`${API}/api/admin/support/requests`)
          .then((r) => { if (!r.ok) throw new Error(`requests HTTP ${r.status}`); return r.json(); });
        const list: VisitorRequest[] = Array.isArray(j?.requests) ? j.requests : [];
        setRequests(list); report(list);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      }
      await loadChats(which);
      setLoading(false);
    }, [loadChats, report]);

    useEffect(() => { void load(''); }, [load]);

    const setStatus = async (r: VisitorRequest, status: string) => {
      setBusy(r.id);
      try {
        const res = await fetch(`${API}/api/admin/support/requests/${encodeURIComponent(r.id)}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status }),
        });
        if (!res.ok) return;
        const j = await res.json().catch(() => ({}));
        setRequests((rs) => {
          const next = rs.map((x) => (x.id === r.id ? { ...x, ...(j.request ?? { status }) } : x));
          report(next);
          return next;
        });
      } catch { /* keep prior state */ } finally { setBusy(null); }
    };

    const remove = (r: VisitorRequest) => setConfirm({
      title: `Delete the request from ${r.email || contactLine(r)}?`,
      body: 'The request and its transcript are removed permanently. The daily visitor chat log is not touched.',
      onConfirm: () => {
        void (async () => {
          setBusy(r.id);
          try {
            const res = await fetch(`${API}/api/admin/support/requests/${encodeURIComponent(r.id)}`, { method: 'DELETE' });
            if (res.ok) {
              setRequests((rs) => { const next = rs.filter((x) => x.id !== r.id); report(next); return next; });
            }
          } catch { /* keep prior state */ } finally { setBusy(null); }
        })();
      },
    });

    const nNew = newRequestCount(requests);

    return (
      <>
        {/* ── inbox ── */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 3, mb: 1 }}>
          <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>Visitor requests</Typography>
          <Typography sx={{ fontSize: 11, color: nNew ? '#60a5fa' : 'var(--text-3)' }}>
            {requests.length} · {nNew} new
          </Typography>
          <Tooltip arrow title={PUSH_HELP}>
            <HelpOutlineIcon sx={{ fontSize: 14, color: 'var(--text-4)', cursor: 'help' }} />
          </Tooltip>
          <Box sx={{ flex: 1 }} />
          <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} disabled={loading}
            onClick={() => void load(day)}
            sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>Refresh</Button>
          {loading && <CircularProgress size={14} />}
        </Box>
        {err && <Typography sx={{ fontSize: 12, color: 'var(--text-4)', mb: 1 }}>Couldn't load visitor requests — {err}</Typography>}

        <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
          <Table size="small" sx={TABLE_SX}>
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: 32 }} />
                <TableCell>Contact</TableCell>
                <TableCell>What they want</TableCell>
                <TableCell>When</TableCell>
                <TableCell>Origin</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {requests.map((r) => (
                <RequestRow key={r.id} r={r} busy={busy === r.id}
                  onStatus={(s) => void setStatus(r, s)}
                  onInvite={() => onInvite(r.email)}
                  onDelete={() => remove(r)} />
              ))}
              {requests.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>
                    No visitor requests yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>

        {/* ── daily log ── */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 3, mb: 1 }}>
          <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>Visitor chats</Typography>
          <Tooltip arrow title="Every conversation a signed-out visitor has with the assistant. Signed-in users' chats are not logged.">
            <HelpOutlineIcon sx={{ fontSize: 13, color: 'var(--text-4)', cursor: 'help' }} />
          </Tooltip>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
            {convs.length} {convs.length === 1 ? 'conversation' : 'conversations'}
          </Typography>
          <Box sx={{ flex: 1 }} />
          <Select
            value={days.includes(day) ? day : (days[0] ?? '')} variant="standard" disableUnderline
            displayEmpty disabled={loading || days.length === 0}
            onChange={(e) => { setDay(e.target.value); void loadChats(e.target.value); }}
            sx={{
              fontSize: 12, color: 'var(--text-2)',
              '& .MuiSelect-select': { py: 0.25, pr: '20px !important' }, '& svg': { color: 'var(--text-4)' },
            }}
          >
            {days.length === 0 && <MenuItem value="" sx={{ fontSize: 12 }}>no days</MenuItem>}
            {days.map((d) => <MenuItem key={d} value={d} sx={{ fontSize: 12 }}>{d}</MenuItem>)}
          </Select>
        </Box>

        <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
          {convs.map((c) => <ConversationBlock key={c.conv} c={c} />)}
          {convs.length === 0 && (
            <Typography sx={{ fontSize: 12.5, color: 'var(--text-4)', textAlign: 'center', py: 3 }}>
              No visitor chats logged yet.
            </Typography>
          )}
        </Paper>

        <ConfirmDialog state={confirm} onClose={() => setConfirm(null)} />
      </>
    );
  };

export default VisitorRequests;
