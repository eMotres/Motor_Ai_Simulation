// Admin · Verification — applicants who signed the model NDA and the checks behind their access to the 3-D tools and the STEP download.
import React from 'react';
import { Box, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import VerificationPanel from '../VerificationPanel';

const VerificationSection: React.FC = () => {
  const { t } = useTranslation('admin');
  return (
    <Box>
      <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>{t('verification.title')}</Typography>
      <VerificationPanel />
    </Box>
  );
};

export default VerificationSection;
