import React from 'react';
import { Link } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { openCookieSettings, useAnalyticsState } from './AnalyticsConsent';

/** Small text link that reopens the consent bar; renders nothing where analytics is blocked. */
const CookieSettingsLink: React.FC<{ prefix?: string }> = ({ prefix = '' }) => {
  const { t } = useTranslation('common');
  if (useAnalyticsState() === 'blocked') return null;
  return (
    <>
      {prefix}
      <Link component="button" type="button" underline="hover" color="inherit"
        onClick={openCookieSettings} sx={{ fontSize: 'inherit', verticalAlign: 'baseline' }}>
        {t('cookies.settings')}
      </Link>
    </>
  );
};

export default CookieSettingsLink;
