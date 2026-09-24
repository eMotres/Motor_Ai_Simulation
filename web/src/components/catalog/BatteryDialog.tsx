/**
 * BatteryDialog — "Battery & voltage match": chemistry preset (NMC/LiFePO₄),
 * series cell count and per-cell min/nom/max voltage; the pack range is
 * derived live.  Saves through PATCH /api/family/config/{die}/{cfg}/battery.
 *
 * CHARGE SIDE (2026-09-01).  Voltages alone answer "does the inverter have
 * headroom?".  Running the machine as a GENERATOR into this pack asks how many
 * amps go in, at what C-rate, and how far the bus rises while they do — and
 * every one of those is an internal resistance or a capacity.  Those four
 * fields are therefore first-class here, PRE-FILLED WITH PLACEHOLDERS and
 * labelled as placeholders, because nobody has measured this pack yet.
 */
import React, { useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField,
  ToggleButton, ToggleButtonGroup, Box, Typography, Tooltip, Checkbox,
  FormControlLabel,
} from '@mui/material';
import HelpTip from '../common/HelpTip';

export interface BatteryValue {
  chemistry?: string | null; cells?: number | null;
  v_cell_min?: number; v_cell_nom?: number | null; v_cell_max?: number;
  v_min: number; v_max: number; v_nom?: number | null;
  // charge side — absent on every pack saved before this existed
  n_parallel?: number | null;
  r_int_mohm?: number | null;
  capacity_ah?: number | null;
  i_charge_max_a?: number | null;
}

const CHEMISTRY: Record<string, { min: number; nom: number; max: number;
                                  r_int: number }> = {
  // r_int: PLACEHOLDER per-cell DC internal resistance [mΩ] — the same
  // order-of-magnitude figures simulation/battery.py carries, mirrored here so
  // the field is never blank and never silently zero.  Replace with the
  // measured DC-IR of the actual cell.
  NMC:     { min: 3.0, nom: 3.7, max: 4.2,  r_int: 12 },
  LiFePO4: { min: 2.5, nom: 3.2, max: 3.65, r_int: 8 },
};

const num = (s: string) => { const n = parseFloat(s.replace(',', '.')); return Number.isFinite(n) ? n : NaN; };

const BatteryDialog: React.FC<{
  open: boolean;
  configName: string;
  initial?: BatteryValue | null;
  onClose: () => void;
  // The charge-side fields are OPTIONAL in what this hands back, and they are
  // omitted while they are still showing a placeholder.  Saving a placeholder
  // would launder it: the yaml would then hold 12 mΩ as if someone had
  // measured 12 mΩ, and the card would stop marking it.  Untouched fields stay
  // unset, and the backend keeps filling them from the chemistry table with
  // the "placeholder" flag attached.
  onSave: (v: { chemistry: string; cells: number;
                v_cell_min: number; v_cell_nom: number; v_cell_max: number;
                n_parallel?: number; r_int_mohm?: number;
                capacity_ah?: number; i_charge_max_a?: number }) => void;
}> = ({ open, configName, initial, onClose, onSave }) => {
  // STORE THE PLACEHOLDERS ANYWAY (user 2026-09-11: "надо тогда предупреждать,
  // почему не сохранилось, или дать возможность перезаписать несмотря ни на
  // что").  Both, in the end: the row below names the fields that will be
  // skipped, and this makes them go anyway.  Off by default — a guess that
  // enters the yaml is indistinguishable from a measurement ever after, and
  // that asymmetry is why the skipping existed at all.
  const [forceStore, setForceStore] = useState(false);
  const [chem, setChem] = useState('NMC');
  const [cells, setCells] = useState('200');
  const [vmin, setVmin] = useState('3.0');
  const [vnom, setVnom] = useState('3.7');
  const [vmax, setVmax] = useState('4.2');
  const [npar, setNpar] = useState('1');
  const [rint, setRint] = useState('12');
  const [cap, setCap] = useState('10');
  const [ichg, setIchg] = useState('10');
  // Which charge-side fields the STORED pack never carried — i.e. the ones
  // showing a placeholder rather than something the user typed.  Shown next to
  // the field, because a placeholder printed like a measurement is exactly the
  // substitution this project refuses everywhere else.
  const [ph, setPh] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!open) return;
    const b = initial;
    const c = b?.chemistry || 'NMC';
    setChem(c);
    setCells(String(b?.cells ?? 200));
    setVmin(String(b?.v_cell_min ?? CHEMISTRY.NMC.min));
    setVnom(String(b?.v_cell_nom ?? CHEMISTRY.NMC.nom));
    setVmax(String(b?.v_cell_max ?? CHEMISTRY.NMC.max));
    const rDef = (CHEMISTRY[c] ?? CHEMISTRY.NMC).r_int;
    const capDef = b?.capacity_ah ?? 10;
    setNpar(String(b?.n_parallel ?? 1));
    setRint(String(b?.r_int_mohm ?? rDef));
    setCap(String(capDef));
    setIchg(String(b?.i_charge_max_a ?? capDef * (b?.n_parallel ?? 1)));
    setPh({
      n_parallel: b?.n_parallel == null,
      r_int_mohm: b?.r_int_mohm == null,
      capacity_ah: b?.capacity_ah == null,
      i_charge_max_a: b?.i_charge_max_a == null,
    });
  }, [open, initial]);

  const pickChem = (c: string) => {
    setChem(c);
    const p = CHEMISTRY[c];
    if (p) {
      setVmin(String(p.min)); setVnom(String(p.nom)); setVmax(String(p.max));
      // Only overwrite r_int while it is still a placeholder — a measured
      // value must survive a chemistry click.
      if (ph.r_int_mohm) setRint(String(p.r_int));
    }
  };

  const n = Math.round(num(cells));
  const lo = num(vmin), no = num(vnom), hi = num(vmax);
  const np = Math.round(num(npar));
  const ri = num(rint), ca = num(cap), ic = num(ichg);
  const rPack = (n >= 1 && np >= 1 && ri >= 0) ? (n * ri * 1e-3) / np : NaN;
  const valid = n >= 1 && lo > 0 && hi >= lo && (Number.isNaN(no) || (no >= lo && no <= hi))
    && np >= 1 && ri >= 0 && ca >= 0 && ic >= 0;
  const packLine = valid
    ? `Pack: ${(n * lo).toFixed(0)}–${(n * hi).toFixed(0)} V`
      + (Number.isNaN(no) ? '' : ` · nominal ${(n * no).toFixed(0)} V`)
    : 'enter cells and a sane min ≤ nom ≤ max';

  const cellSx = { width: 84, '& .MuiInputBase-input': { fontSize: 13, py: 0.7 } };

  return (
    <Dialog open={open} onClose={onClose}
      PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
                          borderRadius: 2, minWidth: 480 } }}>
      <DialogTitle sx={{ color: 'var(--text-0)', fontSize: '0.95rem', fontWeight: 700 }}>
        🔋 Battery & voltage match — {configName}
      </DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 1, flexWrap: 'wrap' }}>
          <ToggleButtonGroup exclusive size="small" value={chem}
            onChange={(_, v) => v && pickChem(v)}>
            <ToggleButton value="NMC" sx={{ px: 1.2, textTransform: 'none' }}>NMC</ToggleButton>
            <ToggleButton value="LiFePO4" sx={{ px: 1.2, textTransform: 'none' }}>LiFePO₄</ToggleButton>
          </ToggleButtonGroup>
          <TextField size="small" label="Cells (series)" value={cells}
            onChange={(e) => setCells(e.target.value)} sx={{ width: 110 }} />
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)' }}>
            CELL V
          </Typography>
          <TextField size="small" label="min" value={vmin}
            onChange={(e) => setVmin(e.target.value)} sx={cellSx} />
          <TextField size="small" label="nom" value={vnom}
            onChange={(e) => setVnom(e.target.value)} sx={cellSx} />
          <TextField size="small" label="max" value={vmax}
            onChange={(e) => setVmax(e.target.value)} sx={cellSx} />
        </Box>
        <Typography sx={{ fontSize: 11.5, mt: 1.5,
          color: valid ? 'var(--text-2)' : '#fca5a5' }}>
          {packLine}
        </Typography>

        {/* ── CHARGE SIDE ────────────────────────────────────────────────
            What the machine needs when it RUNS AS A GENERATOR into this pack:
            V_bus = V_oc + I_charge·R_pack, R_pack = NS·r_int/NP.  Every field
            here is pre-filled with a placeholder and says so until it is
            edited. */}
        <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)',
          mt: 2, mb: 0.75 }}>
          CHARGE SIDE — for running this machine as a generator into the pack
        </Typography>
        {/* WHY A NUMBER IN THE BOX CAN COME BACK (user 2026-09-11: "батарея не
            сохраняется" — the pack had saved; these four had not).  A field
            still showing a placeholder is deliberately NOT sent: a guessed
            12 mOhm written to the yaml would read as a measured one ever after.
            The rule was in the code and in a tooltip; it was not on the screen,
            where the boxes plainly showed numbers. */}
        {(() => {
          const skipped = ([
            ['n_parallel', 'strings'], ['r_int_mohm', 'r_int'],
            ['capacity_ah', 'capacity'], ['i_charge_max_a', 'I charge max'],
          ] as const).filter(([k]) => (ph as any)[k]).map(([, l]) => l);
          if (!skipped.length) return null;
          return (
            <Box sx={{ mb: 0.75 }}>
              <Typography sx={{ fontSize: 11, color: '#f59e0b' }}>
                {skipped.join(', ')} {skipped.length > 1 ? 'are' : 'is'} still a
                placeholder{skipped.length > 1 ? 's' : ''} and will NOT be stored
                on Save — type in the field (even the same number) to make it this
                pack's own value.
              </Typography>
              <Tooltip placement="top" title="Writes the pre-filled numbers into this configuration as if they were this pack's own. Nothing downstream can tell them from a measurement afterwards — the charging card and the report stop marking them as assumed. Use it when the placeholder happens to BE the right number, not to silence the notice.">
                <FormControlLabel
                  control={<Checkbox size="small" checked={forceStore}
                    onChange={(e) => setForceStore(e.target.checked)}
                    sx={{ py: 0.25 }} />}
                  label={'store them anyway, as this pack\u2019s own values'}
                  sx={{ m: 0, '& .MuiFormControlLabel-label': { fontSize: 11,
                        color: forceStore ? '#f59e0b' : 'var(--text-3)' } }} />
              </Tooltip>
            </Box>
          );
        })()}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <Tooltip placement="top" title="Parallel strings (NP). Pack resistance is NS·r_int/NP, so strings in parallel divide it.">
            <TextField size="small" label="Strings (parallel)" value={npar}
              onChange={(e) => { setNpar(e.target.value); setPh(p => ({ ...p, n_parallel: false })); }}
              helperText={ph.n_parallel ? (forceStore ? 'assumed — will be stored' : 'assumed — not saved') : ' '}
              sx={{ width: 128, '& .MuiFormHelperText-root': { fontSize: 9.5, color: '#f59e0b', mx: 0 } }} />
          </Tooltip>
          <Tooltip placement="top" title="DC internal resistance PER CELL. The pack's R is NS·r_int/NP and it is what lifts the bus while charging. The pre-filled figure is a datasheet-range placeholder for the chemistry, NOT a measurement of this pack — it also ignores wiring, fuse, connector and shunt resistance, which on a small pack are a real fraction of the cells' own. Replace it with the measured DC-IR.">
            <TextField size="small" label="r_int (mΩ/cell)" value={rint}
              onChange={(e) => { setRint(e.target.value); setPh(p => ({ ...p, r_int_mohm: false })); }}
              helperText={ph.r_int_mohm ? (forceStore ? 'placeholder — will be stored' : 'placeholder — not saved') : ' '}
              sx={{ width: 128, '& .MuiFormHelperText-root': { fontSize: 9.5, color: '#f59e0b', mx: 0 } }} />
          </Tooltip>
          <Tooltip placement="top" title="Capacity per string [Ah]. Only used to quote a C-rate — there is no honest guess for it from a voltage spec, so the pre-filled value is a bare placeholder.">
            <TextField size="small" label="Capacity (Ah)" value={cap}
              onChange={(e) => {
                setCap(e.target.value);
                setPh(p => ({ ...p, capacity_ah: false }));
                if (ph.i_charge_max_a) {
                  const c2 = num(e.target.value);
                  if (Number.isFinite(c2)) setIchg(String(c2 * Math.max(1, np)));
                }
              }}
              helperText={ph.capacity_ah ? (forceStore ? 'placeholder — will be stored' : 'placeholder — not saved') : ' '}
              sx={{ width: 128, '& .MuiFormHelperText-root': { fontSize: 9.5, color: '#f59e0b', mx: 0 } }} />
          </Tooltip>
          <Tooltip placement="top" title="Maximum charge current the pack will accept [A]. Defaults to 1 C of the capacity above. The charging card flags a run that exceeds it; it is not enforced on the solve — the machine makes what it makes, and clamping it silently would hide the problem.">
            <TextField size="small" label="I charge max (A)" value={ichg}
              onChange={(e) => { setIchg(e.target.value); setPh(p => ({ ...p, i_charge_max_a: false })); }}
              helperText={ph.i_charge_max_a ? (forceStore ? '1 C — will be stored' : '1 C — not saved') : ' '}
              sx={{ width: 138, '& .MuiFormHelperText-root': { fontSize: 9.5, color: '#f59e0b', mx: 0 } }} />
          </Tooltip>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1 }}>
          <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>
            {Number.isFinite(rPack)
              ? `Pack R ≈ ${(rPack * 1000).toFixed(1)} mΩ`
                + (Number.isFinite(ic) && ic > 0
                    ? ` · at ${ic.toFixed(0)} A the bus rises ${(rPack * ic).toFixed(2)} V above open circuit`
                    : '')
              : 'enter a sane cell count, string count and r_int'}
          </Typography>
          <HelpTip title="No state-of-charge model: V_oc is taken as the pack nominal, so a charge SESSION (V_oc walking from min to max) is a sweep of this, not a property of it." />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}
          sx={{ textTransform: 'none', color: 'var(--text-2)' }}>Cancel</Button>
        <Button variant="contained" disabled={!valid}
          onClick={() => onSave({ chemistry: chem, cells: n, v_cell_min: lo,
                                  v_cell_nom: Number.isNaN(no) ? (lo + hi) / 2 : no,
                                  v_cell_max: hi,
                                  // placeholders are NOT saved — see onSave
                                  ...(ph.n_parallel && !forceStore ? {} : { n_parallel: np }),
                                  ...(ph.r_int_mohm && !forceStore ? {} : { r_int_mohm: ri }),
                                  ...(ph.capacity_ah && !forceStore ? {} : { capacity_ah: ca }),
                                  ...(ph.i_charge_max_a && !forceStore ? {} : { i_charge_max_a: ic }) })}
          sx={{ textTransform: 'none' }}>Save</Button>
      </DialogActions>
    </Dialog>
  );
};

export default BatteryDialog;
