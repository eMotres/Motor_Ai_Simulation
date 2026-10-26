/** Proposals: anyone with access raises one; owner/engineer/admin accept, reject or mark it implemented. Nothing here edits the design. */
import React, { useState } from 'react';
import { Box, Button, Chip, MenuItem, Paper, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import HelpTip from '../../../components/common/HelpTip';
import { productionApi } from '../api';
import { canProposeRole, makeBilingual } from '../helpers';
import type { Lang, Part, ProdCtx, Proposal, ProposalStatus } from '../types';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const COLOR: Record<ProposalStatus, string> = { open: '#2563eb', accepted: '#16a34a', rejected: '#dc2626', implemented: '#7c3aed' };

const ProposalsPanel: React.FC<{ proposals: Proposal[]; parts: Part[]; ctx: ProdCtx }> = ({ proposals, parts, ctx }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [partId, setPartId] = useState('');
  const [text, setText] = useState('');
  const [lang, setLang] = useState<Lang>(ui);
  const [note, setNote] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const byId = new Map(parts.map((p) => [p.id, p]));

  const guard = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); ctx.reload(); } catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
  };
  const decide = (p: Proposal, action: 'accept' | 'reject' | 'implement') =>
    guard(() => productionApi.decide(ctx.die, ctx.config, p.id, action, note[p.id]?.trim() ? makeBilingual(note[p.id], ui) : null));

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
      {canProposeRole(ctx.role) && (
        <Paper sx={{ ...PANEL, p: 1.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>{t('proposals.new')}</Typography>
            <HelpTip title={t('proposals.tip')} />
            <Box sx={{ flex: 1 }} />
            <TextField select size="small" label={t('proposals.forPart')} value={partId} onChange={(e) => setPartId(e.target.value)} sx={{ minWidth: 220 }}>
              <MenuItem value="">{t('proposals.general')}</MenuItem>
              {parts.map((p) => <MenuItem key={p.id} value={p.id}>{p.name.en || p.name.zh}</MenuItem>)}
            </TextField>
          </Box>
          <BilingualInput label={t('proposals.text')} value={text} lang={lang} multiline onChange={(v, l) => { setText(v); setLang(l); }} />
          <Box>
            <Button size="small" variant="contained" disabled={!text.trim()} sx={{ textTransform: 'none' }}
              onClick={() => void guard(async () => {
                await productionApi.createProposal(ctx.die, ctx.config, partId || null, makeBilingual(text, lang));
                setText('');
              })}>{t('proposals.submit')}</Button>
          </Box>
        </Paper>
      )}
      {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
      {proposals.length === 0 && <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('proposals.none')}</Typography>}
      {proposals.map((p) => {
        const part = p.part_id ? byId.get(p.part_id) : undefined;
        return (
          <Paper key={p.id} data-item-id={p.id} sx={{ ...PANEL, p: 1.5 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
              <Chip size="small" label={t(`proposals.status.${p.status}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, color: COLOR[p.status], bgcolor: `${COLOR[p.status]}1f` }} />
              {part ? <BilingualText value={part.name} size={12} bold /> : <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('proposals.general')}</Typography>}
              <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{p.author_email} · {p.created_at.slice(0, 10)}</Typography>
              {p.linked_revision && <Chip size="small" label={t('proposals.linked', { rev: p.linked_revision })} sx={{ height: 18, fontSize: 10.5, bgcolor: 'var(--panel)', color: 'var(--text-2)' }} />}
            </Box>
            <BilingualText value={p.text} canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
              tref={{ entity: 'proposal', entity_id: p.id, field: 'text' }} onChanged={ctx.reload} />
            {p.decision_note && (
              <Box sx={{ mt: 0.75, pl: 1, borderLeft: '2px solid var(--line-soft)' }}>
                <BilingualText value={p.decision_note} size={12} canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
                  tref={{ entity: 'proposal', entity_id: p.id, field: 'decision_note' }} onChanged={ctx.reload} />
              </Box>
            )}
            {ctx.canEdit && (p.status === 'open' || p.status === 'accepted') && (
              <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mt: 1, flexWrap: 'wrap' }}>
                <TextField size="small" placeholder={t('proposals.note')} value={note[p.id] ?? ''}
                  onChange={(e) => setNote((n) => ({ ...n, [p.id]: e.target.value }))} sx={{ flex: 1, minWidth: 180, '& .MuiInputBase-input': { fontSize: 12, py: 0.5 } }} />
                {p.status === 'open' && (
                  <>
                    <Button size="small" sx={{ textTransform: 'none', color: '#4ade80' }} onClick={() => void decide(p, 'accept')}>{t('proposals.accept')}</Button>
                    <Button size="small" sx={{ textTransform: 'none', color: '#f87171' }} onClick={() => void decide(p, 'reject')}>{t('proposals.reject')}</Button>
                  </>
                )}
                {p.status === 'accepted' && (
                  <Button size="small" variant="contained" sx={{ textTransform: 'none' }} onClick={() => void decide(p, 'implement')}>{t('proposals.implement')}</Button>
                )}
              </Box>
            )}
          </Paper>
        );
      })}
    </Box>
  );
};

export default ProposalsPanel;
