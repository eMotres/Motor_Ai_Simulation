// "Save mesh / simulation to motor" — stamps the current UI settings onto the
// loaded motor's preset (config/motor_presets.json), so reloading that motor
// from the Motors tab restores the whole working setup.  If no motor is loaded
// yet, it offers to save the current setup as a NEW motor.
import React, { useEffect, useState } from 'react';
import { Box, Button, TextField, Tooltip, CircularProgress } from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import CheckIcon from '@mui/icons-material/Check';
import {
  getActiveMotor, saveSettingsToActiveMotor, createMotorFromCurrent,
} from './motorSettings';

interface Props {
  kind: 'mesh' | 'simulation';
  disabled?: boolean;
}

const LABEL = { mesh: 'mesh', simulation: 'simulation' } as const;

const SaveToMotorButton: React.FC<Props> = ({ kind, disabled }) => {
  const [active, setActive] = useState(() => getActiveMotor());
  useEffect(() => {
    const onChange = (e: Event) => setActive((e as CustomEvent).detail ?? getActiveMotor());
    window.addEventListener('motor:active-changed', onChange);
    return () => window.removeEventListener('motor:active-changed', onChange);
  }, []);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');
  const [err, setErr] = useState<string | null>(null);

  const flashDone = () => { setDone(true); setTimeout(() => setDone(false), 2200); };

  const saveExisting = async () => {
    setBusy(true); setErr(null);
    try { await saveSettingsToActiveMotor(kind); flashDone(); }
    catch (e: any) {
      if (String(e.message).includes('MOTOR_GONE')) { setActive(null); setErr('Motor no longer exists'); }
      else setErr(String(e.message ?? e));
    } finally { setBusy(false); }
  };

  const createNew = async () => {
    const nm = name.trim();
    if (!nm) return;
    setBusy(true); setErr(null);
    try {
      const m = await createMotorFromCurrent(nm);
      setActive(m); setNaming(false); setName(''); flashDone();
    } catch (e: any) { setErr(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  const icon = busy ? <CircularProgress size={13} />
    : done ? <CheckIcon sx={{ fontSize: 15 }} />
    : <SaveIcon sx={{ fontSize: 15 }} />;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      {active ? (
        <Tooltip title={`Save the current ${LABEL[kind]} settings onto the loaded motor "${active.name}". Reloading it from the Motors tab restores them.`} placement="top">
          <span>
            <Button size="small" variant="outlined" fullWidth disableElevation
              startIcon={icon} disabled={busy || disabled}
              onClick={saveExisting}
              sx={{ textTransform: 'none', fontSize: 11, borderColor: '#334155',
                color: done ? '#34d399' : '#a5b4fc',
                '&:hover': { borderColor: '#6366f1', bgcolor: '#1e1b4b' } }}>
              {done ? 'Saved ✓' : `Save ${LABEL[kind]} → ${active.name}`}
            </Button>
          </span>
        </Tooltip>
      ) : naming ? (
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          <TextField size="small" autoFocus placeholder="New motor name…"
            value={name} onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') createNew(); if (e.key === 'Escape') setNaming(false); }}
            inputProps={{ style: { padding: '4px 8px', fontSize: 11, color: '#e2e8f0' } }}
            sx={{ flex: 1, '& .MuiOutlinedInput-root': { bgcolor: '#0f172a',
              '& fieldset': { borderColor: '#334155' } } }} />
          <Button size="small" variant="contained" disableElevation
            disabled={busy || !name.trim()} onClick={createNew}
            sx={{ textTransform: 'none', fontSize: 11, bgcolor: '#4f46e5',
              '&:hover': { bgcolor: '#6366f1' } }}>
            {busy ? <CircularProgress size={13} /> : 'Create'}
          </Button>
        </Box>
      ) : (
        <Tooltip title={`No motor is loaded. Save the current geometry + mesh + simulation as a NEW motor (it appears in Motors → catalog).`} placement="top">
          <span>
            <Button size="small" variant="outlined" fullWidth disableElevation
              startIcon={done ? <CheckIcon sx={{ fontSize: 15 }} /> : <SaveIcon sx={{ fontSize: 15 }} />}
              disabled={disabled} onClick={() => setNaming(true)}
              sx={{ textTransform: 'none', fontSize: 11, borderColor: '#334155',
                color: done ? '#34d399' : '#94a3b8',
                '&:hover': { borderColor: '#6366f1', bgcolor: '#1e1b4b' } }}>
              {done ? 'Saved ✓' : 'Save as new motor…'}
            </Button>
          </span>
        </Tooltip>
      )}
      {err && <Box sx={{ fontSize: 10, color: '#f87171' }}>{err}</Box>}
    </Box>
  );
};

export default SaveToMotorButton;
