/**
 * Admin · Mailing — the only mailing card (the old Newsletter section is retired): how many accounts opted in to each e-mail topic (communication
 * preferences), how many unsubscribed from everything, and the status of the last announcement campaign (from its send log).
 * Below: the old newsletter as READ-ONLY history (counts, past campaigns, CSV of the old consent records); nobody was moved into the
 * new topics. Backend: GET /api/admin/comm-prefs/summary (routes/comm_prefs.py). Sending is done by scripts/send_announcement.py.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Button, Paper, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import HelpTip from '../common/HelpTip';
import { COMM_TOPICS } from '../../lib/commPrefsLogic';
import { downloadSubscribersCsv, nlAdmin, type Campaign } from '../../lib/newsletterApi';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

interface Summary {
  topics: Record<string, number>; any_topic: number; unsubscribed_all: number; records: number;
  users_total: number; verified_users: number; smtp: boolean; text_version: string;
  legacy_newsletter?: { confirmed: number; pending: number; unsubscribed: number; campaigns: number };
  campaign: { campaign: string; sent: number; failed: number; unresolved: number; first: number | null; last: number | null } | null;
}

const LABEL: Record<string, string> = {
  product_updates: 'Product updates', new_motors: 'New motors', engineering_content: 'Engineering content', co_development: 'Co-development',
};
const card = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5, mb: 2 };
const head = { fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.04em' };
const ts = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : '');

const MailingCard: React.FC = () => {
  const [s, setS] = useState<Summary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [past, setPast] = useState<Campaign[]>([]);
  const load = useCallback(() => {
    fetch(`${API}/api/admin/comm-prefs/summary`)
      .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error((j as { detail?: string }).detail ?? String(r.status)); return j as Summary; })
      .then((j) => { setS(j); setErr(null); }).catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(() => { load(); void nlAdmin.campaigns().then((j) => setPast(j.campaigns)).catch(() => { /* history is optional */ }); }, [load]);

  return (
    <Paper sx={card} data-testid="mailing-card">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.5 }}>
        <Typography sx={head}>Mailing</Typography>
        <HelpTip title="Opt-in counts per e-mail topic (communication preferences; every box starts unticked). The launch announcement is sent with scripts/send_announcement.py (dry-run by default); its send log feeds the last-campaign line." />
        <Box sx={{ flex: 1 }} />
        {s && <Typography sx={{ fontSize: 11.5, color: s.smtp ? 'var(--text-3)' : '#f87171' }}>{s.smtp ? '' : 'SMTP not configured · '}{s.verified_users} of {s.users_total} accounts verified</Typography>}
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      {s && (
        <>
          <Box sx={{ display: 'flex', gap: 2.5, flexWrap: 'wrap', fontSize: 12.5 }}>
            {COMM_TOPICS.map((t) => (
              <Box key={t}><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{LABEL[t]}</Typography><Typography sx={{ fontSize: 18, fontWeight: 800 }}>{s.topics[t] ?? 0}</Typography></Box>
            ))}
            <Box><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Any topic</Typography><Typography sx={{ fontSize: 18, fontWeight: 800 }}>{s.any_topic}</Typography></Box>
            <Box><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Unsubscribed from all</Typography><Typography sx={{ fontSize: 18, fontWeight: 800 }}>{s.unsubscribed_all}</Typography></Box>
          </Box>
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 1 }}>
            {s.campaign
              ? `Last campaign ${s.campaign.campaign}: ${s.campaign.sent} sent, ${s.campaign.failed} failed${s.campaign.unresolved ? `, ${s.campaign.unresolved} to check by hand` : ''} · ${ts(s.campaign.first)} to ${ts(s.campaign.last)}`
              : 'No campaign has been sent yet.'}
          </Typography>
          {s.legacy_newsletter && (
            <Box sx={{ mt: 1.5, pt: 1, borderTop: '1px solid var(--line-soft)' }} data-testid="legacy-newsletter">
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Typography sx={head}>Old newsletter (retired, read-only)</Typography>
                <HelpTip title="The single Newsletter opt-in was removed. Its records stay here as history. Nobody was moved into the new topics (the old wording differs); they choose on the preferences page, and an unsubscribe in either place ends both." />
                <Box sx={{ flex: 1 }} />
                <Button size="small" sx={{ textTransform: 'none', fontSize: 12 }} onClick={() => void downloadSubscribersCsv().catch((e: Error) => setErr(e.message))}>Export CSV</Button>
              </Box>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 0.5 }}>
                {s.legacy_newsletter.confirmed} confirmed · {s.legacy_newsletter.pending} pending · {s.legacy_newsletter.unsubscribed} unsubscribed · {s.legacy_newsletter.campaigns} past campaign(s)
              </Typography>
              {past.length > 0 && (
                <Table size="small" sx={{ mt: 0.5, '& td, & th': { borderColor: 'var(--panel)', fontSize: 12 } }}>
                  <TableHead><TableRow><TableCell>Subject</TableCell><TableCell>When</TableCell><TableCell align="right">Sent</TableCell><TableCell align="right">Failed</TableCell><TableCell align="right">Unsubs</TableCell></TableRow></TableHead>
                  <TableBody>
                    {past.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>{c.subject}</TableCell><TableCell>{ts(c.started_at ?? c.scheduled_at ?? c.created)}</TableCell>
                        <TableCell align="right">{c.stats.sent}</TableCell><TableCell align="right">{c.stats.failed + c.stats.bounced}</TableCell>
                        <TableCell align="right">{c.stats.unsubscribes}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Box>
          )}
        </>
      )}
    </Paper>
  );
};

export default MailingCard;
