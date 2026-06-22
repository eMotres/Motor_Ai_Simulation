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
const LABEL ={ fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const SUB = { fontSize: 10, color: '#475569', mb: 0.5 } as const;
const PANEL = { bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1, p: 1.5 } as const;

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
    const keys = Object.keys(mesh).filter((k) => !SKIP(k));
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const k of keys) for (const v of mesh[k].vertices) {
      if (v[0] < minX) minX = v[0]; if (v[0] > maxX) maxX = v[0];
      if (v[1] < minY) minY = v[1]; if (v[1] > maxY) maxY = v[1];
    }
    if (!isFinite(minX)) return;
    const scale = Math.min(W / (maxX - minX), H / (maxY - minY)) * 0.82;  // matches SideView scale
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const TX = (x: number) => (x - cx) * scale + W / 2;
    const TY = (y: number) => H / 2 - (y - cy) * scale;   // flip Y (motor up → canvas down)
    // Draw every region straight from the REAL mesh — iron, magnets and the copper
    // winding — sorted so copper sits on top. The backend builds the ACTUAL winding
    // (individual wires) into the coil regions, so this is the real motor geometry,
    // not a synthetic overlay. It redraws whenever the geometry sent to the mesh
    // changes — which now includes the turns + wire-thickness knobs (see geoStr).
    for (const key of keys.slice().sort((a, b) => drawOrder(a) - drawOrder(b))) {
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
    <Box sx={{ position: 'relative', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <canvas ref={canvasRef} width={720} height={720} style={{ height: '100%', width: 'auto', aspectRatio: '1 / 1', display: 'block', opacity: state === 'loading' ? 0.4 : 1, transition: 'opacity .15s' }} />
      {state === 'loading' && <CircularProgress size={20} sx={{ color: '#3b82f6', position: 'absolute', top: '50%', left: '50%', mt: '-10px', ml: '-10px' }} />}
      {state === 'error' && <Typography sx={{ fontSize: 11, color: '#f87171', position: 'absolute', top: '46%', left: 0, right: 0 }}>geometry preview needs the backend</Typography>}
    </Box>
  );
};

// Side view on a CANVAS (not SVG): a canvas has reliable intrinsic sizing, so it
// renders height-driven without the SVG width/flex-basis clamping — guaranteeing
// the SAME scale as the cross-section (OD pixel-height matches the diameter).
const SideView: React.FC<{ OD_mm: number; L_mm: number }> = ({ OD_mm, L_mm }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const H = 720;                                 // same 720 frame as the cross-section canvas
  const OD = OD_mm > 0 ? OD_mm : 1;              // outer diameter — from the live geometry
  const s2 = (0.82 * H) / OD;                    // SAME scale factor (0.82) → motor same size in both views
  const stackH = OD * s2, stackW = Math.max(1, L_mm) * s2;
  const W = Math.round(stackW + 130);            // buffer hugs the stack (+ room for dimensions)

  useEffect(() => {
    const cv = canvasRef.current; if (!cv) return;
    cv.width = W; cv.height = H;
    const ctx = cv.getContext('2d'); if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2, x0 = cx - stackW / 2, y0 = cy - stackH / 2;
    // lamination stack — solid, no air, no shaft
    ctx.fillStyle = STEEL; ctx.fillRect(x0, y0, stackW, stackH);
    ctx.strokeStyle = '#475569'; ctx.lineWidth = 1.4; ctx.strokeRect(x0, y0, stackW, stackH);
    // lamination hatch
    ctx.strokeStyle = STEEL_DK; ctx.lineWidth = 0.9;
    const n = Math.max(3, Math.round(stackW / 11));
    for (let i = 1; i < n; i++) { const x = x0 + (i / n) * stackW; ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y0 + stackH); ctx.stroke(); }
    // length dimension (bottom)
    ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 1.3;
    const ly = y0 + stackH + 24;
    ctx.beginPath();
    ctx.moveTo(x0, ly); ctx.lineTo(x0 + stackW, ly);
    ctx.moveTo(x0, ly - 8); ctx.lineTo(x0, ly + 8); ctx.moveTo(x0 + stackW, ly - 8); ctx.lineTo(x0 + stackW, ly + 8);
    ctx.stroke();
    ctx.fillStyle = '#cbd5e1'; ctx.font = '700 26px monospace'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText(`${L_mm.toFixed(0)} mm`, cx, ly + 10);
    // diameter dimension (left)
    const dx = x0 - 30;
    ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 1.3;
    ctx.beginPath();
    ctx.moveTo(dx, y0); ctx.lineTo(dx, y0 + stackH);
    ctx.moveTo(dx - 8, y0); ctx.lineTo(dx + 8, y0); ctx.moveTo(dx - 8, y0 + stackH); ctx.lineTo(dx + 8, y0 + stackH);
    ctx.stroke();
    ctx.save(); ctx.translate(dx - 14, cy); ctx.rotate(-Math.PI / 2);
    ctx.fillStyle = '#94a3b8'; ctx.font = '600 20px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(`Ø${OD.toFixed(0)} mm`, 0, 0); ctx.restore();
  }, [OD_mm, L_mm, W, stackW, stackH, OD]);

  return <canvas ref={canvasRef} width={W} height={H}
    style={{ height: '100%', width: 'auto', aspectRatio: `${W} / ${H}`, display: 'block' }} />;
};

const GeometryProjections: React.FC<{ ref0: ReferenceMotor; knobs: Knobs }> = ({ ref0, knobs }) => {
  const storeGeo = useMotorStore((s) => s.geometry) as Record<string, unknown>;
  // The cross-section is the REAL backend mesh. We overlay the configurator's
  // winding knobs onto the geometry sent to /mesh2d — turns → wires/slot, wire
  // thickness → wire height — so the backend rebuilds the actual winding and the
  // drawing reacts to the knobs (the backend models individual wires).
  const geoStr = useMemo(() => {
    const g: Record<string, number> = {};
    for (const [k, v] of Object.entries(storeGeo || {})) if (typeof v === 'number') g[k] = v;
    g.num_wires_per_slot = knobs.N;
    g.wire_height = knobs.wireH_mm;
    return JSON.stringify(g);
  }, [storeGeo, knobs.N, knobs.wireH_mm]);

  // Outer diameter + stack length come from the LIVE geometry store — the same
  // single source the cross-section uses — not the static reference passport.
  const gnum = (k: string): number | undefined =>
    (typeof storeGeo?.[k] === 'number' ? (storeGeo[k] as number) : undefined);
  const OD_mm = gnum('stator_diameter')
    ?? (gnum('stator_outer_radius') !== undefined ? (gnum('stator_outer_radius') as number) * 2 : undefined)
    ?? 2 * ref0.geo.statorOR_mm;
  const L_mm = gnum('motor_length') ?? knobs.L_mm;
  // Slot/pole counts come from the live geometry (matching the cross-section mesh).
  // The winding (turns + wire thickness) is folded into geoStr above, so the backend
  // rebuilds the real winding — nothing is drawn client-side.
  const numSlots  = gnum('num_slots') ?? ref0.geo.numSlots;
  const numPoles  = gnum('num_poles') ?? ref0.geo.numPoles;

  return (
    <Box sx={{ display: 'flex', gap: 2, alignItems: 'stretch', justifyContent: 'center', height: 'min(88vh, 820px)' }}>
      <Box sx={{ ...PANEL, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Typography sx={LABEL}>Cross-section (XY) — real geometry</Typography>
        <Typography sx={SUB}>{knobs.N} turns/slot · {knobs.wireH_mm.toFixed(1)} mm wire · {numSlots} slots / {numPoles} poles</Typography>
        <Box sx={{ flex: 1, minHeight: 0, mt: 0.5 }}>
          <CrossSectionReal geoStr={geoStr} />
        </Box>
      </Box>
      <Box sx={{ ...PANEL, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Typography sx={LABEL}>Side view — stack length</Typography>
        <Typography sx={SUB}>L = {L_mm.toFixed(0)} mm · Ø{OD_mm.toFixed(0)} mm</Typography>
        <Box sx={{ flex: 1, minHeight: 0, mt: 0.5, display: 'flex', justifyContent: 'center' }}>
          <SideView OD_mm={OD_mm} L_mm={L_mm} />
        </Box>
      </Box>
    </Box>
  );
};

export default GeometryProjections;
