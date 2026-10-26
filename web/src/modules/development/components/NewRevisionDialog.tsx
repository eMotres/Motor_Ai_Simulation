import { useState } from 'react';
import { Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Stack, TextField, Typography } from '@mui/material';
import HelpTip from '../../../components/common/HelpTip';
import { motorRevisionsApi } from '../../../lib/motorRevisionsApi';
import { nextRevLetter, type RevisionRow } from '../../../lib/motorRevisions';

/** «New revision» (owner 2026-10-27): the next product revision from the current one.  The reason ("what changed and why") is required;
 *  "new lamination" copies the whole die, otherwise only the configuration is copied.  The revision it replaces becomes read-only. */
export function NewRevisionDialog({ revision, open, t, onClose, onCreated }: {
  revision: RevisionRow; open: boolean; t: (key: string, o?: Record<string, unknown>) => string; onClose: () => void; onCreated: () => void | Promise<void>;
}) {
  const [reason, setReason] = useState('');
  const [newDie, setNewDie] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const to = nextRevLetter(revision.rev);
  const submit = async () => {
    setBusy(true); setError('');
    try {
      await motorRevisionsApi.create(revision.lineage, { reason: reason.trim(), new_die: newDie, from_rev: revision.rev });
      setReason(''); setNewDie(false);
      await onCreated();
      onClose();
    } catch (e) { setError(e instanceof Error ? e.message : t('revisions.failed')); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm" data-testid="new-revision-dialog">
      <DialogTitle sx={{ fontSize: 15 }}>{t('revisions.dialogTitle', { to })}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.2} sx={{ pt: 0.5 }}>
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('revisions.dialogLine', { to, from: revision.rev })}</Typography>
          <Stack direction="row" alignItems="flex-start" spacing={0.5}>
            <TextField autoFocus required fullWidth multiline minRows={3} size="small" label={t('revisions.reason')}
              value={reason} onChange={(e) => setReason(e.target.value)} disabled={busy} inputProps={{ 'data-testid': 'new-revision-reason' }} />
            <HelpTip title={t('revisions.reasonTip')} />
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <FormControlLabel sx={{ m: 0 }} control={<Checkbox size="small" checked={newDie} disabled={busy} onChange={(e) => setNewDie(e.target.checked)} inputProps={{ 'data-testid': 'new-revision-new-die' } as object} />}
              label={<span style={{ fontSize: 12.5 }}>{t('revisions.newDie')}</span>} />
            <HelpTip title={t('revisions.newDieTip')} />
          </Stack>
          {error && <Alert severity="error" sx={{ py: 0 }}>{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose} disabled={busy}>{t('revisions.cancel')}</Button>
        <Button size="small" variant="contained" onClick={() => void submit()} disabled={busy || !reason.trim()} data-testid="new-revision-create">{t('revisions.create', { to })}</Button>
      </DialogActions>
    </Dialog>
  );
}
