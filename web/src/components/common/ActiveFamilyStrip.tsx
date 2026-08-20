/**
 * ActiveFamilyStrip — the always-visible line under the tabs saying WHICH
 * die / configuration / duty is loaded in the editor, whether the Simulation
 * panel has drifted off that duty's operating point, and (for writers) a
 * one-click "Save to duty" that writes the CURRENT point back into it —
 * recording the last run's results too when that run matches the point.
 */
import React, { useEffect, useState } from 'react';
import { Box, Typography, Button, Tooltip, CircularProgress } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface Ctx {
  active: boolean; die?: string; config?: string; duty?: string | null;
  die_locked?: boolean; config_locked?: boolean; can_write?: boolean;
  duty_point?: { current_arms: number; rpm: number; gamma_deg: number;
                 mode: string } | null;
  build?: { stack_mm?: number | null; wire_height_mm?: number | null;
            turns?: number | null } | null;
}

const readLS = (k: string, d: any) => {
  try { const v = localStorage.getItem('sim.' + k); return v == null ? d : JSON.parse(v); }
  catch { return d; }
};

const ActiveFamilyStrip: React.FC = () => {
  const [ctx, setCtx] = useState<Ctx | null>(null);
  const liveGeometry = useMotorStore(s => s.geometry) as Record<string, unknown>;
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // ticks so the drift marker re-evaluates while the user types in the panel
  const [, setTick] = useState(0);

  const load = () => fetch(`${API}/api/family/context`)
    .then(r => r.json()).then(setCtx).catch(() => setCtx(null));
  useEffect(() => {
    load();
    const onChange = () => { void load(); };
    window.addEventListener('family-changed', onChange);
    window.addEventListener('sim-design-applied', onChange);
    const onOp = () => setTick(t => t + 1);
    window.addEventListener('sim-operating-point', onOp);
    const id = setInterval(() => setTick(t => t + 1), 5000);
    return () => {
      window.removeEventListener('family-changed', onChange);
      window.removeEventListener('sim-design-applied', onChange);
      window.removeEventListener('sim-operating-point', onOp);
      clearInterval(id);
    };
  }, []);

  if (!ctx?.active) return null;

  // Has the Simulation panel drifted off the loaded duty's operating point?
  const p = ctx.duty_point;
  const cur = Number(readLS('current', NaN));
  const rpm = Number(readLS('rpm', NaN));
  const gam = Number(readLS('gamma', NaN));
  const mode = readLS('opMode', 'motor');
  const near = (a: number, b: number, tol: number) =>
    Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= tol;
  const drifted = !!p && !(
    near(cur, p.current_arms, Math.max(0.05, 0.001 * p.current_arms))
    && near(rpm, p.rpm, 0.5) && near(gam, p.gamma_deg, 0.01)
    && mode === p.mode);

  const lockGlyph = ctx.die_locked && ctx.config_locked ? '🔒🔒'
                  : ctx.die_locked ? '🔒' : '🔓';
  const lockTip = ctx.die_locked && ctx.config_locked
    ? 'Die AND configuration locked — geometry is read-only; only Simulation parameters move'
    : ctx.die_locked
      ? 'Die locked — stack length, wire and turns are editable; stamped geometry is not'
      : 'Nothing locked — the whole geometry is editable';

  // Save the CURRENT Simulation point back into the loaded duty; then, when
  // the LAST finished run sits exactly on that point, record its results too.
  const save = async () => {
    if (!ctx.duty) return;
    setBusy(true); setMsg(null);
    try {
      // Does the LAST finished run sit on the point being saved?  Then its
      // MEASURED torque/power replace the duty's stored kW/Nm (an old target
      // must not outlive a real run at the new point), and its results are
      // recorded right after.
      const s = readLS('lastSummary', null);
      const runMatches = !!(s
        && near(Number(s.I_phase_rms_A), cur, Math.max(0.5, 0.002 * cur))
        && near(Number(s.rpm), rpm, 1) && (s.op_mode ?? 'motor') === mode);
      const dutyBody: any = { name: ctx.duty, mode, from_current: true };
      if (runMatches) {
        dutyBody.torque_nm = Math.abs(Number(s.T_em_avg_Nm)) || undefined;
        dutyBody.power_kw = Math.abs(Number(s.P_mech_W)) / 1000 || undefined;
      }
      const r = await fetch(`${API}/api/family/duty`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die: ctx.die, config: ctx.config, duty: dutyBody }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      let extra = '';
      if (runMatches) {
        const rr = await fetch(`${API}/api/family/duty_result`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ die: ctx.die, config: ctx.config, duty: ctx.duty,
            result: { efficiency_pct: Number(s.efficiency) * 100,
                      ripple_pct: s.T_ripple_pct, v_ll_peak_v: s.V_line_peak_V,
                      loss_w: s.P_loss_total_W, mass_kg: s.mass_total_kg,
                      p_core_w: s.P_core_W, p_stranded_w: s.P_stranded_W,
                      p_solid_w: s.P_solid_W, v_phase_peak_v: s.V_phase_peak_V,
                      j_coil_a_mm2: s.J_coil_A_per_mm2 } }),
        });
        extra = rr.ok ? ' + run results' : '';
      } else {
        extra = ' (no matching run to record — Run, then save again)';
      }
      setMsg(`✓ saved to ${ctx.duty}${extra}`);
      window.dispatchEvent(new CustomEvent('family-changed'));
    } catch (e: any) { setMsg(`✗ ${e?.message ?? e}`); }
    setBusy(false);
  };

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.35,
               bgcolor: 'var(--panel-2)', borderBottom: '1px solid var(--line-soft)',
               flexShrink: 0, minHeight: 28 }}>
      <Tooltip title={lockTip}>
        <Typography component="span" sx={{ fontSize: 12, cursor: 'help' }}>
          {lockGlyph}
        </Typography>
      </Tooltip>
      {(() => {
        // EFFECTIVE config name from the LIVE geometry: change the stack in
        // Geometry and the strip reads M1-L220 (amber = unsaved) at once —
        // the catalog keeps the saved name until Save (user rule).
        const liveL = Number(liveGeometry?.motor_length);
        const savedL = Number(ctx.build?.stack_mm);
        const liveTurns = Number(liveGeometry?.num_wires_per_slot);
        const savedTurns = Number(ctx.build?.turns);
        const liveWh = Number(liveGeometry?.wire_height);
        const savedWh = Number(ctx.build?.wire_height_mm);
        const lDrift = Number.isFinite(liveL) && Number.isFinite(savedL)
          && Math.abs(liveL - savedL) > 1e-6;
        const otherDrift = (Number.isFinite(liveTurns) && Number.isFinite(savedTurns)
            && Math.abs(liveTurns - savedTurns) > 1e-6)
          || (Number.isFinite(liveWh) && Number.isFinite(savedWh)
            && Math.abs(liveWh - savedWh) > 1e-6);
        let cfgLabel = ctx.config ?? '';
        if (lDrift) {
          const lTxt = `L${Number(liveL.toFixed(1))}`;
          cfgLabel = /-L\d+(\.\d+)?/.test(cfgLabel)
            ? cfgLabel.replace(/-L\d+(\.\d+)?/, `-${lTxt}`)
            : `${cfgLabel} (${lTxt})`;
        }
        const drifted2 = lDrift || otherDrift;
        const tip = drifted2
          ? `Live build differs from the saved configuration:`
            + (lDrift ? ` stack ${savedL} → ${liveL} mm;` : '')
            + (Number.isFinite(liveTurns) && Number.isFinite(savedTurns) && liveTurns !== savedTurns
               ? ` turns ${savedTurns} → ${liveTurns};` : '')
            + (Number.isFinite(liveWh) && Number.isFinite(savedWh) && Math.abs(liveWh - savedWh) > 1e-6
               ? ` wire h ${savedWh} → ${liveWh} mm;` : '')
            + ' Save to duty re-snapshots the build (siblings will flag stale).'
          : '';
        const cfgEl = (
          <b style={{ color: drifted2 ? '#fbbf24' : 'var(--text-1)' }}>{cfgLabel}</b>
        );
        // The DIE name never tracks the stack — it names the stamped
        // lamination and does not depend on the lamination LENGTH (user
        // rule); only the configuration part follows the live build.
        return (
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)',
                            fontVariantNumeric: 'tabular-nums' }}>
            <b style={{ color: 'var(--text-1)' }}>{ctx.die}</b>
            {' / '}{drifted2
              ? <Tooltip title={tip}><span style={{ cursor: 'help' }}>{cfgEl}</span></Tooltip>
              : cfgEl}
            {ctx.duty ? <>{' / '}<b style={{ color: '#60a5fa' }}>{ctx.duty}</b></> : null}
          </Typography>
        );
      })()}
      {p && (
        <Tooltip title={drifted
          ? `The panel is OFF this duty's stored point (${p.current_arms} A @ ${p.rpm} rpm, γ=${p.gamma_deg}°, ${p.mode}) — Save to duty writes the panel's point into it`
          : 'The Simulation panel sits exactly on this duty’s stored point'}>
          <Typography component="span" sx={{ fontSize: 11, cursor: 'help',
            color: drifted ? '#fbbf24' : '#34d399' }}>
            {drifted ? '≠ point changed' : '= on point'}
          </Typography>
        </Tooltip>
      )}
      <Box sx={{ flex: 1 }} />
      {msg && (
        <Typography sx={{ fontSize: 11,
          color: msg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{msg}</Typography>
      )}
      {ctx.can_write && ctx.duty && (
        <Button size="small" variant="outlined" disabled={busy} onClick={() => void save()}
          sx={{ textTransform: 'none', fontSize: 11, py: 0, minHeight: 22 }}>
          {busy ? <CircularProgress size={11} /> : `💾 Save to ${ctx.duty}`}
        </Button>
      )}
    </Box>
  );
};

export default ActiveFamilyStrip;
