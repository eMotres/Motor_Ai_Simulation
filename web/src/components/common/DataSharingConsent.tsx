// The optional dataset-sharing consent (text version data_sharing_consent_v1): a checkbox with ONE short label and a tooltip that
// gives the scope.  It is separate from the NDA acceptance and never required.  The wording lives in locales/*/common.json
// (`dataSharing`) and matches the counsel draft Data_Sharing_Consent_v1_DRAFT.md.
import React from 'react';
import { Box, Checkbox, FormControlLabel, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from './HelpTip';

/** The full scope text, one paragraph per line: used by the tooltips of the NDA dialog and the account menu. */
export const DataSharingScope: React.FC = () => {
  const { t } = useTranslation('common');
  return (
    <Box component="span" sx={{ display: 'block' }}>
      <b>{t('dataSharing.scopeTitle')}</b>
      {['scopeData', 'scopePurpose', 'scopeAnon', 'scopeRestricted', 'withdraw'].map((k) => (
        <Box component="span" key={k} sx={{ display: 'block', mt: 0.5 }}>{t(`dataSharing.${k}`)}</Box>
      ))}
    </Box>
  );
};

const DataSharingConsent: React.FC<{ checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }> = ({ checked, onChange, disabled }) => {
  const { t } = useTranslation('common');
  return (
    <Box data-testid="data-sharing-consent">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <FormControlLabel sx={{ m: 0, alignItems: 'flex-start', '& .MuiFormControlLabel-label': { fontSize: 12.5 } }}
          control={<Checkbox size="small" sx={{ p: 0.5 }} checked={checked} disabled={disabled} onChange={(_, v) => onChange(v)} />}
          label={t('dataSharing.label')} />
        <HelpTip title={<DataSharingScope />} />
      </Box>
      <Typography sx={{ fontSize: 11, color: 'var(--text-4)', ml: 3.5 }}>{t('dataSharing.separate')}</Typography>
    </Box>
  );
};

export default DataSharingConsent;
