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
import { nsT } from '../../i18n/nsT';

import { SHOW_CONFIGURE_CHARTS } from '../../lib/configuratorFlags';
const tx = nsT('controller');   // EN source, ZH mirror (docs/I18N.md)

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1 } as const;
const LABEL = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const AX = { stroke: 'var(--text-4)', fontSize: 10 } as const;
const TT = { contentStyle: { backgroundColor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 6, fontSize: 11 }, labelStyle: { color: 'var(--text-2)' } };
const fmt = (v: number | null | undefined, d = 1) =>
  (v == null || !Number.isFinite(v) ? '—' : v.toFixed(d));

const LIMIT_COLOR: Record<string, string> = {
  none: '#4ade80', current: '#fbbf24', pack: '#fbbf24', modulation: '#f87171',
};
/** what stops the charge, as a locale key (the codes themselves are data) */
const LIMIT_WHY_KEY: Record<string, string> = {
  none: 'configure.limWhyNone', current: 'configure.limWhyCurrent',
  pack: 'configure.limWhyPack', modulation: 'configure.limWhyModulation',
};
const LIMIT_NAME_KEY: Record<string, string> = {
  none: 'configure.limNone', current: 'configure.limCurrent',
  pack: 'configure.limPack', modulation: 'configure.limModulation',
};
const limWhy = (k: string): string => (LIMIT_WHY_KEY[k] ? tx(LIMIT_WHY_KEY[k]) : '');
const limName = (k: string): string => (LIMIT_NAME_KEY[k] ? tx(LIMIT_NAME_KEY[k]) : k);
/** the pack's placeholder notes (data keys) -> a sentence in the interface language */
const PH_KEY: Record<string, string> = {
  n_parallel: 'configure.phNParallel', r_int_mohm: 'configure.phRInt',
  capacity_ah: 'configure.phCapacity', i_charge_max_A: 'configure.phIChargeMax',
};
const phText = (key: string, raw: string, chem: string | null): string => {
  if (key === 'v_oc') return tx(raw.startsWith('midpoint') ? 'configure.phVocMid' : 'configure.phVocNom');
  return PH_KEY[key] ? tx(PH_KEY[key], { chem: chem ?? '—' }) : raw;
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
        <Typography sx={{ fontSize: 13, fontWeight: 800, color: 'var(--text-0)' }}>{tx('configure.boostCharging')}</Typography>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}
          title={tx('configure.shaftToPackTip', { cells: pack.cells, rint: pack.r_int_mohm, np: pack.n_parallel, rpack: (pack.R_pack_ohm * 1000).toFixed(2) })}>
          {tx('configure.shaftToPack')}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {best.charge && best.I_A != null && (
          <Chip size="small" clickable={!!onPickCurrent}
            onClick={onPickCurrent ? () => onPickCurrent(best.I_A as number) : undefined}
            label={tx('configure.maxChip', { kw: fmt(best.charge.P_charge_W / 1000, 2), a: fmt(best.I_A, 0) })}
            title={tx('configure.maxChipTip', {
              lo: fmt(best.range_A[0], 0), hi: fmt(best.range_A[1], 0), rpm: fmt(knobs.rpm, 0),
              tail: best.at_range_top
                ? tx('configure.maxChipTop')
                : tx('configure.maxChipPast', { why: limWhy(best.limited_by) || tx('configure.maxChipRanOut') }),
              click: onPickCurrent ? tx('configure.maxChipClick') : '',
            })}
            sx={{ fontSize: 11, fontWeight: 700, bgcolor: '#065f46', color: '#d1fae5',
                  '&:hover': onPickCurrent ? { bgcolor: '#047857' } : undefined }} />
        )}
      </Box>

      <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', mb: 1 }}>
        <Tile label={tx('configure.chargePower')} value={fmt(c.P_charge_W / 1000, 2)} unit="kW"
          color={c.charging ? '#4ade80' : '#f87171'}
          sub={tx('configure.chargePowerSub', { mech: fmt(c.P_mech_W / 1000, 2), loss: fmt(c.P_loss_W, 0) })}
          title={tx('configure.chargePowerTip')} />
        <Tile label={tx('configure.chargeCurrent')} value={fmt(c.I_charge_A, 1)} unit="A"
          color={lim === 'current' ? '#fbbf24' : undefined}
          sub={pack.i_charge_max_A > 0 ? tx('configure.chargeCurrentSub', { max: fmt(pack.i_charge_max_A, 0) }) : undefined}
          title={tx('configure.chargeCurrentTip')} />
        <Tile label={tx('configure.cRate')} value={fmt(c.C_rate, 3)} unit="C"
          sub={tx('configure.cRateSub', { ah: fmt(pack.capacity_ah, 0) })}
          title={pack.placeholders.capacity_ah
            ? tx('configure.cRateTipPh', { text: phText('capacity_ah', pack.placeholders.capacity_ah, pack.chemistry) })
            : tx('configure.cRateTip')} />
        <Tile label={tx('configure.busUnderCharge')} value={fmt(c.V_bus_V, 1)} unit="V"
          color={lim === 'pack' ? '#fbbf24' : undefined}
          sub={tx('configure.busUnderChargeSub', { rise: fmt(c.V_rise_V, 2) })}
          title={tx('configure.busUnderChargeTip', { voc: fmt(c.V_oc_V, 0), packMax: pack.v_max_V ? tx('configure.busUnderChargePackMax', { v: fmt(pack.v_max_V, 0) }) : '' })} />
        <Tile label={tx('configure.etaCharge')} value={fmt((c.eta_charge ?? 0) * 100, 2)} unit="%"
          sub={tx('configure.etaChargeSub', { w: fmt(c.P_pack_r_loss_W, 1) })}
          title={tx('configure.etaChargeTip')} />
        <Tile label={tx('configure.limitedBy')} value={limName(lim)} color={LIMIT_COLOR[lim]}
          sub={lim === 'modulation' ? tx('configure.limitedBySubMod', { m: fmt(c.modulation_index, 3), lim: M_LIMIT }) : tx('configure.limitedBySub', { m: fmt(c.modulation_index, 3) })}
          title={`${limWhy(lim)}. ${c.note_key ? tx(c.note_key, c.note_params) : ''}`} />
      </Box>

      {SHOW_CONFIGURE_CHARTS && chartData.length > 1 && (
        <Box>
          <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1 }}>
            <Typography sx={{ ...LABEL }}>{tx('configure.chargeMapTitle')}</Typography>
            <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}
              title={tx('configure.chargeMapTip')}>
              {tx('configure.chargeMapSub')}
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
              <Line yAxisId="l" type="monotone" dataKey="kW" name={tx('configure.seriesPCharge')}
                stroke="#4ade80" dot={false} strokeWidth={2} />
              <Line yAxisId="r" type="monotone" dataKey="I" name={tx('configure.seriesWinningI')}
                stroke="#fbbf24" dot={false} strokeWidth={1} strokeDasharray="4 3" />
            </ComposedChart>
          </ResponsiveContainer>
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
            {Array.from(new Set(map.map((r) => r.limited_by))).map((L) => (
              <Typography key={L} sx={{ fontSize: 9.5, color: LIMIT_COLOR[L] ?? 'var(--text-3)' }}
                title={limWhy(L)}>
                {tx('configure.limitCount', { limit: limName(L), n: map.filter((r) => r.limited_by === L).length, total: map.length })}
              </Typography>
            ))}
          </Box>
        </Box>
      )}

      {placeholders.length > 0 && (
        <Typography sx={{ fontSize: 10, color: '#fbbf24', mt: 0.75 }}
          title={placeholders.map(([k2, v]) => phText(k2, v, pack.chemistry)).join('\n')}>
          {tx('configure.placeholders', { n: placeholders.length })}
        </Typography>
      )}
    </Box>
  );
};

export default ChargePanel;
