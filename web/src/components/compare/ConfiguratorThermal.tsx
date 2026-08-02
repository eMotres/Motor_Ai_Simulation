/**
 * ConfiguratorThermal — analytical thermal estimate for the Configure tab.
 *
 * Uses the SAME cooling inputs as Simulation (the shared CoolingControls panel,
 * persisted to localStorage `sim.cool.*`), but computes steady-state temperatures
 * ANALYTICALLY (lumped resistances in lib/thermalEstimate) from the configurator's
 * scaled losses — no FEM. Updates instantly as you tune knobs or cooling.
 */
import React, { useEffect, useReducer } from 'react';
import { Box, Paper, Typography } from '@mui/material';
import CoolingControls, { getCoolingPayload, airH, liqH } from '../simulation/CoolingControls';
import { estimateThermal, type ThermalGeom, type ThermalLosses } from '../../lib/thermalEstimate';
import HelpTip from '../common/HelpTip';

const CARD = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 2 } as const;

const Metric: React.FC<{ label: string; value: string; hot?: boolean }> = ({ label, value, hot }) => (
  <Box sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, px: 1.25, py: 0.75 }}>
    <Typography sx={{ fontSize: 9.5, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</Typography>
    <Typography sx={{ fontSize: 15, fontWeight: 700, color: hot ? '#fb923c' : 'var(--text-0)', fontFamily: 'monospace' }}>{value}</Typography>
  </Box>
);

const ConfiguratorThermal: React.FC<{ geom: ThermalGeom; losses: ThermalLosses }> = ({ geom, losses }) => {
  // CoolingControls writes to localStorage + fires 'cooling:changed' — re-render on it.
  const [, bump] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    window.addEventListener('cooling:changed', bump);
    return () => window.removeEventListener('cooling:changed', bump);
  }, []);

  const cp = getCoolingPayload();
  const isAir = cp.cooling_mode === 'air';
  const D = (geom.statorOD_mm || 150) / 1000;
  const h = isAir ? airH(Number(cp.air_speed_mps) || 0, D) : liqH(Number(cp.flow_lpm) || 0);
  const t = estimateThermal(geom, losses, { h_Wm2K: h, ambient_C: Number(cp.ambient_temp) || 25 });

  const windHot = t.T_winding_C > 155;   // ~class-F winding limit
  const magHot  = t.T_magnet_C > 150;    // typical NdFeB safe ceiling

  return (
    <Paper sx={CARD}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-0)' }}>Thermal — analytical estimate</Typography>
        <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', fontFamily: 'monospace' }}>lumped · no FEM · instant</Typography>
        <HelpTip title="Steady-state lumped estimate: all loss leaves the outer surface by convection (h·A); winding & magnet hot-spots add conduction rise. A fast approximation — for an accurate temperature map, run the FEM thermal solve in Simulation." />
      </Box>

      {/* same cooling inputs as Simulation (shared) */}
      <CoolingControls diameterMm={geom.statorOD_mm} />

      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(112px, 1fr))', gap: 1, mt: 1.5 }}>
        <Metric label="Winding" value={`${t.T_winding_C.toFixed(0)} °C`} hot={windHot} />
        <Metric label="Magnet"  value={`${t.T_magnet_C.toFixed(0)} °C`} hot={magHot} />
        <Metric label="Housing" value={`${t.T_housing_C.toFixed(0)} °C`} />
        <Metric label="Ambient" value={`${t.ambient_C.toFixed(0)} °C`} />
        <Metric label="Total loss" value={`${t.P_total_W.toFixed(0)} W`} />
        <Metric label="h used" value={`${t.h_Wm2K.toFixed(0)} W/m²K`} />
        <Metric label="Surface" value={`${(t.A_surface_m2 * 1e4).toFixed(0)} cm²`} />
      </Box>

      {(windHot || magHot) && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1 }}>
          <Typography sx={{ fontSize: 11.5, color: '#fca5a5' }}>
            ⚠ {windHot ? `Winding ${t.T_winding_C.toFixed(0)} °C` : ''}
            {windHot && magHot ? ' · ' : ''}
            {magHot ? `Magnet ${t.T_magnet_C.toFixed(0)} °C` : ''}
          </Typography>
          <HelpTip title={(windHot ? `Winding ${t.T_winding_C.toFixed(0)} °C exceeds ~155 °C (class F). ` : '')
            + (magHot ? `Magnet ${t.T_magnet_C.toFixed(0)} °C risks demagnetisation. ` : '')
            + 'Use stronger cooling (liquid / higher flow or air speed) or reduce current.'} />
        </Box>
      )}
    </Paper>
  );
};

export default ConfiguratorThermal;
