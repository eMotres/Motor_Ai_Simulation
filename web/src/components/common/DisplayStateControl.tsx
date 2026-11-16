/** Active / Parked / Archived for staff (owner 2026-11-16): the filter chips with counts above a list, the one-click control on a
 *  row or an object's page, and the "new activity" badge of a parked / archived row.  Nothing here is shown to customers or
 *  to the factory: the pages render these only for admins and engineers. */
import React, { useState } from 'react';
import { Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, TextField, ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';
import EditNoteOutlinedIcon from '@mui/icons-material/EditNoteOutlined';
import { useTranslation } from 'react-i18next';
import { DISPLAY_FILTERS, DISPLAY_STATES, type DisplayFilter, type DisplayState, type DisplayStateRec, type FilterCounts } from '../../lib/displayState';

export const DisplayStateFilter: React.FC<{ value: DisplayFilter; onChange: (f: DisplayFilter) => void; counts: FilterCounts }> = ({ value, onChange, counts }) => {
  const { t } = useTranslation('common');
  return (
    <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center', flexWrap: 'wrap' }} data-testid="display-state-filter">
      {DISPLAY_FILTERS.map((f) => {
        const woke = counts.woke[f];
        const chip = (
          <Chip key={f} size="small" clickable color={value === f ? 'primary' : 'default'} variant={value === f ? 'filled' : 'outlined'}
            data-testid={`display-filter-${f}`} onClick={() => onChange(f)}
            label={<>{t(`displayState.${f}`)} {counts.counts[f]}{woke > 0 && <Box component="span" sx={{ ml: 0.5, color: '#f59e0b', fontWeight: 800 }}>+{woke}</Box>}</>}
            sx={{ height: 22, fontSize: 11.5 }} />
        );
        return woke > 0 ? <Tooltip key={f} title={t('displayState.wokeTip', { count: woke })}>{chip}</Tooltip> : chip;
      })}
    </Box>
  );
};

/** One click each way; the optional one-line reason is edited behind the note icon.  Who / when / why sit in the tooltip. */
export const DisplayStateControl: React.FC<{ rec?: DisplayStateRec; busy?: boolean; onChange: (state: DisplayState, reason: string) => Promise<void> | void }> = ({ rec, busy, onChange }) => {
  const { t } = useTranslation('common');
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState('');
  const state = rec?.state ?? 'active';
  const tip = rec?.explicit
    ? t('displayState.setTip', { by: rec.by || '—', at: (rec.at || '').slice(0, 10), reason: rec.reason || '—' })
    : t('displayState.defaultTip');
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();   // a click on a list row's control does not open the row
  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.25 }} onClick={stop} onKeyDown={stop} data-testid="display-state-control">
      <Tooltip title={tip}>
        <ToggleButtonGroup size="small" exclusive value={state} disabled={busy}
          onChange={(_e, next: DisplayState | null) => { if (next && next !== state) void onChange(next, next === 'active' ? '' : rec?.reason ?? ''); }}
          sx={{ '& .MuiToggleButton-root': { py: 0.1, px: 0.8, fontSize: 10.5, textTransform: 'none', lineHeight: 1.6 } }}>
          {DISPLAY_STATES.map((s) => <ToggleButton key={s} value={s} data-testid={`display-state-${s}`}>{t(`displayState.${s}`)}</ToggleButton>)}
        </ToggleButtonGroup>
      </Tooltip>
      {state !== 'active' && (
        <Tooltip title={t('displayState.reason')}>
          <span><IconButton size="small" disabled={busy} aria-label={t('displayState.reason')} onClick={() => { setNote(rec?.reason ?? ''); setNoteOpen(true); }} sx={{ color: 'var(--text-3)' }}>
            <EditNoteOutlinedIcon sx={{ fontSize: 16 }} />
          </IconButton></span>
        </Tooltip>
      )}
      <Dialog open={noteOpen} onClose={() => setNoteOpen(false)} onClick={stop}>
        <DialogTitle sx={{ fontSize: 15 }}>{t('displayState.reason')}</DialogTitle>
        <DialogContent>
          <TextField autoFocus fullWidth size="small" value={note} inputProps={{ maxLength: 200 }} onChange={(e) => setNote(e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNoteOpen(false)}>{t('displayState.cancel')}</Button>
          <Button variant="contained" onClick={() => { setNoteOpen(false); void onChange(state, note); }}>{t('displayState.save')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

/** The badge of a parked / archived row with activity after it was hidden (it does not move the row back to Active). */
export const WokeBadge: React.FC<{ show: boolean }> = ({ show }) => {
  const { t } = useTranslation('common');
  if (!show) return null;
  return (
    <Tooltip title={t('displayState.wokeRowTip')}>
      <Chip size="small" label={t('displayState.woke')} data-testid="display-state-woke"
        sx={{ ml: 1, height: 18, fontSize: 10.5, fontWeight: 700, verticalAlign: 'middle', bgcolor: '#f59e0b', color: '#111' }} />
    </Tooltip>
  );
};
