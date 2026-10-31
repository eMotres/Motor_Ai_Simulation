// "Updated terms: free simulation": the ONE dialog an existing account sees after sign-in when it has no acceptance of the current
// Terms of Use (GET /api/legal/status says `required`).  The "In short" paragraph of the Terms of Use (served text, interface
// language), links to the three texts, the same block as the sign-up (capacity, country, business name, unticked box) plus the optional
// unticked box for the data entered before today, the owner's deadline when set, "Accept" (POST /api/legal/accept, source "existing")
// and "Later" (closes it until the next sign-in; nothing else is blocked).
import React, { useEffect, useState } from 'react';
import { Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Link, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import TermsBox from '../common/TermsBox';
import { acceptCurrentTerms, fetchLegalText } from '../../lib/legalApi';
import { acceptanceComplete, emptyAcceptance, inShort, type AcceptanceForm, type LegalLang } from '../../lib/legalLogic';
import { legalLabels } from '../../lib/legalLabels';
import { agreementUrl, privacyUrl, termsUrl } from '../../lib/localAuth';

const TermsUpdateDialog: React.FC<{ open: boolean; version: string; deadline?: string | null; onAccepted: () => void; onLater: () => void }> = ({
  open, version, deadline, onAccepted, onLater,
}) => {
  const { t, i18n } = useTranslation('common');
  const lang: LegalLang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const [short, setShort] = useState<{ label: string; body: string } | null>(null);
  const [form, setForm] = useState<AcceptanceForm>(emptyAcceptance());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    const ctl = new AbortController();
    setShort(null); setForm(emptyAcceptance()); setErr(null);
    fetchLegalText('terms', lang, ctl.signal).then((x) => setShort(inShort(x.text))).catch(() => { /* the links below still work */ });
    return () => ctl.abort();
  }, [open, lang]);

  const accept = async () => {
    if (!acceptanceComplete(form) || busy) return;
    setBusy(true); setErr(null);
    try { await acceptCurrentTerms(version, lang, form, legalLabels(t, form, true)); onAccepted(); }
    catch (e) { setErr(t('legal.update.error', { message: (e as Error).message })); }
    finally { setBusy(false); }
  };

  const link = (href: string, label: string) => <Link href={href} target="_blank" rel="noopener noreferrer" underline="always">{label}</Link>;

  return (
    <Dialog open={open} onClose={onLater} maxWidth="sm" fullWidth aria-labelledby="terms-update-title">
      <DialogTitle id="terms-update-title" sx={{ fontSize: '1.05rem' }}>{t('legal.update.title')}</DialogTitle>
      <DialogContent>
        {!short && <Box sx={{ p: 1, textAlign: 'center' }}><CircularProgress size={18} /></Box>}
        {short && (
          <Typography sx={{ fontSize: 13.5, lineHeight: 1.6, mb: 1.5 }} data-testid="terms-in-short">
            <strong>{short.label}</strong> {short.body}
          </Typography>
        )}
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', fontSize: 13, mb: 1.5 }}>
          {link(termsUrl(lang), t('legal.accept.terms'))}
          {link(agreementUrl(lang), t('legal.accept.agreement'))}
          {link(privacyUrl(lang), t('legal.accept.privacy'))}
        </Box>
        {deadline && (
          <Typography sx={{ fontSize: 13, lineHeight: 1.5, mb: 1.5, fontWeight: 600 }} data-testid="terms-deadline">
            {t('legal.update.deadline', { date: deadline })}
          </Typography>
        )}
        <TermsBox value={form} onChange={setForm} disabled={busy} showEarlier />
        {err && <Alert severity="error" sx={{ fontSize: 12.5, mt: 1.5 }}>{err}</Alert>}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onLater} disabled={busy} color="inherit" sx={{ textTransform: 'none' }}>{t('legal.update.later')}</Button>
        <Button onClick={() => void accept()} disabled={!acceptanceComplete(form) || busy} variant="contained" sx={{ textTransform: 'none' }}>
          {busy ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : t('legal.update.accept')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default TermsUpdateDialog;
