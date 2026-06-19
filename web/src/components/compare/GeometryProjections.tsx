/**
 * GeometryProjections — two projections of the tuned motor for the Configurator.
 *
 *   • Cross-section (XY): the REAL CadQuery cross-section, fetched from the
 *     backend /api/geometry/mesh2d with the tuned winding (num_wires_per_slot +
 *     wire_height) overlaid on the active geometry, then drawn on a canvas by
 *     component (stator / rotor / shaft steel, magnets, copper coils).  Not a
 *     schematic — the actual triangulated geometry.  Debounced + cached.
 *   • Side view: the lamination-stack envelope — a rectangle whose length tracks
 *     motor_length against the fixed outer diameter (the true axial silhouette
 *     of the cylindrical stack), with a length dimension.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Typography, CircularProgress } from '@mui/material';
import type { Knobs } from '../../lib/motorScaling';
import type { ReferenceMotor } from '../../lib/referencePassports';
import { useMotorStore } from '../../stores/motorStore';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');
const STEEL = '#3b4453', STEEL_DK = '#2a3142', SHAFT = '#5b6675', BG = '#060d17';
const COPPER = '#c27d33', COPPER_DK = '#5e3a16';
const LABEL = { fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const SUB = { fontSize: 10, color: '#475569', mb: 0.5 } as const;
const PANEL = { flex: '1 1 380px', minWidth: 320, maxWidth: 500, bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1, p: 1.5 } as const;

type Comp = { vertices: number[][]; faces: number[][] };
type Mesh2D = Record<string, Comp>;

const meshCache = new Map<string, Mesh2D>();

const colorFor = (key: string): string => {
  if (key === 'shaft') return SHAFT;
  if (key.startsWith('stator')) return STEEL;
  if (key.startsWith('rotor')) return STEEL_DK;
  if (key.startsWith('magnet')) return (parseInt(key.split('_')[1] || '0', 10) || 0) % 2 ? '#3b82f6' : '#ef4444';
  if (key.startsWith('coil')) return ['#c27d33', '#b8860b', '#a0651e'][(parseInt(key.split('_')[1] || '0', 10) || 0) % 3];
  return '#475569';
};
// Skip the air-gap band meshes (in_band / out_band), the shaft and any air
// region — only the iron, magnets and copper winding are drawn.
const SKIP = (key: string): boolean =>
  key === 'shaft' || key.includes('band') || key.includes('air');
// Draw coils LAST so the copper sits on top of the iron (and is never hidden).
const drawOrder = (key: string): number =>
  key.startsWith('stator') ? 0 : key.startsWith('rotor') ? 1
    : key.startsWith('magnet') ? 2 : key.startsWith('coil') ? 3 : 4;

const CrossSectionReal: React.FC<{
  geoStr: string; N: number; wireH: number; insulation: number; spacing: number;
}> = ({ geoStr, N, wireH, insulation, spacing }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mesh, setMesh] = useState<Mesh2D | null>(meshCache.get(geoStr) ?? null);
  const [state, setState] = useState<'idle' | 'loading' | 'error'>(meshCache.has(geoStr) ? 'idle' : 'loading');

  useEffect(() => {
    const cached = meshCache.get(geoStr);
    if (cached) { setMesh(cached); setState('idle'); return; }
    let cancel = false;
    setState('loading');
    const t = setTimeout(() => {
      fetch(`${API}/api/geometry/mesh2d?geo=${encodeURIComponent(geoStr)}`)
        .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<Mesh2D>; })
        .then((d) => { if (cancel) return; if (!d || Object.keys(d).length === 0) throw new Error('empty'); meshCache.set(geoStr, d); setMesh(d); setState('idle'); })
        .catch(() => { if (!cancel) setState('error'); });
    }, 280);
    return () => { cancel = true; clearTimeout(t); };
  }, [geoStr]);

  useEffect(() => {
    const cv = canvasRef.current; if (!cv || !mesh) return;
    const ctx = cv.getContext('2d'); if (!ctx) return;
    const W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    const keys = Object.keys(mesh).filter((k) => !SKIP(k));
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const k of keys) for (const v of mesh[k].vertices) {
      if (v[0] < minX) minX = v[0]; if (v[0] > maxX) maxX = v[0];
      if (v[1] < minY) minY = v[1]; if (v[1] > maxY) maxY = v[1];
    }
    if (!isFinite(minX)) return;
    const scale = Math.min(W / (maxX - minX), H / (maxY - minY)) * 0.94;
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const TX = (x: number) => (x - cx) * scale + W / 2;
    const TY = (y: number) => H / 2 - (y - cy) * scale;   // flip Y (motor up → canvas down)
    // 1) iron + magnets from the real mesh (coils handled separately below)
    for (const key of keys.filter((k) => !k.startsWith('coil')).sort((a, b) => drawOrder(a) - drawOrder(b))) {
      const { vertices, faces } = mesh[key];
      ctx.fillStyle = colorFor(key);
      ctx.beginPath();
      for (const f of faces) {
        const a = vertices[f[0]], b = vertices[f[1]], c = vertices[f[2]];
        if (!a || !b || !c) continue;
        ctx.moveTo(TX(a[0]), TY(a[1])); ctx.lineTo(TX(b[0]), TY(b[1])); ctx.lineTo(TX(c[0]), TY(c[1])); ctx.closePath();
      }
      ctx.fill();
    }
    // 2) winding: draw the ACTUAL N wire rows inside each real coil slot, so the
    //    coils respond to the turns + wire-thickness knobs (the backend mesh gives
    //    a fixed slot-fill region; we place the real wires using its geometry).
    ctx.strokeStyle = COPPER_DK; ctx.lineWidth = 0.6;
    const rowPitch = wireH + spacing;
    for (const key of keys.filter((k) => k.startsWith('coil'))) {
      const verts = mesh[key].vertices;
      const m = verts.length || 1;
      let sx = 0, sy = 0; for (const v of verts) { sx += v[0]; sy += v[1]; }
      const th = Math.atan2(sy / m, sx / m), ct = Math.cos(th), st = Math.sin(th);
      let rMin = Infinity, rMax = -Infinity, tMax = 0;
      for (const v of verts) {
        const rr = v[0] * ct + v[1] * st, tt = -v[0] * st + v[1] * ct;
        if (rr < rMin) rMin = rr; if (rr > rMax) rMax = rr; if (Math.abs(tt) > tMax) tMax = Math.abs(tt);
      }
      const hT = tMax * 0.92;
      const C = (r: number, t: number): [number, number] => [r * ct - t * st, r * st + t * ct];
      ctx.fillStyle = COPPER;
      for (let k = 0; k < N; k++) {
        const ri = rMin + insulation + k * rowPitch, ro = ri + wireH;
        if (ro > rMax - insulation + 1e-6) break;
        const p1 = C(ri, -hT), p2 = C(ri, hT), p3 = C(ro, hT), p4 = C(ro, -hT);
        ctx.beginPath();
        ctx.moveTo(TX(p1[0]), TY(p1[1])); ctx.lineTo(TX(p2[0]), TY(p2[1]));
        ctx.lineTo(TX(p3[0]), TY(p3[1])); ctx.lineTo(TX(p4[0]), TY(p4[1])); ctx.closePath();
        ctx.fill(); ctx.stroke();
      }
    }
  }, [mesh, N, wireH, insulation, spacing]);

  return (
    <Box sx={{ position: 'relative', textAlign: 'center' }}>
      <canvas ref={canvasRef} width={480} height={480} style={{ width: '100%', maxWidth: 460, aspectRatio: '1 / 1', height: 'auto', display: 'block', opacity: state === 'loading' ? 0.4 : 1, transition: 'opacity .15s' }} />
      {state === 'loading' && <CircularProgress size={20} sx={{ color: '#3b82f6', position: 'absolute', top: '50%', left: '50%', mt: '-10px', ml: '-10px' }} />}
      {state === 'error' && <Typography sx={{ fontSize: 11, color: '#f87171', position: 'absolute', top: '46%', left: 0, right: 0 }}>geometry preview needs the backend</Typography>}
    </Box>
  );
};

const SideView: React.FC<{ ref0: ReferenceMotor; knobs: Knobs }> = ({ ref0, knobs }) => {
  const g = ref0.geo;
  const W = 360, H = 360;
  const OD = 2 * g.statorOR_mm;
  const s2 = 270 / OD;                          // outer diameter → 270 px (radial, vertical)
  const stackH = OD * s2, stackW = knobs.L_mm * s2;
  const cx = W / 2, cy = H / 2 - 6;
  const x0 = cx - stackW / 2, y0 = cy - stackH / 2;
  // lamination hatch (the stack is many thin sheets along the length)
  const hatch: React.ReactNode[] = [];
  const n = Math.max(3, Math.round(stackW / 6));
  for (let i = 1; i < n; i++) {
    const x = x0 + (i / n) * stackW;
    hatch.push(<line key={i} x1={x} y1={y0} x2={x} y2={y0 + stackH} stroke={STEEL_DK} strokeWidth={0.6} />);
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: 460, aspectRatio: '1 / 1', height: 'auto', display: 'block' }}>
      {/* the motor stack — solid, no air, no shaft */}
      <rect x={x0} y={y0} width={stackW} height={stackH} fill={STEEL} stroke="#475569" strokeWidth={1} />
      {hatch}
      {/* length dimension (bottom) */}
      <line x1={x0} y1={y0 + stackH + 13} x2={x0 + stackW} y2={y0 + stackH + 13} stroke="#94a3b8" strokeWidth={0.9} />
      <line x1={x0} y1={y0 + stackH + 8} x2={x0} y2={y0 + stackH + 18} stroke="#94a3b8" strokeWidth={0.9} />
      <line x1={x0 + stackW} y1={y0 + stackH + 8} x2={x0 + stackW} y2={y0 + stackH + 18} stroke="#94a3b8" strokeWidth={0.9} />
      <text x={cx} y={y0 + stackH + 30} fill="#cbd5e1" fontSize={13} textAnchor="middle" fontFamily="monospace">{knobs.L_mm.toFixed(0)} mm</text>
      {/* diameter dimension (left) */}
      <line x1={x0 - 13} y1={y0} x2={x0 - 13} y2={y0 + stackH} stroke="#94a3b8" strokeWidth={0.9} />
      <line x1={x0 - 18} y1={y0} x2={x0 - 8} y2={y0} stroke="#94a3b8" strokeWidth={0.9} />
      <line x1={x0 - 18} y1={y0 + stackH} x2={x0 - 8} y2={y0 + stackH} stroke="#94a3b8" strokeWidth={0.9} />
      <text x={x0 - 20} y={cy} fill="#94a3b8" fontSize={11} textAnchor="middle" dominantBaseline="middle"
        transform={`rotate(-90 ${x0 - 20} ${cy})`}>Ø{OD.toFixed(0)} mm</text>
    </svg>
  );
};

const GeometryProjections: React.FC<{ ref0: ReferenceMotor; knobs: Knobs }> = ({ ref0, knobs }) => {
  const storeGeo = useMotorStore((s) => s.geometry) as Record<string, unknown>;
  // The iron / magnet cross-section does not depend on the winding knobs, so the
  // mesh is fetched once per geometry; the N wires are drawn on top client-side.
  const geoStr = useMemo(() => {
    const g: Record<string, number> = {};
    for (const [k, v] of Object.entries(storeGeo || {})) if (typeof v === 'number') g[k] = v;
    return JSON.stringify(g);
  }, [storeGeo]);

  return (
    <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
      <Box sx={PANEL}>
        <Typography sx={LABEL}>Cross-section (XY) — real geometry</Typography>
        <Typography sx={SUB}>{knobs.N} turns/slot · {knobs.wireH_mm.toFixed(2)} mm wire · {ref0.geo.numSlots} slots / {ref0.geo.numPoles} poles</Typography>
        <CrossSectionReal geoStr={geoStr} N={knobs.N} wireH={knobs.wireH_mm}
          insulation={ref0.fit.insulation_mm} spacing={ref0.fit.wireSpacingY_mm} />
      </Box>
      <Box sx={PANEL}>
        <Typography sx={LABEL}>Side view — stack length</Typography>
        <Typography sx={SUB}>Lamination stack · L = {knobs.L_mm.toFixed(0)} mm · Ø{(2 * ref0.geo.statorOR_mm).toFixed(0)} mm</Typography>
        <Box sx={{ textAlign: 'center' }}><SideView ref0={ref0} knobs={knobs} /></Box>
      </Box>
    </Box>
  );
};

export default GeometryProjections;
