/**
 * Admin · Newsletter — self-contained section (mounts in the Admin sub-
 * navigation; no props). Compose a campaign (subject + Markdown), preview the
 * branded HTML, send a test to yourself, send now or schedule; the audience is
 * CONFIRMED subscribers only (optional tier filter). Sending is a throttled
 * server queue; this view polls the per-campaign stats. Also: subscribers
 * table + CSV export, and in-app notices (product notices, not marketing).
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Paper, Typography, TextField, Button, Tabs, Tab, Table, TableHead, TableRow,
  TableCell, TableBody, Chip, MenuItem, Select, CircularProgress,
} from '@mui/material';
import HelpTip from '../common/HelpTip';
import {
  nlAdmin, downloadSubscribersCsv,
  type AdminStatus, type Campaign, type Subscriber, type AdminNotice, type Draft,
} from '../../lib/newsletterApi';

const TIERS = ['free', 'pro', 'admin'];
const card = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5, mb: 2 };
const head = { fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.04em' };
const tbl = { '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 } };
const btn = { textTransform: 'none' as const, fontSize: 12 };
const ts = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : '');

const TierPick: React.FC<{ value: string[]; onChange: (v: string[]) => void }> = ({ value, onChange }) => (
  <Select multiple size="small" displayEmpty value={value}
    onChange={(e) => onChange(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)}
    renderValue={(v) => ((v as string[]).length ? (v as string[]).join(', ') : 'all tiers')}
    sx={{ fontSize: 12.5, minWidth: 140 }}>
    {TIERS.map((t) => <MenuItem key={t} value={t} sx={{ fontSize: 12.5 }}>{t}</MenuItem>)}
  </Select>
);

const Compose: React.FC<{ status: AdminStatus | null; onSaved: () => void }> = ({ status, onSaved }) => {
  const [d, setD] = useState<Draft>({ subject: '', body_md: '', tiers: [] });
  const [id, setId] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ html: string; audience: number } | null>(null);
  const [when, setWhen] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const invalid = !d.subject.trim() || !d.body_md.trim();

  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setMsg(null);
    try { setMsg({ ok: true, text: await fn() }); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    finally { setBusy(false); }
  };
  const save = async (): Promise<string> => {
    const c = id ? await nlAdmin.update(id, d) : await nlAdmin.create(d);
    setId(c.id); onSaved();
    return c.id;
  };

  return (
    <Box>
      <Box sx={{ display: 'flex', gap: 1, mb: 1, alignItems: 'center' }}>
        <TextField size="small" label="Subject" value={d.subject} fullWidth
          onChange={(e) => setD({ ...d, subject: e.target.value })} inputProps={{ maxLength: 200 }} />
        <TierPick value={d.tiers} onChange={(tiers) => setD({ ...d, tiers })} />
      </Box>
      <TextField size="small" label="Body (Markdown)" value={d.body_md} multiline minRows={8} fullWidth
        onChange={(e) => setD({ ...d, body_md: e.target.value })}
        helperText="# heading, **bold**, *italic*, - list, [text](https://…). Footer + unsubscribe link are added." />
      <Box sx={{ display: 'flex', gap: 1, mt: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <Button size="small" variant="outlined" sx={btn} disabled={busy || invalid}
          onClick={() => void run(async () => { const p = await nlAdmin.preview(d); setPreview(p); return `Audience: ${p.audience} confirmed subscriber(s).`; })}>
          Preview
        </Button>
        <Button size="small" variant="outlined" sx={btn} disabled={busy || invalid || !status?.smtp}
          onClick={() => void run(async () => `Test sent to ${(await nlAdmin.test(d)).to}.`)}>
          Send test to me
        </Button>
        <Button size="small" variant="outlined" sx={btn} disabled={busy || invalid}
          onClick={() => void run(async () => `Draft saved (${await save()}).`)}>
          Save draft
        </Button>
        <TextField size="small" type="datetime-local" label="Schedule (optional)" value={when}
          onChange={(e) => setWhen(e.target.value)} InputLabelProps={{ shrink: true }} sx={{ width: 220 }} />
        <Button size="small" variant="contained" sx={btn} disabled={busy || invalid || !status?.smtp}
          onClick={() => {
            const at = when ? new Date(when).getTime() / 1000 : null;
            if (at !== null && (!Number.isFinite(at) || at < Date.now() / 1000)) { setMsg({ ok: false, text: 'schedule time is in the past' }); return; }
            if (!window.confirm(at ? `Schedule for ${new Date(at * 1000).toLocaleString()}?` : 'Send to all confirmed subscribers now?')) return;
            void run(async () => {
              const cid = await save();
              await nlAdmin.send(cid, at);
              setId(null); setD({ subject: '', body_md: '', tiers: [] }); setPreview(null); setWhen(''); onSaved();
              return at ? 'Scheduled.' : 'Queued — sending at the configured rate.';
            });
          }}>
          {when ? 'Schedule' : 'Send now'}
        </Button>
        {busy && <CircularProgress size={16} />}
      </Box>
      {msg && <Typography sx={{ fontSize: 12, mt: 1, color: msg.ok ? '#34d399' : '#f87171' }}>{msg.text}</Typography>}
      {preview && (
        <Box sx={{ mt: 1.5, border: '1px solid var(--line-soft)', borderRadius: 1, overflow: 'hidden' }}>
          <iframe title="preview" sandbox="" srcDoc={preview.html} style={{ width: '100%', height: 480, border: 0, background: '#fff' }} />
        </Box>
      )}
    </Box>
  );
};

const Campaigns: React.FC<{ rows: Campaign[]; reload: () => void }> = ({ rows, reload }) => (
  <Table size="small" sx={tbl}>
    <TableHead><TableRow>
      <TableCell>Subject</TableCell><TableCell>Status</TableCell><TableCell>When</TableCell>
      <TableCell align="right">Sent</TableCell><TableCell align="right">Failed</TableCell>
      <TableCell align="right">Bounced</TableCell><TableCell align="right">Queued</TableCell>
      <TableCell align="right">Unsubs</TableCell><TableCell />
    </TableRow></TableHead>
    <TableBody>
      {rows.map((c) => (
        <TableRow key={c.id}>
          <TableCell>{c.subject}{c.tiers.length ? <Chip size="small" label={c.tiers.join(',')} sx={{ ml: 1, height: 18, fontSize: 10 }} /> : null}</TableCell>
          <TableCell>{c.status}</TableCell>
          <TableCell>{ts(c.started_at ?? c.scheduled_at ?? c.created)}</TableCell>
          <TableCell align="right">{c.stats.sent}</TableCell>
          <TableCell align="right">{c.stats.failed}</TableCell>
          <TableCell align="right">{c.stats.bounced}</TableCell>
          <TableCell align="right">{c.stats.pending + c.stats.sending}</TableCell>
          <TableCell align="right">{c.stats.unsubscribes}</TableCell>
          <TableCell align="right">
            {(c.status === 'scheduled' || c.status === 'sending') && (
              <Button size="small" color="error" sx={btn}
                onClick={() => { if (window.confirm('Stop this campaign?')) void nlAdmin.cancel(c.id).then(reload); }}>
                Cancel
              </Button>
            )}
          </TableCell>
        </TableRow>
      ))}
      {rows.length === 0 && <TableRow><TableCell colSpan={9} sx={{ color: 'var(--text-4)' }}>No campaigns yet.</TableCell></TableRow>}
    </TableBody>
  </Table>
);

const Subscribers: React.FC = () => {
  const [rows, setRows] = useState<Subscriber[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { void nlAdmin.subscribers().then((j) => setRows(j.subscribers)).catch((e: Error) => setErr(e.message)); }, []);
  return (
    <Box>
      <Button size="small" variant="outlined" sx={{ ...btn, mb: 1 }}
        onClick={() => void downloadSubscribersCsv().catch((e: Error) => setErr(e.message))}>Export CSV</Button>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      <Table size="small" sx={tbl}>
        <TableHead><TableRow>
          <TableCell>E-mail</TableCell><TableCell>Status</TableCell><TableCell>Tier</TableCell>
          <TableCell>Source</TableCell><TableCell>Consent</TableCell><TableCell>Confirmed</TableCell><TableCell>Text ver.</TableCell>
        </TableRow></TableHead>
        <TableBody>
          {rows.map((s) => (
            <TableRow key={s.email}>
              <TableCell>{s.email}</TableCell><TableCell>{s.status}</TableCell><TableCell>{s.tier}</TableCell>
              <TableCell>{s.source}</TableCell><TableCell>{ts(s.consent_at)}</TableCell>
              <TableCell>{ts(s.confirmed_at)}</TableCell><TableCell>{s.text_version}</TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && <TableRow><TableCell colSpan={7} sx={{ color: 'var(--text-4)' }}>Nobody yet.</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Box>
  );
};

const Notices: React.FC = () => {
  const [rows, setRows] = useState<AdminNotice[]>([]);
  const [n, setN] = useState({ title: '', body: '', level: 'info', emails: '', tiers: [] as string[] });
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => { void nlAdmin.notices().then((j) => setRows(j.notices)).catch((e: Error) => setErr(e.message)); }, []);
  useEffect(load, [load]);
  const post = async () => {
    setErr(null);
    try {
      await nlAdmin.postNotice({ ...n, emails: n.emails.split(/[\s,;]+/).filter(Boolean) });
      setN({ title: '', body: '', level: 'info', emails: '', tiers: [] }); load();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Box>
      <Box sx={{ display: 'flex', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <TextField size="small" label="Title" value={n.title} onChange={(e) => setN({ ...n, title: e.target.value })}
          inputProps={{ maxLength: 140 }} sx={{ flex: 2, minWidth: 220 }} />
        <Select size="small" value={n.level} onChange={(e) => setN({ ...n, level: e.target.value })} sx={{ fontSize: 12.5 }}>
          {['info', 'warning', 'important'].map((l) => <MenuItem key={l} value={l} sx={{ fontSize: 12.5 }}>{l}</MenuItem>)}
        </Select>
        <TierPick value={n.tiers} onChange={(tiers) => setN({ ...n, tiers })} />
      </Box>
      <TextField size="small" label="Text (optional)" value={n.body} multiline minRows={2} fullWidth
        onChange={(e) => setN({ ...n, body: e.target.value })} sx={{ mb: 1 }} />
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
        <TextField size="small" label="Only these e-mails (optional)" value={n.emails} fullWidth
          onChange={(e) => setN({ ...n, emails: e.target.value })} />
        <Button size="small" variant="contained" sx={btn} disabled={!n.title.trim()} onClick={() => void post()}>Post</Button>
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171', mt: 0.5 }}>{err}</Typography>}
      <Table size="small" sx={{ ...tbl, mt: 1.5 }}>
        <TableHead><TableRow>
          <TableCell>Title</TableCell><TableCell>Level</TableCell><TableCell>Audience</TableCell>
          <TableCell>Posted</TableCell><TableCell align="right">Read</TableCell><TableCell />
        </TableRow></TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id} sx={{ opacity: r.active ? 1 : 0.5 }}>
              <TableCell>{r.title}</TableCell><TableCell>{r.level}</TableCell>
              <TableCell>{r.emails.length ? `${r.emails.length} user(s)` : r.tiers.length ? r.tiers.join(',') : 'all'}</TableCell>
              <TableCell>{ts(r.created)}</TableCell><TableCell align="right">{r.read_count}</TableCell>
              <TableCell align="right">
                {r.active && <Button size="small" color="error" sx={btn}
                  onClick={() => void nlAdmin.withdrawNotice(r.id).then(load)}>Withdraw</Button>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
};

const NewsletterSection: React.FC = () => {
  const [tab, setTab] = useState<'compose' | 'campaigns' | 'subscribers' | 'notices'>('compose');
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(() => {
    void Promise.all([nlAdmin.status(), nlAdmin.campaigns()])
      .then(([s, c]) => { setStatus(s); setCampaigns(c.campaigns); setErr(null); })
      .catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(() => {
    reload();
    const t = window.setInterval(reload, 10000);
    return () => window.clearInterval(t);
  }, [reload]);

  return (
    <Paper sx={card}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.5 }}>
        <Typography sx={head}>Newsletter</Typography>
        <HelpTip title="Goes only to confirmed (double opt-in) subscribers. Every mail carries a one-click unsubscribe link and header. Rate and daily cap: NEWSLETTER_RATE_PER_MIN / NEWSLETTER_DAILY_CAP in api.env." />
        <Box sx={{ flex: 1 }} />
        {status && (
          <Typography sx={{ fontSize: 11.5, color: status.smtp ? 'var(--text-3)' : '#f87171' }}>
            {status.smtp ? '' : 'SMTP not configured · '}
            {status.confirmed} confirmed · {status.pending} pending · {status.sent_24h}/{status.daily_cap} sent 24 h · {status.rate_per_min}/min
          </Typography>
        )}
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      <Tabs value={tab} onChange={(_, v) => setTab(v)}
        sx={{ minHeight: 32, mb: 1.5, '& .MuiTab-root': { minHeight: 32, textTransform: 'none', fontSize: 12.5 } }}>
        <Tab value="compose" label="Compose" />
        <Tab value="campaigns" label={`Campaigns (${campaigns.length})`} />
        <Tab value="subscribers" label="Subscribers" />
        <Tab value="notices" label="In-app notices" />
      </Tabs>
      {tab === 'compose' && <Compose status={status} onSaved={reload} />}
      {tab === 'campaigns' && <Campaigns rows={campaigns} reload={reload} />}
      {tab === 'subscribers' && <Subscribers />}
      {tab === 'notices' && <Notices />}
    </Paper>
  );
};

export default NewsletterSection;
