/**
 * CoolingControls — the cooling-system inputs of the CONFIGURE tab.
 *
 *   • Air                        → air temp + air speed; h(v) computed LIVE
 *                                  (Churchill-Bernstein, cylinder cross-flow).
 *   • Water / Water-glycol / Oil → inlet temp + flow (L/min); jacket h shown.
 *
 * Moved here from `simulation/CoolingControls` on 2026-09-07, when the thermal
 * solve left the Electromagnetic tab: this widget is thermal input, and its only
 * remaining host is `compare/ConfiguratorThermal`.  The localStorage keys are
 * UNCHANGED (`sim.cool.*`, read through `readCool`/`lsSetCool` in `./api`) —
 * they are the saved cooling system of every existing user, and renaming them
 * would silently reset all of them to the defaults.
 *
 * The Thermal TAB does not use this component: it carries its own cooling
 * controls (with a manual-h mode and an outlet temperature the jacket law here
 * has no concept of) in `ThermalPanel`.
 */
import React, { useState } from 'react';
import { Box, Typography, TextField, MenuItem, Chip } from '@mui/material';

import HelpTip from './HelpTip';
import { useMotorStore } from '../../stores/motorStore';
import { airH, liqH, lsSetCool, readCool } from './api';

const CoolingControls: React.FC<{ diameterMm?: number }> = ({ diameterMm }) => {
  const geometry = useMotorStore((s) => s.geometry as Record<string, unknown> | null);
  const [sys, setSys] = useState<string>(() => readCool('mode', 'Air'));
  const [temp, setTemp] = useState<number>(() => parseFloat(readCool('temp', '25')) || 25);
  const [airSpeed, setAirSpeed] = useState<number>(() => parseFloat(readCool('air', '8')) || 8);
  const [flow, setFlow] = useState<number>(() => parseFloat(readCool('flow', '8')) || 8);

  const isAir = sys === 'Air';
  const D = (diameterMm ?? (Number(geometry?.stator_diameter) || 150)) / 1000;  // housing OD [m]
  const h = isAir ? airH(airSpeed, D) : liqH(flow);
  // The Configure tab's thermal card recomputes off localStorage, so a change
  // has to announce itself — it does not share React state with this widget.
  const changed = () => { try { window.dispatchEvent(new Event('cooling:changed')); } catch { /* ignore */ } };
  const onSys = (v: string) => { setSys(v); lsSetCool('mode', v); changed(); };
  const onTemp = (v: number) => { setTemp(v); lsSetCool('temp', String(v)); changed(); };
  const onAir = (v: number) => { setAirSpeed(v); lsSetCool('air', String(v)); changed(); };
  const onFlow = (v: number) => { setFlow(v); lsSetCool('flow', String(v)); changed(); };

  return (
    <Box sx={{ mt: 1.5 }}>
      {/* The hint sits on the ⓘ, never round the select: a tooltip's popper is
          drawn above the menu and takes the pointer, which is what stopped the
          duty-cycle editor's picker from opening at all (2026-09-15). */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.375, mb: 0.75 }}>
        <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', letterSpacing: 0.5 }}>
          COOLING — outer stator surface
        </Typography>
        <HelpTip title="Air: h from the air speed. Liquid: inlet temperature + flow → the jacket's h." />
      </Box>
      <TextField select size="small" fullWidth label="System" value={sys} onChange={(e) => onSys(e.target.value)}
        sx={{ mb: 1, '& .MuiSelect-select': { fontSize: 13 } }}>
        {['Air', 'Water', 'Water-glycol', 'Oil'].map((o) => (
          <MenuItem key={o} value={o} sx={{ fontSize: 13 }}>{o}</MenuItem>
        ))}
      </TextField>
      <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
        <TextField size="small" type="number" label={isAir ? 'Air temp °C' : 'Inlet °C'} value={temp}
          onChange={(e) => onTemp(parseFloat(e.target.value) || 0)} sx={{ flex: 1 }} />
        {isAir ? (
          <TextField size="small" type="number" label="Air speed m/s" value={airSpeed}
            onChange={(e) => onAir(parseFloat(e.target.value) || 0)} sx={{ flex: 1 }} />
        ) : (
          <TextField size="small" type="number" label="Flow L/min" value={flow}
            onChange={(e) => onFlow(parseFloat(e.target.value) || 0)} sx={{ flex: 1 }} />
        )}
      </Box>
      <Chip size="small" label={`h ≈ ${h.toFixed(0)} W/m²K  ${isAir ? (h > 7 ? '· forced' : '· natural') : '· jacket'}`}
        sx={{ width: '100%', justifyContent: 'flex-start', fontFamily: 'monospace', fontWeight: 700,
          bgcolor: 'var(--line-accent)', color: 'var(--brand)' }} />
    </Box>
  );
};

export default CoolingControls;
