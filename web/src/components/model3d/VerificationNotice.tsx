// A small dialog for the places that offer a locked file (Geometry > Files) when the NDA IS signed but the verification is not
// complete: it says where the person stands instead of re-opening the NDA form.
import React from 'react';
import { Button, Dialog, DialogActions, DialogContent, DialogTitle } from '@mui/material';
import { useTranslation } from 'react-i18next';
import VerificationStatus from './VerificationStatus';
import type { AccessInfo } from '../../lib/verification';

const VerificationNotice: React.FC<{ open: boolean; onClose: () => void; access: AccessInfo }> = ({ open, onClose, access }) => {
  const { t } = useTranslation('model3d');
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: 16, fontWeight: 800 }}>{t('verification.title')}</DialogTitle>
      <DialogContent dividers>
        <VerificationStatus access={access} dense={false} />
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose} sx={{ textTransform: 'none' }}>{t('nda.cancel')}</Button>
      </DialogActions>
    </Dialog>
  );
};

export default VerificationNotice;
