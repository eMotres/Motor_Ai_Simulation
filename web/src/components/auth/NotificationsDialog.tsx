// Account menu → Notifications: the list of in-app product notices with read state.
// (The old newsletter toggle is retired: e-mail is chosen on the Communication preferences page, account menu → E-mail preferences.)
import React, { useCallback, useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, Box, Typography, Button,
} from '@mui/material';
import { getNotices, markNoticeRead, type Notice } from '../../lib/newsletterApi';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';

// UI strings: locales/<lng>/common.json (docs/I18N.md).
const tx = nsT('common');

interface Props { open: boolean; onClose: () => void; onRead?: () => void }

const NotificationsDialog: React.FC<Props> = ({ open, onClose, onRead }) => {
  useTranslation('common'); // re-render on language change; lazy-loads the namespace
  const [notices, setNotices] = useState<Notice[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setNotices((await getNotices()).notices); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { if (open) void load(); }, [open, load]);

  const read = async (id: string) => {
    try {
      await markNoticeRead(id);
      setNotices((ns) => ns.map((n) => (n.id === id ? { ...n, read: true } : n)));
      onRead?.();
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem', pb: 0.5 }}>{tx('notifications')}</DialogTitle>
      <DialogContent>
        {err && <Typography sx={{ fontSize: 12, color: '#f87171', mb: 0.5 }}>{err}</Typography>}
        <Typography sx={{ fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', mb: 0.5 }}>
          {tx('notices')}
        </Typography>
        {notices.length === 0 ? (
          <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>{tx('none')}</Typography>
        ) : notices.map((n) => (
          <Box key={n.id} sx={{ py: 0.75, opacity: n.read ? 0.6 : 1, borderBottom: '1px solid var(--line-soft)' }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography sx={{ fontSize: 12.5, fontWeight: n.read ? 400 : 700, flex: 1 }}>{n.title}</Typography>
              {!n.read && (
                <Button size="small" onClick={() => void read(n.id)} sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>
                  {tx('markRead')}
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
