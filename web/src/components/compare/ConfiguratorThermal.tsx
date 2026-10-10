/**
 * ConfiguratorThermal — analytical thermal estimate for the Configure tab.
 *
 * Uses the shared CoolingControls panel (persisted to localStorage `sim.cool.*`),
 * but computes steady-state temperatures ANALYTICALLY (lumped resistances in
 * lib/thermalEstimate) from the configurator's scaled losses — no FEM. Updates
 * instantly as you tune knobs or cooling.
 *
 * 2026-09-07: the widget and its two correlations moved from
 * `simulation/CoolingControls` to the thermal module with the rest of the
 * thermal code.  The localStorage keys are unchanged, so every saved cooling
 * system survived the move.
 */
import React, { useEffect, useReducer } from 'react';
import { Box, Paper, Typography } from '@mui/material';
import CoolingControls from '../thermal/CoolingControls';
import { airH, getCoolingPayload, liqH } from '../thermal/api';
import { estimateThermal, type ThermalGeom, type ThermalLosses } from '../../lib/thermalEstimate';
import HelpTip from '../common/HelpTip';
import { nsT } from '../../i18n/nsT';

const tx = nsT('controller');   // EN source, ZH mirror (docs/I18N.md)

const CARD = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 2 } as const;

const Metric: React.FC<{ label: string; value: string; hot?: boolean }> = ({ label, value, hot }) => (
  <Box sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, px: 1.25, py: 0.75 }}>
    <Typography sx={{ fontSize: 9.5, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</Typography>
    <Typography sx={{ fontSize: 15, fontWeight: 700, color: hot ? '#fb923c' : 'var(--text-0)', fontFamily: 'monospace' }}>{value}</Typography>
  </Box>
);

type CoolingEstimate = {
  mode: 'robotics' | 'propeller';
  ambient_C: number;
  air_speed_ms: number | null;
  h_Wm2K: number | null;
  temperatureLimits: { winding_C: number; magnet_C: number; windingBasis: string };
};

const ConfiguratorThermal: React.FC<{
  geom: ThermalGeom;
  losses: ThermalLosses;
  coolingEstimate?: CoolingEstimate;
}> = ({ geom, losses, coolingEstimate }) => {
  // CoolingControls writes to localStorage + fires 'cooling:changed' — re-render on it.
  const [, bump] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    window.addEventListener('cooling:changed', bump);
    return () => window.removeEventListener('cooling:changed', bump);
  }, []);

  const cp = coolingEstimate ? null : getCoolingPayload();
  const isAir = coolingEstimate ? true : cp?.cooling_mode === 'air';
  const D = (geom.statorOD_mm || 150) / 1000;
  const h = coolingEstimate
    ? (coolingEstimate.mode === 'robotics' ? airH(0, D) : coolingEstimate.h_Wm2K ?? 0)
    : isAir ? airH(Number(cp?.air_speed_mps) || 0, D) : liqH(Number(cp?.flow_lpm) || 0);
  const ambient_C = coolingEstimate?.ambient_C ?? (Number(cp?.ambient_temp) || 25);
  const t = h > 0 ? estimateThermal(geom, losses, { h_Wm2K: h, ambient_C }) : null;

  const windingLimit_C = coolingEstimate?.temperatureLimits.winding_C ?? 155;
  const magnetLimit_C = coolingEstimate?.temperatureLimits.magnet_C ?? 150;
  const windHot = t != null && t.T_winding_C > windingLimit_C;   // legacy generic estimate thresholds
  const magHot  = t != null && t.T_magnet_C > magnetLimit_C;
  const format = (value: number | undefined) => value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(0)} °C`;

  return (
    <Paper sx={CARD}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-0)' }}>
          {coolingEstimate ? tx('configurePropeller.estimateTitle') : tx('configure.thermalTitle')}
        </Typography>
        <HelpTip title={coolingEstimate ? tx('configurePropeller.estimateTip') : tx('configure.thermalHelp')} />
      </Box>

      {/* the shared cooling inputs (localStorage `sim.cool.*`) */}
      {!coolingEstimate && <CoolingControls diameterMm={geom.statorOD_mm} />}
      {coolingEstimate && (
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mb: 1 }}>
          {coolingEstimate.mode === 'robotics'
            ? tx('configurePropeller.stillAirBasis', { h: h.toFixed(0) })
            : tx('configurePropeller.propellerAirBasis', {
              speed: coolingEstimate.air_speed_ms == null ? '—' : coolingEstimate.air_speed_ms.toFixed(1), h: coolingEstimate.h_Wm2K == null ? '—' : coolingEstimate.h_Wm2K.toFixed(0),
            })}
        </Typography>
      )}
      {coolingEstimate && (
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mb: 1 }}>
          {tx('configurePropeller.temperatureLimits', {
            winding: coolingEstimate.temperatureLimits.winding_C.toFixed(0),
            magnet: coolingEstimate.temperatureLimits.magnet_C.toFixed(0),
            basis: coolingEstimate.temperatureLimits.windingBasis,
          })}
        </Typography>
      )}

      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(112px, 1fr))', gap: 1, mt: 1.5 }}>
        <Metric label={tx('configure.winding')} value={format(t?.T_winding_C)} hot={windHot} />
        <Metric label={tx('configure.magnet')} value={format(t?.T_magnet_C)} hot={magHot} />
        <Metric label={tx('configure.housing')} value={format(t?.T_housing_C)} />
        <Metric label={tx('configure.ambient')} value={`${(t?.ambient_C ?? ambient_C).toFixed(0)} °C`} />
        <Metric label={tx('configure.totalLoss')} value={t ? `${t.P_total_W.toFixed(0)} W` : '—'} />
        <Metric label={tx('configure.hUsed')} value={h > 0 ? `${h.toFixed(0)} W/m²K` : '—'} />
        {coolingEstimate && <Metric label={tx('configurePropeller.coolingAirSpeed')} value={coolingEstimate.air_speed_ms == null ? '—' : `${coolingEstimate.air_speed_ms.toFixed(1)} m/s`} />}
        <Metric label={tx('configure.surface')} value={t ? `${(t.A_surface_m2 * 1e4).toFixed(0)} cm²` : '—'} />
      </Box>

      {(windHot || magHot) && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1 }}>
          <Typography sx={{ fontSize: 11.5, color: '#fca5a5' }}>
            ⚠ {windHot ? (coolingEstimate
              ? tx('configurePropeller.hotWinding', { t: t?.T_winding_C.toFixed(0) ?? '', limit: windingLimit_C.toFixed(0) })
              : tx('configure.hotWinding', { t: t?.T_winding_C.toFixed(0) ?? '' })) : ''}
            {windHot && magHot ? ' · ' : ''}
            {magHot ? (coolingEstimate
              ? tx('configurePropeller.hotMagnet', { t: t?.T_magnet_C.toFixed(0) ?? '', limit: magnetLimit_C.toFixed(0) })
              : tx('configure.hotMagnet', { t: t?.T_magnet_C.toFixed(0) ?? '' })) : ''}
          </Typography>
          <HelpTip title={(windHot ? (coolingEstimate
            ? tx('configurePropeller.hotWindingTip', { t: t?.T_winding_C.toFixed(0) ?? '', limit: windingLimit_C.toFixed(0), basis: coolingEstimate.temperatureLimits.windingBasis })
            : tx('configure.hotWindingTip', { t: t?.T_winding_C.toFixed(0) ?? '' })) : '')
            + (magHot ? (coolingEstimate
              ? tx('configurePropeller.hotMagnetTip', { t: t?.T_magnet_C.toFixed(0) ?? '', limit: magnetLimit_C.toFixed(0) })
              : tx('configure.hotMagnetTip', { t: t?.T_magnet_C.toFixed(0) ?? '' })) : '')
            + tx('configure.hotAdvice')} />
        </Box>
      )}
    </Paper>
  );
};

export default ConfiguratorThermal;
