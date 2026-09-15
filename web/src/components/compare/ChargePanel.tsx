/**
 * ChargePanel — BOOST-CHARGING for the Configure tab.
 *
 * A generator's whole point is the pack behind it, so the tuner answers the
 * question the shaft is being turned for: how many kilowatts land in the
 * battery at this operating point, at what current, at what C-rate, how far
 * the bus rises while it happens — and what is stopping it going higher.
 *
 * Every number comes from lib/generatorCharge.ts, which is the analytical twin
 * of the FEM charging run (routes/simulation._battery_charge_block): same
 * energy balance, same V_bus = V_oc + I·R_pack fixed point (solved exactly
 * here instead of iterated, because here it costs no solve), same ideal-bridge
 * caveat.
 */
import React, { useMemo } from 'react';
import { Box, Typography, Chip } from '@mui/material';
import {
  ResponsiveContainer, ComposedChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RcTooltip, ReferenceLine,
} from 'recharts';
import type { Passport, Knobs, ScaledResult } from '../../lib/motorScaling';
import {
  chargeAt, maxCharge, chargeMap, canCharge, M_LIMIT,
} from '../../lib/generatorCharge';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1 } as const;
const LABEL = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const AX = { stroke: 'var(--text-4)', fontSize: 10 } as const;
const TT = { contentStyle: { backgroundColor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 6, fontSize: 11 }, labelStyle: { color: 'var(--text-2)' } };
const fmt = (v: number | null | undefined, d = 1) =>
  (v == null || !Number.isFinite(v) ? '—' : v.toFixed(d));

const LIMIT_COLOR: Record<string, string> = {
  none: '#4ade80', current: '#fbbf24', pack: '#fbbf24', modulation: '#f87171',
};
const LIMIT_WHY: Record<string, string> = {
  none: 'nothing is capping this point — the shaft power is the limit',
  current: "the pack's own charge-current ceiling",
  pack: 'the pack would be pushed above its maximum terminal voltage',
  modulation: 'the bus cannot synthesise the voltage this point needs',
};

const Tile: React.FC<{ label: string; value: string; unit?: string;
                       color?: string; title?: string; sub?: string }> =
({ label, value, unit, color, title, sub }) => (
  <Box sx={{ ...PANEL, p: 0.9, flex: '0 1 auto', minWidth: 108, maxWidth: 178 }} title={title}>
    <Typography sx={{ ...LABEL, fontSize: 9.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</Typography>
    <Typography sx={{ fontSize: 16, fontWeight: 800, color: color ?? 'var(--text-0)', fontFamily: 'monospace', lineHeight: 1.2, whiteSpace: 'nowrap' }}>
      {value}<Box component="span" sx={{ fontSize: 10.5, color: 'var(--text-3)', ml: 0.5 }}>{unit}</Box>
    </Typography>
    {sub ? <Typography sx={{ fontSize: 9.5, color: 'var(--text-4)', whiteSpace: 'nowrap' }}>{sub}</Typography> : null}
  </Box>
);

const ChargePanel: React.FC<{
  p: Passport; knobs: Knobs; poles?: number; result: ScaledResult;
  onPickCurrent?: (I: number) => void;
}> = ({ p, knobs, poles, result, onPickCurrent }) => {
  const c = useMemo(() => chargeAt(p, knobs, poles, result), [p, knobs, poles, result]);
  const best = useMemo(() => maxCharge(p, knobs, poles), [p, knobs, poles]);
  const map = useMemo(() => chargeMap(p, knobs, poles), [p, knobs, poles]);
  if (!canCharge(p) || !c.available) return null;

  const lim = c.limited_by;
  const pack = c.pack!;
  const placeholders = Object.entries(pack.placeholders || {});
  const chartData = map.map((r) => ({
    rpm: r.rpm, kW: r.P_charge_W / 1000, I: r.I_A ?? 0,
    A: r.I_charge_A, lim: r.limited_by,
  }));

  return (
    <Box sx={{ ...PANEL, p: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 13, fontWeight: 800, color: 'var(--text-0)' }}>Boost charging</Typography>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}
          title={`Shaft power minus the machine's own losses goes into the pack through an IDEAL bridge — no dead time, no device conduction or switching loss, so a real charger delivers less, never more. The bus is the exact root of V_bus = V_oc + I·R_pack with I = P_charge/V_bus (R_pack = ${pack.cells}·${pack.r_int_mohm} mΩ / ${pack.n_parallel} = ${(pack.R_pack_ohm * 1000).toFixed(2)} mΩ).`}>
          shaft → pack, ideal bridge
        </Typography>
        <Box sx={{ flex: 1 }} />
        {best.charge && best.I_A != null && (
          <Chip size="small" clickable={!!onPickCurrent}
            onClick={onPickCurrent ? () => onPickCurrent(best.I_A as number) : undefined}
            label={`max ${fmt(best.charge.P_charge_W / 1000, 2)} kW @ ${fmt(best.I_A, 0)} A`}
            title={`The most charge power any current between ${fmt(best.range_A[0], 0)} and ${fmt(best.range_A[1], 0)} A reaches at ${fmt(knobs.rpm, 0)} rpm, with every pack and modulation limit held. `
              + (best.at_range_top
                ? 'It sits at the TOP of that range: nothing in the pack or the modulation stopped it — the machine\'s own current ceiling (conductor headroom and the measured demagnetisation knee) did.'
                : `Past it: ${LIMIT_WHY[best.limited_by] ?? 'the sweep ran out of measured current'}.`)
              + (onPickCurrent ? ' Click to set the current knob there.' : '')}
            sx={{ fontSize: 11, fontWeight: 700, bgcolor: '#065f46', color: '#d1fae5',
                  '&:hover': onPickCurrent ? { bgcolor: '#047857' } : undefined }} />
        )}
      </Box>

      <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', mb: 1 }}>
        <Tile label="Charge power" value={fmt(c.P_charge_W / 1000, 2)} unit="kW"
          color={c.charging ? '#4ade80' : '#f87171'}
          sub={`${fmt(c.P_mech_W / 1000, 2)} kW shaft − ${fmt(c.P_loss_W, 0)} W loss`}
          title="Mechanical power in, minus every loss the card reports. Ideal bridge — a real charger delivers less." />
        <Tile label="Charge current" value={fmt(c.I_charge_A, 1)} unit="A"
          color={lim === 'current' ? '#fbbf24' : undefined}
          sub={pack.i_charge_max_A > 0 ? `of ${fmt(pack.i_charge_max_A, 0)} A max` : undefined}
          title="P_charge / V_bus — the balance number, because that is the honest power." />
        <Tile label="C-rate" value={fmt(c.C_rate, 3)} unit="C"
          sub={`${fmt(pack.capacity_ah, 0)} Ah pack`}
          title={placeholders.length ? `Capacity: ${pack.placeholders.capacity_ah ?? 'from the configuration'}` : 'from the pack on the configuration'} />
        <Tile label="Bus under charge" value={fmt(c.V_bus_V, 1)} unit="V"
          color={lim === 'pack' ? '#fbbf24' : undefined}
          sub={`+${fmt(c.V_rise_V, 2)} V over V_oc`}
          title={`Charging pushes current through R_pack the other way from a discharge, so the terminal sits ABOVE the open circuit. V_oc ${fmt(c.V_oc_V, 0)} V${pack.v_max_V ? `, pack max ${fmt(pack.v_max_V, 0)} V` : ''}.`} />
        <Tile label="η charge" value={fmt((c.eta_charge ?? 0) * 100, 2)} unit="%"
          sub={`${fmt(c.P_pack_r_loss_W, 1)} W in R_pack`}
          title="Shaft in → pack in. The pack's own I²R is reported separately and is NOT in the machine's efficiency." />
        <Tile label="Limited by" value={lim} color={LIMIT_COLOR[lim]}
          sub={lim === 'modulation' ? `m ${fmt(c.modulation_index, 3)} > ${M_LIMIT}` : `m ${fmt(c.modulation_index, 3)}`}
          title={`${LIMIT_WHY[lim]}. ${c.note}`} />
      </Box>

      {chartData.length > 1 && (
        <Box>
          <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1 }}>
            <Typography sx={{ ...LABEL }}>Charge map — P_charge at the max-charge current</Typography>
            <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}
              title="At every speed the current knob is swept over the passport's own measured range and the best feasible point kept. The band under the curve is what stops it going higher at that speed.">
              across the measured speed range
            </Typography>
          </Box>
          <ResponsiveContainer width="100%" height={190}>
            <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid stroke="var(--panel)" strokeDasharray="3 3" />
              <XAxis dataKey="rpm" tick={AX} tickFormatter={(v) => `${Math.round(Number(v))}`} />
              <YAxis yAxisId="l" tick={AX} width={44}
                label={{ value: 'kW', angle: -90, position: 'insideLeft', fill: 'var(--text-4)', fontSize: 10 }} />
              <YAxis yAxisId="r" orientation="right" tick={AX} width={40} />
              <RcTooltip {...TT} />
              <ReferenceLine x={knobs.rpm} yAxisId="l" stroke="#60a5fa" strokeDasharray="4 3" />
              <Line yAxisId="l" type="monotone" dataKey="kW" name="P_charge, kW"
                stroke="#4ade80" dot={false} strokeWidth={2} />
              <Line yAxisId="r" type="monotone" dataKey="I" name="winning I, A"
                stroke="#fbbf24" dot={false} strokeWidth={1} strokeDasharray="4 3" />
            </ComposedChart>
          </ResponsiveContainer>
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
            {Array.from(new Set(map.map((r) => r.limited_by))).map((L) => (
              <Typography key={L} sx={{ fontSize: 9.5, color: LIMIT_COLOR[L] ?? 'var(--text-3)' }}
                title={LIMIT_WHY[L]}>
                ● {L}: {map.filter((r) => r.limited_by === L).length} of {map.length} speeds
              </Typography>
            ))}
          </Box>
        </Box>
      )}

      {placeholders.length > 0 && (
        <Typography sx={{ fontSize: 10, color: '#fbbf24', mt: 0.75 }}
          title={placeholders.map(([k2, v]) => `${k2}: ${v}`).join('\n')}>
          {placeholders.length} pack value{placeholders.length > 1 ? 's are' : ' is'} a placeholder — hover
        </Typography>
      )}
    </Box>
  );
};

export default ChargePanel;
