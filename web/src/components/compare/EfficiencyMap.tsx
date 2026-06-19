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

// Ansys-style spectrum: blue (low) → cyan → green → yellow → orange → red (high).
const JET_STOPS: [number, [number, number, number]][] = [
  [0.00, [0, 0, 255]],     // blue
  [0.22, [0, 220, 255]],   // cyan
  [0.45, [0, 210, 0]],     // green
  [0.65, [240, 240, 0]],   // yellow
  [0.82, [255, 140, 0]],   // orange
  [1.00, [220, 0, 0]],     // red
];
/** efficiency → Ansys spectrum colour, clamped to [lo,hi]. */
function effColor(eta: number, lo: number, hi: number): string {
  const t = Math.max(0, Math.min(1, (eta - lo) / (hi - lo)));
  for (let i = 1; i < JET_STOPS.length; i++) {
    if (t <= JET_STOPS[i][0]) {
      const [t0, c0] = JET_STOPS[i - 1], [t1, c1] = JET_STOPS[i];
      const u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
      return `rgb(${Math.round(c0[0] + u * (c1[0] - c0[0]))},${Math.round(c0[1] + u * (c1[1] - c0[1]))},${Math.round(c0[2] + u * (c1[2] - c0[2]))})`;
    }
  }
  return 'rgb(220,0,0)';
}

const EfficiencyMap: React.FC<{ p: Passport; knobs: Knobs; packMax: number }> = ({ p, knobs, packMax }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = canvasRef.current; if (!cv) return;
    const ctx = cv.getContext('2d'); if (!ctx) return;
    // Render at device resolution so the labels stay crisp: the canvas is shown
    // at ~full panel width via CSS, so a small fixed buffer would be upscaled and
    // blur the text.  Draw in logical (LW×LH) coordinates scaled to the buffer.
    const LW = 780, LH = 340;
    const dpr = window.devicePixelRatio || 1;
    const cssW = cv.clientWidth || LW;
    cv.width = Math.round(cssW * dpr);
    cv.height = Math.round(cssW * (LH / LW) * dpr);
    ctx.setTransform(cv.width / LW, 0, 0, cv.height / LH, 0, 0);
    const W = LW, H = LH;
    const ML = 46, MB = 30, MT = 8, MR = 70;
    const pw = W - ML - MR, ph = H - MT - MB;
    ctx.clearRect(0, 0, W, H);

    const iMax = maxCurrent(p, knobs);
    // T = base·(I/I0)  ⇒  I = I0·T/base  (base = torque at I0 for this winding/length)
    const fN = p.N0 ? knobs.N / p.N0 : 1, fL = p.L0_mm ? knobs.L_mm / p.L0_mm : 1;
    const fConn = p.nP0 && knobs.nP ? p.nP0 / knobs.nP : 1;
    const base = p.T0_Nm * fN * fL * fConn;
    const Tmax = scaleMotor(p, { ...knobs, I_A: iMax }).T_Nm || 1;

    // axes don't start at 0 (degenerate there): rpm from 500, torque from ~10% nominal
    const RPM_MIN = 500, T_MIN = 0.1 * base;
    const NX = 90, NY = 56, cw = pw / NX, ch = ph / NY;
    // pass 1 — efficiency grid + auto-range over reachable cells (Ansys scales the
    // legend to the field's actual min/max, so the full spectrum spans the data).
    const eta = new Array<number>(NX * NY), reach = new Array<boolean>(NX * NY);
    let lo = Infinity, hi = -Infinity;
    for (let ix = 0; ix < NX; ix++) {
      const rpm = RPM_MIN + ((ix + 0.5) / NX) * (RPM_MAX - RPM_MIN);
      for (let iy = 0; iy < NY; iy++) {
        const T = T_MIN + ((iy + 0.5) / NY) * (Tmax - T_MIN);
        const I = base > 0 ? (p.I0_A * T) / base : 0;
        const s = scaleMotor(p, { ...knobs, I_A: I, rpm });
        const r = s.Vphase_peak_V * SQRT3 <= packMax && s.efficiency > 0;
        eta[ix * NY + iy] = s.efficiency; reach[ix * NY + iy] = r;
        if (r) { if (s.efficiency < lo) lo = s.efficiency; if (s.efficiency > hi) hi = s.efficiency; }
      }
    }
    if (!isFinite(lo) || hi - lo < 1e-4) { lo = ETA_LO; hi = ETA_HI; }
    // pass 2 — coloured cells
    for (let ix = 0; ix < NX; ix++) for (let iy = 0; iy < NY; iy++) {
      ctx.fillStyle = reach[ix * NY + iy] ? effColor(eta[ix * NY + iy], lo, hi) : '#0f172a';
      ctx.fillRect(ML + ix * cw, MT + ph - (iy + 1) * ch, cw + 0.6, ch + 0.6);
    }
    // pass 3 — iso-efficiency contour lines (marching squares over cell centres)
    const cX = (ix: number) => ML + (ix + 0.5) * cw, cY = (iy: number) => MT + ph - (iy + 0.5) * ch;
    const SEGS: Record<number, number[][]> = { 1: [[3, 0]], 2: [[0, 1]], 3: [[3, 1]], 4: [[1, 2]], 5: [[3, 0], [1, 2]], 6: [[0, 2]], 7: [[3, 2]], 8: [[2, 3]], 9: [[2, 0]], 10: [[0, 1], [2, 3]], 11: [[2, 1]], 12: [[1, 3]], 13: [[1, 0]], 14: [[0, 3]] };
    const LEVELS = [0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.975, 0.98, 0.985];
    ctx.lineWidth = 1; ctx.font = '600 10px sans-serif';
    for (const L of LEVELS) {
      if (L <= lo || L >= hi) continue;
      ctx.strokeStyle = 'rgba(255,255,255,0.5)';
      let labelPt: [number, number] | null = null;
      for (let ix = 0; ix < NX - 1; ix++) for (let iy = 0; iy < NY - 1; iy++) {
        const corners = [[ix, iy], [ix + 1, iy], [ix + 1, iy + 1], [ix, iy + 1]];
        if (!corners.every(([a, b]) => reach[a * NY + b])) continue;
        const v = corners.map(([a, b]) => eta[a * NY + b]);
        const idx = (v[0] > L ? 1 : 0) | (v[1] > L ? 2 : 0) | (v[2] > L ? 4 : 0) | (v[3] > L ? 8 : 0);
        const segs = SEGS[idx]; if (!segs) continue;
        const pos = corners.map(([a, b]) => [cX(a), cY(b)] as [number, number]);
        const ept = (e: number): [number, number] => { const a = e, b = (e + 1) % 4; const t = (L - v[a]) / (v[b] - v[a]); return [pos[a][0] + t * (pos[b][0] - pos[a][0]), pos[a][1] + t * (pos[b][1] - pos[a][1])]; };
        for (const [ea, eb] of segs) { const pa = ept(ea), pb = ept(eb); ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]); ctx.stroke(); if (!labelPt && pa[0] > ML + pw * 0.25) labelPt = pa; }
      }
      if (labelPt) {
        const tx = `${(L * 100).toFixed(L >= 0.975 ? 1 : 0)}`; const tw = ctx.measureText(tx).width;
        ctx.fillStyle = 'rgba(11,20,36,0.7)'; ctx.fillRect(labelPt[0] - tw / 2 - 2, labelPt[1] - 6, tw + 4, 12);
        ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(tx, labelPt[0], labelPt[1]);
      }
    }

    // operating point
    const opT = scaleMotor(p, knobs).T_Nm;
    const fx = Math.max(0, Math.min(1, (knobs.rpm - RPM_MIN) / (RPM_MAX - RPM_MIN)));
    const fy = Math.max(0, Math.min(1, (opT - T_MIN) / (Tmax - T_MIN)));
    const ox = ML + fx * pw;
    const oy = MT + ph - fy * ph;
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(ox, oy, 4.5, 0, 2 * Math.PI); ctx.stroke();

    // frame + axes
    ctx.strokeStyle = '#334155'; ctx.lineWidth = 1; ctx.strokeRect(ML, MT, pw, ph);
    ctx.fillStyle = '#64748b'; ctx.font = '10px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (const r of [RPM_MIN, 2000, 4000, 6000, 8000]) ctx.fillText(`${r / 1000}k`, ML + ((r - RPM_MIN) / (RPM_MAX - RPM_MIN)) * pw, MT + ph + 5);
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let i = 0; i <= 4; i++) { const t = T_MIN + (i / 4) * (Tmax - T_MIN); ctx.fillText(`${t.toFixed(0)}`, ML - 5, MT + ph - (i / 4) * ph); }
    ctx.textAlign = 'center'; ctx.fillText('Speed (rpm)', ML + pw / 2, MT + ph + 17);
    ctx.save(); ctx.translate(11, MT + ph / 2); ctx.rotate(-Math.PI / 2); ctx.fillText('Torque (N·m)', 0, 0); ctx.restore();

    // colour bar
    const cbX = W - MR + 16, cbW = 12;
    for (let i = 0; i < ph; i++) ctx.fillStyle = effColor(hi - (i / ph) * (hi - lo), lo, hi), ctx.fillRect(cbX, MT + i, cbW, 1);
    ctx.strokeStyle = '#334155'; ctx.strokeRect(cbX, MT, cbW, ph);
    ctx.fillStyle = '#64748b'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    [hi, (hi + lo) / 2, lo].forEach((e, i) => ctx.fillText(`${(e * 100).toFixed(1)}%`, cbX + cbW + 3, MT + (i / 2) * ph));
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
