// Account menu · "Share my data with the AeroStator dataset": change the optional consent later.  Every change is an appended
// audit line on the server (routes/data_sharing.py).  Off by default; withdrawing stops future inclusion only.
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Switch, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { DataSharingScope } from '../common/DataSharingConsent';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

interface State { consent: boolean; version: string; time: string; current_version: string }

const DataSharingDialog: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const { t } = useTranslation('common');
  const [st, setSt] = useState<State | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const call = useCallback(async (init?: RequestInit) => {
    const r = await fetch(`${API}/api/data-sharing/mine`, init);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(((j as { detail?: string }).detail) ?? String(r.status));
    return j as State;
  }, []);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    call().then(setSt).catch((e: Error) => setErr(t('dataSharing.saveError', { message: e.message })));
  }, [open, call, t]);

  const toggle = async (v: boolean) => {
    setBusy(true); setErr(null);
    try {
      setSt(await call({ method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ consent: v }) }));
    } catch (e) { setErr(t('dataSharing.saveError', { message: (e as Error).message })); } finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: 16, fontWeight: 800 }}>{t('dataSharing.dialogTitle')}</DialogTitle>
      <DialogContent dividers>
        {!st && !err && <Box sx={{ p: 2, textAlign: 'center' }}><CircularProgress size={22} /></Box>}
        {st && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <Switch size="small" checked={st.consent} disabled={busy} onChange={(_, v) => void toggle(v)}
              inputProps={{ 'aria-label': t('dataSharing.toggle') }} />
            <Typography sx={{ fontSize: 13, flex: 1 }}>{t('dataSharing.toggle')}</Typography>
            <HelpTip title={<DataSharingScope />} />
          </Box>
        )}
        {st && (
          <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)', mt: 0.5 }}>
            {st.consent ? t('dataSharing.on', { time: st.time.replace('T', ' ').replace('Z', ' UTC') }) : t('dataSharing.off')}
            {' · '}{t('dataSharing.textVersion', { version: st.current_version })}
          </Typography>
        )}
        {err && <Alert severity="error" sx={{ fontSize: 12, mt: 1 }}>{err}</Alert>}
      </DialogContent>
      <DialogActions><Button size="small" onClick={onClose} sx={{ textTransform: 'none' }}>{t('dataSharing.close')}</Button></DialogActions>
    </Dialog>
  );
};

export default DataSharingDialog;
