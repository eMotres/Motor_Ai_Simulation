/** Revisions (newest first). The acknowledge banner lists revisions this account has not acknowledged yet; owner/engineer/admin publish. */
import React, { useState } from 'react';
import { Alert, Box, Button, Chip, Paper, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import HelpTip from '../../../components/common/HelpTip';
import { productionApi } from '../api';
import { canAckRole, hasAcked, makeBilingual, nextRevision, sortRevisions, unackedRevisions } from '../helpers';
import type { Lang, ProdCtx, Revision } from '../types';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

const ChangesPanel: React.FC<{ revisions: Revision[]; currentRevision: string; ctx: ProdCtx }> = ({ revisions, currentRevision, ctx }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [summary, setSummary] = useState('');
  const [lang, setLang] = useState<Lang>(ui);
  const [lines, setLines] = useState<string[]>(['']);
  const [err, setErr] = useState<string | null>(null);
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
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setLines((a) => [...a, ''])}>{t('changes.addChange')}</Button>
            <Button size="small" variant="contained" disabled={!summary.trim()} sx={{ textTransform: 'none' }}
              onClick={() => void guard(async () => {
                await productionApi.publishRevision(ctx.die, ctx.config, makeBilingual(summary, lang),
                  lines.filter((l) => l.trim()).map((l) => makeBilingual(l, lang)));
                setSummary(''); setLines(['']);
              })}>{t('changes.publish', { rev: nextRevision(newest) })}</Button>
          </Box>
        </Paper>
      )}
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
        </Paper>
      ))}
    </Box>
  );
};

export default ChangesPanel;
