/**
 * Admin · Digests - the ONLY place where an activity digest is sent (owner 2026-10-26: no outside message without the owner's OK
 * on the final text).  Pending drafts (one per project / production and period) with the single text, the recipient list and a
 * preview of the e-mail as one recipient gets it; Send (after a confirmation) or Discard; below, the history of what was sent.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Paper, Select, Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../common/HelpTip';
import { parseAdminLink } from '../../../lib/adminLink';
import { digestApi, type DigestDraftDetail, type DigestPreview } from '../../digest/digestApi';
import { splitDrafts, type DigestSummary } from '../../digest/digestModel';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5 } as const;
const when = (iso: string) => iso.slice(0, 16).replace('T', ' ');

const DraftCard: React.FC<{ row: DigestSummary; smtp: boolean; onDone: (msg: string) => void; onChanged: () => void }> = ({ row, smtp, onDone, onChanged }) => {
  const { t } = useTranslation('common');
  // the owner's daily report links to one draft: /?tab=admin&section=digests&draft=<id> opens it expanded
  const [open, setOpen] = useState(() => parseAdminLink(window.location.search)?.draft === row.id);
  const [detail, setDetail] = useState<DigestDraftDetail | null>(null);
  const [who, setWho] = useState('');
  const [preview, setPreview] = useState<DigestPreview | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try {
      const d = await digestApi.detail(row.id);
      setDetail(d);
      const first = d.recipients[0]?.email ?? '';
      setWho(first);
      if (first) setPreview(await digestApi.preview(row.id, first));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, [row.id]);
  useEffect(() => { if (open && !detail) void load(); }, [open, detail, load]);

  const pick = async (email: string) => {
    setWho(email);
    try { setPreview(await digestApi.preview(row.id, email)); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const send = async () => {
    const hash = preview?.content_hash ?? detail?.content_hash;
    if (!hash) { setErr(t('digest.admin.previewFirst')); setConfirm(false); return; }
    setBusy(true); setErr('');
    try {
      // the approval carries the hash of the text that was previewed: if the draft changed meanwhile the server refuses
      const r = await digestApi.send(row.id, hash);
      setConfirm(false);
      onDone(t('digest.admin.sentDone', { sent: r.sent, failed: r.failed, skipped: r.skipped.length }));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); setConfirm(false); onChanged(); } finally { setBusy(false); }
  };
  const discard = async () => {
    setBusy(true); setErr('');
    try { await digestApi.discard(row.id); onDone(t('digest.admin.discardedDone')); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  return (
    <Paper variant="outlined" sx={PANEL} data-testid="digest-draft">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', cursor: 'pointer' }} onClick={() => setOpen((o) => !o)}>
        <Chip size="small" label={t(`digest.kind.${row.kind}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700 }} />
        <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>{row.title}</Typography>
        <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>
          {t('digest.admin.period', { from: when(row.period.start), to: when(row.period.end) })} · {t('digest.admin.itemsCount', { count: row.items })} · {t('digest.recipientsCount', { count: row.recipients })}
        </Typography>
        {row.bilingual && <Chip size="small" label="EN + 中文" sx={{ height: 18, fontSize: 10.5 }} />}
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{when(row.updated_at)}</Typography>
      </Box>
      {open && (
        <Box sx={{ mt: 1.25, display: 'grid', gap: 1 }}>
          {err && <Alert severity="error" sx={{ py: 0 }}>{err}</Alert>}
          {row.last_error && <Alert severity="warning" sx={{ py: 0 }}>{t('digest.admin.lastError', { failed: row.last_error.failed })}</Alert>}
          {row.partial && <Alert severity="warning" sx={{ py: 0 }}>{t('digest.admin.partialNote', { sent: row.partial.sent, failed: row.partial.failed })}</Alert>}
          {!detail && !err && <CircularProgress size={16} />}
          {detail && (
            <>
              <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('digest.admin.subject')}: <b>{detail.subject}</b></Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('digest.admin.previewAs')}</Typography>
                <Select size="small" value={who} onChange={(e) => void pick(String(e.target.value))} inputProps={{ 'aria-label': t('digest.admin.previewAs') }}>
                  {detail.recipients.map((r) => <MenuItem key={r.email} value={r.email}>{r.email} · {r.role}</MenuItem>)}
                </Select>
                <HelpTip title={t('digest.admin.oneTextTip')} />
              </Box>
              {preview && (
                // the e-mail exactly as it would be received; sandboxed (no script, no navigation) and text/HTML only
                <iframe title={t('digest.admin.previewAs')} sandbox="" srcDoc={preview.html}
                  style={{ width: '100%', height: 420, border: '1px solid var(--line-soft)', borderRadius: 6, background: '#fff' }} />
              )}
              <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                {t('digest.admin.recipients')}: {detail.recipients.map((r) => r.email).join(', ')}
              </Typography>
              {detail.skipped.length > 0 && (
                <Typography sx={{ fontSize: 11.5, color: 'var(--text-4)' }}>
                  {t('digest.admin.notReceiving')}: {detail.skipped.map((s) => `${s.email} (${t(`digest.skip.${s.reason}`, { defaultValue: s.reason })})`).join(', ')}
                </Typography>
              )}
              {!smtp && <Alert severity="warning" sx={{ py: 0 }}>{t('digest.admin.noSmtp')}</Alert>}
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Button size="small" variant="contained" disabled={busy || !smtp || detail.recipients.length === 0 || !(preview ?? detail).content_hash} onClick={() => setConfirm(true)} sx={{ textTransform: 'none' }}>
                  {t('digest.admin.send')}
                </Button>
                <Button size="small" disabled={busy} onClick={() => void discard()} sx={{ textTransform: 'none' }}>{t('digest.admin.discard')}</Button>
              </Box>
            </>
          )}
        </Box>
      )}
      <Dialog open={confirm} onClose={() => !busy && setConfirm(false)}>
        <DialogTitle>{t('digest.admin.confirmTitle')}</DialogTitle>
        <DialogContent>
          <Typography sx={{ fontSize: 13 }}>{t('digest.admin.confirmBody', { count: row.recipients, title: row.title })}</Typography>
        </DialogContent>
        <DialogActions>
          <Button disabled={busy} onClick={() => setConfirm(false)} sx={{ textTransform: 'none' }}>{t('digest.admin.cancel')}</Button>
          <Button variant="contained" disabled={busy} onClick={() => void send()} sx={{ textTransform: 'none' }}>{t('digest.admin.confirmSend')}</Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
};

const DigestsSection: React.FC = () => {
  const { t } = useTranslation('common');
  const [rows, setRows] = useState<{ pending: DigestSummary[]; history: DigestSummary[] }>({ pending: [], history: [] });
  const [smtp, setSmtp] = useState(true);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const r = await digestApi.list();
      setRows(splitDrafts([...r.pending, ...r.history]));
      setSmtp(r.smtp_configured);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const refresh = async () => {
    setLoading(true); setErr(''); setNote('');
    try {
      const r = await digestApi.refresh(false);
      setNote(t('digest.admin.refreshed', { count: r.rows.filter((x) => x.action !== 'skipped').length }));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    await load();
  };
  const done = useMemo(() => (msg: string) => { setNote(msg); void load(); }, [load]);

  return (
    <Box sx={{ display: 'grid', gap: 1.25 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>{t('digest.admin.title')}</Typography>
        <HelpTip title={t('digest.admin.tip')} />
        <Box sx={{ flex: 1 }} />
        <Button size="small" startIcon={<RefreshIcon />} onClick={() => void refresh()} disabled={loading} sx={{ textTransform: 'none' }}>{t('digest.admin.refresh')}</Button>
      </Box>
      {err && <Alert severity="error" sx={{ py: 0 }}>{err}</Alert>}
      {note && <Alert severity="success" sx={{ py: 0 }} onClose={() => setNote('')}>{note}</Alert>}
      {loading && <CircularProgress size={18} />}
      {!loading && rows.pending.length === 0 && <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('digest.admin.nonePending')}</Typography>}
      {rows.pending.map((r) => <DraftCard key={`${r.id}:${r.updated_at}`} row={r} smtp={smtp} onDone={done} onChanged={() => void load()} />)}
      {rows.history.length > 0 && (
        <>
          <Typography sx={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-2)', mt: 1 }}>{t('digest.admin.history')}</Typography>
          {rows.history.map((r) => (
            <Box key={r.id} sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, color: 'var(--text-2)' }}>
              <Chip size="small" label={t(`digest.status.${r.status}`)} sx={{ height: 18, fontSize: 10.5 }} />
              <b>{r.title}</b>
              <span>{t('digest.admin.period', { from: when(r.period.start), to: when(r.period.end) })}</span>
              {r.sent && <span>{t('digest.admin.sentLine', { sent: r.sent.sent, failed: r.sent.failed, by: r.sent.by, at: when(r.sent.at) })}</span>}
              {r.discarded && <span>{t('digest.admin.discardedLine', { by: r.discarded.by, at: when(r.discarded.at) })}</span>}
            </Box>
          ))}
        </>
      )}
    </Box>
  );
};

export default DigestsSection;
