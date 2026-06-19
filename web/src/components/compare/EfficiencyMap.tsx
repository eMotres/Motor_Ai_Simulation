/**
 * EfficiencyMap — efficiency over the torque × speed plane for the tuned motor.
 *
 * For each (rpm, torque) cell we invert torque → required current (T ∝ I at the
 * fixed load angle), run the passport model, and colour the cell by efficiency.
 * Cells the battery can't reach (required DC bus > pack max) are shaded dark.
 * The torque axis tops out at the wire-current limit; the white ring marks the
 * current operating point.
 */
import React, { useEffect, useRef } from 'react';
import { Box, Typography } from '@mui/material';
import { scaleMotor, maxCurrent, type Passport, type Knobs } from '../../lib/motorScaling';

const SQRT3 = Math.sqrt(3);
const RPM_MAX = 8000;
const ETA_LO = 0.80, ETA_HI = 0.99;

/** efficiency → colour: red (low) → yellow → green (high), clamped to [lo,hi]. */
function effColor(eta: number, lo: number, hi: number): string {
  const t = Math.max(0, Math.min(1, (eta - lo) / (hi - lo)));
  if (t < 0.5) { const u = t / 0.5; return `rgb(230,${Math.round(70 + u * 180)},45)`; }
  const u = (t - 0.5) / 0.5; return `rgb(${Math.round(230 - u * 185)},225,${Math.round(45 + u * 45)})`;
}

const EfficiencyMap: React.FC<{ p: Passport; knobs: Knobs; packMax: number }> = ({ p, knobs, packMax }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = canvasRef.current; if (!cv) return;
    const ctx = cv.getContext('2d'); if (!ctx) return;
    const W = cv.width, H = cv.height;
    const ML = 46, MB = 30, MT = 8, MR = 70;
    const pw = W - ML - MR, ph = H - MT - MB;
    ctx.clearRect(0, 0, W, H);

    const iMax = maxCurrent(p, knobs);
    // T = base·(I/I0)  ⇒  I = I0·T/base  (base = torque at I0 for this winding/length)
    const fN = p.N0 ? knobs.N / p.N0 : 1, fL = p.L0_mm ? knobs.L_mm / p.L0_mm : 1;
    const fConn = p.nP0 && knobs.nP ? p.nP0 / knobs.nP : 1;
    const base = p.T0_Nm * fN * fL * fConn;
    const Tmax = scaleMotor(p, { ...knobs, I_A: iMax }).T_Nm || 1;

    const NX = 90, NY = 56, cw = pw / NX, ch = ph / NY;
    for (let ix = 0; ix < NX; ix++) {
      const rpm = ((ix + 0.5) / NX) * RPM_MAX;
      const x = ML + ix * cw;
      for (let iy = 0; iy < NY; iy++) {
        const T = ((iy + 0.5) / NY) * Tmax;
        const I = base > 0 ? (p.I0_A * T) / base : 0;
        const s = scaleMotor(p, { ...knobs, I_A: I, rpm });
        const vdc = s.Vphase_peak_V * SQRT3;
        const y = MT + ph - (iy + 1) * ch;
        ctx.fillStyle = vdc > packMax ? '#0f172a' : s.efficiency <= 0 ? '#0b1424' : effColor(s.efficiency, ETA_LO, ETA_HI);
        ctx.fillRect(x, y, cw + 0.6, ch + 0.6);
      }
    }

    // operating point
    const opT = scaleMotor(p, knobs).T_Nm;
    const ox = ML + Math.min(1, knobs.rpm / RPM_MAX) * pw;
    const oy = MT + ph - Math.min(1, opT / Tmax) * ph;
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(ox, oy, 4.5, 0, 2 * Math.PI); ctx.stroke();

    // frame + axes
    ctx.strokeStyle = '#334155'; ctx.lineWidth = 1; ctx.strokeRect(ML, MT, pw, ph);
    ctx.fillStyle = '#64748b'; ctx.font = '10px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (let r = 0; r <= RPM_MAX; r += 2000) ctx.fillText(`${r / 1000}k`, ML + (r / RPM_MAX) * pw, MT + ph + 5);
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let i = 0; i <= 4; i++) { const t = (i / 4) * Tmax; ctx.fillText(`${t.toFixed(0)}`, ML - 5, MT + ph - (i / 4) * ph); }
    ctx.textAlign = 'center'; ctx.fillText('Speed (rpm)', ML + pw / 2, MT + ph + 17);
    ctx.save(); ctx.translate(11, MT + ph / 2); ctx.rotate(-Math.PI / 2); ctx.fillText('Torque (N·m)', 0, 0); ctx.restore();

    // colour bar
    const cbX = W - MR + 16, cbW = 12;
    for (let i = 0; i < ph; i++) ctx.fillStyle = effColor(ETA_HI - (i / ph) * (ETA_HI - ETA_LO), ETA_LO, ETA_HI), ctx.fillRect(cbX, MT + i, cbW, 1);
    ctx.strokeStyle = '#334155'; ctx.strokeRect(cbX, MT, cbW, ph);
    ctx.fillStyle = '#64748b'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    [ETA_HI, (ETA_HI + ETA_LO) / 2, ETA_LO].forEach((e, i) => ctx.fillText(`${(e * 100).toFixed(0)}%`, cbX + cbW + 3, MT + (i / 2) * ph));
  }, [p, knobs, packMax]);

  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography sx={{ fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em', mb: 0.5 }}>
        Efficiency map (torque × speed) — ○ operating point · dark = battery can't reach
      </Typography>
      <canvas ref={canvasRef} width={780} height={340} style={{ width: '100%', height: 'auto', display: 'block' }} />
    </Box>
  );
};

export default EfficiencyMap;
