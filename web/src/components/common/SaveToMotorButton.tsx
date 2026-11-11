// Shows the active "working motor" (everything auto-saves into it) and a
// "Save as new motor…" button to fork the current setup into a separate named
// motor (which appears in the Motors tab).
import React, { useEffect, useState } from 'react';
import { Box, Button, TextField, Tooltip, CircularProgress } from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import CheckIcon from '@mui/icons-material/Check';
import BoltIcon from '@mui/icons-material/Bolt';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import { getActiveMotor, createMotorFromCurrent, overwriteActiveMotor } from './motorSettings';
import { activeDuty } from '../../lib/dutySettings';
import HelpTip from './HelpTip';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';

// UI strings: locales/<lng>/geometry.json (docs/I18N.md).
const tx = nsT('geometry');

interface Props { disabled?: boolean; }

const SaveToMotorButton: React.FC<Props> = ({ disabled }) => {
  useTranslation('geometry'); // re-render on language change; lazy-loads the namespace
  const [active, setActive] = useState(() => getActiveMotor());
  useEffect(() => {
    const on = (e: Event) => setActive((e as CustomEvent).detail ?? getActiveMotor());
    window.addEventListener('motor:active-changed', on);
    return () => window.removeEventListener('motor:active-changed', on);
  }, []);
  // A CATALOG motor on the panel (owner 2026-11-11, phone: "Motor: My motor · auto-saved" and a big "Overwrite this
  // motor" while CIANO14 40 new / L12 was loaded).  The two buttons below act on the account's own working preset
  // ("My motor", /api/presets) — never on the catalog configuration — so naming that preset here, with an Overwrite
  // next to it, read as "this overwrites the catalog motor".  With a catalog point loaded the panel names the catalog
  // motor, offers "Save as new…" (a private copy) only, and leaves catalog saves to the header's explicit
  // "Save to <point>" (admins / owners).
  const [catalog, setCatalog] = useState(() => activeDuty());
  useEffect(() => {
    const on = () => setCatalog(activeDuty());
    for (const ev of ['sim-settings-restored', 'family-changed', 'motor:active-changed']) window.addEventListener(ev, on);
    return () => { for (const ev of ['sim-settings-restored', 'family-changed', 'motor:active-changed']) window.removeEventListener(ev, on); };
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

  // Someone else's motor (or a locked one) is loaded as a WORKING COPY: the
  // editor drives it normally, but nothing auto-saves into it. ONE short chip
  // says so; the owner's name lives in the tip, not in a paragraph on the panel.
  const ro = !!active?.readOnly;
  // the working preset is not what is on screen: no Overwrite, no "auto-saved" claim
  const showOverwrite = !!active && !ro && !catalog;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      {catalog ? (
        <Tooltip title={tx('catalogMotorTip')} placement="top">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, fontSize: 11, color: 'var(--text-2)', minWidth: 0 }}
            data-testid="motor-chip-catalog">
            <LockOutlinedIcon sx={{ fontSize: 13, color: '#fbbf24' }} />
            <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {tx('motorLabel')}&nbsp;<b style={{ color: 'var(--text-0)' }}>{catalog.die} / {catalog.config}</b>
            </span>
            <span style={{ color: 'var(--text-4)', whiteSpace: 'nowrap' }}>· {tx('catalogMotor')}</span>
          </Box>
        </Tooltip>
      ) : active && (ro ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, fontSize: 11, color: 'var(--text-2)' }}>
          <LockOutlinedIcon sx={{ fontSize: 13, color: '#fbbf24' }} />
          <span>Motor:&nbsp;<b style={{ color: 'var(--text-0)' }}>{active.name}</b></span>
          <span style={{ color: '#fbbf24' }}>· read-only — working copy</span>
          <HelpTip title={`${active.locked
            ? 'This motor is locked by an admin'
            : `This motor belongs to ${active.owner ?? 'another account'}`}, so your edits are NOT auto-saved into it. Tune it freely here, then "Save as new…" to keep the result as your own motor.`} />
        </Box>
      ) : (
        <Tooltip title={`Every geometry / mesh / simulation change auto-saves into "${active.name}". Use "Save as new motor" to keep a separate named copy in the Motors tab.`} placement="top">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, fontSize: 11, color: 'var(--text-2)' }}>
            <BoltIcon sx={{ fontSize: 13, color: '#34d399' }} />
            <span>Motor:&nbsp;<b style={{ color: 'var(--text-0)' }}>{active.name}</b></span>
            <span style={{ color: 'var(--text-4)' }}>· auto-saved</span>
          </Box>
        </Tooltip>
      ))}
      {naming ? (
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          <TextField size="small" autoFocus placeholder={tx('newMotorName')} value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') create(); if (e.key === 'Escape') setNaming(false); }}
            inputProps={{ style: { padding: '4px 8px', fontSize: 11, color: 'var(--text-0)' } }}
            sx={{ flex: 1, '& .MuiOutlinedInput-root': { bgcolor: 'var(--app-bg)', '& fieldset': { borderColor: 'var(--line)' } } }} />
          <Button size="small" variant="contained" disableElevation disabled={busy || !name.trim()} onClick={create}
            sx={{ textTransform: 'none', fontSize: 11, bgcolor: '#4f46e5', '&:hover': { bgcolor: '#6366f1' } }}>
            {busy ? <CircularProgress size={13} /> : 'Create'}
          </Button>
        </Box>
      ) : (
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          {showOverwrite && (
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
            sx={{ flex: showOverwrite ? 'unset' : 1, whiteSpace: 'nowrap', textTransform: 'none', fontSize: 11, borderColor: 'var(--line)',
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
