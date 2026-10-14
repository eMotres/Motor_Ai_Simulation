// Edit one configuration's applications: tick the ones that apply, pick the default (the first),
// Save.  Used by Admin > Catalogs > Applications and the Motors catalog pencil (admin only).
// After a save the family tree is refetched and 'family-changed' is dispatched, so the Motors badge
// and Configure's gating update at once.
import React, { useState } from 'react';
import { Box, Button, Checkbox, FormControlLabel, Radio, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { invalidateFamilyTree } from '../../lib/familyTree';
import {
  APPLICATIONS, canSaveApplications, cleanApplications, saveApplications, setDefaultApplication,
  toggleApplication, type Application,
} from '../../lib/applications';

const tx = nsT('motors');
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const ApplicationEditor: React.FC<{
  die: string; config: string; applications: unknown; onDone?: () => void;
}> = ({ die, config, applications, onDone }) => {
  useTranslation('motors');
  const saved = cleanApplications(applications);
  const [list, setList] = useState<Application[]>(saved);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      await saveApplications(API, die, config, list);
      invalidateFamilyTree();
      try { window.dispatchEvent(new CustomEvent('family-changed')); } catch { /* SSR */ }
      onDone?.();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
      {APPLICATIONS.map((a) => {
        const on = list.includes(a);
        return (
          <Box key={a} sx={{ display: 'inline-flex', alignItems: 'center' }}>
            <FormControlLabel sx={{ m: 0 }} label={<Typography sx={{ fontSize: 11 }}>{tx(`applications.${a}`)}</Typography>}
              control={<Checkbox size="small" checked={on} sx={{ p: 0.3 }}
                onChange={() => setList((l) => toggleApplication(l, a))} />} />
            <Radio size="small" sx={{ p: 0.3, visibility: on ? 'visible' : 'hidden' }} checked={on && list[0] === a}
              title={tx('applications.defaultTip')} inputProps={{ 'aria-label': tx('applications.default') }}
              onChange={() => setList((l) => setDefaultApplication(l, a))} />
          </Box>
        );
      })}
      <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }} title={tx('applications.defaultTip')}>
        {tx('applications.default')}
      </Typography>
      <Button size="small" variant="contained" disabled={busy || !canSaveApplications(saved, list)} onClick={save}
        sx={{ textTransform: 'none', fontSize: 11, py: 0, px: 1 }}>{tx('applications.save')}</Button>
      {onDone && (
        <Button size="small" disabled={busy} onClick={onDone}
          sx={{ textTransform: 'none', fontSize: 11, py: 0, px: 1 }}>{tx('applications.cancel')}</Button>
      )}
      {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
    </Box>
  );
};

export default ApplicationEditor;
