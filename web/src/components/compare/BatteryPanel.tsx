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
import { Box, Typography, ToggleButton, ToggleButtonGroup, TextField } from '@mui/material';
import BatteryChargingFullIcon from '@mui/icons-material/BatteryChargingFull';

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

const numField = (label: string, value: number, onChange: (e: any) => void, step = 0.05) => (
  <TextField label={label} type="number" value={value} onChange={onChange} size="small"
    inputProps={{ step, style: { fontSize: 13, padding: '4px 6px', width: 56 } }}
    InputLabelProps={{ sx: { fontSize: 11 } }}
    sx={{ '& .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--line)' } }} />
);

const BatteryPanel: React.FC<{ vDc: number; bat: Battery; onChange: (b: Battery) => void }> = ({ vDc, bat, onChange }) => {
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
  const W = 640, padX = 14;
  const lo = 0, hi = Math.max(packMax, vDc) * 1.05;   // axis from 0 → whole range visible
  const X = (v: number) => padX + ((v - lo) / (hi - lo)) * (W - 2 * padX);
  const barY = 30, barH = 22;
  const mX = X(vDc);

  return (
    <Box sx={PANEL}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.25, flexWrap: 'wrap' }}>
        <BatteryChargingFullIcon sx={{ color: '#22c55e', fontSize: 20 }} />
        <Typography sx={{ fontSize: 13, fontWeight: 800, color: 'var(--text-0)' }}>Battery & voltage match</Typography>
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)', fontFamily: 'monospace' }}>
          Pack {fmt(packNom)} V nom · {fmt(packMin)}–{fmt(packMax)} V
        </Typography>
      </Box>

      {/* inputs */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', mb: 1.5 }}>
        <ToggleButtonGroup exclusive size="small" value={bat.type} onChange={(_, v) => setType(v)}>
          {(['NMC', 'LFP'] as CellType[]).map((t) => (
            <ToggleButton key={t} value={t} sx={{ px: 1.5, py: 0.3, fontSize: 12, textTransform: 'none', color: 'var(--text-2)', borderColor: 'var(--line)',
              '&.Mui-selected': { bgcolor: '#15803d', color: '#fff', '&:hover': { bgcolor: '#16a34a' } } }}>
              {PRESETS[t].label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        {numField('Cells (series)', bat.cells, setF('cells'), 1)}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography sx={LABEL}>Cell V</Typography>
          {numField('min', bat.min, setF('min'))}
          {numField('nom', bat.nom, setF('nom'))}
          {numField('max', bat.max, setF('max'))}
        </Box>
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
        <text x={X(packMin)} y={barY - 8} fill="var(--text-4)" fontSize={9} textAnchor="middle">empty</text>
        <text x={X(packMax)} y={barY - 8} fill="var(--text-4)" fontSize={9} textAnchor="middle">full</text>
        {/* motor DC-voltage marker */}
        <line x1={mX} y1={barY - 14} x2={mX} y2={barY + barH + 8} stroke={color} strokeWidth={1} />
        <polygon points={`${mX - 5},${barY - 14} ${mX + 5},${barY - 14} ${mX},${barY - 6}`} fill={color} />
        <text x={Math.min(Math.max(mX, 40), W - 40)} y={barY - 18} fill={color} fontSize={12} fontWeight={700} textAnchor="middle" fontFamily="monospace">motor {fmt(vDc)} V</text>
      </svg>
    </Box>
  );
};

export default BatteryPanel;
