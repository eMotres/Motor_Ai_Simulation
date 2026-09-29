import React from 'react';
import { IconButton, ListItemIcon, Menu, MenuItem, Tooltip } from '@mui/material';
import TranslateIcon from '@mui/icons-material/Translate';
import CheckIcon from '@mui/icons-material/Check';
import { useTranslation } from 'react-i18next';
import { LOCALE_LABELS, SUPPORTED_LOCALES, type Locale } from '../../i18n/locale';
import { setLocale } from '../../i18n/persist';

/**
 * Interface language (docs/I18N.md).  `variant="menu"` renders the choices as
 * items inside the account menu; `variant="button"` is the header icon for a
 * signed-out visitor (the landing page must be readable before sign-in).
 * Each language is named in itself ("简体中文"), never translated.
 */
const LanguageSwitcher: React.FC<{ variant?: 'menu' | 'button'; onChosen?: () => void }> = ({
  variant = 'button', onChosen,
}) => {
  const { t, i18n } = useTranslation('common');
  const [anchor, setAnchor] = React.useState<HTMLElement | null>(null);
  const current = (i18n.resolvedLanguage ?? i18n.language) as Locale;
  const choose = (loc: Locale) => {
    setAnchor(null);
    void setLocale(i18n, loc);
    onChosen?.();
  };
  const items = SUPPORTED_LOCALES.map((loc) => (
    <MenuItem key={loc} sx={{ fontSize: 12.5 }} selected={loc === current}
      lang={loc} onClick={() => choose(loc)} data-testid={`lang-${loc}`}>
      <ListItemIcon>{loc === current ? <CheckIcon sx={{ fontSize: 16 }} /> : <TranslateIcon sx={{ fontSize: 16, opacity: 0.4 }} />}</ListItemIcon>
      {LOCALE_LABELS[loc]}
    </MenuItem>
  ));
  if (variant === 'menu') return <>{items}</>;
  return (
    <>
      <Tooltip title={t('language.label')}>
        <IconButton size="small" aria-label={t('language.label')} onClick={(e) => setAnchor(e.currentTarget)}>
          <TranslateIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}>{items}</Menu>
    </>
  );
};

export default LanguageSwitcher;
