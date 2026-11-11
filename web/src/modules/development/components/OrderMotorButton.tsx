import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Tooltip, Typography } from '@mui/material';
import { developmentApi } from '../api';
import { activeOrderRequest, motorHasResults, orderQuantity } from '../orderRequests';
import type { OrderRequest } from '../orderRequests';
import type { ProjectMotor } from '../types';

/** «Order this motor» (owner 2026-11-11): next to a linked motor that has results.  It only sends a REQUEST to our team; the app never
 *  shows a price or a lead time.  After sending, the button gives way to the date of the request. */
export function OrderMotorButton({ projectId, motor, canOrder, t, lang }: {
  projectId: string; motor: ProjectMotor; canOrder: boolean; t: (key: string, o?: Record<string, unknown>) => string; lang?: string;
}) {
  const [requests, setRequests] = useState<OrderRequest[]>([]);
  const [open, setOpen] = useState(false);
  const [quantity, setQuantity] = useState('1');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const die = motor.canonical_die ?? motor.die;
  const hasResults = motorHasResults(motor);
  const load = useCallback(async () => {
    try { setRequests(await developmentApi.orderRequests(projectId)); } catch { /* the button stays; a failed read is not an order */ }
  }, [projectId]);
  useEffect(() => { void load(); }, [load]);
  const sent = activeOrderRequest(requests, die, motor.config);
  const qty = orderQuantity(quantity);
  const submit = async () => {
    if (qty == null) return;
    setBusy(true); setError('');
    try {
      await developmentApi.createOrderRequest(projectId, { die, config: motor.config, quantity: qty, note: note.trim() || undefined });
      await load();
      setOpen(false); setNote('');
    } catch (e) { setError(e instanceof Error ? e.message : t('order.failed')); } finally { setBusy(false); }
  };
  if (sent) {
    const when = new Date(sent.at).toLocaleDateString(lang || undefined);
    return <Typography data-testid="order-requested" sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('order.requested', { date: when })}</Typography>;
  }
  if (!canOrder) return null;
  return (
    <>
      <Tooltip title={hasResults ? t('order.tip') : t('order.noResultsTip')}>
        <span>
          <Button size="small" variant="outlined" disabled={!hasResults} data-testid="order-motor" onClick={() => setOpen(true)} sx={{ minHeight: 44 }}>{t('order.button')}</Button>
        </span>
      </Tooltip>
      <Dialog open={open} onClose={busy ? undefined : () => setOpen(false)} fullWidth maxWidth="xs" data-testid="order-dialog">
        <DialogTitle sx={{ fontSize: 15 }}>{t('order.button')}</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ pt: 0.5 }}>
            <TextField autoFocus size="small" type="number" label={t('order.quantity')} value={quantity} disabled={busy}
              onChange={(e) => setQuantity(e.target.value)} error={qty == null} inputProps={{ min: 1, step: 1, inputMode: 'numeric', 'data-testid': 'order-quantity' }}
              sx={{ '& input': { fontSize: 16, minHeight: 28 } }} />
            <TextField size="small" multiline minRows={3} label={t('order.note')} value={note} disabled={busy}
              onChange={(e) => setNote(e.target.value.slice(0, 2000))} inputProps={{ 'data-testid': 'order-note' }} sx={{ '& textarea': { fontSize: 16 } }} />
            <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('order.contact')}</Typography>
            {error && <Alert severity="error">{error}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button disabled={busy} onClick={() => setOpen(false)} sx={{ minHeight: 44 }}>{t('order.cancel')}</Button>
          <Button variant="contained" disabled={busy || qty == null} data-testid="order-send" onClick={() => void submit()} sx={{ minHeight: 44 }}>{t('order.send')}</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
