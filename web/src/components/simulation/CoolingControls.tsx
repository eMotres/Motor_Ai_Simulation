/**
 * CoolingControls — the cooling-system inputs, shown in the Simulation LEFT panel
 * (next to the operating point).  Cooling acts on the whole outer stator surface.
 *
 *   • Air                      → air temp + air speed; h(v) computed LIVE
 *                                (Churchill-Bernstein, cylinder cross-flow).
 *   • Water / Water-glycol / Oil → inlet temp + flow (L/min); jacket h shown.
 *
 * State persists to localStorage and is read by the coupled-solve panel
 * (CoupledEmThermal) via getCoolingPayload() — so the inputs live here while the
 * "Run coupled solve" button + results stay in the Multiphysics block.
 */
import React, { useState } from 'react';
import { Box, Typography, TextField, MenuItem, Chip, Tooltip } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';

const COOLANTS: Record<string, string> = { Water: 'water', 'Water-glycol': 'water_glycol_50', Oil: 'oil' };

const lsGet = (k: string, d: string): string => { try { return localStorage.getItem('sim.cool.' + k) ?? d; } catch { return d; } };
const lsSet = (k: string, v: string): void => { try { localStorage.setItem('sim.cool.' + k, v); } catch { /* quota */ } };

export function airH(v: number, D: number): number {    // Churchill-Bernstein, cylinder cross-flow
  const nu = 1.56e-5, ka = 0.0263, Pr = 0.707;
  if (!(v > 0) || !(D > 0)) return 7;
  const Re = v * D / nu;
  const Nu = 0.3 + (0.62 * Math.sqrt(Re) * Math.cbrt(Pr)) / Math.pow(1 + Math.pow(0.4 / Pr, 2 / 3), 0.25)
    * Math.pow(1 + Math.pow(Re / 282000, 5 / 8), 4 / 5);
  return Math.max(Nu * ka / D, 7);                       // natural-convection floor
}
export const liqH = (q: number): number => Math.min(8000, Math.max(600, 1800 * Math.pow(Math.max(q, 0.1) / 8, 0.8)));

/** Cooling spec for the thermal / coupled-solve payload (read from localStorage). */
export function getCoolingPayload(): Record<string, number | string> {
  const sys = lsGet('mode', 'Air');
  const isAir = sys === 'Air';
  const t = parseFloat(lsGet('temp', '25')) || 0;
  return {
    cooling_mode: isAir ? 'air' : 'liquid',
    fluid: isAir ? 'water' : (COOLANTS[sys] || 'water'),
    air_speed_mps: parseFloat(lsGet('air', '8')) || 0,
    flow_lpm: parseFloat(lsGet('flow', '8')) || 0,
    ambient_temp: t, fluid_temp_in_c: t,
  };
}

const CoolingControls: React.FC<{ diameterMm?: number }> = ({ diameterMm }) => {
  const geometry = useMotorStore((s: any) => s.geometry);
  const [sys, setSys] = useState<string>(() => lsGet('mode', 'Air'));
  const [temp, setTemp] = useState<number>(() => parseFloat(lsGet('temp', '25')) || 25);
  const [airSpeed, setAirSpeed] = useState<number>(() => parseFloat(lsGet('air', '8')) || 8);
  const [flow, setFlow] = useState<number>(() => parseFloat(lsGet('flow', '8')) || 8);

  const isAir = sys === 'Air';
  const D = (diameterMm ?? (Number(geometry?.stator_diameter) || 150)) / 1000;  // housing OD [m]
  const h = isAir ? airH(airSpeed, D) : liqH(flow);
  const changed = () => { try { window.dispatchEvent(new Event('cooling:changed')); } catch { /* ignore */ } };
  const onSys   = (v: string) => { setSys(v);   lsSet('mode', v); changed(); };
  const onTemp  = (v: number) => { setTemp(v);  lsSet('temp', String(v)); changed(); };
  const onAir   = (v: number) => { setAirSpeed(v); lsSet('air', String(v)); changed(); };
  const onFlow  = (v: number) => { setFlow(v);  lsSet('flow', String(v)); changed(); };

  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', letterSpacing: 0.5, mb: 0.75 }}>
        COOLING — outer stator surface
      </Typography>
      <Tooltip title="Air: h from air speed (cylinder cross-flow). Liquid: inlet temp + flow → outlet temp + jacket h. Used by the coupled EM↔thermal solve." placement="right">
        <TextField select size="small" fullWidth label="System" value={sys} onChange={(e) => onSys(e.target.value)}
          sx={{ mb: 1, '& .MuiSelect-select': { fontSize: 13 } }}>
          {['Air', 'Water', 'Water-glycol', 'Oil'].map((o) => (
            <MenuItem key={o} value={o} sx={{ fontSize: 13 }}>{o}</MenuItem>
          ))}
        </TextField>
      </Tooltip>
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
