/**
 * CoupledEmThermal — the multiphysics EM<->thermal coupling, on/off in the UI.
 *
 * Runs the `solver.em_thermal` module through the kernel: it iterates
 *   EM loss field -> thermal solve -> winding temperature -> fed BACK as the next
 *   EM operating temp (rho_Cu rises with T => more loss => hotter)
 * until the copper reaches thermal EQUILIBRIUM (or flags thermal runaway).
 *
 * It is OFF by default and gated behind an explicit "Run" because each iteration
 * is a full EM+thermal solve (slow). The iteration history makes the inter-module
 * loss<->temperature feedback visible.
 */
import React, { useState } from 'react';
import { Box, Paper, Typography, Button, Switch, FormControlLabel, CircularProgress, Chip } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';
import { getCoolingPayload } from './CoolingControls';
import HelpTip from '../common/HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const CARD = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 2 } as const;

interface Props { I_phase_rms?: number; gamma_deg?: number; steps?: number; }

const CoupledEmThermal: React.FC<Props> = ({ I_phase_rms = 85, gamma_deg = 0, steps = 12 }) => {
  const geometry = useMotorStore((s: any) => s.geometry);
  const [enabled, setEnabled] = useState<boolean>(() => localStorage.getItem('sim.multiphysics') === '1');
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (on: boolean) => { setEnabled(on); localStorage.setItem('sim.multiphysics', on ? '1' : '0'); };

  const run = async () => {
    setBusy(true); setErr(null); setRes(null);
    try {
      const r = await fetch(`${API}/api/kernel/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          capability: 'solver.em_thermal',
          payload: {
            I_phase_rms, gamma_deg, n_steps_per_period: steps, n_periods: 1, n_sectors: 4, max_iter: 6,
            ...getCoolingPayload(),                         // cooling system from the left panel
            geo: JSON.stringify(geometry || {}),
          },
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || 'coupled solve failed');
      setRes(j.result);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const raw = res?.raw || {};
  const comp = raw.components || {};
  const converged = raw.converged;
  const runaway = raw.runaway;
  const hist: number[] = raw.coil_temp_history_C || [];

  return (
    <Paper sx={CARD}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-0)' }}>Multiphysics — coupled EM ↔ thermal</Typography>
        <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', fontFamily: 'monospace' }}>module: solver.em_thermal</Typography>
        <Box sx={{ flex: 1 }} />
        <FormControlLabel
          control={<Switch size="small" checked={enabled} onChange={(e) => toggle(e.target.checked)} />}
          label={<Typography sx={{ fontSize: 12, color: enabled ? '#60a5fa' : 'var(--text-2)' }}>{enabled ? 'On' : 'Off'}</Typography>}
          sx={{ mr: 0 }}
        />
        <HelpTip title={'Off — torque/losses use a fixed coil temperature. Turn on to iterate losses ↔ temperature '
          + 'to the self-consistent operating point (slower: each step is a full EM + thermal solve).'} />
      </Box>

      {enabled && (
        <Box sx={{ mt: 1.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', mb: 1 }}>
            <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>
              Cooling system set in the left panel ←
            </Typography>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'monospace' }}>
              @ {I_phase_rms} A · γ {gamma_deg}° · {steps} steps
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Button size="small" variant="contained" onClick={run} disabled={busy}
              sx={{ textTransform: 'none', fontSize: 11 }}>
              {busy ? 'Solving…' : 'Run coupled solve'}
            </Button>
          </Box>

          {busy && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: 'var(--text-3)', py: 0.5 }}>
              <CircularProgress size={14} /> Iterating EM ↔ thermal to equilibrium…
            </Box>
          )}
          {err && <Typography sx={{ fontSize: 11.5, color: '#fca5a5' }}>Coupled solve failed: {err}</Typography>}

          {res && hist.length > 0 && (
            <Box>
              {/* the loss<->temperature feedback, iteration by iteration */}
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap', mb: 1 }}>
                <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>copper temp per iteration:</Typography>
                {hist.map((t, i) => (
                  <Chip key={i} size="small" label={`${t}°C`}
                    sx={{ height: 20, fontSize: 10.5, fontFamily: 'monospace',
                          bgcolor: i === hist.length - 1 ? '#1d4ed8' : 'var(--panel)', color: 'var(--text-0)' }} />
                ))}
              </Box>

              {runaway ? (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography sx={{ fontSize: 12.5, color: '#fca5a5', fontWeight: 600 }}>
                    ⚠ Thermal runaway
                  </Typography>
                  <HelpTip title="No stable equilibrium at this operating point — the loss ↔ temperature loop diverges. Increase cooling (h_conv) or reduce current." />
                </Box>
              ) : (
                <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 1 }}>
                  <Metric label="Equilibrium copper" value={`${raw.coil_temp_converged_C} °C`} hot />
                  <Metric label="Hot-spot T_max" value={`${raw.T_max} °C`} />
                  <Metric label="Winding (avg)" value={comp.winding ? `${comp.winding.avg} °C` : '—'} />
                  <Metric label="Magnet (avg)" value={comp.magnet ? `${comp.magnet.avg} °C` : '—'} />
                  <Metric label="Copper loss" value={`${raw.P_cu_W} W`} />
                  {raw.cooling?.fluid_temp_out_c != null && (
                    <Metric label="Coolant out" value={`${raw.cooling.fluid_temp_out_c} °C`} hot />
                  )}
                  {raw.cooling?.h_conv != null && (
                    <Metric label="h used" value={`${raw.cooling.h_conv} W/m²K`} />
                  )}
                </Box>
              )}
            </Box>
          )}
        </Box>
      )}
    </Paper>
  );
};

const Metric: React.FC<{ label: string; value: string; hot?: boolean }> = ({ label, value, hot }) => (
  <Box sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, px: 1.25, py: 0.75 }}>
    <Typography sx={{ fontSize: 9.5, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</Typography>
    <Typography sx={{ fontSize: 15, fontWeight: 700, color: hot ? '#fb923c' : 'var(--text-0)', fontFamily: 'monospace' }}>{value}</Typography>
  </Box>
);

export default CoupledEmThermal;
