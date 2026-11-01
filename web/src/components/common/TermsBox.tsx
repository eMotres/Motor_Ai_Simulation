// The sign-up box (owner 2026-11-01): ONE unticked, required checkbox that accepts the Terms of Use and the Privacy Policy
// ("I use AeroStator for business and accept the Terms of Use and the Privacy Policy.", both names linked in a new tab so a half-filled
// form survives).  No capacity, country or business name is asked here: the Platform Agreement is accepted at the start of a simulation
// (components/agreement/SimulationAgreementDialog).  Wording: common.json legal.signup.*; the server records the hashes of the texts.
import React from 'react';
import { Checkbox, FormControlLabel, Link, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { privacyUrl, termsUrl } from '../../lib/localAuth';

const TermsBox: React.FC<{ ticked: boolean; onChange: (ticked: boolean) => void; disabled?: boolean; compact?: boolean }> = ({
  ticked, onChange, disabled, compact,
}) => {
  const { t, i18n } = useTranslation('common');
  const lang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const link = (href: string, label: string) => (
    <Link href={href} target="_blank" rel="noopener noreferrer" underline="always">{label}</Link>
  );
  return (
    <FormControlLabel data-testid="terms-box" disabled={disabled} sx={{ mr: 0, alignItems: 'flex-start', '& .MuiCheckbox-root': { py: 0.25 } }}
      control={<Checkbox size="small" required checked={ticked} onChange={(e) => onChange(e.target.checked)} inputProps={{ 'aria-required': true }} />}
      label={(
        <Typography component="span" sx={{ fontSize: compact ? 12 : 13, color: 'var(--text-2)', lineHeight: 1.4, pt: 0.35, display: 'block' }}>
          {t('legal.signup.lead')}
          {link(termsUrl(lang), t('legal.accept.terms'))}
          {t('legal.signup.sep')}
          {link(privacyUrl(lang), t('legal.accept.privacy'))}
          {t('legal.signup.tail')}
        </Typography>
      )} />
  );
};

export default TermsBox;
