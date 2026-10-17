/**
 * One bilingual text: shown in the reader's language; a machine translation carries a badge, and a member can
 * confirm or edit it (POST translate/confirm) or retry a pending one (POST translate/retry). The toggle shows
 * the author's original next to the translation.
 */
import React, { useState } from 'react';
import { Box, Button, Chip, MenuItem, TextField, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { contentLang, displayText } from '../helpers';
import { productionApi } from '../api';
import type { Bilingual, Lang, TranslationRef } from '../types';

export const useContentLang = (): Lang => {
  const { i18n } = useTranslation('production');
  return contentLang(i18n.resolvedLanguage ?? i18n.language);
};

const BilingualText: React.FC<{
  value: Bilingual | null | undefined;
  /** who may confirm: true for owner/engineer/admin */
  canConfirm?: boolean;
  die?: string; config?: string;
  /** what the server addresses; without it the confirm/retry actions are hidden */
  tref?: TranslationRef;
  onChanged?: () => void;
  bold?: boolean;
  size?: number;
}> = ({ value, canConfirm, die, config, tref, onChanged, bold, size = 13 }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [original, setOriginal] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const shown = displayText(value, ui, original);
  const bilingual = !!value && !!value.en.trim() && !!value.zh.trim();
  const actions = !!(canConfirm && tref && die && config);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); setEditing(false); onChanged?.(); }
    catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
    finally { setBusy(false); }
  };

  if (!value) return null;
  return (
    <Box sx={{ minWidth: 0 }}>
      {editing ? (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
          <TextField size="small" multiline minRows={2} value={draft} onChange={(e) => setDraft(e.target.value)}
            sx={{ '& .MuiInputBase-input': { fontSize: size } }} />
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" variant="contained" disabled={busy || !draft.trim()} sx={{ textTransform: 'none' }}
              onClick={() => void run(() => productionApi.confirmTranslation(die!, config!, tref!, shown.lang, draft))}>{t('save')}</Button>
            <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setEditing(false)}>{t('cancel')}</Button>
          </Box>
        </Box>
      ) : (
        <Typography sx={{ fontSize: size, fontWeight: bold ? 700 : 400, color: 'var(--text-1)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {shown.text || '—'}
        </Typography>
      )}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap', mt: 0.25 }}>
        {shown.machine && (
          <Tooltip title={t('bilingual.machineTip')}>
            <Chip size="small" label={t('bilingual.machine')} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, color: '#fbbf24', bgcolor: '#fbbf241f' }} />
          </Tooltip>
        )}
        {shown.pending && (
          <Tooltip title={t('bilingual.pendingTip')}>
            <Chip size="small" label={t('bilingual.pending')} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, color: '#f87171', bgcolor: '#f871711f' }} />
          </Tooltip>
        )}
        {(bilingual || value.src_lang !== ui) && (
          <Button size="small" sx={{ textTransform: 'none', fontSize: 10.5, minWidth: 0, p: 0, color: 'var(--text-3)' }}
            onClick={() => setOriginal((v) => !v)}>
            {original ? t('bilingual.showTranslation') : t('bilingual.showOriginal')}
          </Button>
        )}
        {actions && !editing && shown.pending && (
          <Button size="small" disabled={busy} sx={{ textTransform: 'none', fontSize: 10.5, minWidth: 0, p: 0 }}
            onClick={() => void run(() => productionApi.retryTranslation(die!, config!, tref!))}>{t('bilingual.retry')}</Button>
        )}
        {actions && !editing && shown.machine && (
          <Button size="small" disabled={busy} sx={{ textTransform: 'none', fontSize: 10.5, minWidth: 0, p: 0 }}
            onClick={() => void run(() => productionApi.confirmTranslation(die!, config!, tref!, shown.lang))}>{t('bilingual.confirm')}</Button>
        )}
        {actions && !editing && !shown.isOriginal && (
          <Button size="small" sx={{ textTransform: 'none', fontSize: 10.5, minWidth: 0, p: 0, color: 'var(--text-3)' }}
            onClick={() => { setDraft(shown.text); setEditing(true); }}>{t('bilingual.edit')}</Button>
        )}
      </Box>
      {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
    </Box>
  );
};

/** A text box plus the language the author writes in (defaults to the interface language). */
export const BilingualInput: React.FC<{
  label: string; value: string; lang: Lang;
  onChange: (text: string, lang: Lang) => void;
  multiline?: boolean; minRows?: number;
}> = ({ label, value, lang, onChange, multiline, minRows = 2 }) => {
  const { t } = useTranslation('production');
  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
      <TextField size="small" fullWidth label={label} value={value} multiline={multiline} minRows={multiline ? minRows : undefined}
        onChange={(e) => onChange(e.target.value, lang)} sx={{ '& .MuiInputBase-input': { fontSize: 13 } }} />
      <TextField select size="small" value={lang} onChange={(e) => onChange(value, e.target.value as Lang)} sx={{ width: 96, '& .MuiInputBase-input': { fontSize: 12 } }}>
        <MenuItem value="en">{t('bilingual.lang.en')}</MenuItem>
        <MenuItem value="zh">{t('bilingual.lang.zh')}</MenuItem>
      </TextField>
    </Box>
  );
};

export default BilingualText;
