// The acceptance block of a sign-up (and of the existing-user dialog): the country where the business is established (REQUIRED, the
// NDA / agreement list), the business name (OPTIONAL: empty for a sole trader) and then the box, initially UNTICKED.  The box is one
// sentence with the three texts as links (a new tab, so a half-filled form survives): Terms of Use, Platform Agreement, Privacy Policy.
// Wording: common.json legal.accept.*; the server records the hashes of the texts, the country, the name and the forum variant
// (src/motor_ai_sim/legal_texts.py).
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Checkbox, FormControlLabel, Link, MenuItem, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { agreementUrl, privacyUrl, termsUrl } from '../../lib/localAuth';
import { fetchCountries } from '../../lib/legalApi';
import type { AcceptanceForm } from '../../lib/legalLogic';

const TermsBox: React.FC<{ value: AcceptanceForm; onChange: (v: AcceptanceForm) => void; disabled?: boolean; compact?: boolean }> = ({
  value, onChange, disabled, compact,
}) => {
  const { t, i18n } = useTranslation('common');
  const lang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const [codes, setCodes] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    void fetchCountries().then((c) => { if (live) setCodes(c); });
    return () => { live = false; };
  }, []);
  const countries = useMemo(() => {
    let dn: Intl.DisplayNames | null = null;
    try { dn = new Intl.DisplayNames([lang], { type: 'region' }); } catch { /* old browser: the code is shown */ }
    const name = (c: string) => { try { return dn?.of(c) || c; } catch { return c; } };
    return codes.map((c) => ({ code: c, name: name(c) })).sort((a, b) => a.name.localeCompare(b.name, lang));
  }, [codes, lang]);
  const fs = compact ? 12 : 13;
  const link = (href: string, label: string) => (
    <Link href={href} target="_blank" rel="noopener noreferrer" underline="always">{label}</Link>
  );
  return (
    <Box data-testid="terms-box" sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <TextField select size="small" fullWidth required disabled={disabled} label={t('legal.accept.country')} value={value.country}
        onChange={(e) => onChange({ ...value, country: e.target.value })} data-testid="terms-country"
        slotProps={{ select: { MenuProps: { PaperProps: { sx: { maxHeight: 320 } } } } }}>
        {countries.map((c) => <MenuItem key={c.code} value={c.code} sx={{ fontSize: 13 }}>{c.name}</MenuItem>)}
      </TextField>
      <TextField size="small" fullWidth disabled={disabled} label={t('legal.accept.business')} value={value.business}
        onChange={(e) => onChange({ ...value, business: e.target.value })} data-testid="terms-business"
        autoComplete="organization" slotProps={{ htmlInput: { maxLength: 300 } }} />
      <FormControlLabel disabled={disabled} sx={{ mr: 0, alignItems: 'flex-start', '& .MuiCheckbox-root': { py: 0.25 } }}
        control={<Checkbox size="small" required checked={value.ticked} onChange={(e) => onChange({ ...value, ticked: e.target.checked })}
          inputProps={{ 'aria-required': true }} />}
        label={(
          <Typography component="span" sx={{ fontSize: fs, color: 'var(--text-2)', lineHeight: 1.4, pt: 0.35, display: 'block' }}>
            {t('legal.accept.lead')}
            {link(termsUrl(lang), t('legal.accept.terms'))}
            {t('legal.accept.sep1')}
            {link(agreementUrl(lang), t('legal.accept.agreement'))}
            {t('legal.accept.mid')}
            {link(privacyUrl(lang), t('legal.accept.privacy'))}
            {t('legal.accept.tail')}
          </Typography>
        )} />
    </Box>
  );
};

export default TermsBox;
