// ─────────────────────────────────────────────────────────────────────────────
// 3-D objective-space scatter for the optimizer: torque/mass (x) × efficiency
// (y) × ripple (HEIGHT, z).  The 2-D ScatterChart is the floor of this; lifting
// ripple onto z makes the eff×td-vs-ripple trade-off literally visual.  A
// translucent plane marks the ripple gate — points below it (green) are
// feasible, above (red) are not.  Self-contained canvas (no 3-D lib): drag to
// orbit, hover for values.  Live — redraws as the run streams new points.
// ─────────────────────────────────────────────────────────────────────────────
import React, { useEffect, useRef, useState } from 'react';
import { Box, FormControlLabel, Checkbox, Typography } from '@mui/material';

export interface P3 { td: number; eff: number; ripple: number; kind?: string }

const Scatter3D: React.FC<{ points: P3[]; rippleMax: number; best?: P3 | null }> =
  ({ points, rippleMax, best }) => {
    const cvRef = useRef<HTMLCanvasElement | null>(null);
    const rot = useRef({ yaw: -0.62, pitch: 0.46, drag: false, lx: 0, ly: 0 });
    const hitRef = useRef<{ x: number; y: number; p: P3; best: boolean }[]>([]);
    const [showProj, setShowProj] = useState(true);
    const [hover, setHover] = useState<P3 | null>(null);

    useEffect(() => {
      const cv = cvRef.current; if (!cv) return;
      const ctx = cv.getContext('2d'); if (!ctx) return;
      const data = (points || []).filter(p => p && p.td != null && p.eff != null && p.ripple != null);
      const all: { p: P3; best: boolean }[] =
        [...data.map(p => ({ p, best: false })), ...(best && best.td != null ? [{ p: best, best: true }] : [])];
      let raf = 0; let dirty = true;
      let W = 0, H = 0, cx = 0, cy = 0, scale = 0, dpr = 1;

      const ext = (f: (p: P3) => number) => {
        let lo = Infinity, hi = -Infinity;
        all.forEach(({ p }) => { const v = f(p); if (v < lo) lo = v; if (v > hi) hi = v; });
        return [lo, hi];
      };
      let tdR = ext(p => p.td), efR = ext(p => p.eff * 100), rpR = ext(p => p.ripple);
      rpR = [Math.min(rpR[0], rippleMax), Math.max(rpR[1], rippleMax)];
      const pad = (e: number[]) => { const p = (e[1] - e[0]) * 0.1 || 1; return [e[0] - p, e[1] + p]; };
      tdR = pad(tdR); efR = pad(efR); rpR = pad(rpR);
      const nx = (v: number, r: number[]) => (v - r[0]) / (r[1] - r[0]) * 2 - 1;

      const resize = () => {
        dpr = Math.min(2, window.devicePixelRatio || 1);
        W = cv.clientWidth || cv.parentElement?.clientWidth || 0;
        H = cv.clientHeight || cv.parentElement?.clientHeight || 360;
        if (!W) { requestAnimationFrame(resize); return; }   // not laid out yet → retry next frame
        cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        cx = W * 0.5; cy = H * 0.56; scale = Math.min(W, H) * 0.32; dirty = true;
      };
      const proj = (x: number, y: number, z: number) => {
        const { yaw, pitch } = rot.current;
        const ca = Math.cos(yaw), sa = Math.sin(yaw), cb = Math.cos(pitch), sb = Math.sin(pitch);
        const x1 = x * ca + y * sa, y1 = -x * sa + y * ca, z1 = z;
        return { x: cx + x1 * scale, y: cy - (-y1 * sb + z1 * cb) * scale, d: y1 * cb + z1 * sb };
      };
      const AX = 'rgba(255,255,255,.55)', GRID = 'rgba(255,255,255,.13)', TXT = 'rgba(255,255,255,.72)';
      const line = (a: any, b: any, col: string, w = 1) => {
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.strokeStyle = col; ctx.lineWidth = w; ctx.stroke();
      };
      const txt = (p: any, s: string, col: string, al: CanvasTextAlign = 'center') => {
        ctx.fillStyle = col; ctx.font = '11px sans-serif'; ctx.textAlign = al; ctx.textBaseline = 'middle'; ctx.fillText(s, p.x, p.y);
      };

      const draw = () => {
        ctx.clearRect(0, 0, W, H);
        if (!all.length) { txt({ x: cx, y: cy }, 'no points yet', TXT); return; }
        const c: any[] = [];
        for (let xi = -1; xi <= 1; xi += 2) for (let yi = -1; yi <= 1; yi += 2) for (let zi = -1; zi <= 1; zi += 2) c.push(proj(xi, yi, zi));
        [[0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3], [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7]]
          .forEach(e => line(c[e[0]], c[e[1]], GRID, 1));
        const gz = nx(rippleMax, rpR);
        if (gz >= -1 && gz <= 1) {
          const g = [proj(-1, -1, gz), proj(1, -1, gz), proj(1, 1, gz), proj(-1, 1, gz)];
          ctx.beginPath(); ctx.moveTo(g[0].x, g[0].y); g.slice(1).forEach(q => ctx.lineTo(q.x, q.y)); ctx.closePath();
          ctx.fillStyle = 'rgba(239,68,68,.10)'; ctx.fill(); ctx.strokeStyle = 'rgba(239,68,68,.45)'; ctx.lineWidth = 1; ctx.stroke();
          txt(proj(1, 1, gz), `gate ${rippleMax.toFixed(0)}%`, 'rgba(248,113,113,.9)', 'left');
        }
        const Pj = all.map(({ p, best: b }) => ({ ...proj(nx(p.td, tdR), nx(p.eff * 100, efR), nx(p.ripple, rpR)), p, best: b, feas: p.ripple <= rippleMax }));
        Pj.sort((a, b) => a.d - b.d);
        hitRef.current = Pj.map(q => ({ x: q.x, y: q.y, p: q.p, best: q.best }));
        if (showProj) Pj.forEach(q => {
          const s = proj(nx(q.p.td, tdR), nx(q.p.eff * 100, efR), -1);
          line(q, s, 'rgba(255,255,255,.07)', 1);
          ctx.beginPath(); ctx.arc(s.x, s.y, 2, 0, 7); ctx.fillStyle = 'rgba(170,180,200,.22)'; ctx.fill();
        });
        Pj.forEach(q => {
          if (q.best) {
            ctx.save(); ctx.translate(q.x, q.y); ctx.beginPath();
            for (let i = 0; i < 10; i++) { const a = Math.PI / 5 * i - Math.PI / 2, rr = i % 2 ? 3.5 : 8; ctx.lineTo(Math.cos(a) * rr, Math.sin(a) * rr); }
            ctx.closePath(); ctx.fillStyle = '#fbbf24'; ctx.fill(); ctx.restore();
          } else {
            ctx.beginPath(); ctx.arc(q.x, q.y, 4, 0, 7);
            ctx.globalAlpha = 0.8; ctx.fillStyle = q.feas ? '#22c55e' : '#ef4444'; ctx.fill(); ctx.globalAlpha = 1;
            ctx.lineWidth = 0.6; ctx.strokeStyle = 'rgba(0,0,0,.4)'; ctx.stroke();
          }
        });
        const tick = (ax: number) => {
          let r: number[], lab: string, v0: any, v1: any;
          if (ax === 0) { lab = 'Torque / mass'; r = tdR; v0 = proj(-1, -1, -1); v1 = proj(1, -1, -1); }
          else if (ax === 1) { lab = 'Efficiency %'; r = efR; v0 = proj(1, -1, -1); v1 = proj(1, 1, -1); }
          else { lab = 'Ripple %'; r = rpR; v0 = proj(-1, -1, -1); v1 = proj(-1, -1, 1); }
          line(v0, v1, AX, 1.4);
          for (let t = 0; t <= 2; t++) {
            const f = t / 2, val = r[0] + (r[1] - r[0]) * f;
            let wp: any; if (ax === 0) wp = proj(-1 + 2 * f, -1, -1); else if (ax === 1) wp = proj(1, -1 + 2 * f, -1); else wp = proj(-1, -1, -1 + 2 * f);
            const off = ax === 1 ? { x: wp.x + 12, y: wp.y } : (ax === 2 ? { x: wp.x - 14, y: wp.y } : { x: wp.x, y: wp.y + 12 });
            txt(off, ax === 0 ? val.toFixed(2) : val.toFixed(ax === 2 ? 0 : 1), TXT, ax === 1 ? 'left' : (ax === 2 ? 'end' : 'center'));
          }
          const mid = proj(ax === 0 ? 0 : (ax === 1 ? 1 : -1), ax === 1 ? 0 : -1, ax === 2 ? 0 : -1);
          const lp = ax === 0 ? { x: mid.x, y: mid.y + 26 } : (ax === 1 ? { x: mid.x + 14, y: mid.y + 18 } : { x: mid.x - 28, y: mid.y });
          txt(lp, lab, AX, ax === 1 ? 'left' : (ax === 2 ? 'end' : 'center'));
        };
        tick(0); tick(1); tick(2);
      };
      const loop = () => { if (dirty) { draw(); dirty = false; } raf = requestAnimationFrame(loop); };
      resize(); loop();

      const onResize = () => resize();
      const md = (e: MouseEvent) => { rot.current.drag = true; rot.current.lx = e.clientX; rot.current.ly = e.clientY; };
      const mu = () => { rot.current.drag = false; };
      const mvRot = (e: MouseEvent) => {
        if (!rot.current.drag) return;
        rot.current.yaw += (e.clientX - rot.current.lx) * 0.01;
        rot.current.pitch = Math.max(-0.2, Math.min(1.3, rot.current.pitch + (e.clientY - rot.current.ly) * 0.006));
        rot.current.lx = e.clientX; rot.current.ly = e.clientY; dirty = true;
      };
      const mvHover = (e: MouseEvent) => {
        if (rot.current.drag) { setHover(null); return; }
        const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
        let found: P3 | null = null, bd = 150;
        hitRef.current.forEach(q => { const dx = q.x - mx, dy = q.y - my, dd = dx * dx + dy * dy; if (dd < bd) { bd = dd; found = q.p; } });
        setHover(found);
      };
      window.addEventListener('resize', onResize);
      cv.addEventListener('mousedown', md); window.addEventListener('mouseup', mu);
      window.addEventListener('mousemove', mvRot); cv.addEventListener('mousemove', mvHover);
      cv.addEventListener('mouseleave', () => setHover(null));
      return () => {
        cancelAnimationFrame(raf); window.removeEventListener('resize', onResize);
        cv.removeEventListener('mousedown', md); window.removeEventListener('mouseup', mu);
        window.removeEventListener('mousemove', mvRot); cv.removeEventListener('mousemove', mvHover);
      };
    }, [points, best, rippleMax, showProj]);

    return (
      <Box sx={{ position: 'relative', width: '100%', height: '100%' }}>
        <canvas ref={cvRef} style={{ width: '100%', height: '100%', display: 'block', cursor: 'grab' }} />
        <Box sx={{ position: 'absolute', top: 4, left: 6, display: 'flex', gap: 1, alignItems: 'center', pointerEvents: 'auto' }}>
          <FormControlLabel sx={{ m: 0 }} control={
            <Checkbox size="small" checked={showProj} onChange={e => setShowProj(e.target.checked)} sx={{ p: 0.25 }} />
          } label={<span style={{ fontSize: 10, color: '#94a3b8' }}>floor proj</span>} />
          <Typography sx={{ fontSize: 10, color: '#64748b' }}>drag = rotate · height = ripple · ◯green ≤ gate</Typography>
        </Box>
        {hover && (
          <Box sx={{ position: 'absolute', bottom: 6, right: 8, fontSize: 11, color: '#cbd5e1', bgcolor: 'rgba(10,22,40,.88)', px: 1, py: 0.25, borderRadius: 1 }}>
            td {hover.td.toFixed(2)} · eff {(hover.eff * 100).toFixed(2)}% · ripple {hover.ripple.toFixed(1)}%
          </Box>
        )}
      </Box>
    );
  };

export default Scatter3D;
