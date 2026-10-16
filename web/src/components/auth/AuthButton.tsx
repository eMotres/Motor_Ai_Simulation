import React, { Suspense, lazy } from 'react';
import { Button } from '@mui/material';
import LoginIcon from '@mui/icons-material/Login';
import { useAuth } from '../../contexts/AuthContext';
import { NewsletterLinkHandler } from './NoticeBell';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '../common/LanguageSwitcher';

// The signed-in menu and its six dialogs are a chunk of their own: a signed-out visitor
// (the landing, often on a slow link) never downloads them.
const loadAuthMenu = () => import('./AuthMenu');
const AuthMenu = lazy(loadAuthMenu);
/** Start fetching the signed-in menu early (App calls this when it loads). */
export const preloadAuthMenu = () => { void loadAuthMenu(); };

/** Header login/logout control (self-hosted auth — see contexts/AuthContext). */
const AuthButton: React.FC = () => {
  const { user, signIn } = useAuth();
  const { t } = useTranslation('common');

  if (!user) {
    return (
      <>
        <NewsletterLinkHandler />
        <LanguageSwitcher variant="button" />
        <Button size="small" variant="outlined"
          startIcon={<LoginIcon sx={{ fontSize: 16 }} />}
          onClick={() => signIn().catch(() => {})}
          sx={{ textTransform: 'none', fontSize: '0.75rem' }}>
          {t('account.signIn')}
        </Button>
      </>
    );
  }

  return (
    <Suspense fallback={null}>
      <AuthMenu />
    </Suspense>
  );
};

export default AuthButton;
