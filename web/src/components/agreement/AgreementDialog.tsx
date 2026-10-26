// AeroStator Platform Services and Data Agreement: the dialog a company accepts before it starts a development.
// Shown (a) by AgreementHost when a gated action answered 428, (b) from the account menu to read the accepted copy or accept.
// Mode FREE / PRIVATE (one line each), the text in the UI language (version line; the other language one click away), the
// business-use and authority boxes, signer name and title.  The server validates again and stores the append-only record
// (routes/platform_agreement.py).  No price is shown: PRIVATE says our team will contact the company about the fee.
import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Radio,
  RadioGroup, TextField, Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../contexts/AuthContext';
import { mdBlocks, utcToday } from '../../lib/nda';
import {
  AGREEMENT_MODES, COMPANY_FIELDS, acceptBody, agreementErrors, agreementParts, emptyAgreementForm, renderAgreement,
  type AgreementForm, type AgreementStatus, type CompanyFacts,
} from '../../lib/platformAgreement';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

interface TextPayload { version: string; lang: string; texts: Record<'en' | 'zh-CN', string>; countries: string[] }

const FIELD_LABEL: Record<(typeof COMPANY_FIELDS)[number], string> = { company: 'company', reg_no: 'regNo', country: 'country', address: 'address' };

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof (j as { detail?: unknown }).detail === 'string' ? (j as { detail: string }).detail : String(r.status));
  return j as T;
}

const AgreementDialog: React.FC<{ open: boolean; onClose: (accepted: boolean) => void }> = ({ open, onClose }) => {
  const { t, i18n } = useTranslation('common');
  const { user } = useAuth();
  const uiLang: 'en' | 'zh-CN' = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const email = user?.email ?? '';
  const [status, setStatus] = useState<AgreementStatus | null>(null);
  const [text, setText] = useState<TextPayload | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [form, setForm] = useState<AgreementForm>(emptyAgreementForm());
  const [shownLang, setShownLang] = useState<'en' | 'zh-CN'>(uiLang);
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [srvErr, setSrvErr] = useState<string | null>(null);
  const [srvFields, setSrvFields] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    setStatus(null); setText(null); setLoadErr(null); setTried(false); setSrvErr(null); setSrvFields({}); setShownLang(uiLang);
    (async () => {
      try {
        const st = await getJson<AgreementStatus>('/api/agreement/status');
        if (!live) return;
        setStatus(st);
        setForm(emptyAgreementForm(st.company));
        if (st.enforced || st.admin) {
          const tx = await getJson<TextPayload>(`/api/agreement/text?lang=${uiLang}`).catch(() => null);
          if (live && tx) setText(tx);
        }
      } catch (e: any) { if (live) setLoadErr(e?.message ?? String(e)); }
    })();
    return () => { live = false; };
  }, [open, uiLang]);

  const known: Partial<CompanyFacts> = useMemo(() => {
    const c = status?.company;
    return c ? Object.fromEntries(COMPANY_FIELDS.filter((k) => c[k]).map((k) => [k, c[k]])) : {};
  }, [status]);
  const needsCompany = COMPANY_FIELDS.some((k) => !known[k]);
  const errors = useMemo(() => agreementErrors(form, known, text?.countries ?? []), [form, known, text]);
  const shown = (k: string) => (tried ? (errors[k] ?? srvFields[k]) : srvFields[k]);
  const err = (k: string) => { const e = shown(k); return e ? t(`agreement.errors.${e}`, { defaultValue: e }) : undefined; };
  const facts: CompanyFacts = {
    company: known.company ?? form.company, reg_no: known.reg_no ?? form.reg_no, country: known.country ?? form.country, address: known.address ?? form.address,
  };
  const rendered = useMemo(
    () => (text ? renderAgreement(text.texts[shownLang], facts, form, email, utcToday(), text.version) : ''),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [text, shownLang, form, known, email],
  );

  const canAccept = !!status?.enforced && status.required && !!text;
  const acc = status?.acceptance;

  const submit = async () => {
    setTried(true); setSrvErr(null); setSrvFields({});
    if (!text || Object.keys(errors).length) return;
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/agreement/accept`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(acceptBody(form, known, text.version, uiLang)),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        const d = (j as { detail?: unknown }).detail;
        if (d && typeof d === 'object' && 'errors' in d) setSrvFields((d as { errors: Record<string, string> }).errors);
        setSrvErr(typeof d === 'string' ? d : ((d as { error?: string } | undefined)?.error ?? String(r.status)));
        return;
      }
      window.dispatchEvent(new CustomEvent('platform-agreement-changed'));
      onClose(true);
    } catch (e: any) { setSrvErr(e?.message ?? String(e)); } finally { setBusy(false); }
  };

  const downloadCopy = async () => {
    try {
      const r = await fetch(`${API}/api/agreement/mine/pdf`);
      if (!r.ok) return;
      const href = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = href; a.download = 'Platform_Agreement.pdf';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch { /* the copy stays available in the next try */ }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : () => onClose(false)} maxWidth="md" fullWidth>
      <DialogTitle sx={{ fontSize: 16, fontWeight: 800, display: 'flex', alignItems: 'center', gap: 1 }}>
        {t('agreement.title')}
        {text && <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-4)', fontWeight: 400 }}>{t('agreement.version', { version: text.version })}</Typography>}
      </DialogTitle>
      <DialogContent dividers>
        {!status && !loadErr && <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={24} /></Box>}
        {loadErr && <Alert severity="error" sx={{ fontSize: 12 }}>{t('agreement.loadError', { message: loadErr })}</Alert>}
        {status && !status.enforced && status.admin && (
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>{t('agreement.notInstalled')}</Typography>
        )}
        {status && status.enforced && status.exempt && (
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>{t('agreement.exempt')}</Typography>
        )}
        {status && acc && (
          <Box sx={{ mb: 1.5 }}>
            <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('agreement.line', agreementParts(acc))}</Typography>
            {acc.mode === 'PRIVATE' && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('agreement.privatePending')}</Typography>}
            <Typography sx={{ fontSize: 11.5, color: 'var(--text-4)' }}>
              {status.scope === 'company' ? t('agreement.coversCompany', { company: acc.company ?? '' }) : t('agreement.coversAccount')}
              {!status.accepted && ` · ${t('agreement.newVersion', { version: status.version ?? '' })}`}
            </Typography>
            {status.accepted && (
              <Button size="small" onClick={() => void downloadCopy()} sx={{ textTransform: 'none', fontSize: 12, px: 0 }}>{t('agreement.download')}</Button>
            )}
          </Box>
        )}
        {canAccept && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('agreement.needed')}</Typography>
            {needsCompany ? (
              <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.25 }}>
                {COMPANY_FIELDS.filter((k) => !known[k]).map((k) => k === 'country' ? (
                  <TextField key={k} select size="small" required label={t(`agreement.${FIELD_LABEL[k]}`)} value={form.country}
                    onChange={(e) => setForm((f) => ({ ...f, country: e.target.value }))} error={!!err(k)} helperText={err(k)}>
                    {(text?.countries ?? []).map((c) => <MenuItem key={c} value={c} sx={{ fontSize: 13 }}>{c}</MenuItem>)}
                  </TextField>
                ) : (
                  <TextField key={k} size="small" required label={t(`agreement.${FIELD_LABEL[k]}`)} value={form[k]}
                    onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))} error={!!err(k)} helperText={err(k)} inputProps={{ maxLength: 300 }} />
                ))}
              </Box>
            ) : (
              <Typography sx={{ fontSize: 12.5, fontWeight: 700 }}>{t('agreement.companyLine', { company: facts.company })}</Typography>
            )}

            <Box>
              <Typography sx={{ fontSize: 12.5, fontWeight: 800, color: 'var(--text-1)' }}>{t('agreement.modeHeading')}</Typography>
              <RadioGroup value={form.mode} onChange={(_, v) => setForm((f) => ({ ...f, mode: v as AgreementForm['mode'] }))}>
                {AGREEMENT_MODES.map((m) => (
                  <FormControlLabel key={m} value={m} control={<Radio size="small" sx={{ p: 0.75 }} />} sx={{ m: 0, alignItems: 'flex-start' }}
                    label={(
                      <Box>
                        <Typography sx={{ fontSize: 12.5, fontWeight: 700 }}>{m}</Typography>
                        <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t(m === 'FREE' ? 'agreement.modeFree' : 'agreement.modePrivate')}</Typography>
                      </Box>
                    )} />
                ))}
              </RadioGroup>
              {err('mode') && <Typography sx={{ fontSize: 11, color: 'error.main' }}>{err('mode')}</Typography>}
            </Box>

            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Button size="small" onClick={() => setShownLang((l) => (l === 'en' ? 'zh-CN' : 'en'))} sx={{ textTransform: 'none', fontSize: 12 }}>
                  {t(shownLang === 'en' ? 'agreement.showChinese' : 'agreement.showEnglish')}
                </Button>
              </Box>
              <Box lang={shownLang} sx={{ maxHeight: 340, overflowY: 'auto', p: 1.5, border: '1px solid var(--line-soft)', borderRadius: 1, bgcolor: 'var(--panel-2)' }}>
                {mdBlocks(rendered).map((b, i) => (
                  <Typography key={i} sx={{ fontSize: b.kind === 'h1' ? 15 : 12.5, fontWeight: b.kind === 'p' ? 400 : 800, mb: 1, whiteSpace: 'pre-wrap', color: 'var(--text-1)' }}>
                    {b.runs.map((r, j) => <span key={j} style={{ fontWeight: r.bold ? 700 : undefined, fontStyle: r.italic ? 'italic' : undefined }}>{r.text}</span>)}
                  </Typography>
                ))}
              </Box>
            </Box>

            <Box>
              <FormControlLabel sx={{ m: 0, alignItems: 'flex-start', '& .MuiFormControlLabel-label': { fontSize: 12.5 } }}
                control={<Checkbox size="small" sx={{ p: 0.5 }} checked={form.business} onChange={(_, v) => setForm((f) => ({ ...f, business: v }))} />}
                label={t('agreement.business')} />
              {err('business') && <Typography sx={{ fontSize: 11, color: 'error.main', ml: 3.5 }}>{err('business')}</Typography>}
              <FormControlLabel sx={{ m: 0, alignItems: 'flex-start', '& .MuiFormControlLabel-label': { fontSize: 12.5 } }}
                control={<Checkbox size="small" sx={{ p: 0.5 }} checked={form.authority} onChange={(_, v) => setForm((f) => ({ ...f, authority: v }))} />}
                label={t('agreement.authority')} />
              {err('authority') && <Typography sx={{ fontSize: 11, color: 'error.main', ml: 3.5 }}>{err('authority')}</Typography>}
            </Box>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.25 }}>
              <TextField size="small" required label={t('agreement.signer')} value={form.signer} autoComplete="name"
                onChange={(e) => setForm((f) => ({ ...f, signer: e.target.value }))} error={!!err('signer')} helperText={err('signer')} inputProps={{ maxLength: 300 }} />
              <TextField size="small" required label={t('agreement.titleField')} value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} error={!!err('title')} helperText={err('title')} inputProps={{ maxLength: 300 }} />
            </Box>
            {srvErr && <Alert severity="error" sx={{ fontSize: 12 }}>{srvErr}</Alert>}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={() => onClose(false)} disabled={busy} sx={{ textTransform: 'none' }}>{canAccept ? t('agreement.cancel') : t('agreement.close')}</Button>
        {canAccept && (
          <Button size="small" variant="contained" onClick={() => void submit()} disabled={busy}
            startIcon={busy ? <CircularProgress size={14} /> : undefined} sx={{ textTransform: 'none' }}>{t('agreement.accept')}</Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default AgreementDialog;
