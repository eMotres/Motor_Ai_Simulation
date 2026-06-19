/**
 * BatteryPanel — battery the user runs the motor from, and a voltage-match bar.
 *
 * The user picks a cell chemistry (NMC / LiFePO4), the series-cell count and the
 * per-cell nominal / max / min voltages.  The pack window (empty → full) is
 * cells × cell-voltage.  A horizontal bar shows that window with the motor's
 * required DC-bus voltage marked on it, so it's obvious whether the battery can
 * drive the motor — and down to what state of charge.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Typography, ToggleButton, ToggleButtonGroup, TextField } from '@mui/material';
import BatteryChargingFullIcon from '@mui/icons-material/BatteryChargingFull';

type CellType = 'NMC' | 'LFP';
interface Battery { type: CellType; cells: number; nom: number; max: number; min: number; }

const PRESETS: Record<CellType, { nom: number; max: number; min: number; label: string }> = {
  NMC: { nom: 3.7, max: 4.2, min: 3.0, label: 'NMC' },
  LFP: { nom: 3.2, max: 3.65, min: 2.5, label: 'LiFePO₄' },
};
const LS = 'configurator.battery.v1';
const LABEL = { fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const PANEL = { bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1, p: 2 } as const;
const fmt = (v: number, d = 0) => (Number.isFinite(v) ? v.toFixed(d) : '—');

const numField = (label: string, value: number, onChange: (e: any) => void, step = 0.05) => (
  <TextField label={label} type="number" value={value} onChange={onChange} size="small"
    inputProps={{ step, style: { fontSize: 13, padding: '4px 6px', width: 56 } }}
    InputLabelProps={{ sx: { fontSize: 11 } }}
    sx={{ '& .MuiOutlinedInput-notchedOutline': { borderColor: '#334155' } }} />
);

const BatteryPanel: React.FC<{ vDc: number }> = ({ vDc }) => {
  const [bat, setBat] = useState<Battery>(() => {
    try { const r = localStorage.getItem(LS); if (r) { const b = JSON.parse(r); if (b && b.type) return b; } } catch { /* ignore */ }
    return { type: 'NMC', cells: 100, ...PRESETS.NMC };
  });
  useEffect(() => { try { localStorage.setItem(LS, JSON.stringify(bat)); } catch { /* ignore */ } }, [bat]);

  const setType = (t: CellType | null) => { if (t) setBat((b) => ({ ...b, type: t, ...PRESETS[t] })); };
  const setF = (k: keyof Battery) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseFloat(e.target.value); if (Number.isFinite(v)) setBat((b) => ({ ...b, [k]: v }));
  };

  const packMin = bat.cells * bat.min, packNom = bat.cells * bat.nom, packMax = bat.cells * bat.max;

  const { status, color } = useMemo(() => {
    if (vDc > packMax) return { status: `Battery can't drive the motor — needs ${fmt(vDc)} V, pack maxes at ${fmt(packMax)} V (full). Add cells or lower the motor voltage.`, color: '#f87171' };
    if (vDc > packNom) return { status: `Runs only near full charge — motor needs ${fmt(vDc)} V, above the ${fmt(packNom)} V nominal.`, color: '#fbbf24' };
    if (vDc > packMin) return { status: `Within range — motor ${fmt(vDc)} V sits between empty (${fmt(packMin)} V) and nominal (${fmt(packNom)} V).`, color: '#4ade80' };
    return { status: `Full headroom — motor ${fmt(vDc)} V is below even the empty-pack voltage (${fmt(packMin)} V).`, color: '#4ade80' };
  }, [vDc, packMin, packNom, packMax]);

  // ── voltage bar ───────────────────────────────────────────────────────────
  const W = 640, padX = 14;
  const lo = Math.min(packMin, vDc) * 0.95, hi = Math.max(packMax, vDc) * 1.05;
  const X = (v: number) => padX + ((v - lo) / (hi - lo)) * (W - 2 * padX);
  const barY = 30, barH = 22;
  const mX = X(vDc);

  return (
    <Box sx={PANEL}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.25, flexWrap: 'wrap' }}>
        <BatteryChargingFullIcon sx={{ color: '#22c55e', fontSize: 20 }} />
        <Typography sx={{ fontSize: 13, fontWeight: 800, color: '#e2e8f0' }}>Battery & voltage match</Typography>
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 12, color: '#94a3b8', fontFamily: 'monospace' }}>
          Pack {fmt(packNom)} V nom · {fmt(packMin)}–{fmt(packMax)} V
        </Typography>
      </Box>

      {/* inputs */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', mb: 1.5 }}>
        <ToggleButtonGroup exclusive size="small" value={bat.type} onChange={(_, v) => setType(v)}>
          {(['NMC', 'LFP'] as CellType[]).map((t) => (
            <ToggleButton key={t} value={t} sx={{ px: 1.5, py: 0.3, fontSize: 12, textTransform: 'none', color: '#94a3b8', borderColor: '#334155',
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
        <line x1={padX} y1={barY + barH / 2} x2={W - padX} y2={barY + barH / 2} stroke="#1e293b" strokeWidth={2} />
        {/* battery window (empty → full) */}
        <defs>
          <linearGradient id="batgrad" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="#b45309" /><stop offset="50%" stopColor="#ca8a04" /><stop offset="100%" stopColor="#16a34a" />
          </linearGradient>
        </defs>
        <rect x={X(packMin)} y={barY} width={Math.max(1, X(packMax) - X(packMin))} height={barH} rx={3} fill="url(#batgrad)" opacity={0.55} stroke="#334155" />
        {/* nominal tick */}
        <line x1={X(packNom)} y1={barY - 5} x2={X(packNom)} y2={barY + barH + 5} stroke="#22c55e" strokeWidth={1.5} strokeDasharray="3 2" />
        {/* end + nominal labels */}
        <text x={X(packMin)} y={barY + barH + 18} fill="#94a3b8" fontSize={11} textAnchor="middle" fontFamily="monospace">{fmt(packMin)}</text>
        <text x={X(packNom)} y={barY + barH + 18} fill="#22c55e" fontSize={11} textAnchor="middle" fontFamily="monospace">{fmt(packNom)}</text>
        <text x={X(packMax)} y={barY + barH + 18} fill="#94a3b8" fontSize={11} textAnchor="middle" fontFamily="monospace">{fmt(packMax)}</text>
        <text x={X(packMin)} y={barY - 8} fill="#475569" fontSize={9} textAnchor="middle">empty</text>
        <text x={X(packMax)} y={barY - 8} fill="#475569" fontSize={9} textAnchor="middle">full</text>
        {/* motor DC-voltage marker */}
        <line x1={mX} y1={barY - 14} x2={mX} y2={barY + barH + 8} stroke={color} strokeWidth={2.5} />
        <polygon points={`${mX - 5},${barY - 14} ${mX + 5},${barY - 14} ${mX},${barY - 6}`} fill={color} />
        <text x={Math.min(Math.max(mX, 40), W - 40)} y={barY - 18} fill={color} fontSize={12} fontWeight={700} textAnchor="middle" fontFamily="monospace">motor {fmt(vDc)} V</text>
      </svg>

      <Typography sx={{ fontSize: 12, color, mt: 0.5 }}>{status}</Typography>
    </Box>
  );
};

export default BatteryPanel;
