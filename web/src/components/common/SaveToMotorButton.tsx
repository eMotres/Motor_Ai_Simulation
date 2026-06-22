// Shows the active "working motor" (everything auto-saves into it) and a
// "Save as new motor…" button to fork the current setup into a separate named
// motor (which appears in the Motors tab).
import React, { useEffect, useState } from 'react';
import { Box, Button, TextField, Tooltip, CircularProgress } from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import CheckIcon from '@mui/icons-material/Check';
import BoltIcon from '@mui/icons-material/Bolt';
import { getActiveMotor, createMotorFromCurrent, overwriteActiveMotor } from './motorSettings';

interface Props { disabled?: boolean; }

const SaveToMotorButton: React.FC<Props> = ({ disabled }) => {
  const [active, setActive] = useState(() => getActiveMotor());
  useEffect(() => {
    const on = (e: Event) => setActive((e as CustomEvent).detail ?? getActiveMotor());
    window.addEventListener('motor:active-changed', on);
    return () => window.removeEventListener('motor:active-changed', on);
  }, []);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [ovrBusy, setOvrBusy] = useState(false);
  const [ovrDone, setOvrDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const create = async () => {
    const nm = name.trim();
    if (!nm) return;
    setBusy(true); setErr(null);
    try {
      const m = await createMotorFromCurrent(nm);
      setActive(m); setNaming(false); setName('');
      setDone(true); setTimeout(() => setDone(false), 2200);
    } catch (e: any) { setErr(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  const overwrite = async () => {
    setOvrBusy(true); setErr(null);
    try {
      await overwriteActiveMotor();
      setOvrDone(true); setTimeout(() => setOvrDone(false), 2200);
    } catch (e: any) { setErr(String(e.message ?? e)); }
    finally { setOvrBusy(false); }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      {active && (
        <Tooltip title={`Every geometry / mesh / simulation change auto-saves into "${active.name}". Use "Save as new motor" to keep a separate named copy in the Motors tab.`} placement="top">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, fontSize: 11, color: '#94a3b8' }}>
            <BoltIcon sx={{ fontSize: 13, color: '#34d399' }} />
            <span>Motor:&nbsp;<b style={{ color: '#e2e8f0' }}>{active.name}</b></span>
            <span style={{ color: '#475569' }}>· auto-saved</span>
          </Box>
        </Tooltip>
      )}
      {naming ? (
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          <TextField size="small" autoFocus placeholder="New motor name…" value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') create(); if (e.key === 'Escape') setNaming(false); }}
            inputProps={{ style: { padding: '4px 8px', fontSize: 11, color: '#e2e8f0' } }}
            sx={{ flex: 1, '& .MuiOutlinedInput-root': { bgcolor: '#0f172a', '& fieldset': { borderColor: '#334155' } } }} />
          <Button size="small" variant="contained" disableElevation disabled={busy || !name.trim()} onClick={create}
            sx={{ textTransform: 'none', fontSize: 11, bgcolor: '#4f46e5', '&:hover': { bgcolor: '#6366f1' } }}>
            {busy ? <CircularProgress size={13} /> : 'Create'}
          </Button>
        </Box>
      ) : (
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          {active && (
            <Tooltip title={`Overwrite "${active.name}" in place with the current geometry / mesh / simulation (updates its Motors-tab card). Use "Save as new" to keep the original and fork a copy.`} placement="top">
              <span style={{ flex: 1 }}>
                <Button size="small" variant="contained" fullWidth disableElevation disabled={disabled || ovrBusy}
                  startIcon={ovrBusy ? <CircularProgress size={13} /> : ovrDone ? <CheckIcon sx={{ fontSize: 15 }} /> : <SaveIcon sx={{ fontSize: 15 }} />}
                  onClick={overwrite}
                  sx={{ textTransform: 'none', fontSize: 11, bgcolor: ovrDone ? '#059669' : '#4f46e5', '&:hover': { bgcolor: '#6366f1' } }}>
                  {ovrDone ? 'Saved ✓' : 'Overwrite this motor'}
                </Button>
              </span>
            </Tooltip>
          )}
          <Button size="small" variant="outlined" disableElevation disabled={disabled}
            startIcon={done ? <CheckIcon sx={{ fontSize: 15 }} /> : <SaveIcon sx={{ fontSize: 15 }} />}
            onClick={() => setNaming(true)}
            sx={{ flex: active ? 'unset' : 1, whiteSpace: 'nowrap', textTransform: 'none', fontSize: 11, borderColor: '#334155',
              color: done ? '#34d399' : '#a5b4fc', '&:hover': { borderColor: '#6366f1', bgcolor: '#1e1b4b' } }}>
            {done ? 'Saved ✓' : active ? 'Save as new…' : 'Save as new motor…'}
          </Button>
        </Box>
      )}
      {err && <Box sx={{ fontSize: 10, color: '#f87171' }}>{err}</Box>}
    </Box>
  );
};

export default SaveToMotorButton;
