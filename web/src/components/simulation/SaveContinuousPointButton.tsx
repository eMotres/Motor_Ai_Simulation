/**
 * "Save continuous point as duty" (owner 2026-10-19) — beside the S1 line when
 * a coupled run found a CONTINUOUS POINT (no rating at the duty's own speed,
 * the highest speed that holds instead).  Nothing changes silently: a dialog
 * names the new duty and, when it takes the loaded duty's name, what the
 * loaded duty becomes ("rated" → "rated_short_time"), and only "Save" sends.
 * Writers only; the point must not be one its real pass refused.
 */
import { useEffect, useState } from 'react';
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField,
         Typography } from '@mui/material';
import type { CouplingBlock } from './coupledApi';
import { continuousSavePlan, type DutyClassInfo } from '../../lib/dutyClass';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface Ctx {
  active?: boolean; die?: string; config?: string; duty?: string | null;
  can_write?: boolean; duty_classes?: Record<string, DutyClassInfo>;
}

export default function SaveContinuousPointButton(
    { coupling }: { coupling: CouplingBlock | null | undefined }) {
  const cp = coupling?.continuous_rating?.continuous_point;
  const usable = !!(coupling?.continuous_rating?.feasible === false && cp?.feasible
                    && cp.rpm != null && cp.I_cont_A_rms != null
                    && cp.verified !== false);
  const [ctx, setCtx] = useState<Ctx | null>(null);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!usable) return;
    let gone = false;
    fetch(`${API}/api/family/context`).then(r => (r.ok ? r.json() : null))
      .then(j => { if (!gone) setCtx(j); }).catch(() => { /* no button */ });
    return () => { gone = true; };
  }, [usable, cp?.rpm, cp?.I_cont_A_rms]);

  if (!usable || !ctx?.active || !ctx.can_write || !ctx.duty || !ctx.die || !ctx.config) {
    return null;
  }
  const duty = ctx.duty;
  const info = ctx.duty_classes?.[duty] ?? null;
  const plan = continuousSavePlan(duty, info, name);

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch(`${API}/api/family/duty/${encodeURIComponent(ctx.die!)}/`
        + `${encodeURIComponent(ctx.config!)}/${encodeURIComponent(duty)}/save_continuous_point`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: plan.name,
                               rename_source_to: plan.renameSourceTo }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j?.detail ?? `HTTP ${r.status}`);
      setOpen(false);
      setMsg(`Saved as '${j.duty}'`
        + (j.renamed_source_to ? ` — '${duty}' is now '${j.renamed_source_to}'` : ''));
      window.dispatchEvent(new CustomEvent('family-changed'));
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button size="small" variant="outlined" sx={{ ml: 1, whiteSpace: 'nowrap' }}
        onClick={() => { setName(continuousSavePlan(duty, info).name); setOpen(true); }}>
        Save continuous point as duty
      </Button>
      {msg && <Typography component="span" sx={{ ml: 1, fontSize: 11 }}>{msg}</Typography>}
      <Dialog open={open} onClose={() => !busy && setOpen(false)}>
        <DialogTitle>Save continuous point as duty</DialogTitle>
        <DialogContent>
          <Typography sx={{ fontSize: 13, mb: 1 }}>
            {`${Math.round(cp!.rpm!)} rpm, ${cp!.I_cont_A_rms!.toFixed(1)} A rms, `
              + `γ and cooling of '${duty}'`}
          </Typography>
          <TextField autoFocus fullWidth size="small" label="Duty name" value={name}
            onChange={(e) => setName(e.target.value)} disabled={busy}/>
          {plan.renameSourceTo && (
            <Typography sx={{ fontSize: 12, mt: 1, color: '#f59e0b' }}>
              {`'${duty}' will be renamed '${plan.renameSourceTo}' (short-time); its results move with it.`}
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
          <Button variant="contained" onClick={save} disabled={busy || !plan.name}>Save</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
