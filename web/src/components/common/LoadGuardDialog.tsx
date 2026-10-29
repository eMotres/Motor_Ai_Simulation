/**
 * The ONE dialog in front of every load of another machine (lib/loadGuard).
 * One short line + a tooltip with what is at stake; three buttons; nothing else.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle } from '@mui/material';
import HelpTip from './HelpTip';
import { subscribeLoadAsk, watchRuns, type LoadAsk } from '../../lib/loadGuard';
import { stakeItems } from '../../lib/unsavedWork';

const LoadGuardDialog: React.FC = () => {
  const { t } = useTranslation('common');
  const [ask, setAsk] = useState<LoadAsk | null>(null);
  const [busy, setBusy] = useState<'save' | 'discard' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => watchRuns(), []);
  useEffect(() => subscribeLoadAsk((a) => { setAsk(a); setBusy(null); setError(null); }), []);

  if (!ask) return null;
  const duty = ask.here?.duty ?? ask.target.duty;
  const items = stakeItems(ask.stake, ask.here?.duty ?? null,
    (w) => t(`loadGuard.job.${w}`));
  const line = ask.stake.jobs.length && !ask.stake.unsavedRun && !ask.stake.edits?.any
    ? t('loadGuard.lineJobs') : t('loadGuard.lineWork');
  const go = async (choice: 'save' | 'discard') => {
    setBusy(choice); setError(null);
    const why = await ask.choose(choice);
    if (why) { setBusy(null); setError(t('loadGuard.failed', { why })); }
  };
  const tipItems = (
    <Box component="ul" sx={{ m: 0, pl: 2 }}>
      {items.map((it) => <li key={it.key}>{t(`loadGuard.item.${it.key}`, it.vars)}</li>)}
    </Box>
  );
  const here = ask.here ? `${ask.here.die} / ${ask.here.config}` : '';
  return (
    <Dialog open onClose={() => { if (!busy) ask.cancel(); }}
      PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2, minWidth: 380 } }}>
      <DialogTitle sx={{ color: 'var(--text-0)', fontSize: '0.95rem', fontWeight: 700 }}>
        {t('loadGuard.title', { target: `${ask.target.die} / ${ask.target.config}` })}
      </DialogTitle>
      <DialogContent>
        <Box sx={{ fontSize: 12.5, color: 'var(--text-2)', display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <span>{line}</span>
          <HelpTip title={<Box>{here && <Box sx={{ mb: 0.5 }}>{here}{duty ? ` / ${duty}` : ''}</Box>}{tipItems}</Box>} />
        </Box>
        {error && <Box sx={{ mt: 1, fontSize: 12, color: '#fca5a5' }} role="alert">{error}</Box>}
      </DialogContent>
      <DialogActions>
        <Button disabled={!!busy} onClick={() => ask.cancel()}
          sx={{ textTransform: 'none', color: 'var(--text-2)' }}>{t('loadGuard.cancel')}</Button>
        <Button color="error" disabled={!!busy} onClick={() => void go('discard')}
          title={t('loadGuard.tipDiscard')} sx={{ textTransform: 'none' }}>
          {busy === 'discard' ? <CircularProgress size={14} /> : t('loadGuard.discard')}
        </Button>
        {ask.canSave && (
          <Button variant="contained" disabled={!!busy} onClick={() => void go('save')}
            title={t('loadGuard.tipSave')} sx={{ textTransform: 'none' }}>
            {busy === 'save' ? <CircularProgress size={14} /> : t('loadGuard.save')}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default LoadGuardDialog;
