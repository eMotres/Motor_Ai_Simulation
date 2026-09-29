// Canvas2D fallback for the FEM mesh viewer.  The 3-D viewer (FemMeshViewer3D)
// needs WebGL, which embedded / sandboxed browser panels often disable
// ("GL_VENDOR = Disabled, Sandboxed = yes") → the 3-D canvas renders black.
// This draws the SAME 2-D cross-section mesh with the plain Canvas2D API (no
// WebGL), so the mesh is always visible.  Wheel = zoom, drag = pan,
// double-click = reset.
import React, { useCallback, useEffect, useRef } from 'react';
import type { FemMeshPayload } from './FemMeshViewer3D';

// Domain id → fill colour (same palette as the 3-D viewer / field map).
const RGB: Record<number, string> = {
  0:  'rgb(140,166,199)',   // air
  1:  'rgb(87,102,122)',    // stator steel
  2:  'rgb(250,191,36)',    // winding
  3:  'rgb(56,189,247)',    // air gap
  4:  'rgb(59,130,245)',    // magnet N
  5:  'rgb(56,69,82)',      // rotor steel
  6:  'rgb(181,181,191)',   // shaft
  7:  'rgb(115,217,204)',   // motion band
  8:  'rgb(140,166,199)',   // outer air — SAME as air: one substance, one colour
  44: 'rgb(240,69,69)',     // magnet S
};
const colorOf = (d: number) => RGB[d] ?? 'rgb(100,116,139)';
// Back-to-front draw order so small features (magnets, coils) sit on top.
const ORDER = [8, 0, 3, 7, 5, 1, 6, 4, 44, 2];
const rank = (d: number) => { const i = ORDER.indexOf(d); return i < 0 ? 50 : i; };

interface Props {
  payload: FemMeshPayload | null;
  showWire?: boolean;
}

const FemMeshViewer2D: React.FC<Props> = ({ payload, showWire = false }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const view = useRef({ scale: 1, ox: 0, oy: 0, init: false });
  const drag = useRef<{ x: number; y: number } | null>(null);

  const draw = useCallback(() => {
    const cv = canvasRef.current;
    if (!cv || !payload) return;
    const parent = cv.parentElement;
    const w = parent?.clientWidth ?? 0;
    const h = parent?.clientHeight ?? 0;
    if (w < 2 || h < 2) return;
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
      cv.style.width = w + 'px'; cv.style.height = h + 'px';
    }
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#060d17'; ctx.fillRect(0, 0, w, h);

    const [xmin, xmax, ymin, ymax] = payload.extent;
    if (!view.current.init) {
      const sx = w / ((xmax - xmin) * 1.08);
      const sy = h / ((ymax - ymin) * 1.08);
      const s = Math.min(sx, sy);
      view.current = {
        scale: s,
        ox: w / 2 - 0.5 * (xmin + xmax) * s,
        oy: h / 2 + 0.5 * (ymin + ymax) * s,   // +y up → screen flip
        init: true,
      };
    }
    const { scale, ox, oy } = view.current;
    const V = payload.vertices, T = payload.triangles, D = payload.domain_per_tri;
    const X = (x: number) => x * scale + ox;
    const Y = (y: number) => -y * scale + oy;

    // Draw grouped by domain (back-to-front) → few fillStyle changes + correct
    // layering.  Build the order once.
    const order = T.map((_, i) => i).sort((a, b) => rank(D[a]) - rank(D[b]));
    let last = -999;
    ctx.lineJoin = 'round';
    for (const i of order) {
      const d = D[i];
      if (d !== last) { ctx.fillStyle = colorOf(d); last = d; }
      const t = T[i];
      const a = V[t[0]], b = V[t[1]], c = V[t[2]];
      ctx.beginPath();
      ctx.moveTo(X(a[0]), Y(a[1]));
      ctx.lineTo(X(b[0]), Y(b[1]));
      ctx.lineTo(X(c[0]), Y(c[1]));
      ctx.closePath();
      ctx.fill();
      if (showWire) {
        ctx.strokeStyle = 'rgba(0,0,0,0.22)';
        ctx.lineWidth = 0.4;
        ctx.stroke();
      }
    }
  }, [payload, showWire]);

  // redraw on payload change (reset view), and keep canvas sized to container
  useEffect(() => { view.current.init = false; draw(); }, [draw]);
  useEffect(() => {
    const cv = canvasRef.current; const parent = cv?.parentElement;
    if (!parent) return;
    const ro = new ResizeObserver(() => draw());
    ro.observe(parent);
    draw();
    return () => ro.disconnect();
  }, [draw]);

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const cv = canvasRef.current; if (!cv) return;
    const r = cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const v = view.current;
    v.ox = mx - (mx - v.ox) * f;
    v.oy = my - (my - v.oy) * f;
    v.scale *= f;
    draw();
  };
  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX, y: e.clientY }; };
  const onMove = (e: React.MouseEvent) => {
    if (!drag.current) return;
    const v = view.current;
    v.ox += e.clientX - drag.current.x;
    v.oy += e.clientY - drag.current.y;
    drag.current = { x: e.clientX, y: e.clientY };
    draw();
  };
  const onUp = () => { drag.current = null; };
  const onDouble = () => { view.current.init = false; draw(); };

  if (!payload || payload.n_triangles === 0) return null;
  return (
    <canvas ref={canvasRef}
      style={{ display: 'block', width: '100%', height: '100%', cursor: 'grab', background: 'var(--panel-2)' }}
      onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove}
      onMouseUp={onUp} onMouseLeave={onUp} onDoubleClick={onDouble} />
  );
};

export default FemMeshViewer2D;
