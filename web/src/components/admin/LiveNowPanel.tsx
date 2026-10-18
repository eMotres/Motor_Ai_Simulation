// Admin · "Now on the site" (2026-10-18): who is in which module right now, the requests being served and the jobs,
// from GET /api/admin/live (live_status.py).  The deploy script reads the same answer to restart only idle modules.
import React, { useEffect, useState } from 'react';
import { Box, Chip, Paper, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface Mod { users: number; requests: number; oldest_request_s: number; accounts?: string[] }
interface Live {
  users_total: number; requests_total: number;
  jobs: { running: number; queued: number; kinds: Record<string, number> };
  modules: Record<string, Mod>;
  notice: { message: { en?: string; zh?: string }; until: number } | null;
}

const LiveNowPanel: React.FC = () => {
  const { t } = useTranslation('admin');
  const [d, setD] = useState<Live | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    const read = () => fetch(`${API}/api/admin/live`, { cache: 'no-store' })
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then((j) => { if (alive) { setD(j); setFailed(false); } })
      .catch(() => { if (alive) setFailed(true); });
    read();
    const id = setInterval(read, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  const rows = d ? Object.entries(d.modules).sort((a, b) => (b[1].users - a[1].users) || (b[1].requests - a[1].requests)) : [];
  return (
    <Paper variant="outlined" sx={{ p: 1.5, mb: 2, bgcolor: 'var(--panel-2)' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 800, color: 'var(--text-0)' }}>{t('live.title')}</Typography>
        {d && <>
          <Chip size="small" label={t('live.users', { n: d.users_total })} />
          <Chip size="small" label={t('live.requests', { n: d.requests_total })} />
          <Chip size="small" color={d.jobs.running ? 'warning' : 'default'}
            label={t('live.jobs', { running: d.jobs.running, queued: d.jobs.queued })} />
          {d.notice && <Chip size="small" color="info" label={d.notice.message.en ?? ''} />}
        </>}
        {failed && <Typography sx={{ fontSize: 12, color: '#fca5a5' }}>{t('live.error')}</Typography>}
      </Box>
      {rows.length > 0 ? (
        <Table size="small">
          <TableHead><TableRow>
            <TableCell>{t('live.module')}</TableCell><TableCell align="right">{t('live.usersCol')}</TableCell>
            <TableCell align="right">{t('live.requestsCol')}</TableCell><TableCell>{t('live.accounts')}</TableCell>
          </TableRow></TableHead>
          <TableBody>
            {rows.map(([name, m]) => (
              <TableRow key={name}>
                <TableCell>{name}</TableCell>
                <TableCell align="right">{m.users}</TableCell>
                <TableCell align="right">{m.requests}{m.requests ? ` (${m.oldest_request_s.toFixed(0)} s)` : ''}</TableCell>
                <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }}>{(m.accounts ?? []).join(', ')}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : d && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('live.empty')}</Typography>}
    </Paper>
  );
};

export default LiveNowPanel;
