/**
 * BatteryPanel — battery the user runs the motor from, and a voltage-match bar.
 *
 * The user picks a cell chemistry (NMC / LiFePO4), the series-cell count and the
 * per-cell nominal / max / min voltages.  The pack window (empty → full) is
 * cells × cell-voltage.  A horizontal bar shows that window with the motor's
 * required DC-bus voltage marked on it, so it's obvious whether the battery can
 * drive the motor — and down to what state of charge.
 */
import React, { useMemo } from 'react';
import { Box, Typography, ToggleButton, ToggleButtonGroup, Button } from '@mui/material';
import BatteryChargingFullIcon from '@mui/icons-material/BatteryChargingFull';
import { nsT } from '../../i18n/nsT';

const tx = nsT('controller');   // EN source, ZH mirror (docs/I18N.md)

export type CellType = 'NMC' | 'LFP';
export interface Battery { type: CellType; cells: number; nom: number; max: number; min: number; }

export const PRESETS: Record<CellType, { nom: number; max: number; min: number; label: string }> = {
  NMC: { nom: 3.7, max: 4.2, min: 3.0, label: 'NMC' },
  LFP: { nom: 3.2, max: 3.65, min: 2.5, label: 'LiFePO₄' },
};
export const defaultBattery = (): Battery => ({ type: 'NMC', cells: 100, ...PRESETS.NMC });
const LABEL = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, p: 2 } as const;
const fmt = (v: number, d = 0) => (Number.isFinite(v) ? v.toFixed(d) : '—');

/** A labelled number field: the label is a normal title (the panel's LABEL style, never
 *  truncated), the value a plain input — no floating small print. */
const numField = (label: string, value: number, onChange: (e: any) => void, step = 0.05, width = 66) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
    <Typography sx={LABEL}>{label}</Typography>
    <input type="number" value={value} step={step} onChange={onChange}
      style={{ width, background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--text-0)', fontSize: 14, fontWeight: 700, fontFamily: 'monospace', textAlign: 'right', padding: '1px 5px' }} />
  </Box>
);

const sameBat = (a: Battery, b: Battery) => a.cells === b.cells && a.type === b.type
  && Math.abs(a.nom - b.nom) < 1e-6 && Math.abs(a.max - b.max) < 1e-6 && Math.abs(a.min - b.min) < 1e-6;

const BatteryPanel: React.FC<{
  vDc: number; bat: Battery; onChange: (b: Battery) => void;
  /** the pack the machine was saved with; with `onReset` it adds a "reset to machine pack" control */
  machinePack?: Battery | null; onReset?: () => void;
  /** no pack is saved for this machine: the block shows the stock default and SAYS so */
  stockNote?: boolean;
  /** hover text of the motor-voltage marker (which operating point it is for) */
  vDcTip?: string;
}> = ({ vDc, bat, onChange, machinePack, onReset, stockNote, vDcTip }) => {
  const setType = (t: CellType | null) => { if (t) onChange({ ...bat, type: t, ...PRESETS[t] }); };
  const setF = (k: keyof Battery) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseFloat(e.target.value); if (Number.isFinite(v)) onChange({ ...bat, [k]: v });
  };

  const packMin = bat.cells * bat.min, packNom = bat.cells * bat.nom, packMax = bat.cells * bat.max;

  const color = useMemo(() => {
    if (vDc > packMax) return '#f87171';   // can't drive
    if (vDc > packNom) return '#fbbf24';   // only near full charge
    return '#4ade80';                      // within range
  }, [vDc, packNom, packMax]);

  // ── voltage bar ───────────────────────────────────────────────────────────
  const W = 360, padX = 14;   // the viewBox is about the column it sits in, so its labels read at their own size
  const lo = 0, hi = Math.max(packMax, vDc) * 1.05;   // axis from 0 → whole range visible
  const X = (v: number) => padX + ((v - lo) / (hi - lo)) * (W - 2 * padX);
  const barY = 30, barH = 22;
  const mX = X(vDc);

  return (
    <Box sx={PANEL}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.25, flexWrap: 'wrap' }}>
        <BatteryChargingFullIcon sx={{ color: '#22c55e', fontSize: 20 }} />
        <Typography sx={{ fontSize: 13, fontWeight: 800, color: 'var(--text-0)' }}>
          {tx('configure.batteryTitle')}
          {stockNote && <Box component="span" sx={{ color: '#fbbf24', fontSize: 11, fontWeight: 700 }}>{' · '}{tx('configure.packStock')}</Box>}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)', fontFamily: 'monospace' }}>
          {tx('configure.packSummary', { nom: fmt(packNom), min: fmt(packMin), max: fmt(packMax) })}
        </Typography>
      </Box>

      {/* inputs */}
      <Box sx={{ display: 'flex', alignItems: 'flex-end', gap: 2, flexWrap: 'wrap', mb: 1.5 }}>
        <ToggleButtonGroup exclusive size="small" value={bat.type} onChange={(_, v) => setType(v)}>
          {(['NMC', 'LFP'] as CellType[]).map((t) => (
            <ToggleButton key={t} value={t} sx={{ px: 1.5, py: 0.3, fontSize: 12, textTransform: 'none', color: 'var(--text-2)', borderColor: 'var(--line)',
              '&.Mui-selected': { bgcolor: '#15803d', color: '#fff', '&:hover': { bgcolor: '#16a34a' } } }}>
              {PRESETS[t].label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        {numField(tx('configure.cellsSeries'), bat.cells, setF('cells'), 1)}
        <Box sx={{ display: 'flex', alignItems: 'flex-end', gap: 1, flexWrap: 'wrap' }}>
          {numField(`${tx('configure.cellV')} · ${tx('configure.cellMin')}`, bat.min, setF('min'))}
          {numField(tx('configure.cellNom'), bat.nom, setF('nom'))}
          {numField(tx('configure.cellMax'), bat.max, setF('max'))}
        </Box>
        {machinePack && onReset && !sameBat(bat, machinePack) && (
          <Button size="small" onClick={onReset} title={tx('configure.resetPackTip')}
            sx={{ textTransform: 'none', fontSize: 11, color: '#60a5fa', ml: 'auto' }}>
            {tx('configure.resetPack')}
          </Button>
        )}
      </Box>

      {/* voltage-match bar */}
      <svg viewBox={`0 0 ${W} 84`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {/* baseline track */}
        <line x1={padX} y1={barY + barH / 2} x2={W - padX} y2={barY + barH / 2} stroke="var(--panel)" strokeWidth={1.25} />
        {/* battery window (empty → full) */}
        <defs>
          <linearGradient id="batgrad" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="#b45309" /><stop offset="50%" stopColor="#ca8a04" /><stop offset="100%" stopColor="#16a34a" />
          </linearGradient>
        </defs>
        <rect x={X(packMin)} y={barY} width={Math.max(1, X(packMax) - X(packMin))} height={barH} rx={3} fill="url(#batgrad)" opacity={0.55} stroke="var(--line)" />
        {/* nominal tick */}
        <line x1={X(packNom)} y1={barY - 5} x2={X(packNom)} y2={barY + barH + 5} stroke="#22c55e" strokeWidth={1} strokeDasharray="3 2" />
        {/* axis labels: 0 / min / nom / max */}
        <line x1={X(0)} y1={barY + barH + 2} x2={X(0)} y2={barY + barH + 8} stroke="var(--text-4)" strokeWidth={1} />
        <text x={X(0)} y={barY + barH + 18} fill="var(--text-3)" fontSize={11} textAnchor="start" fontFamily="monospace">0</text>
        <text x={X(packMin)} y={barY + barH + 18} fill="var(--text-2)" fontSize={11} textAnchor="middle" fontFamily="monospace">{fmt(packMin)}</text>
        <text x={X(packNom)} y={barY + barH + 18} fill="#22c55e" fontSize={11} textAnchor="middle" fontFamily="monospace">{fmt(packNom)}</text>
        <text x={X(packMax)} y={barY + barH + 18} fill="var(--text-2)" fontSize={11} textAnchor="middle" fontFamily="monospace">{fmt(packMax)}</text>
        <text x={X(packMin) + 4} y={barY + barH - 7} fill="var(--text-0)" fontSize={11} textAnchor="start">{tx('configure.barEmpty')}</text>
        <text x={X(packMax) - 4} y={barY + barH - 7} fill="var(--text-0)" fontSize={11} textAnchor="end">{tx('configure.barFull')}</text>
        {/* motor DC-voltage marker */}
        <line x1={mX} y1={barY - 14} x2={mX} y2={barY + barH + 8} stroke={color} strokeWidth={1} />
        <polygon points={`${mX - 5},${barY - 14} ${mX + 5},${barY - 14} ${mX},${barY - 6}`} fill={color} />
        <text x={Math.min(Math.max(mX, 40), W - 40)} y={barY - 18} fill={color} fontSize={12} fontWeight={700} textAnchor="middle" fontFamily="monospace">{vDcTip ? <title>{vDcTip}</title> : null}{tx('configure.motorV', { v: fmt(vDc) })}</text>
      </svg>
    </Box>
  );
};

export default BatteryPanel;
