import React from 'react';
import { Tooltip } from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { useTranslation } from 'react-i18next';

/**
 * HelpTip — a small ⓘ icon that reveals parameter help on hover, replacing the
 * inline `helperText` captions under fields so panels stay compact.  Drop it into
 * a field's `InputProps.endAdornment`, or render it next to a label/heading.
 *
 *   <TextField … InputProps={{ endAdornment: <HelpTip title="…explanation…" /> }} />
 *
 * Localised help (docs/I18N.md): pass `i18nKey` (+ optional `ns`, default
 * `help`) instead of — or beside — `title`; `title` is then the English
 * fallback shown until the namespace loads or when the key is missing.
 */
const HelpTip: React.FC<{ title?: React.ReactNode; i18nKey?: string; ns?: string;
  values?: Record<string, unknown> }> = ({ title, i18nKey, ns = 'help', values }) => {
  const { t } = useTranslation(ns);
  const text = i18nKey
    ? t(i18nKey, { ...values, defaultValue: typeof title === 'string' ? title : undefined })
    : title;
  return (
  <Tooltip title={text ?? ''} placement="top" arrow enterTouchDelay={0}
    componentsProps={{ tooltip: { sx: { fontSize: 11, maxWidth: 300, lineHeight: 1.4 } } }}>
    <InfoOutlinedIcon
      sx={{ fontSize: 15, color: 'var(--text-4)', cursor: 'help', flexShrink: 0,
        '&:hover': { color: 'var(--text-2)' } }} />
  </Tooltip>
  );
};

export default HelpTip;
