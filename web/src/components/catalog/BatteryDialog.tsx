/**
 * BatteryDialog — "Battery & voltage match": chemistry preset (NMC/LiFePO₄),
 * series cell count and per-cell min/nom/max voltage; the pack range is
 * derived live.  Saves through PATCH /api/family/config/{die}/{cfg}/battery.
 */
import React, { useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField,
  ToggleButton, ToggleButtonGroup, Box, Typography,
} from '@mui/material';

export interface BatteryValue {
  chemistry?: string | null; cells?: number | null;
  v_cell_min?: number; v_cell_nom?: number | null; v_cell_max?: number;
  v_min: number; v_max: number; v_nom?: number | null;
}

const CHEMISTRY: Record<string, { min: number; nom: number; max: number }> = {
  NMC:     { min: 3.0, nom: 3.7, max: 4.2 },
  LiFePO4: { min: 2.5, nom: 3.2, max: 3.65 },
};

const num = (s: string) => { const n = parseFloat(s.replace(',', '.')); return Number.isFinite(n) ? n : NaN; };

const BatteryDialog: React.FC<{
  open: boolean;
  configName: string;
  initial?: BatteryValue | null;
  onClose: () => void;
  onSave: (v: { chemistry: string; cells: number;
                v_cell_min: number; v_cell_nom: number; v_cell_max: number }) => void;
}> = ({ open, configName, initial, onClose, onSave }) => {
  const [chem, setChem] = useState('NMC');
  const [cells, setCells] = useState('200');
  const [vmin, setVmin] = useState('3.0');
  const [vnom, setVnom] = useState('3.7');
  const [vmax, setVmax] = useState('4.2');

  useEffect(() => {
    if (!open) return;
    const b = initial;
    setChem(b?.chemistry || 'NMC');
    setCells(String(b?.cells ?? 200));
    setVmin(String(b?.v_cell_min ?? CHEMISTRY.NMC.min));
    setVnom(String(b?.v_cell_nom ?? CHEMISTRY.NMC.nom));
    setVmax(String(b?.v_cell_max ?? CHEMISTRY.NMC.max));
  }, [open, initial]);

  const pickChem = (c: string) => {
    setChem(c);
    const p = CHEMISTRY[c];
    if (p) { setVmin(String(p.min)); setVnom(String(p.nom)); setVmax(String(p.max)); }
  };

  const n = Math.round(num(cells));
  const lo = num(vmin), no = num(vnom), hi = num(vmax);
  const valid = n >= 1 && lo > 0 && hi >= lo && (Number.isNaN(no) || (no >= lo && no <= hi));
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
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}
          sx={{ textTransform: 'none', color: 'var(--text-2)' }}>Cancel</Button>
        <Button variant="contained" disabled={!valid}
          onClick={() => onSave({ chemistry: chem, cells: n, v_cell_min: lo,
                                  v_cell_nom: Number.isNaN(no) ? (lo + hi) / 2 : no,
                                  v_cell_max: hi })}
          sx={{ textTransform: 'none' }}>Save</Button>
      </DialogActions>
    </Dialog>
  );
};

export default BatteryDialog;
