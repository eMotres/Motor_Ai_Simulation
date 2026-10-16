// Header bell for in-app product notices (independent of e-mail consent):
// a badge with the unread count, and the newest unread notice as a
// dismissible banner (dismiss = mark read, stored per user on the server).
// Also consumes the links of mails sent by the RETIRED newsletter: ?unsubscribe= still unsubscribes (and switches every new topic off),
// ?nl_confirm= only says the newsletter is gone; both work signed in or not — see NewsletterLinkHandler below.
import React, { useCallback, useEffect, useState } from 'react';
import { Badge, IconButton, Tooltip, Snackbar, Alert } from '@mui/material';
import NotificationsIcon from '@mui/icons-material/NotificationsNone';
import {
  getNotices, markNoticeRead, pendingNewsletterLink, clearNewsletterLink,
  unsubscribeNewsletter, type Notice,
} from '../../lib/newsletterApi';
import { useTranslation } from 'react-i18next';

const POLL_MS = 5 * 60 * 1000;

export const NoticeBell: React.FC<{ onOpen: () => void; refreshKey?: number }> = ({ onOpen, refreshKey }) => {
  const [notices, setNotices] = useState<Notice[]>([]);
  const load = useCallback(async () => {
    try { setNotices((await getNotices()).notices); } catch { /* signed out / offline */ }
  }, []);
  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [load, refreshKey]);

  const unread = notices.filter((n) => !n.read);
  const banner = unread[0];
  const dismiss = async (id: string) => {
    setNotices((ns) => ns.map((n) => (n.id === id ? { ...n, read: true } : n)));
    try { await markNoticeRead(id); } catch { /* retried on next load */ }
  };

  return (
    <>
      <Tooltip title={unread.length ? `${unread.length} unread notice(s)` : 'Notifications'} arrow>
        <IconButton size="small" onClick={onOpen} aria-label="notifications">
          <Badge color="error" badgeContent={unread.length} max={9}>
            <NotificationsIcon sx={{ fontSize: 19 }} />
          </Badge>
        </IconButton>
      </Tooltip>
      <Snackbar open={!!banner} anchorOrigin={{ vertical: 'top', horizontal: 'center' }}>
        {banner ? (
          <Alert severity={banner.level === 'info' ? 'info' : 'warning'} variant="filled"
            onClose={() => void dismiss(banner.id)} sx={{ maxWidth: 560 }}>
            <strong>{banner.title}</strong>{banner.body ? ` — ${banner.body}` : ''}
          </Alert>
        ) : <span />}
      </Snackbar>
    </>
  );
};

/** One-shot handler for the old newsletter links in the address bar. */
export const NewsletterLinkHandler: React.FC = () => {
  const { t } = useTranslation('common');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    const link = pendingNewsletterLink();
    if (!link) return;
    clearNewsletterLink();
    if (link.kind === 'confirm') { setMsg({ ok: true, text: t('commPrefs.newsletterRetired') }); return; }
    void unsubscribeNewsletter(link.token)
      .then(() => setMsg({ ok: true, text: t('commPrefs.unsubscribed') }))
      .catch((e: Error) => setMsg({ ok: false, text: e.message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <Snackbar open={!!msg} autoHideDuration={8000} onClose={() => setMsg(null)}
      anchorOrigin={{ vertical: 'top', horizontal: 'center' }}>
      {msg ? <Alert severity={msg.ok ? 'success' : 'error'} onClose={() => setMsg(null)}>{msg.text}</Alert> : <span />}
    </Snackbar>
  );
};
