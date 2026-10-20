import React from 'react';
import Snackbar from '@mui/material/Snackbar';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Tooltip from '@mui/material/Tooltip';
import { useTranslation } from 'react-i18next';

/** The idle limit named on screen (server default, sessions.py / session_activity.py). */
export const IDLE_MINUTES = 30;

/**
 * One short line when the server closed the session after 30 minutes without activity, with a Sign in button.
 * Not an error: the stored session is forgotten exactly as a normal sign-out forgets it, nothing else is wiped.
 */
const IdleSignedOutNotice: React.FC<{ open: boolean; onSignIn: () => void; onClose: () => void }> = ({ open, onSignIn, onClose }) => {
  const { t } = useTranslation('common');
  return (
    <Snackbar open={open} anchorOrigin={{ vertical: 'top', horizontal: 'center' }} onClose={(_e, reason) => { if (reason !== 'clickaway') onClose(); }}>
      <Alert severity="info" variant="filled" sx={{ fontSize: 13, alignItems: 'center' }}
        action={<Button color="inherit" size="small" onClick={onSignIn}>{t('auth.cta.signin')}</Button>}>
        <Tooltip title={t('auth.idleClosedTip', { minutes: IDLE_MINUTES })}>
          <span>{t('auth.idleClosed', { minutes: IDLE_MINUTES })}</span>
        </Tooltip>
      </Alert>
    </Snackbar>
  );
};

export default IdleSignedOutNotice;
