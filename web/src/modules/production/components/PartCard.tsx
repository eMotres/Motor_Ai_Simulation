/** One part: name, kind, revision, drawings (PNG preview / PDF download), 3-D model link, bilingual description; editable by owner/engineer/admin. */
import React, { useEffect, useState } from 'react';
import { Box, Button, Chip, MenuItem, Paper, TextField, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import { downloadFile, downloadModelStep, fileObjectUrl, productionApi } from '../api';
import { displayText, makeBilingual } from '../helpers';
import type { DrawingFile, Lang, Part, ProdCtx } from '../types';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const CHIP = { height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: 'var(--text-2)' } as const;

const DrawingPreview: React.FC<{ ctx: ProdCtx; d: DrawingFile }> = ({ ctx, d }) => {
  const { t } = useTranslation('production');
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (d.kind !== 'png') return;
    let live = true; let made: string | null = null;
    fileObjectUrl(ctx.die, ctx.config, d.file_id).then((u) => { made = u; if (live) setUrl(u); else URL.revokeObjectURL(u); }).catch(() => {});
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [ctx.die, ctx.config, d.file_id, d.kind]);
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, width: 160 }}>
      {d.kind === 'png' && url && (
        <Box component="img" src={url} alt={d.name} sx={{ width: 160, height: 110, objectFit: 'contain', bgcolor: '#fff', borderRadius: 1 }} />
      )}
      <Button size="small" sx={{ textTransform: 'none', fontSize: 11, justifyContent: 'flex-start', p: 0 }}
        onClick={() => void downloadFile(ctx.die, ctx.config, d.file_id, d.name)}>
        {d.kind.toUpperCase()} · {d.name || t('parts.download')}
      </Button>
    </Box>
  );
};

const PartCard: React.FC<{ part: Part; ctx: ProdCtx; onChanged: () => void }> = ({ part, ctx, onChanged }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [lang, setLang] = useState<Lang>(ui);
  const [material, setMaterial] = useState('');
  const [spec, setSpec] = useState('');
  const [qty, setQty] = useState('1');
  const [kind, setKind] = useState<Part['kind']>('made');
  const [err, setErr] = useState<string | null>(null);

  const startEdit = () => {
    setName(displayText(part.name, part.name.src_lang, true).text);
    setDesc(displayText(part.description, part.description.src_lang, true).text);
    setLang(part.name.src_lang);
    setMaterial(part.material); setSpec(part.spec); setQty(String(part.qty_per_motor)); setKind(part.kind);
    setErr(null); setEditing(true);
  };
  const save = async () => {
    try {
      await productionApi.updatePart(ctx.die, ctx.config, part.id, {
        name: makeBilingual(name, lang), description: makeBilingual(desc, lang),
        material, spec, kind, qty_per_motor: Number(qty) || 0,
      });
      setEditing(false); onChanged();
    } catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
  };
  const remove = async () => {
    if (!window.confirm(t('parts.deleteConfirm'))) return;
    try { await productionApi.deletePart(ctx.die, ctx.config, part.id); onChanged(); }
    catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
  };

  const tref = (field: string) => ({ entity: 'part' as const, entity_id: part.id, field });
  return (
    <Paper sx={{ ...PANEL, p: 1.5 }}>
      {editing ? (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <BilingualInput label={t('parts.name')} value={name} lang={lang} onChange={(v, l) => { setName(v); setLang(l); }} />
          <BilingualInput label={t('parts.description')} value={desc} lang={lang} multiline onChange={(v, l) => { setDesc(v); setLang(l); }} />
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <TextField select size="small" label={t('parts.kind')} value={kind} onChange={(e) => setKind(e.target.value as Part['kind'])} sx={{ width: 140 }}>
              {(['made', 'purchased', 'consumable'] as const).map((k) => <MenuItem key={k} value={k}>{t(`parts.kindValue.${k}`)}</MenuItem>)}
            </TextField>
            <TextField size="small" label={t('parts.qty')} value={qty} onChange={(e) => setQty(e.target.value)} sx={{ width: 100 }} />
            <TextField size="small" label={t('parts.material')} value={material} onChange={(e) => setMaterial(e.target.value)} sx={{ flex: 1, minWidth: 160 }} />
            <TextField size="small" label={t('parts.spec')} value={spec} onChange={(e) => setSpec(e.target.value)} sx={{ flex: 1, minWidth: 160 }} />
          </Box>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" variant="contained" disabled={!name.trim()} sx={{ textTransform: 'none' }} onClick={() => void save()}>{t('save')}</Button>
            <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setEditing(false)}>{t('cancel')}</Button>
          </Box>
        </Box>
      ) : (
        <>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <BilingualText value={part.name} bold size={14} canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
                tref={tref('name')} onChanged={onChanged} />
            </Box>
            <Chip size="small" label={t(`parts.kindValue.${part.kind}`)} sx={CHIP} />
            <Chip size="small" label={`× ${part.qty_per_motor}`} sx={CHIP} />
            <Chip size="small" label={t('parts.revision', { rev: part.revision })} sx={CHIP} />
            {ctx.canEdit && (
              <>
                <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }} onClick={startEdit}>{t('edit')}</Button>
                <Button size="small" sx={{ textTransform: 'none', fontSize: 11, color: '#f87171' }} onClick={() => void remove()}>{t('remove')}</Button>
              </>
            )}
          </Box>
          {(part.material || part.spec) && (
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 0.5 }}>{[part.material, part.spec].filter(Boolean).join(' · ')}</Typography>
          )}
          <Box sx={{ mt: 0.75 }}>
            <BilingualText value={part.description} canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
              tref={tref('description')} onChanged={onChanged} size={12.5} />
          </Box>
        </>
      )}
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mt: 1, alignItems: 'flex-start' }}>
        {part.drawing_files.length === 0 && <Typography sx={{ fontSize: 11.5, color: 'var(--text-4)' }}>{t('parts.noDrawings')}</Typography>}
        {part.drawing_files.map((d) => <DrawingPreview key={d.file_id} ctx={ctx} d={d} />)}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
          {part.optional_step && (
            <Button size="small" sx={{ textTransform: 'none', fontSize: 11, justifyContent: 'flex-start', p: 0 }}
              onClick={() => void downloadFile(ctx.die, ctx.config, part.optional_step!.file_id, `${part.id}.step`)}>{t('parts.partStep')}</Button>
          )}
          <Tooltip title={t('parts.model3dTip')}>
            <Button size="small" sx={{ textTransform: 'none', fontSize: 11, justifyContent: 'flex-start', p: 0 }}
              onClick={() => void downloadModelStep(ctx.die, ctx.config).catch((e) => setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })))}>
              {t('parts.model3d')}
            </Button>
          </Tooltip>
        </Box>
      </Box>
      {err && <Typography sx={{ fontSize: 11, color: '#f87171', mt: 0.5 }}>{err}</Typography>}
    </Paper>
  );
};

export default PartCard;
