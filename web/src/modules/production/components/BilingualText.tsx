/**
 * One bilingual text, shown in the reader's language.  Only chat messages carry a switch to the author's original.
 */
import React, { useState } from 'react';
import { Box, Button, MenuItem, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { contentLang, displayText } from '../helpers';
import type { Bilingual, Lang, TranslationRef } from '../types';

export const useContentLang = (): Lang => {
  const { i18n } = useTranslation('production');
  return contentLang(i18n.resolvedLanguage ?? i18n.language);
};

const BilingualText: React.FC<{
  value: Bilingual | null | undefined;
  bold?: boolean;
  size?: number;
  /** chat messages only: a small "Original" switch; everywhere else the text is shown plainly in the reader's language
   *  (owner 2026-10-17: no translation badges or controls outside the chat - one language per interface) */
  chat?: boolean;
  // kept for callers; translation is automatic and not confirmed in the UI any more
  canConfirm?: boolean; die?: string; config?: string; tref?: TranslationRef; onChanged?: () => void;
}> = ({ value, bold, size = 13, chat }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [original, setOriginal] = useState(false);
  if (!value) return null;
  const shown = displayText(value, ui, chat ? original : false);
  const text = (
    <Typography sx={{ fontSize: size, fontWeight: bold ? 700 : 400, color: 'var(--text-1)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', minWidth: 0 }}>
      {shown.text || '—'}
    </Typography>
  );
  const bilingual = !!value.en.trim() && !!value.zh.trim();
  if (!chat || !(bilingual || value.src_lang !== ui)) return text;
  return (
    <Box sx={{ minWidth: 0 }}>
      {text}
      <Button size="small" sx={{ textTransform: 'none', fontSize: 10.5, minWidth: 0, p: 0, mt: 0.25, color: 'var(--text-3)' }}
        onClick={() => setOriginal((v) => !v)}>
        {original ? t('bilingual.showTranslation') : t('bilingual.showOriginal')}
      </Button>
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
