// Admin · per-user Motors dialog — which catalog motors an account may open, and
// which die + configuration Configure opens on for him (the DEFAULT motor).
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Button, Dialog, DialogTitle, DialogContent, DialogActions, FormControl, InputLabel,
  MenuItem, Select, Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../common/HelpTip';
import { CardBadge } from '../../common/CardBadge';
import { MotorPicker, useCatalog, type CatalogDie, type MotorGrants } from './MotorPicker';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export interface RegistryUser {
  email: string; role: string; name: string; disabled: boolean; created?: string | null;
  motors?: MotorGrants;
}

/** The dies a default may name: every die for an "all" grant, else the picked ones. */
const defaultChoices = (dies: CatalogDie[] | null, all: boolean, picked: Set<string>): CatalogDie[] =>
  (dies ?? []).filter((d) => all || picked.has(d.name));

const MotorsDialog: React.FC<{
  user: RegistryUser | null; onClose: () => void;
  onSaved: (email: string, motors: MotorGrants) => void;
}> = ({ user, onClose, onSaved }) => {
  const { t } = useTranslation('admin');
  const dies = useCatalog(!!user);
  const [all, setAll] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [defDie, setDefDie] = useState('');
  const [defCfg, setDefCfg] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!user) return;
    setErr(null);
    setAll(user.motors?.all === true);
    setPicked(new Set(user.motors?.dies ?? []));
    setDefDie(user.motors?.default?.die ?? '');
    setDefCfg(user.motors?.default?.config ?? '');
  }, [user]);

  const toggle = (name: string) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(name)) n.delete(name); else n.add(name);
    return n;
  });

  // Only granted dies can be the default; dropping the grant drops the default
  // (the server does the same - this keeps the dropdowns honest while editing).
  const choices = useMemo(() => defaultChoices(dies, all, picked), [dies, all, picked]);
  const dieRow = choices.find((d) => d.name === defDie) ?? null;
  useEffect(() => {
    if (dies === null) return;
    if (defDie && !dieRow) { setDefDie(''); setDefCfg(''); }
    else if (dieRow && defCfg && !(dieRow.config_names ?? []).includes(defCfg)) setDefCfg('');
  }, [dies, defDie, defCfg, dieRow]);

  const save = async () => {
    if (!user || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/admin/users/${encodeURIComponent(user.email)}/motors`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          all, dies: [...picked],
          default: defDie && defCfg ? { die: defDie, config: defCfg } : null,
        }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(j.detail ?? `HTTP ${r.status}`); return; }
      onSaved(user.email, j.motors as MotorGrants);
      onClose();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  return (
    <Dialog open={!!user} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem', display: 'flex', alignItems: 'center', gap: 0.75 }}>
        {t('motors.title', { email: user?.email ?? '' })}
        <HelpTip title={t('motors.titleTip')} />
      </DialogTitle>
      <DialogContent sx={{ pt: '8px !important' }}>
        <MotorPicker dies={dies} all={all} picked={picked} onAll={setAll} onToggle={toggle} />

        <Box sx={{ mt: 2.5, display: 'flex', alignItems: 'center', gap: 0.75 }}>
          <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-1)' }}>
            {t('motors.defaultTitle')}
          </Typography>
          <HelpTip title={t('motors.defaultTip')} />
          {dieRow && defCfg && <CardBadge date={dieRow.cards?.[defCfg]} />}
        </Box>
        <Box sx={{ mt: 1, display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
          <FormControl size="small" sx={{ minWidth: 200, flex: 1 }}>
            <InputLabel>{t('motors.die')}</InputLabel>
            <Select label={t('motors.die')} value={defDie}
              onChange={(e) => { setDefDie(String(e.target.value)); setDefCfg(''); }}>
              <MenuItem value=""><em>{t('motors.none')}</em></MenuItem>
              {choices.map((d) => <MenuItem key={d.name} value={d.name}>{d.name}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 140 }} disabled={!dieRow}>
            <InputLabel>{t('motors.config')}</InputLabel>
            <Select label={t('motors.config')} value={defCfg}
              onChange={(e) => setDefCfg(String(e.target.value))}>
              {(dieRow?.config_names ?? []).map((c) => (
                <MenuItem key={c} value={c} sx={{ display: 'flex', gap: 1 }}>
                  {c} <CardBadge date={dieRow?.cards?.[c]} />
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>
        {err && <Typography variant="caption" color="error" sx={{ display: 'block', mt: 1 }}>{err}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ textTransform: 'none' }}>{t('motors.cancel')}</Button>
        <Button variant="contained" disabled={busy} onClick={() => void save()}
          sx={{ textTransform: 'none' }}>{t('motors.save')}</Button>
      </DialogActions>
    </Dialog>
  );
};

export default MotorsDialog;
export type { MotorGrants };
