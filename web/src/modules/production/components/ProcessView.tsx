/** Process sections in order (fits, assembly, winding, tests); owner/engineer/admin can edit text, reorder, add and remove. */
import React, { useEffect, useState } from 'react';
import { Box, Button, Chip, MenuItem, Paper, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import { productionApi } from '../api';
import { displayText, makeBilingual } from '../helpers';
import type { Lang, ProcessKind, ProcessSection, ProdCtx } from '../types';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const KINDS: ProcessKind[] = ['fits', 'assembly', 'winding', 'tests'];

const ProcessView: React.FC<{ sections: ProcessSection[]; ctx: ProdCtx }> = ({ sections, ctx }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ProcessSection[]>(sections);
  const [lang, setLang] = useState<Lang>(ui);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!editing) setDraft(sections); }, [sections, editing]);

  const patch = (i: number, p: Partial<ProcessSection>) => setDraft((d) => d.map((s, k) => (k === i ? { ...s, ...p } : s)));
  const move = (i: number, by: number) => setDraft((d) => {
    const j = i + by; if (j < 0 || j >= d.length) return d;
    const n = [...d]; [n[i], n[j]] = [n[j], n[i]]; return n;
  });
  const save = async () => {
    try { await productionApi.saveProcess(ctx.die, ctx.config, draft); setEditing(false); ctx.reload(); }
    catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
  };
  const textOf = (b: ProcessSection['title']) => displayText(b, b.src_lang, true).text;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
      {ctx.canEdit && (
        <Box sx={{ display: 'flex', gap: 1 }}>
          {!editing && <Button size="small" sx={{ textTransform: 'none' }} onClick={() => { setDraft(sections); setErr(null); setEditing(true); }}>{t('edit')}</Button>}
          {editing && (
            <>
              <Button size="small" variant="contained" sx={{ textTransform: 'none' }} onClick={() => void save()}>{t('save')}</Button>
              <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setEditing(false)}>{t('cancel')}</Button>
              <Button size="small" sx={{ textTransform: 'none' }}
                onClick={() => setDraft((d) => [...d, { id: `sec_new_${Date.now()}`, kind: 'assembly', title: makeBilingual('', lang), body: makeBilingual('', lang), tables: [] }])}>
                {t('process.add')}
              </Button>
            </>
          )}
          {err && <Typography sx={{ fontSize: 11, color: '#f87171', alignSelf: 'center' }}>{err}</Typography>}
        </Box>
      )}
      {!editing && sections.length === 0 && <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('process.none')}</Typography>}
      {!editing && sections.map((s) => (
        <Paper key={s.id} sx={{ ...PANEL, p: 1.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Chip size="small" label={t(`process.kind.${s.kind}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: 'var(--text-2)' }} />
            <BilingualText value={s.title} bold size={14} canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
              tref={{ entity: 'process', entity_id: s.id, field: 'title' }} onChanged={ctx.reload} />
          </Box>
          <BilingualText value={s.body} size={12.5} canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
            tref={{ entity: 'process', entity_id: s.id, field: 'body' }} onChanged={ctx.reload} />
          {(s.tables_i18n?.[ui] || s.tables_i18n?.zh || s.tables || []).map((tb, ti) => (
            <Box key={ti} component="table" sx={{ mt: 1, borderCollapse: 'collapse', fontSize: 12, '& td, & th': { border: '1px solid var(--line-soft)', px: 0.75, py: 0.25 } }}>
              <tbody>{tb.map((row, ri) => <tr key={ri}>{row.map((c, ci) => <td key={ci}>{c}</td>)}</tr>)}</tbody>
            </Box>
          ))}
        </Paper>
      ))}
      {editing && draft.map((s, i) => (
        <Paper key={s.id} sx={{ ...PANEL, p: 1.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            <TextField select size="small" label={t('process.kindLabel')} value={s.kind} onChange={(e) => patch(i, { kind: e.target.value as ProcessKind })} sx={{ width: 140 }}>
              {KINDS.map((k) => <MenuItem key={k} value={k}>{t(`process.kind.${k}`)}</MenuItem>)}
            </TextField>
            <Box sx={{ flex: 1 }} />
            <Button size="small" disabled={i === 0} sx={{ textTransform: 'none', fontSize: 11 }} onClick={() => move(i, -1)}>{t('process.up')}</Button>
            <Button size="small" disabled={i === draft.length - 1} sx={{ textTransform: 'none', fontSize: 11 }} onClick={() => move(i, 1)}>{t('process.down')}</Button>
            <Button size="small" sx={{ textTransform: 'none', fontSize: 11, color: '#f87171' }} onClick={() => setDraft((d) => d.filter((_, k) => k !== i))}>{t('remove')}</Button>
          </Box>
          <BilingualInput label={t('process.title')} value={textOf(s.title)} lang={s.title.src_lang}
            onChange={(v, l) => { setLang(l); patch(i, { title: makeBilingual(v, l) }); }} />
          <BilingualInput label={t('process.body')} value={textOf(s.body)} lang={s.body.src_lang} multiline minRows={3}
            onChange={(v, l) => { setLang(l); patch(i, { body: makeBilingual(v, l) }); }} />
        </Paper>
      ))}
    </Box>
  );
};

export default ProcessView;
