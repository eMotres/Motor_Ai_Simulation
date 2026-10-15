// Online NDA click-through: company fields, the CURRENT agreement text with the placeholders filled live (only the variant of
// the selected country), the acceptance box and "type your full name to sign".  The server validates again, stores the record
// and the signed PDF and opens an applicant-verification case; the STEP opens when it is VERIFIED (routes/nda.py,
// docs/APPLICANT_VERIFICATION.md).  One short line per control, help in tooltips.
import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem,
  TextField, Tooltip, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import DataSharingConsent from '../common/DataSharingConsent';
import { useAuth } from '../../contexts/AuthContext';
import { filenameFromDisposition } from '../../lib/model3d';
import {
  NDA_FIELDS, emptyForm, formErrors, mdBlocks, renderNda, utcToday, values, type NdaField, type NdaForm,
} from '../../lib/nda';
import { emptyIntake, intakeBody, intakeErrors, type IntakeForm } from '../../lib/verification';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

interface Current { version: string; lang: string; text: string; sha256: string; signable: boolean; countries: string[] }

const FIELD_KEY: Record<NdaField, string> = {
  company: 'company', reg_no: 'regNo', country: 'country', address: 'address', signatory: 'signatory', title: 'titleField',
};

const NdaDialog: React.FC<{ open: boolean; onClose: () => void; onSigned: () => void }> = ({ open, onClose, onSigned }) => {
  const { t, i18n } = useTranslation('model3d');
  const { user } = useAuth();
  const lang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const email = user?.email ?? '';
  const [cur, setCur] = useState<Current | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [form, setForm] = useState<NdaForm>(emptyForm());
  const [intake, setIntake] = useState<IntakeForm>(emptyIntake());      // for the applicant verification that follows the signature
  const [accepted, setAccepted] = useState(false);
  const [typed, setTyped] = useState('');
  const [share, setShare] = useState(false);          // optional dataset consent, separate from the NDA; starts at the current choice
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dlBusy, setDlBusy] = useState(false);
  const [srvErr, setSrvErr] = useState<string | null>(null);
  const [srvFields, setSrvFields] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    setCur(null); setLoadErr(null); setTried(false); setSrvErr(null); setSrvFields({});
    fetch(`${API}/api/data-sharing/mine`).then((r) => (r.ok ? r.json() : null))
      .then((j: { consent?: boolean } | null) => { if (live) setShare(j?.consent === true); }).catch(() => { /* default: unticked */ });
    (async () => {
      try {
        const r = await fetch(`${API}/api/nda/current?lang=${lang}`);
        if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? String(r.status));
        if (live) setCur(await r.json() as Current);
      } catch (e: any) { if (live) setLoadErr(e?.message ?? String(e)); }
    })();
    return () => { live = false; };
  }, [open, lang]);

  const names = useMemo(() => {
    let dn: Intl.DisplayNames | null = null;
    try { dn = new Intl.DisplayNames([lang], { type: 'region' }); } catch { /* old browser */ }
    return (code: string) => { try { return dn?.of(code) ?? code; } catch { return code; } };
  }, [lang]);
  const countries = useMemo(() => (cur?.countries ?? []).map((c) => ({ code: c, name: names(c) })).sort((a, b) => a.name.localeCompare(b.name, lang)), [cur, names, lang]);

  const baseErrors = useMemo(() => formErrors(form, cur?.countries ?? [], typed, accepted), [form, cur, typed, accepted]);
  const iErrors = useMemo(() => intakeErrors(intake), [intake]);
  const errors: Record<string, string> = { ...baseErrors, ...iErrors };
  const shown = (k: string) => (tried ? (errors[k] ?? srvFields[k]) : srvFields[k]);
  const text = useMemo(() => (cur ? renderNda(cur.text, values(form, email, utcToday(), cur.version, '______')) : ''), [cur, form, email]);

  const downloadSpecimen = async () => {
    if (!cur) return;
    setDlBusy(true); setSrvErr(null);
    try {
      const r = await fetch(`${API}/api/nda/current/pdf?lang=${encodeURIComponent(cur.lang)}`);
      if (!r.ok) {
        const j = (await r.json().catch(() => ({}))) as { detail?: string };
        setSrvErr(j.detail ?? String(r.status));
        return;
      }
      const fallback = `AeroStator_Core_Model_NDA_${cur.version}_${cur.lang}_SPECIMEN.pdf`;
      const href = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = href;
      a.download = filenameFromDisposition(r.headers.get('Content-Disposition'), fallback);
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch (e: any) { setSrvErr(e?.message ?? String(e)); } finally { setDlBusy(false); }
  };

  const submit = async () => {
    setTried(true); setSrvErr(null); setSrvFields({});
    if (!cur || Object.keys(errors).length) return;
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/nda/sign`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, ...intakeBody(intake), lang: cur.lang, version: cur.version, accept: accepted, typed_name: typed, data_sharing: share }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        const d = (j as { detail?: unknown }).detail;
        if (d && typeof d === 'object' && 'errors' in d) setSrvFields((d as { errors: Record<string, string> }).errors);
        setSrvErr(typeof d === 'string' ? d : ((d as { error?: string } | undefined)?.error ?? String(r.status)));
        return;
      }
      onSigned();
      onClose();
    } catch (e: any) { setSrvErr(e?.message ?? String(e)); } finally { setBusy(false); }
  };

  const err = (k: string) => { const e = shown(k); return e ? t(`nda.errors.${e}`, { defaultValue: e }) : undefined; };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ fontSize: 16, fontWeight: 800, display: 'flex', alignItems: 'center', gap: 1 }}>
        {t('nda.title')}
        {cur && <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-4)', fontWeight: 400 }}>{t('nda.version', { version: cur.version })}</Typography>}
      </DialogTitle>
      <DialogContent dividers>
        {!cur && !loadErr && <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={24} /></Box>}
        {loadErr && <Alert severity="error" sx={{ fontSize: 12 }}>{loadErr}</Alert>}
        {cur && !cur.signable && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <Alert severity="warning" sx={{ fontSize: 12, flex: 1 }}>{t('nda.disabled')}</Alert>
            <HelpTip title={t('nda.disabledHelp')} />
          </Box>
        )}
        {cur && cur.signable && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.25 }}>
              {NDA_FIELDS.map((k) => k === 'country' ? (
                <TextField key={k} select size="small" required label={t(`nda.${FIELD_KEY[k]}`)} value={form.country}
                  onChange={(e) => setForm((f) => ({ ...f, country: e.target.value }))} error={!!err(k)} helperText={err(k)}>
                  {countries.map((c) => <MenuItem key={c.code} value={c.code} sx={{ fontSize: 13 }}>{c.name}</MenuItem>)}
                </TextField>
              ) : (
                <TextField key={k} size="small" required label={t(`nda.${FIELD_KEY[k]}`)} value={form[k]}
                  onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))} error={!!err(k)} helperText={err(k)}
                  inputProps={{ maxLength: 300 }} />
              ))}
              <TextField size="small" label={t('nda.email')} value={email} disabled InputProps={{ readOnly: true }} />
            </Box>

            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Typography sx={{ fontSize: 12.5, fontWeight: 800, color: 'var(--text-1)' }}>{t('nda.verifyHeading')}</Typography>
                <HelpTip title={t('nda.verifyHelp')} />
              </Box>
              <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.25 }}>
                <TextField size="small" required label={t('nda.website')} value={intake.website} placeholder="https://" autoComplete="url"
                  onChange={(e) => setIntake((f) => ({ ...f, website: e.target.value }))} error={!!err('website')} helperText={err('website')}
                  inputProps={{ maxLength: 300 }} />
                <TextField size="small" label={t('nda.workEmail')} value={intake.work_email} placeholder={email} autoComplete="email"
                  onChange={(e) => setIntake((f) => ({ ...f, work_email: e.target.value }))} error={!!err('work_email')} helperText={err('work_email')}
                  inputProps={{ maxLength: 254 }} />
                <TextField size="small" required label={t('nda.purpose')} value={intake.purpose} multiline minRows={2} sx={{ gridColumn: { sm: '1 / -1' } }}
                  onChange={(e) => setIntake((f) => ({ ...f, purpose: e.target.value }))} error={!!err('purpose')} helperText={err('purpose')}
                  inputProps={{ maxLength: 1000 }} />
                <TextField size="small" label={t('nda.vatNo')} value={intake.vat_no}
                  onChange={(e) => setIntake((f) => ({ ...f, vat_no: e.target.value }))} inputProps={{ maxLength: 30 }} />
              </Box>
            </Box>

            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 0.5 }}>
                <Tooltip title={t('nda.downloadSpecimenHelp')}>
                  <span>
                    <Button size="small" onClick={() => void downloadSpecimen()} disabled={dlBusy}
                      startIcon={dlBusy ? <CircularProgress size={14} /> : <DownloadIcon sx={{ fontSize: 15 }} />}
                      sx={{ textTransform: 'none', fontSize: 12 }}>
                      {t('nda.downloadSpecimen')}
                    </Button>
                  </span>
                </Tooltip>
              </Box>
              <Box sx={{ maxHeight: 280, overflowY: 'auto', p: 1.5, border: '1px solid var(--line-soft)', borderRadius: 1, bgcolor: 'var(--panel-2)' }}>
                {mdBlocks(text).map((b, i) => (
                  <Typography key={i} sx={{ fontSize: b.kind === 'h1' ? 15 : 12.5, fontWeight: b.kind === 'p' ? 400 : 800, mb: 1, whiteSpace: 'pre-wrap', color: 'var(--text-1)' }}>
                    {b.runs.map((r, j) => <span key={j} style={{ fontWeight: r.bold ? 700 : undefined, fontStyle: r.italic ? 'italic' : undefined }}>{r.text}</span>)}
                  </Typography>
                ))}
              </Box>
            </Box>

            <Box>
              <FormControlLabel sx={{ m: 0, alignItems: 'flex-start', '& .MuiFormControlLabel-label': { fontSize: 12.5 } }}
                control={<Checkbox size="small" sx={{ p: 0.5 }} checked={accepted} onChange={(_, v) => setAccepted(v)} />}
                label={t('nda.accept')} />
              {err('accept') && <Typography sx={{ fontSize: 11, color: 'error.main', ml: 3.5 }}>{err('accept')}</Typography>}
            </Box>
            <DataSharingConsent checked={share} onChange={setShare} disabled={busy} />
            <TextField size="small" required label={t('nda.typeName')} value={typed} onChange={(e) => setTyped(e.target.value)}
              error={!!err('typed_name')} helperText={err('typed_name')} autoComplete="off" />
            {srvErr && <Alert severity="error" sx={{ fontSize: 12 }}>{srvErr}</Alert>}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose} disabled={busy} sx={{ textTransform: 'none' }}>{t('nda.cancel')}</Button>
        <Button size="small" variant="contained" onClick={() => void submit()} disabled={busy || !cur?.signable}
          startIcon={busy ? <CircularProgress size={14} /> : undefined} sx={{ textTransform: 'none' }}>{t('nda.submit')}</Button>
      </DialogActions>
    </Dialog>
  );
};

export default NdaDialog;
