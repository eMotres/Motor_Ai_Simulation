// The four opt-in topics of the communication preferences: one checkbox per topic, all UNTICKED by default (GDPR).
// Used by the sign-up form (components/auth/LoginDialog.tsx) and the preferences page (components/auth/CommPrefsPage.tsx).
// The wording is common.json commPrefs.*; the server hashes the same wording (src/motor_ai_sim/comm_prefs.py) and a test keeps them equal.
import React from 'react';
import { Box, Checkbox, FormControlLabel, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from './HelpTip';
import { COMM_TOPICS, type CommTopic, type Topics } from '../../lib/commPrefsLogic';

const CommPrefsBoxes: React.FC<{ value: Topics; onChange: (v: Topics) => void; disabled?: boolean; compact?: boolean }> = ({
  value, onChange, disabled, compact,
}) => {
  const { t } = useTranslation('common');
  const fs = compact ? 11.5 : 13;
  return (
    <Box data-testid="comm-prefs-boxes">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.25 }}>
        <Typography sx={{ fontSize: fs, color: 'var(--text-2)' }}>{t('commPrefs.intro')}</Typography>
        <HelpTip title={t('commPrefs.introHelp')} />
      </Box>
      <Box sx={{ display: 'flex', flexDirection: 'column' }}>
        {COMM_TOPICS.map((id: CommTopic) => (
          <FormControlLabel key={id} disabled={disabled} sx={{ mr: 0, alignItems: 'flex-start', '& .MuiCheckbox-root': { py: 0.25 } }}
            control={<Checkbox size="small" checked={value[id]} onChange={(e) => onChange({ ...value, [id]: e.target.checked })} />}
            label={<Typography sx={{ fontSize: fs, color: 'var(--text-2)', lineHeight: 1.35, pt: 0.35 }}>{t(`commPrefs.topics.${id}`)}</Typography>} />
        ))}
      </Box>
    </Box>
  );
};

export default CommPrefsBoxes;
