/**
 * PromptDialogs — MUI replacements for window.prompt / window.confirm.
 *
 * Chrome silently suppresses the native dialogs once the user has ever
 * ticked "prevent this page from creating additional dialogs" — the caller
 * sees `null` as if Cancel was pressed and the control just looks dead
 * (this repo hit that with the motor rename pencil, and again with the
 * family catalog's battery editor).  These never get suppressed.
 */
import React, { useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, Box,
} from '@mui/material';

export interface TextPromptState {
  title: string;
  label?: string;
  initial?: string;
  hint?: string;
  onSubmit: (value: string) => void;
}

export const TextPromptDialog: React.FC<{
  state: TextPromptState | null;
  onClose: () => void;
}> = ({ state, onClose }) => {
  const [value, setValue] = useState('');
  useEffect(() => { setValue(state?.initial ?? ''); }, [state]);
  const submit = () => {
    const v = value.trim();
    if (!v) return;
    const fn = state?.onSubmit;
    onClose();
    if (fn) fn(v);
  };
  return (
    <Dialog open={!!state} onClose={onClose}
      PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
                          borderRadius: 2, minWidth: 420 } }}>
      <DialogTitle sx={{ color: 'var(--text-0)', fontSize: '0.95rem', fontWeight: 700 }}>
        {state?.title}
      </DialogTitle>
      <DialogContent>
        <TextField autoFocus fullWidth size="small" label={state?.label ?? 'Name'}
          value={value} onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
          helperText={state?.hint ?? ' '} sx={{ mt: 1 }} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}
          sx={{ textTransform: 'none', color: 'var(--text-2)' }}>Cancel</Button>
        <Button variant="contained" disabled={!value.trim()} onClick={submit}
          sx={{ textTransform: 'none' }}>OK</Button>
      </DialogActions>
    </Dialog>
  );
};

export interface ConfirmState {
  title: string;
  body?: string;
  onConfirm: () => void;
}

export const ConfirmDialog: React.FC<{
  state: ConfirmState | null;
  onClose: () => void;
}> = ({ state, onClose }) => {
  const confirm = () => {
    const fn = state?.onConfirm;
    onClose();
    if (fn) fn();
  };
  return (
    <Dialog open={!!state} onClose={onClose}
      PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
                          borderRadius: 2, minWidth: 380 } }}>
      <DialogTitle sx={{ color: 'var(--text-0)', fontSize: '0.95rem', fontWeight: 700 }}>
        {state?.title}
      </DialogTitle>
      {state?.body && (
        <DialogContent>
          <Box sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{state.body}</Box>
        </DialogContent>
      )}
      <DialogActions>
        <Button onClick={onClose}
          sx={{ textTransform: 'none', color: 'var(--text-2)' }}>Cancel</Button>
        <Button variant="contained" color="error" onClick={confirm}
          sx={{ textTransform: 'none' }}>Delete</Button>
      </DialogActions>
    </Dialog>
  );
};
