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
const LABEL = { fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const SUB = { fontSize: 10, color: '#475569', mb: 0.5 } as const;
const PANEL = { flex: '1 1 300px', minWidth: 270, bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1, p: 1.5 } as const;

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
const drawOrder = (key: string): number =>
  key.startsWith('stator') ? 0 : key.startsWith('rotor') ? 1 : key === 'shaft' ? 2
    : key.startsWith('magnet') ? 3 : key.startsWith('coil') ? 4 : 5;

const CrossSectionReal: React.FC<{ geoStr: string }> = ({ geoStr }) => {
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
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const comp of Object.values(mesh)) for (const v of comp.vertices) {
      if (v[0] < minX) minX = v[0]; if (v[0] > maxX) maxX = v[0];
      if (v[1] < minY) minY = v[1]; if (v[1] > maxY) maxY = v[1];
    }
    if (!isFinite(minX)) return;
    const scale = Math.min(W / (maxX - minX), H / (maxY - minY)) * 0.94;
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const TX = (x: number) => (x - cx) * scale + W / 2;
    const TY = (y: number) => H / 2 - (y - cy) * scale;   // flip Y (motor up → canvas down)
    for (const key of Object.keys(mesh).sort((a, b) => drawOrder(a) - drawOrder(b))) {
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
  }, [mesh]);

  return (
    <Box sx={{ position: 'relative', textAlign: 'center' }}>
      <canvas ref={canvasRef} width={320} height={320} style={{ width: '100%', maxWidth: 320, height: 'auto', opacity: state === 'loading' ? 0.4 : 1, transition: 'opacity .15s' }} />
      {state === 'loading' && <CircularProgress size={20} sx={{ color: '#3b82f6', position: 'absolute', top: '50%', left: '50%', mt: '-10px', ml: '-10px' }} />}
      {state === 'error' && <Typography sx={{ fontSize: 11, color: '#f87171', position: 'absolute', top: '46%', left: 0, right: 0 }}>geometry preview needs the backend</Typography>}
    </Box>
  );
};

const SideView: React.FC<{ ref0: ReferenceMotor; knobs: Knobs }> = ({ ref0, knobs }) => {
  const g = ref0.geo;
  const W = 320, H = 168;
  const OD = 2 * g.statorOR_mm;
  const s2 = 118 / OD;
  const stackH = OD * s2, stackW = knobs.L_mm * s2;
  const cx = W / 2, cy = H / 2 - 6;
  const x0 = cx - stackW / 2, y0 = cy - stackH / 2;
  const shaftR = g.rotorIR_mm * s2, overhang = 24;
  const hatch: React.ReactNode[] = [];
  const n = Math.max(2, Math.round(stackW / 5));
  for (let i = 1; i < n; i++) {
    const x = x0 + (i / n) * stackW;
    hatch.push(<line key={i} x1={x} y1={y0} x2={x} y2={y0 + stackH} stroke={STEEL_DK} strokeWidth={0.5} />);
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: 340, height: 'auto' }}>
      <rect x={x0 - overhang} y={cy - shaftR} width={stackW + 2 * overhang} height={2 * shaftR} rx={2} fill={SHAFT} />
      <rect x={x0} y={y0} width={stackW} height={stackH} rx={2} fill={STEEL} stroke="#1e293b" />
      {hatch}
      <line x1={x0} y1={y0 + stackH + 11} x2={x0 + stackW} y2={y0 + stackH + 11} stroke="#94a3b8" strokeWidth={0.8} />
      <line x1={x0} y1={y0 + stackH + 7} x2={x0} y2={y0 + stackH + 15} stroke="#94a3b8" strokeWidth={0.8} />
      <line x1={x0 + stackW} y1={y0 + stackH + 7} x2={x0 + stackW} y2={y0 + stackH + 15} stroke="#94a3b8" strokeWidth={0.8} />
      <text x={cx} y={y0 + stackH + 26} fill="#cbd5e1" fontSize={12} textAnchor="middle" fontFamily="monospace">{knobs.L_mm.toFixed(0)} mm</text>
      <text x={x0 - overhang - 4} y={cy} fill="#64748b" fontSize={10} textAnchor="middle" dominantBaseline="middle"
        transform={`rotate(-90 ${x0 - overhang - 4} ${cy})`}>Ø{OD.toFixed(0)} mm</text>
    </svg>
  );
};

const GeometryProjections: React.FC<{ ref0: ReferenceMotor; knobs: Knobs }> = ({ ref0, knobs }) => {
  const storeGeo = useMotorStore((s) => s.geometry) as Record<string, unknown>;
  // real geometry = active geometry with the tuned winding overlaid
  const geoStr = useMemo(() => {
    const g: Record<string, number> = {};
    for (const [k, v] of Object.entries(storeGeo || {})) if (typeof v === 'number') g[k] = v;
    g.num_wires_per_slot = knobs.N;
    g.wire_height = knobs.wireH_mm;
    return JSON.stringify(g);
  }, [storeGeo, knobs.N, knobs.wireH_mm]);

  return (
    <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
      <Box sx={PANEL}>
        <Typography sx={LABEL}>Cross-section (XY) — real geometry</Typography>
        <Typography sx={SUB}>{knobs.N} turns/slot · {knobs.wireH_mm.toFixed(2)} mm wire · {ref0.geo.numSlots} slots / {ref0.geo.numPoles} poles</Typography>
        <CrossSectionReal geoStr={geoStr} />
      </Box>
      <Box sx={PANEL}>
        <Typography sx={LABEL}>Side view — stack length</Typography>
        <Typography sx={SUB}>L = {knobs.L_mm.toFixed(0)} mm · Ø{(2 * ref0.geo.statorOR_mm).toFixed(0)} mm (grey = shaft)</Typography>
        <Box sx={{ textAlign: 'center' }}><SideView ref0={ref0} knobs={knobs} /></Box>
      </Box>
    </Box>
  );
};

export default GeometryProjections;
