/** Revisions (newest first). The acknowledge banner lists revisions this account has not acknowledged yet; owner/engineer/admin publish. */
import React, { useEffect, useState } from 'react';
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Paper, Select, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import HelpTip from '../../../components/common/HelpTip';
import { productionApi } from '../api';
import { ackStatus, alertSummary, canAckRole, hasAcked, makeBilingual, nextRevision, replaceableDocs, sortRevisions, supersedePairs, unackedRevisions } from '../helpers';
import type { DocumentRec, Lang, Member, ProdCtx, Revision, RevisionPreview } from '../types';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

const ChangesPanel: React.FC<{ revisions: Revision[]; currentRevision: string; ctx: ProdCtx; members?: Member[] }> = ({ revisions, currentRevision, ctx, members = [] }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [summary, setSummary] = useState('');
  const [lang, setLang] = useState<Lang>(ui);
  const [lines, setLines] = useState<string[]>(['']);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<RevisionPreview | null>(null);
  const [replaces, setReplaces] = useState<Array<{ old: string; new: string }>>([]);
  const [docs, setDocs] = useState<DocumentRec[]>([]);
  useEffect(() => { if (ctx.canEdit) productionApi.documents(ctx.die, ctx.config).then((d) => setDocs(replaceableDocs(d))).catch(() => setDocs([])); }, [ctx.canEdit, ctx.die, ctx.config, revisions.length]);
  const sorted = sortRevisions(revisions);
  const pending = canAckRole(ctx.role) ? unackedRevisions(revisions, ctx.email) : [];
  const newest = sorted[0]?.number ?? currentRevision;

  const guard = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); ctx.reload(); } catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
      {pending.length > 0 && (
        <Alert severity="warning" sx={{ fontSize: 12.5, alignItems: 'center' }}
          action={<Button size="small" color="inherit" sx={{ textTransform: 'none' }}
            onClick={() => void guard(async () => { for (const r of pending) await productionApi.ackRevision(ctx.die, ctx.config, r.number); })}>{t('changes.ack')}</Button>}>
          {t('changes.banner', { count: pending.length })}
        </Alert>
      )}
      {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
      {ctx.canEdit && (
        <Paper sx={{ ...PANEL, p: 1.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>{t('changes.publish', { rev: nextRevision(newest) })}</Typography>
            <HelpTip title={t('changes.tip')} />
          </Box>
          <BilingualInput label={t('changes.summary')} value={summary} lang={lang} onChange={(v, l) => { setSummary(v); setLang(l); }} />
          {lines.map((ln, i) => (
            <BilingualInput key={i} label={t('changes.changeLine')} value={ln} lang={lang}
              onChange={(v, l) => { setLang(l); setLines((a) => a.map((x, k) => (k === i ? v : x))); }} />
          ))}
          {replaces.map((r, i) => (
            <Box key={i} sx={{ display: 'flex', gap: 0.75, alignItems: 'center', flexWrap: 'wrap' }} data-testid="changes-replace-row">
              <Select size="small" displayEmpty value={r.old} sx={{ fontSize: 12, minWidth: 190 }} inputProps={{ 'aria-label': t('changes.replaceOld') }}
                onChange={(e) => setReplaces((a) => a.map((x, k) => (k === i ? { ...x, old: String(e.target.value) } : x)))}
                renderValue={(v) => (v ? docs.find((d) => d.id === v)?.name ?? v : t('changes.replaceOld'))}>
                {docs.map((d) => <MenuItem key={d.id} value={d.id} sx={{ fontSize: 12 }}>{d.name}</MenuItem>)}
              </Select>
              <span aria-hidden>→</span>
              <Select size="small" displayEmpty value={r.new} sx={{ fontSize: 12, minWidth: 190 }} inputProps={{ 'aria-label': t('changes.replaceNew') }}
                onChange={(e) => setReplaces((a) => a.map((x, k) => (k === i ? { ...x, new: String(e.target.value) } : x)))}
                renderValue={(v) => (v ? docs.find((d) => d.id === v)?.name ?? v : t('changes.replaceNew'))}>
                {docs.map((d) => <MenuItem key={d.id} value={d.id} sx={{ fontSize: 12 }}>{d.name}</MenuItem>)}
              </Select>
              <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setReplaces((a) => a.filter((_, k) => k !== i))}>{t('changes.removeReplace')}</Button>
            </Box>
          ))}
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setLines((a) => [...a, ''])}>{t('changes.addChange')}</Button>
            <Button size="small" sx={{ textTransform: 'none' }} disabled={docs.length < 2} onClick={() => setReplaces((a) => [...a, { old: '', new: '' }])}>{t('changes.addReplace')}</Button>
            <Button size="small" variant="contained" disabled={!summary.trim()} sx={{ textTransform: 'none' }}
              onClick={() => void guard(async () => {
                setPreview(await productionApi.previewRevision(ctx.die, ctx.config, makeBilingual(summary, lang),
                  lines.filter((l) => l.trim()).map((l) => makeBilingual(l, lang)), supersedePairs(replaces)));
              })}>{t('changes.review', { rev: nextRevision(newest) })}</Button>
          </Box>
        </Paper>
      )}
      <Dialog open={preview !== null} onClose={() => setPreview(null)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ fontSize: 15 }}>{t('changes.reviewTitle', { rev: preview?.number ?? '' })}</DialogTitle>
        <DialogContent data-testid="changes-preview">
          {preview && !preview.will_send && <Alert severity="info" sx={{ mb: 1, fontSize: 12.5 }}>{t('changes.cannotMail')}</Alert>}
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('changes.recipients', { count: preview?.recipients.length ?? 0 })}</Typography>
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', my: 0.5 }}>
            {(preview?.recipients ?? []).map((r) => <Chip key={r.email} size="small" label={`${r.email} · ${t(`role.${r.role}`, { defaultValue: r.role })}`} sx={{ fontSize: 11 }} />)}
          </Box>
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 1 }}>{t('changes.subject')}</Typography>
          <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{preview?.subject}</Typography>
          <Box component="pre" sx={{ mt: 1, p: 1, bgcolor: 'var(--panel)', border: '1px solid var(--line-soft)', borderRadius: 1, fontSize: 12, whiteSpace: 'pre-wrap',
            wordBreak: 'break-word', maxHeight: 320, overflow: 'auto', fontFamily: 'inherit' }}>{preview?.body}</Box>
        </DialogContent>
        <DialogActions>
          <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setPreview(null)}>{t('changes.cancel')}</Button>
          <Button size="small" variant="contained" sx={{ textTransform: 'none' }}
            onClick={() => void guard(async () => {
              await productionApi.publishRevision(ctx.die, ctx.config, makeBilingual(summary, lang),
                lines.filter((l) => l.trim()).map((l) => makeBilingual(l, lang)), supersedePairs(replaces));
              setSummary(''); setLines(['']); setReplaces([]); setPreview(null);
            })}>{t('changes.sendPublish', { rev: preview?.number ?? '' })}</Button>
        </DialogActions>
      </Dialog>
      <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('changes.current', { rev: currentRevision })}</Typography>
      {sorted.length === 0 && <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('changes.none')}</Typography>}
      {sorted.map((r) => (
        <Paper key={r.number} data-item-id={r.number} sx={{ ...PANEL, p: 1.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
            <Chip size="small" label={r.number} sx={{ height: 18, fontSize: 11, fontWeight: 800, bgcolor: 'var(--panel)', color: 'var(--text-0)' }} />
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{r.date.slice(0, 10)} · {t('changes.by', { email: r.author_email })}</Typography>
            <Box sx={{ flex: 1 }} />
            {hasAcked(r, ctx.email)
              ? <Chip size="small" label={t('changes.acked')} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, color: '#16a34a', bgcolor: '#16a34a1f' }} />
              : canAckRole(ctx.role) && (
                <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }} onClick={() => void guard(() => productionApi.ackRevision(ctx.die, ctx.config, r.number))}>{t('changes.ack')}</Button>
              )}
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{t('changes.ackedBy', { count: r.acknowledgements.length })}</Typography>
          </Box>
          <BilingualText value={r.summary} bold canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
            tref={{ entity: 'revision', entity_id: r.number, field: 'summary' }} onChanged={ctx.reload} />
          {r.changes.map((c, i) => (
            <Box key={i} sx={{ display: 'flex', gap: 0.75, mt: 0.5 }}>
              <span style={{ color: 'var(--text-4)' }}>•</span>
              <BilingualText value={c} size={12.5} canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
                tref={{ entity: 'revision', entity_id: r.number, field: `changes.${i}` }} onChanged={ctx.reload} />
            </Box>
          ))}
          {(r.files?.new.length ?? 0) > 0 && (
            <Typography sx={{ fontSize: 12, mt: 0.75 }} data-testid="rev-new-files">{t('changes.newFiles')}: {r.files!.new.map((f) => f.name).join(', ')}</Typography>
          )}
          {(r.files?.superseded.length ?? 0) > 0 && (
            <Box sx={{ mt: 0.5 }} data-testid="rev-superseded-files">
              <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#d97706' }}>{t('changes.superseded')}</Typography>
              {r.files!.superseded.map((f) => (
                <Typography key={f.doc_id} sx={{ fontSize: 12 }}>{f.name}{f.replaced_by ? ` → ${f.replaced_by.name}` : ''}</Typography>
              ))}
            </Box>
          )}
          {ctx.canEdit && members.length > 0 && (
            <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.75 }} data-testid="rev-ack-status">
              {ackStatus(members, r, Date.now()).filter((a) => a.required || a.ackedAt).map((a) => (
                <Chip key={a.email} size="small" color={a.ackedAt ? 'success' : 'warning'} variant="outlined" sx={{ fontSize: 11 }}
                  label={a.ackedAt ? t('changes.ackRowDone', { email: a.email, date: a.ackedAt.slice(0, 16).replace('T', ' ') })
                                   : t('changes.ackRowWaiting', { email: a.email, hours: a.waitingHours ?? 0 })} />
              ))}
            </Box>
          )}
          {r.alert && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mt: 0.75 }} data-testid="rev-alert">
              {t('changes.alertLine', { date: r.alert.sent_at.slice(0, 16).replace('T', ' '), ...alertSummary(r.alert) })}
            </Typography>
          )}
        </Paper>
      ))}
    </Box>
  );
};

export default ChangesPanel;
