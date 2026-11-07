// The Platform Agreement at the START of a simulation (owner 2026-11-01: "the agreement is needed only by users who want to simulate a
// motor").  Opened by AgreementHost when a job start answers 428 `agreement_required` (lib/apiAuth.ts repeats the call once after the
// acceptance).  Short on purpose: one heading, the key points as five short lines with links to the full texts, then the fields: the
// mode (FREE or "Ask for PRIVATE (paid)", nothing preselected), the capacity (agreement 2.3), the country, the business name, the
// acceptance box (its sentence carries the FREE exchange and its irreversibility, or the PRIVATE no-training promise), and the optional,
// unticked box for the data entered before today (7.9; FREE only, a PRIVATE request has no such box).  PRIVATE also places the no-training hold (server) and says that our team will
// contact the person.  Wording: common.json legal.agreement.* and legal.accept.*; the server records everything with the hashes
// (POST /api/legal/accept-agreement, source "simulation_start").
import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, FormControlLabel, FormLabel,
  Link, MenuItem, Radio, RadioGroup, TextField, Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import { acceptAgreement, fetchCountries, fetchLegalStatus } from '../../lib/legalApi';
import {
  AGREEMENT_CHOICES, CAPACITIES, agreementComplete, asksBusinessName, asksEarlierData, emptyAgreementForm, forumVariant, type AgreementChoice,
  type AgreementForm, type Capacity, type LegalLang,
} from '../../lib/legalLogic';
import { agreementLabels } from '../../lib/legalLabels';
import { agreementUrl, privacyUrl, termsUrl } from '../../lib/localAuth';

const SimulationAgreementDialog: React.FC<{ open: boolean; version?: string; onClose: (accepted: boolean) => void }> = ({ open, version, onClose }) => {
  const { t, i18n } = useTranslation('common');
  const lang: LegalLang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const [form, setForm] = useState<AgreementForm>(emptyAgreementForm());
  const [codes, setCodes] = useState<string[]>([]);
  const [ver, setVer] = useState(version ?? '');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<AgreementChoice | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    setForm(emptyAgreementForm()); setErr(null); setDone(null); setVer(version ?? '');
    void fetchCountries().then((c) => { if (live) setCodes(c); });
    if (!version) void fetchLegalStatus().then((st) => { if (live && st) setVer(st.agreement_version ?? st.version); });
    return () => { live = false; };
  }, [open, version]);

  const countries = useMemo(() => {
    let dn: Intl.DisplayNames | null = null;
    try { dn = new Intl.DisplayNames([lang], { type: 'region' }); } catch { /* old browser: the code is shown */ }
    const name = (c: string) => { try { return dn?.of(c) || c; } catch { return c; } };
    return codes.map((c) => ({ code: c, name: name(c) })).sort((a, b) => a.name.localeCompare(b.name, lang));
  }, [codes, lang]);

  const cap = form.capacity;
  const mode = form.mode;
  const link = (href: string, label: string) => <Link href={href} target="_blank" rel="noopener noreferrer" underline="always">{label}</Link>;

  const accept = async () => {
    if (!agreementComplete(form) || busy || !ver) return;
    setBusy(true); setErr(null);
    try {
      await acceptAgreement(form, ver, lang, agreementLabels(t, form));
      if (form.mode === 'PRIVATE') setDone('PRIVATE'); else onClose(true);
    } catch (e) { setErr(t('legal.agreement.error', { message: (e as Error).message })); }
    finally { setBusy(false); }
  };

  if (done === 'PRIVATE') {
    return (
      <Dialog open={open} maxWidth="xs" fullWidth aria-labelledby="sim-agreement-title">
        <DialogTitle id="sim-agreement-title" sx={{ fontSize: '1.05rem' }}>{t('legal.agreement.title')}</DialogTitle>
        <DialogContent>
          <Typography sx={{ fontSize: 13.5, lineHeight: 1.6 }} data-testid="sim-agreement-private-done">{t('legal.agreement.privateDone')}</Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => onClose(true)} variant="contained" sx={{ textTransform: 'none' }}>{t('legal.agreement.continue')}</Button>
        </DialogActions>
      </Dialog>
    );
  }

  const fs = 13;
  return (
    <Dialog open={open} onClose={busy ? undefined : () => onClose(false)} maxWidth="sm" fullWidth aria-labelledby="sim-agreement-title">
      <DialogTitle id="sim-agreement-title" sx={{ fontSize: '1.05rem' }}>{t('legal.agreement.title')}</DialogTitle>
      <DialogContent>
        <Box component="ul" data-testid="sim-agreement-points" sx={{ m: 0, mb: 1, pl: 2.5, fontSize: fs, lineHeight: 1.5, color: 'var(--text-2)' }}>
          {[1, 2, 3, 4].map((n) => <li key={n}>{t(`legal.agreement.point${n}`)}</li>)}
          <li>
            {t('legal.agreement.point5')}{' '}
            {link(agreementUrl(lang), t('legal.accept.agreement'))} · {link(termsUrl(lang), t('legal.accept.terms'))} · {link(privacyUrl(lang), t('legal.accept.privacy'))}
          </li>
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <FormControl component="fieldset" required disabled={busy} data-testid="sim-agreement-mode">
            <FormLabel component="legend" sx={{ fontSize: fs, color: 'var(--text-2)' }}>{t('legal.agreement.modeLabel')}</FormLabel>
            <RadioGroup value={mode} onChange={(e) => {
              const next = e.target.value as AgreementChoice;
              setForm({ ...form, mode: next, earlier: asksEarlierData(next) ? form.earlier : false });
            }}>
              {AGREEMENT_CHOICES.map((m) => (
                <FormControlLabel key={m} value={m} data-testid={`sim-agreement-mode-${m}`} sx={{ alignItems: 'flex-start', mr: 0, '& .MuiRadio-root': { py: 0.25 } }}
                  control={<Radio size="small" />}
                  label={<Typography component="span" sx={{ fontSize: fs, lineHeight: 1.4, pt: 0.35, display: 'block' }}>{t(`legal.agreement.mode${m === 'FREE' ? 'Free' : 'Private'}`)}</Typography>} />
              ))}
            </RadioGroup>
          </FormControl>
          {mode === 'PRIVATE' && (
            <Typography data-testid="sim-agreement-private-note" sx={{ fontSize: fs - 1, color: 'var(--text-3)', lineHeight: 1.4 }}>
              {t('legal.agreement.privateNote')}
            </Typography>
          )}
          <FormControl component="fieldset" required disabled={busy} data-testid="terms-capacity">
            <FormLabel component="legend" sx={{ fontSize: fs, color: 'var(--text-2)' }}>{t('legal.accept.capacity.label')}</FormLabel>
            <RadioGroup value={cap} onChange={(e) => {
              const next = e.target.value as Capacity;
              setForm({ ...form, capacity: next, business: asksBusinessName(next) ? form.business : '' });
            }}>
              {CAPACITIES.map((c) => (
                <FormControlLabel key={c} value={c} data-testid={`terms-capacity-${c}`} sx={{ alignItems: 'flex-start', mr: 0, '& .MuiRadio-root': { py: 0.25 } }}
                  control={<Radio size="small" />}
                  label={<Typography component="span" sx={{ fontSize: fs, lineHeight: 1.4, pt: 0.35, display: 'block' }}>{t(`legal.accept.capacity.${c}`)}</Typography>} />
              ))}
            </RadioGroup>
          </FormControl>
          <TextField select size="small" fullWidth required disabled={busy} label={t(cap === 'other' ? 'legal.accept.countryOther' : 'legal.accept.country')}
            value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} data-testid="terms-country"
            slotProps={{ select: { MenuProps: { PaperProps: { sx: { maxHeight: 320 } } } } }}>
            {countries.map((c) => <MenuItem key={c.code} value={c.code} sx={{ fontSize: 13 }}>{c.name}</MenuItem>)}
          </TextField>
          {form.country && (
            <Typography data-testid="terms-forum" sx={{ fontSize: fs - 1, color: 'var(--text-3)', lineHeight: 1.4 }}>
              {t(`legal.accept.forum${forumVariant(form.country)}`, { variant: forumVariant(form.country) })}
            </Typography>
          )}
          {asksBusinessName(cap) && (
            <TextField size="small" fullWidth disabled={busy} required={cap === 'business'}
              label={t(cap === 'business' ? 'legal.accept.business' : 'legal.accept.businessOptional')} value={form.business}
              onChange={(e) => setForm({ ...form, business: e.target.value })} data-testid="terms-business"
              autoComplete="organization" slotProps={{ htmlInput: { maxLength: 300 } }} />
          )}
          <FormControlLabel disabled={busy || !cap || !mode} sx={{ mr: 0, alignItems: 'flex-start', '& .MuiCheckbox-root': { py: 0.25 } }}
            control={<Checkbox size="small" required checked={form.ticked} onChange={(e) => setForm({ ...form, ticked: e.target.checked })}
              inputProps={{ 'aria-required': true }} />}
            label={(
              <Typography component="span" sx={{ fontSize: fs, color: 'var(--text-2)', lineHeight: 1.4, pt: 0.35, display: 'block' }} data-testid="sim-agreement-label">
                {cap && mode ? (
                  <>
                    {t(`legal.accept.lead.${cap}`)}
                    {link(termsUrl(lang), t('legal.accept.terms'))}
                    {t('legal.accept.sep1')}
                    {link(agreementUrl(lang), t('legal.accept.agreement'))}
                    {mode === 'PRIVATE' ? t('legal.accept.privateMid') : t(`legal.accept.mid.${cap}`)}
                    {link(privacyUrl(lang), t('legal.accept.privacy'))}
                    {t('legal.accept.tail')}
                  </>
                ) : t('legal.accept.chooseCapacity')}
              </Typography>
            )} />
          {asksEarlierData(mode) && (
            <FormControlLabel disabled={busy} sx={{ mr: 0, alignItems: 'flex-start', '& .MuiCheckbox-root': { py: 0.25 } }}
              control={<Checkbox size="small" checked={form.earlier} onChange={(e) => setForm({ ...form, earlier: e.target.checked })}
                inputProps={{ 'data-testid': 'terms-earlier' } as React.InputHTMLAttributes<HTMLInputElement>} />}
              label={(
                <Typography component="span" sx={{ fontSize: fs, color: 'var(--text-2)', lineHeight: 1.4, pt: 0.35, display: 'block' }}>
                  {t('legal.accept.earlier')} <span style={{ color: 'var(--text-3)' }}>{t('legal.accept.optional')}</span>
                </Typography>
              )} />
          )}
        </Box>
        {err && <Alert severity="error" sx={{ fontSize: 12.5, mt: 1.5 }}>{err}</Alert>}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={() => onClose(false)} disabled={busy} color="inherit" sx={{ textTransform: 'none' }}>{t('legal.agreement.cancel')}</Button>
        <Button onClick={() => void accept()} disabled={!agreementComplete(form) || busy || !ver} variant="contained" sx={{ textTransform: 'none' }}>
          {busy ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : t('legal.agreement.accept')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default SimulationAgreementDialog;
