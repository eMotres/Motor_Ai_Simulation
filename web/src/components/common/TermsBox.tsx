// The acceptance block of a sign-up (and of the existing-user dialog), agreement sections 2.3, 7.9, 13.4 and 15.2: the capacity in which the
// person accepts (REQUIRED radio: a business they may bind / themselves as a registered sole trader or self-employed professional /
// neither), the country where the business is established (REQUIRED, the NDA / agreement list) with the section 13 variant that applies,
// the business name (REQUIRED for a business, optional for a sole trader, not asked for "neither"), the box (initially UNTICKED, its
// sentence follows the capacity and links the three texts in a new tab so a half-filled form survives) and, only where `showEarlier`
// (the existing-user dialog), the second OPTIONAL unticked box for the data the account entered before today.
// Wording: common.json legal.accept.*; the server records the hashes of the texts, the capacity, country, name, election, labels and the
// forum variant (src/motor_ai_sim/legal_texts.py).
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Checkbox, FormControl, FormControlLabel, FormLabel, Link, MenuItem, Radio, RadioGroup, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { agreementUrl, privacyUrl, termsUrl } from '../../lib/localAuth';
import { fetchCountries } from '../../lib/legalApi';
import { CAPACITIES, asksBusinessName, forumVariant, type AcceptanceForm, type Capacity } from '../../lib/legalLogic';

const TermsBox: React.FC<{
  value: AcceptanceForm; onChange: (v: AcceptanceForm) => void; disabled?: boolean; compact?: boolean; showEarlier?: boolean;
}> = ({ value, onChange, disabled, compact, showEarlier }) => {
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
  const cap = value.capacity;
  const link = (href: string, label: string) => (
    <Link href={href} target="_blank" rel="noopener noreferrer" underline="always">{label}</Link>
  );
  return (
    <Box data-testid="terms-box" sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <FormControl component="fieldset" required disabled={disabled} data-testid="terms-capacity">
        <FormLabel component="legend" sx={{ fontSize: fs, color: 'var(--text-2)' }}>{t('legal.accept.capacity.label')}</FormLabel>
        <RadioGroup value={cap} onChange={(e) => {
          const next = e.target.value as Capacity;
          onChange({ ...value, capacity: next, business: asksBusinessName(next) ? value.business : '' });
        }}>
          {CAPACITIES.map((c) => (
            <FormControlLabel key={c} value={c} data-testid={`terms-capacity-${c}`} sx={{ alignItems: 'flex-start', mr: 0, '& .MuiRadio-root': { py: 0.25 } }}
              control={<Radio size="small" />}
              label={<Typography component="span" sx={{ fontSize: fs, lineHeight: 1.4, pt: 0.35, display: 'block' }}>{t(`legal.accept.capacity.${c}`)}</Typography>} />
          ))}
        </RadioGroup>
      </FormControl>
      <TextField select size="small" fullWidth required disabled={disabled} label={t(cap === 'other' ? 'legal.accept.countryOther' : 'legal.accept.country')}
        value={value.country} onChange={(e) => onChange({ ...value, country: e.target.value })} data-testid="terms-country"
        slotProps={{ select: { MenuProps: { PaperProps: { sx: { maxHeight: 320 } } } } }}>
        {countries.map((c) => <MenuItem key={c.code} value={c.code} sx={{ fontSize: 13 }}>{c.name}</MenuItem>)}
      </TextField>
      {value.country && (
        <Typography data-testid="terms-forum" sx={{ fontSize: fs - 1, color: 'var(--text-3)', lineHeight: 1.4 }}>
          {t(`legal.accept.forum${forumVariant(value.country)}`, { variant: forumVariant(value.country) })}
        </Typography>
      )}
      {asksBusinessName(cap) && (
        <TextField size="small" fullWidth disabled={disabled} required={cap === 'business'}
          label={t(cap === 'business' ? 'legal.accept.business' : 'legal.accept.businessOptional')} value={value.business}
          onChange={(e) => onChange({ ...value, business: e.target.value })} data-testid="terms-business"
          autoComplete="organization" slotProps={{ htmlInput: { maxLength: 300 } }} />
      )}
      <FormControlLabel disabled={disabled || !cap} sx={{ mr: 0, alignItems: 'flex-start', '& .MuiCheckbox-root': { py: 0.25 } }}
        control={<Checkbox size="small" required checked={value.ticked} onChange={(e) => onChange({ ...value, ticked: e.target.checked })}
          inputProps={{ 'aria-required': true }} />}
        label={(
          <Typography component="span" sx={{ fontSize: fs, color: 'var(--text-2)', lineHeight: 1.4, pt: 0.35, display: 'block' }}>
            {cap ? (
              <>
                {t(`legal.accept.lead.${cap}`)}
                {link(termsUrl(lang), t('legal.accept.terms'))}
                {t('legal.accept.sep1')}
                {link(agreementUrl(lang), t('legal.accept.agreement'))}
                {t(`legal.accept.mid.${cap}`)}
                {link(privacyUrl(lang), t('legal.accept.privacy'))}
                {t('legal.accept.tail')}
              </>
            ) : t('legal.accept.chooseCapacity')}
          </Typography>
        )} />
      {showEarlier && (
        <FormControlLabel disabled={disabled} sx={{ mr: 0, alignItems: 'flex-start', '& .MuiCheckbox-root': { py: 0.25 } }}
          control={<Checkbox size="small" checked={value.earlier} onChange={(e) => onChange({ ...value, earlier: e.target.checked })}
            inputProps={{ 'data-testid': 'terms-earlier' } as React.InputHTMLAttributes<HTMLInputElement>} />}
          label={(
            <Typography component="span" sx={{ fontSize: fs, color: 'var(--text-2)', lineHeight: 1.4, pt: 0.35, display: 'block' }}>
              {t('legal.accept.earlier')} <span style={{ color: 'var(--text-3)' }}>{t('legal.accept.optional')}</span>
            </Typography>
          )} />
      )}
    </Box>
  );
};

export default TermsBox;
