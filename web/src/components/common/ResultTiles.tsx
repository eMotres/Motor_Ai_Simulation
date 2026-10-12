// The result tile panel, shared by Configure and the Electromagnetic tab (owner 2026-10-12:
// «Сделай точно такую же панель в Electromagnetic»).  The tile itself, the row wrapper and the
// lead-cable tile are extracted from ConfiguratorPanel so BOTH panels render the same tiles
// from the same props; the row builders stay pure in lib/configuratorTiles.ts (Configure) and
// lib/emResultTiles.ts (the FEM run summary).
import React from 'react';
import { Box, Typography } from '@mui/material';
import GreekLabel from '../compare/GreekLabel';
import { nsT } from '../../i18n/nsT';
import { pickCable } from '../../lib/cableTable';

const tx = nsT('controller');   // the tiles' own strings (EN source, ZH mirror — docs/I18N.md)

export const fmt = (v: number, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : '—');
export const pctDelta = (cur: number, base: number) => (base ? ((cur - base) / base) * 100 : 0);

// theme bits (match ComparePanel)
export const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1 } as const;
export const LABEL = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;

/** the one width of every result tile */
export const TILE_W = 132;

// ── one result tile: value + unit + Δ vs reference ──
export const MetricTile: React.FC<{
  label: string; value: number | null; unit: string; d?: number; base: number; goodHi?: boolean;
  /** why the value is "—" (shown in the tooltip instead of the vs-reference line) */
  blankTip?: string;
  /** printed instead of the number (a temperature over its limit reads "> 200") */
  display?: string;
  /** no "vs the reference design" line in the tooltip (a quantity with no reference, like a temperature) */
  plain?: boolean;
  /** ABSOLUTE colouring for quantities that have a meaning of their own
   *  (current density): 'ok' | 'warn' | 'bad' overrides the vs-reference
   *  colour, because 9 A/mm² is fine whether or not it grew (user
   *  2026-08-26: "why is the current highlighted in red?"). */
  absLevel?: 'ok' | 'warn' | 'bad';
  /** What the number IS, when the label cannot say it (the heat split's
   *  terms).  Prepended to the vs-reference line in the hover title. */
  tip?: string;
  /** Basis marker for an unavailable, explicitly temperature-qualified value. */
  labelColor?: string;
  blankColor?: string;
  /** Explicit colour for a physical constant whose temperature basis is shown below. */
  valueColor?: string;
}> = ({ label, value, unit, d = 1, base, goodHi, absLevel, tip, blankTip, display, plain, labelColor, blankColor, valueColor }) => {
  const blank = value == null && display == null;
  const delta = value == null ? 0 : pctDelta(value, base);
  // No "% vs ref" line under every tile (user 2026-08-26) — the deltas are
  // carried by COLOUR only; the header's Reset button returns to the
  // reference design.
  const show = false;
  const good = goodHi === undefined ? null : goodHi ? delta > 0 : delta < 0;
  const dColor = good === null ? 'var(--text-2)' : good ? '#4ade80' : '#f87171';
  void show; void dColor;
  // Compact (user 2026-08-25 "too spread out"): fixed narrow tiles in a
  // dense wrap — the same visual weight as the Simulation summary cells.
  // The tile shows the VALUE; how it moved against the reference design is
  // told by the value's colour and by the tooltip (green = better, red =
  // worse, grey = neutral quantity).
  const changed = Math.abs(delta) >= 0.5;
  const valueLineColor = valueColor ?? (changed && good !== null ? dColor : 'var(--text-0)');
  return (
    // ONE fixed width and a fixed value line: a number changing (or becoming "—") never moves
    // another tile (owner 2026-10-05: the block must not jump when the drive is toggled)
    <Box sx={{ ...PANEL, p: 0.9, flex: '0 0 auto', width: TILE_W, boxSizing: 'border-box' }}
      title={(tip ? `${tip}  ` : '') + (blank ? (blankTip ?? '') : plain ? '' : changed
        ? tx('configure.vsRef', { delta: `${delta > 0 ? '+' : ''}${fmt(delta, 1)}`, base: fmt(base, d), unit })
        : tx('configure.sameAsRef'))}>
      <Typography title={label} sx={{ ...LABEL, fontSize: 12, lineHeight: '14px',
        color: labelColor ?? LABEL.color, overflow: 'hidden', whiteSpace: 'nowrap',
        textOverflow: 'ellipsis' }}><GreekLabel text={label} /></Typography>
      <Typography sx={{ fontSize: 16, fontWeight: 800,
        color: blank ? (blankColor ?? 'var(--text-3)') : absLevel
          ? (absLevel === 'bad' ? '#f87171' : absLevel === 'warn' ? '#fbbf24' : '#4ade80')
          : valueLineColor,
        fontFamily: 'monospace', lineHeight: 1.2, whiteSpace: 'nowrap', minHeight: 19 }}>
        {blank ? '—' : (display ?? fmt(value as number, d))}{!blank && <Box component="span" sx={{ fontSize: 10.5,
          color: 'var(--text-3)', ml: 0.5 }}>{unit}</Box>}
      </Typography>
    </Box>
  );
};

/** one row of result tiles — the dense wrap every tile panel uses */
export const TileRow: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>{children}</Box>
);

/** Nearest lead cable for that section (user 2026-08-26): gauge
 *  number + bare conductor diameter. */
export const LeadCableTile: React.FC<{ section_mm2: number }> = ({ section_mm2 }) => {
  const c = pickCable(section_mm2);
  return c ? (
    <Box sx={{ ...PANEL, p: 0.9, flex: '0 0 auto', width: TILE_W, boxSizing: 'border-box' }}
      title={tx('configure.leadCableTip', { strands: c.strands, area: c.area_mm2, section: section_mm2.toFixed(2),
          d: c.d_mm, od: c.od_mm, thk: c.thk_mm, r: c.r_ohm_km, irated: c.i_rated_A, imax: c.i_max_A, roll: c.roll_m })
        + (c.suspect ? tx('configure.cableSuspectLine', { text: tx(c.awg === '10awg' ? 'configure.cableSuspect10' : 'configure.cableSuspect75') }) : '')}>
      <Typography sx={{ ...LABEL, fontSize: 12, lineHeight: '14px',
        overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{tx('configure.leadCable')}</Typography>
      <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)',
        fontFamily: 'monospace', lineHeight: 1.2, whiteSpace: 'nowrap' }}>
        {c.awg.replace('awg', ' AWG')}
        <Box component="span" sx={{ fontSize: 10.5, color: 'var(--text-3)', ml: 0.5 }}>
          Ø{c.od_mm.toFixed(1)} mm
        </Box>
      </Typography>
    </Box>
  ) : null;
};
