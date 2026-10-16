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
import { batteryFieldText, editBatteryField, validBatteryNumbers, batteryInputIssues, sameBattery } from '../../lib/configuratorBattery';

const tx = nsT('controller');   // EN source, ZH mirror (docs/I18N.md)

export type CellType = 'NMC' | 'LFP';
export interface Battery { type: CellType; cells: number; nom: number; max: number; min: number;
  draft?: Partial<Record<'cells' | 'min' | 'nom' | 'max', string>>; }

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
const numField = (label: string, value: string, onChange: (e: any) => void, step = 0.05, width = 66, error?: string) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
    <Typography sx={{ ...LABEL, color: error ? '#f87171' : LABEL.color }}>{label}</Typography>
    <input type="number" aria-label={label} aria-invalid={!!error} value={value} step={step} onChange={onChange}
      style={{ width, background: 'transparent', border: `1px solid ${error ? '#f87171' : 'var(--line)'}`, borderRadius: 4, color: 'var(--text-0)', fontSize: 14, fontWeight: 700, fontFamily: 'monospace', textAlign: 'right', padding: '1px 5px' }} />
    {error && <Typography role="alert" sx={{ color: '#f87171', fontSize: 10, lineHeight: 1.15, maxWidth: width + 18 }}>{error}</Typography>}
  </Box>
);

const BatteryPanel: React.FC<{
  vDc: number; bat: Battery; onChange: (b: Battery) => void;
  /** the pack the machine was saved with; with `onReset` it adds a "reset to machine pack" control */
  machinePack?: Battery | null; onReset?: () => void;
  /** no pack is saved for this machine: the block shows the stock default and SAYS so */
  stockNote?: boolean;
  /** hover text of the motor-voltage marker (which operating point it is for) */
  vDcTip?: string;
}> = ({ vDc, bat, onChange, machinePack, onReset, stockNote, vDcTip }) => {
  const setType = (t: CellType | null) => {
    if (t) onChange({ ...bat, type: t, ...PRESETS[t], draft: bat.draft?.cells == null ? undefined : { cells: bat.draft.cells } });
  };
  const setF = (k: 'cells' | 'min' | 'nom' | 'max') => (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(editBatteryField(bat, k, e.target.value));
  };

  const nums = validBatteryNumbers(bat);
  const packMin = nums ? nums.cells * nums.min : NaN;
  const packNom = nums ? nums.cells * nums.nom : NaN;
  const packMax = nums ? nums.cells * nums.max : NaN;
  const issues = new Set(batteryInputIssues(bat));
  const valid = issues.size === 0;

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
        {valid ? <Typography sx={{ fontSize: 12, color: 'var(--text-2)', fontFamily: 'monospace' }}>
          {tx('configure.packSummary', { nom: fmt(packNom), min: fmt(packMin), max: fmt(packMax) })}
        </Typography> : <Typography role="alert" sx={{ fontSize: 12, color: '#f87171', fontWeight: 700 }}>
          {tx('configure.invalidPack')}
        </Typography>}
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
        {numField(tx('configure.cellsSeries'), batteryFieldText(bat, 'cells'), setF('cells'), 1, 66,
          issues.has('cells') ? tx('configure.invalidCellCount') : undefined)}
        <Box sx={{ display: 'flex', alignItems: 'flex-end', gap: 1, flexWrap: 'wrap' }}>
          {numField(`${tx('configure.cellV')} · ${tx('configure.cellMin')}`, batteryFieldText(bat, 'min'), setF('min'), 0.05, 66,
            issues.has('min') ? tx('configure.invalidCellMin') : undefined)}
          {numField(tx('configure.cellNom'), batteryFieldText(bat, 'nom'), setF('nom'), 0.05, 66,
            issues.has('nom') ? tx('configure.invalidCellNom') : undefined)}
          {numField(tx('configure.cellMax'), batteryFieldText(bat, 'max'), setF('max'), 0.05, 66,
            issues.has('max') ? tx('configure.invalidCellMax') : undefined)}
        </Box>
        {machinePack && onReset && !sameBattery(bat, machinePack) && (
          <Button size="small" onClick={onReset} title={tx('configure.resetPackTip')}
            sx={{ textTransform: 'none', fontSize: 11, color: '#60a5fa', ml: 'auto' }}>
            {tx('configure.resetPack')}
          </Button>
        )}
      </Box>

      {/* voltage-match bar */}
      {valid ? <svg viewBox={`0 0 ${W} 84`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {/* baseline track */}
        <line x1={padX} y1={barY + barH / 2} x2={W - padX} y2={barY + barH / 2} stroke="var(--panel)" strokeWidth={1.25} />
        {/* battery window (empty → full) */}
        <defs>
          <linearGradient id="batgrad" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="#b45309" /><stop offset="50%" stopColor="#ca8a04" /><stop offset="100%" stopColor="#16a34a" />
          </linearGradient>
        </defs>
        {/* below empty (0 -> pack min): the range the pack never supplies, own colour (owner 2026-10-15) */}
        <rect x={X(0)} y={barY} width={Math.max(0, X(packMin) - X(0))} height={barH} rx={3} fill="#64748b" opacity={0.28} stroke="var(--line)" />
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
      </svg> : <Typography sx={{ fontSize: 11, color: '#f87171', py: 1 }}>{tx('configure.invalidPackTip')}</Typography>}
    </Box>
  );
};

export default BatteryPanel;
