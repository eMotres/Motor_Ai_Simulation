// Mounted once next to the workspace (Root.tsx): after a sign-in asks the server whether this account has accepted the current Terms of Use
// and Privacy Policy and, if not, shows ONE dismissible line (a small snackbar, not a dialog; owner 2026-11-01): "We updated the Terms of Use
// and the Privacy Policy." with both names linked and the buttons "Accept" (POST /api/legal/accept, source "existing") and "Dismiss"
// (hidden for the browser session).  Nothing is blocked by it; a failed status call shows nothing.
import React, { useEffect, useState } from 'react';
import { Alert, Button, Link, Snackbar } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../contexts/AuthContext';
import { acceptCurrentTerms, dismissedFor, fetchLegalStatus, rememberDismissed } from '../../lib/legalApi';
import { termsLineVisible, type LegalLang } from '../../lib/legalLogic';
import { termsLineLabel } from '../../lib/legalLabels';
import { privacyUrl, termsUrl } from '../../lib/localAuth';

const TermsUpdateLine: React.FC = () => {
  const { t, i18n } = useTranslation('common');
  const lang: LegalLang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const { user, resolved } = useAuth();
  const email = user?.email ?? '';
  const [version, setVersion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setVersion(null); setFailed(false);
    if (!resolved || !email) return undefined;
    let live = true;
    void fetchLegalStatus().then((st) => {
      if (live && st && termsLineVisible(st, email, dismissedFor())) setVersion(st.version);
    });
    return () => { live = false; };
  }, [resolved, email]);

  if (!version) return null;
  const accept = async () => {
    if (busy) return;
    setBusy(true); setFailed(false);
    try { await acceptCurrentTerms(version, lang, termsLineLabel(t)); setVersion(null); }
    catch { setFailed(true); }
    finally { setBusy(false); }
  };
  const dismiss = () => { rememberDismissed(email); setVersion(null); };
  const link = (href: string, label: string) => <Link href={href} target="_blank" rel="noopener noreferrer" underline="always" color="inherit">{label}</Link>;

  return (
    <Snackbar open anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }} data-testid="terms-update-line">
      <Alert severity="info" icon={false} sx={{ alignItems: 'center', fontSize: 13, boxShadow: 3 }}
        action={(
          <>
            <Button size="small" color="inherit" onClick={() => void accept()} disabled={busy} sx={{ textTransform: 'none', fontWeight: 700 }}>{t('legal.banner.accept')}</Button>
            <Button size="small" color="inherit" onClick={dismiss} disabled={busy} sx={{ textTransform: 'none' }}>{t('legal.banner.dismiss')}</Button>
          </>
        )}>
        {t('legal.banner.lead')}{link(termsUrl(lang), t('legal.accept.terms'))}{t('legal.banner.sep')}{link(privacyUrl(lang), t('legal.accept.privacy'))}{t('legal.banner.tail')}
        {failed && <> {t('legal.banner.error')}</>}
      </Alert>
    </Snackbar>
  );
};

export default TermsUpdateLine;
