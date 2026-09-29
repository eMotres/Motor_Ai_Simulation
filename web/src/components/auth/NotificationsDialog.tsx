// Account menu → Notifications: the newsletter opt-in/out toggle (e-mail,
// double opt-in) and the list of in-app product notices with read state.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, Box, Typography, Switch, Button, Divider,
} from '@mui/material';
import HelpTip from '../common/HelpTip';
import {
  getMyNewsletter, setMyNewsletter, getNotices, markNoticeRead,
  CONSENT_LINE, CONSENT_HELP, type NewsletterStatus, type Notice,
} from '../../lib/newsletterApi';

interface Props { open: boolean; onClose: () => void; onRead?: () => void }

const STATE: Record<string, string> = {
  none: 'Not subscribed.',
  pending: 'Check your inbox — confirm the link we sent.',
  confirmed: 'Subscribed.',
  unsubscribed: 'Unsubscribed.',
};

const NotificationsDialog: React.FC<Props> = ({ open, onClose, onRead }) => {
  const [st, setSt] = useState<NewsletterStatus | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, n] = await Promise.all([getMyNewsletter(), getNotices()]);
      setSt(s); setNotices(n.notices); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { if (open) void load(); }, [open, load]);

  const toggle = async (on: boolean) => {
    setBusy(true); setErr(null);
    try { setSt(await setMyNewsletter(on)); } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  const read = async (id: string) => {
    try {
      await markNoticeRead(id);
      setNotices((ns) => ns.map((n) => (n.id === id ? { ...n, read: true } : n)));
      onRead?.();
    } catch (e) { setErr((e as Error).message); }
  };

  const on = st?.status === 'confirmed' || st?.status === 'pending';

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem', pb: 0.5 }}>Notifications</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Typography sx={{ fontSize: 13, flex: 1 }}>{CONSENT_LINE}</Typography>
          <HelpTip title={CONSENT_HELP} />
          <Switch size="small" checked={on} disabled={busy || !st}
            onChange={(e) => void toggle(e.target.checked)} />
        </Box>
        <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{st ? STATE[st.status] ?? st.status : '…'}</Typography>
        {err && <Typography sx={{ fontSize: 12, color: '#f87171', mt: 0.5 }}>{err}</Typography>}
        <Divider sx={{ my: 1.5 }} />
        <Typography sx={{ fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', mb: 0.5 }}>
          Notices
        </Typography>
        {notices.length === 0 ? (
          <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>None.</Typography>
        ) : notices.map((n) => (
          <Box key={n.id} sx={{ py: 0.75, opacity: n.read ? 0.6 : 1, borderBottom: '1px solid var(--line-soft)' }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography sx={{ fontSize: 12.5, fontWeight: n.read ? 400 : 700, flex: 1 }}>{n.title}</Typography>
              {!n.read && (
                <Button size="small" onClick={() => void read(n.id)} sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>
                  Mark read
                </Button>
              )}
            </Box>
            {n.body && <Typography sx={{ fontSize: 12, color: 'var(--text-2)', whiteSpace: 'pre-wrap' }}>{n.body}</Typography>}
            <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{new Date(n.created * 1000).toLocaleString()}</Typography>
          </Box>
        ))}
      </DialogContent>
    </Dialog>
  );
};

export default NotificationsDialog;
