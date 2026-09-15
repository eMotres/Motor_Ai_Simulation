/**
 * FieldViewer — ONE picture, ONE menu, ONE colour bar, for every 2-D field map
 * in the app: electromagnetic, thermal, mechanical and modal.
 *
 * User 2026-09-06, on the Mechanical tab (three static canvases side by side,
 * no zoom): "сделай наш интерфейс для просмотра, чтобы можно было приближать и
 * удалять; нужно сделать одну картинку и меню для переключения выводов
 * графиков; интерфейс должен быть единым для всех графиков — электромагнитных,
 * механических и термо".
 *
 * What that means concretely, and what this component is responsible for:
 *
 *   • ONE canvas.  An OrthographicCamera with wheel-zoom AT THE CURSOR, drag to
 *     pan, double-click (or the Fit button) to frame the model.  The camera is
 *     per viewer instance and SURVIVES an output switch — zooming into a tooth
 *     tip on |B| and then picking Loss must not throw the zoom away, which is
 *     the whole reason the Simulation view's old fit-on-payload-change had to
 *     go.  A different machine (`fitKey`) does refit.
 *   • ONE menu.  Outputs grouped by domain; a host passes only what it has.
 *   • ONE header.  Name, unit, true max/min, the value under the cursor, and
 *     the host's context line (case / frame / provenance).
 *   • ONE colour bar.  It prints the BAND EDGES of the very FieldScale the fill
 *     bands with, so the legend cannot describe a range the picture does not
 *     use — continuous ramps and semantic bands (the safety factor's red /
 *     green / blue) are the same widget with a different palette.
 *
 *   • TWO viewer-wide toggles, added 2026-09-06 at the user's request and
 *     therefore living HERE rather than in any host — every output in every tab
 *     gets them for free, which is the whole point of one viewer:
 *       – **Max** ("подсвечивать точки максимальных деформаций, напряжений и
 *         полей; в виде меню — можно посмотреть, а можно убрать"): rings the
 *         maximum and the minimum of whatever is displayed, with the value, the
 *         radius / angle and the part they sit in.
 *       – **Mesh** ("также чтобы была возможность отображения сетки или без
 *         неё"): the solver's triangle edges over the field.
 *     Both default OFF and are remembered in localStorage (`fieldview.max`,
 *     `fieldview.mesh`) — a preference, not a per-output state.
 *
 *   • ONE **part tree**, added 2026-09-06: "нужно ещё добавить в вывод название
 *     частей ротора и статора, чтобы можно было смотреть отдельно на каждую
 *     часть и видеть только её деформации и стрессы" — and then, seeing the
 *     3-D tab's tree next to the dropdown that first answered it: "используй то
 *     же самое дерево, которое у нас уже есть, чтобы всё было универсально".
 *     So it IS that tree — `viewer3d/ComponentTreeView`, the same rows, eyes,
 *     colour dots, isolate and Show All — driven here by a LOCAL model built
 *     from the parts of the picture instead of the global 3-D visibility store.
 *     Whatever is left visible is drawn against ITS OWN colour range: scale,
 *     colour bar, header max/min, cursor readout, Max markers, mesh overlay and
 *     the band iso-lines are all recomputed over it.  That re-scaling IS the
 *     feature: a magnet's 40 MPa read against the rotor lips' 5 GPa is one flat
 *     band, which answers nothing.  Hidden parts stay as a faint grey ghost
 *     outline so what is left is still somewhere.  Session state per viewer,
 *     not a stored preference: it is kept across output switches (a part the
 *     new output does not have goes grey in the tree rather than disappearing),
 *     and it NEVER moves the camera (Fit frames what is visible when you do
 *     want that).
 *
 * Per-output extras (deform ×, SF bands, contacts, mode index, case, log/lin)
 * are the host's business and arrive as `controls` — one short line with
 * tooltips, per the project's no-walls-of-text UI rule.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Box, Button, CircularProgress, ListSubheader, MenuItem, Select, Tooltip,
  Typography,
} from '@mui/material';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { guardCanvas } from '../viewer3d/webglGuard';
import { OrthographicCamera } from '@react-three/drei';
import * as THREE from 'three';

import {
  GROUP_ORDER, PART_GROUP_ORDER, buildEdgeGeometry, extremaOf, jetPalette,
  loopsToSegments, partsOf, restrictToParts,
} from './fieldOutput';
import type {
  FieldExtremum, FieldGroup, FieldOutput, FieldPart, FieldProbeData,
} from './fieldOutput';
// The app's own component tree — the SAME one the 3-D tab has always shown
// (user 2026-09-06: "используй то же самое дерево, которое у нас уже есть,
// чтобы всё было универсально").  There it is bound to the global visibility
// store; here to the parts of the picture below.
import ComponentTreeView, {
  type TreeModel, type TreeRow,
} from '../viewer3d/ComponentTreeView';

/** Viewer preference, remembered across sessions.  Both toggles are OFF by
 *  default: the plain picture is what the user asked to be able to come back to
 *  ("можно посмотреть, а можно убрать"). */
function readFlag(key: string): boolean {
  try { return localStorage.getItem(key) === '1'; } catch { return false; }
}
function writeFlag(key: string, v: boolean): void {
  try { localStorage.setItem(key, v ? '1' : '0'); } catch { /* private mode */ }
}

/** Marker inks.  White for the maximum, violet for the minimum: NEITHER exists
 *  anywhere in the jet ramp (which runs blue → cyan → green → yellow → red), so
 *  a marker can never be read as part of the field under it, and the two can
 *  never be mistaken for each other. */
const MAX_INK = '#ffffff';
const MIN_INK = '#c084fc';

/** Hard cap on the palette uniform.  11 jet bands and 3 safety-factor bands are
 *  the only two schemes in the app; 16 leaves room without a dynamic array. */
const MAX_BANDS = 16;

/** Above this many items in one part, the tree keeps the group row alone — a
 *  48-slot machine's coils are a scroll bar, not a control. */
const PER_ITEM_ROWS = 64;

/** "stator iron" → "Stator iron": the solvers name their parts in lower case,
 *  the tree writes them like the 3-D tree's rows. */
const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

const FIELD_VERT = `
  attribute float aVal;
  varying float vVal;
  void main() {
    vVal = aVal;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

/**
 * Banding + the band-edge iso-line, per PIXEL.
 *
 * Same maths as `fieldView.BAND_FRAG` (which the 3-D static view still uses),
 * with the jet ramp lifted out into a `uCols` palette so a semantic banding
 * (safety factor) is the SAME code path as a continuous quantity instead of a
 * second renderer.  `jetPalette()` reproduces the old colours exactly.
 *
 * The iso-line is NOT drawn at the clamped ends: vmin/vmax are percentiles, so
 * whole regions sit outside them at t = 0 or t = 1 exactly, where fract(s) = 0
 * — that used to paint a flat dark wash over half a shaft and render the
 * hottest copper near black.
 */
const FIELD_FRAG = `
  #define MAXB ${MAX_BANDS}
  uniform vec3  uCols[MAXB];
  uniform float uBands;
  uniform float uIso;
  varying float vVal;
  void main() {
    float t = clamp(vVal, 0.0, 1.0);
    float b = min(floor(t * uBands), uBands - 1.0);
    vec3 col = uCols[0];
    for (int i = 0; i < MAXB; i++) {
      if (float(i) <= b) col = uCols[i];
    }
    if (uIso > 0.0 && t > 0.0 && t < 1.0) {
      float s = t * uBands;
      float d = min(fract(s), 1.0 - fract(s)) / max(fwidth(s), 1e-6);
      col = mix(col, col * 0.30, uIso * (1.0 - smoothstep(0.0, 1.0, d)));
    }
    gl_FragColor = vec4(col, 1.0);
  }
`;

/* ── the drawn output ─────────────────────────────────────────────────────── */

const OutputMesh: React.FC<{ out: FieldOutput; mesh: boolean }> = ({ out, mesh }) => {
  const bands = Math.max(1, Math.min(MAX_BANDS, out.scale?.bands ?? 1));
  const uniforms = useMemo(() => {
    const pal = (out.palette ?? jetPalette(bands)).slice(0, MAX_BANDS);
    const cols: THREE.Color[] = [];
    for (let i = 0; i < MAX_BANDS; i++) {
      const c = pal[Math.min(i, pal.length - 1)] ?? [255, 255, 255];
      cols.push(new THREE.Color(c[0] / 255, c[1] / 255, c[2] / 255));
    }
    return {
      uCols:  { value: cols },
      uBands: { value: bands },
      uIso:   { value: out.iso ?? 0.85 },
    };
  }, [out.palette, out.iso, bands]);

  const outGeo = useMemo(() => {
    if (!out.outlines?.length) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position',
      new THREE.BufferAttribute(loopsToSegments(out.outlines, 0.8), 3));
    return g;
  }, [out.outlines]);

  // The UNDEFORMED silhouette under a deformed picture.  Dashed, so the growth
  // is the sliver of colour sticking out past it.
  const ghostGeo = useMemo(() => {
    if (!out.ghostOutlines?.length) return null;
    const pos = loopsToSegments(out.ghostOutlines, 1.2);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    // `lineDistance` by hand: three dropped BufferGeometry.computeLineDistances,
    // and LineDashedMaterial draws nothing without the attribute.
    const ld = new Float32Array(pos.length / 3);
    for (let i = 0; i + 5 < pos.length; i += 6) {
      ld[i / 3] = 0;
      ld[i / 3 + 1] = Math.hypot(pos[i + 3] - pos[i], pos[i + 4] - pos[i + 1]);
    }
    g.setAttribute('lineDistance', new THREE.BufferAttribute(ld, 1));
    return g;
  }, [out.ghostOutlines]);

  // The solver's own triangle edges (Mesh toggle).  Built ONCE per output
  // geometry — never on a camera move, and not at all while the toggle is off,
  // because the de-duplicating pass is the only expensive thing in this file.
  const edgeGeo = useMemo(
    () => (mesh ? buildEdgeGeometry(out.geometry) : null),
    [mesh, out.geometry]);

  const overlayGeos = useMemo(() => (out.overlays ?? []).map((o, i) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(o.positions, 3));
    if (o.colors) g.setAttribute('color', new THREE.BufferAttribute(o.colors, 3));
    return { key: o.key ?? `ov${i}`, geo: g, spec: o };
  }), [out.overlays]);

  return (
    <group>
      {out.geometry && (
        <mesh geometry={out.geometry}>
          <shaderMaterial
            key={`field-${out.id}-${bands}`}
            side={THREE.DoubleSide}
            vertexShader={FIELD_VERT}
            fragmentShader={FIELD_FRAG}
            uniforms={uniforms}
          />
        </mesh>
      )}
      {edgeGeo && (
        // Thin and semi-transparent, and UNDER the material outlines (z 0.5 vs
        // 0.8): the mesh is context for the field, never the subject.
        <lineSegments geometry={edgeGeo}>
          <lineBasicMaterial color={0x0f172a} transparent opacity={0.28}/>
        </lineSegments>
      )}
      {overlayGeos.map(({ key, geo, spec }) => (
        <lineSegments key={key} geometry={geo}>
          <lineBasicMaterial
            vertexColors={!!spec.colors}
            color={spec.colors ? 0xffffff : (spec.color ?? 0xffffff)}
            transparent opacity={spec.opacity ?? 1}
          />
        </lineSegments>
      ))}
      {ghostGeo && (
        <lineSegments geometry={ghostGeo}>
          <lineDashedMaterial color={0x8c8c96} dashSize={1.2} gapSize={1.2}
            transparent opacity={0.75}/>
        </lineSegments>
      )}
      {outGeo && (
        <lineSegments geometry={outGeo}>
          <lineBasicMaterial color={out.outlineColor ?? 0x0f172a}
            transparent opacity={out.outlineOpacity ?? 0.55}/>
        </lineSegments>
      )}
    </group>
  );
};

/* ── camera: fit, wheel-zoom at the cursor, drag-pan ──────────────────────── */

interface View2DProps {
  /** null until an output with real data is selected — fitting to a placeholder
   *  box would burn the one automatic fit and leave the real picture off-frame */
  extent: [number, number, number, number] | null;
  /** bumped by the Fit button */
  fitToken: number;
  /** a different machine — refit even though the user had zoomed in */
  fitKey?: string;
  onFitRequest: () => void;
  onHover: (x: number | null, y: number | null) => void;
}

const View2D: React.FC<View2DProps> = ({ extent, fitToken, fitKey, onFitRequest, onHover }) => {
  const camera = useThree(s => s.camera);
  const size = useThree(s => s.size);
  const gl = useThree(s => s.gl);
  // `base` is the world half-height the frustum was built from; the aspect is
  // reapplied on resize WITHOUT touching zoom or position, so growing the panel
  // does not silently re-frame a picture the user had zoomed into.
  const base = useRef(0);
  const fitted = useRef<string | null>(null);
  // false until the user zooms or pans: while untouched, every resize re-fits
  // (the panel often gets its final size a few frames after the first data),
  // so the picture always fills the area it was given without a manual Fit.
  const touched = useRef(false);

  const fit = React.useCallback((): boolean => {
    const cam = camera as THREE.OrthographicCamera;
    // drei's <OrthographicCamera makeDefault> becomes the default camera one
    // layout-effect AFTER the first render: on the very first pass `camera` is
    // still R3F's perspective default, so the fit must report failure and be
    // retried when the real camera lands (user 2026-09-06: "ты опять не
    // исправил масштабирование после расчёта по умолчанию" — the key had been
    // marked fitted on that failed attempt and no fit ever ran again).
    if (!(cam as unknown as { isOrthographicCamera?: boolean }).isOrthographicCamera) return false;
    if (!size.width || !size.height || !extent) return false;
    const [xmin, xmax, ymin, ymax] = extent;
    const cx = (xmin + xmax) * 0.5;
    const cy = (ymin + ymax) * 0.5;
    const aspect = size.width / size.height;
    // Half-height such that BOTH the model's height and its width (divided by
    // the canvas aspect) fit inside the frustum — user 2026-09-06: "сделай сразу
    // масштаб изображения, чтобы вписывался в отведённую площадь".  The old
    // max(w, h) ignored the aspect, so a tall canvas cut the sides off.
    const r = Math.max((ymax - ymin) * 0.53, (xmax - xmin) * 0.53 / Math.max(aspect, 1e-3)) || 1;
    base.current = r;
    touched.current = false;
    cam.left = -r * aspect; cam.right = r * aspect;
    cam.top = r; cam.bottom = -r;
    cam.zoom = 1;
    cam.position.set(cx, cy, 300);
    cam.lookAt(cx, cy, 0);
    cam.updateProjectionMatrix();
    return true;
  }, [camera, extent, size.width, size.height]);

  // First real data (and a genuinely different machine) frames itself; an
  // output switch does NOT — that is the state the user asked to keep.  The
  // key is marked fitted ONLY when a fit really happened; `fit` changes when
  // the camera does, so a failed first attempt is retried automatically.
  useEffect(() => {
    const key = fitKey ?? 'default';
    const valid = !!extent && Number.isFinite(extent[0]) && extent[1] > extent[0];
    if (!valid || !size.width) return;
    if (fitted.current === key) return;
    if (fit()) fitted.current = key;
  }, [fit, fitKey, extent, size.width]);

  useEffect(() => { if (fitToken > 0) fit(); }, [fitToken]);   // eslint-disable-line react-hooks/exhaustive-deps

  // The resize effect below must NOT re-run when the EXTENT changes.  Selecting
  // a part changes it (the extent is that part's bounding box now), and the user
  // 2026-09-06 asked to LOOK at a part, not to be re-framed every time one is
  // picked — the zoom is kept exactly as it is kept across an output switch, and
  // Fit is there for when the part should be framed.  So the fit function is
  // reached through a ref and this effect keys on the canvas SIZE alone.
  const fitRef = useRef(fit);
  useEffect(() => { fitRef.current = fit; }, [fit]);

  // Resize: untouched view → re-fit to the new area; zoomed/panned view → keep
  // zoom and centre, re-apply the aspect only.
  useEffect(() => {
    const cam = camera as THREE.OrthographicCamera;
    if (!base.current || !size.width || !size.height) return;
    if (!touched.current) { fitRef.current(); return; }
    const r = base.current;
    const aspect = size.width / size.height;
    cam.left = -r * aspect; cam.right = r * aspect;
    cam.top = r; cam.bottom = -r;
    cam.updateProjectionMatrix();
  }, [camera, size.width, size.height]);

  useEffect(() => {
    const el = gl.domElement;
    const cam = camera as THREE.OrthographicCamera;
    let dragging = false;
    let lastX = 0, lastY = 0;
    let raf = 0;

    /** normalised device coords of a pointer event, in this canvas */
    const ndc = (e: { clientX: number; clientY: number }) => {
      const r = el.getBoundingClientRect();
      return [
        ((e.clientX - r.left) / Math.max(r.width, 1)) * 2 - 1,
        -((((e.clientY - r.top) / Math.max(r.height, 1)) * 2) - 1),
      ] as [number, number];
    };
    /** world point under the pointer */
    const world = (e: { clientX: number; clientY: number }): [number, number] => {
      const [nx, ny] = ndc(e);
      return [cam.position.x + nx * cam.right / cam.zoom,
              cam.position.y + ny * cam.top / cam.zoom];
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (!cam.right) return;
      const [nx, ny] = ndc(e);
      const [wx, wy] = world(e);
      const k = Math.exp(-e.deltaY * 0.0015);
      touched.current = true;
      cam.zoom = Math.max(0.05, Math.min(2000, cam.zoom * k));
      cam.updateProjectionMatrix();
      // Keep the world point that was under the cursor under the cursor — the
      // difference between "zoom" and "zoom where I am looking".
      cam.position.x = wx - nx * cam.right / cam.zoom;
      cam.position.y = wy - ny * cam.top / cam.zoom;
      cam.updateProjectionMatrix();
    };
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0 && e.button !== 1) return;
      dragging = true; lastX = e.clientX; lastY = e.clientY;
      el.setPointerCapture?.(e.pointerId);
      el.style.cursor = 'grabbing';
    };
    const onMove = (e: PointerEvent) => {
      if (dragging) {
        touched.current = true;
        const r = el.getBoundingClientRect();
        const wpp = (2 * cam.right / cam.zoom) / Math.max(r.width, 1);
        const hpp = (2 * cam.top / cam.zoom) / Math.max(r.height, 1);
        cam.position.x -= (e.clientX - lastX) * wpp;
        cam.position.y += (e.clientY - lastY) * hpp;
        lastX = e.clientX; lastY = e.clientY;
        return;
      }
      // Hover readout, throttled to a frame — a pointermove storm over a 100k
      // triangle mesh must not become a React render storm.
      if (raf) return;
      const [wx, wy] = world(e);
      raf = requestAnimationFrame(() => { raf = 0; onHover(wx, wy); });
    };
    const onUp = (e: PointerEvent) => {
      dragging = false;
      el.releasePointerCapture?.(e.pointerId);
      el.style.cursor = 'grab';
    };
    const onLeave = () => { onHover(null, null); };
    const onDbl = () => onFitRequest();

    el.style.cursor = 'grab';
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('pointerdown', onDown);
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointerleave', onLeave);
    el.addEventListener('dblclick', onDbl);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('pointerdown', onDown);
      el.removeEventListener('pointermove', onMove);
      el.removeEventListener('pointerup', onUp);
      el.removeEventListener('pointerleave', onLeave);
      el.removeEventListener('dblclick', onDbl);
    };
  }, [camera, gl, onHover, onFitRequest]);

  return null;
};

/* ── max / min markers: world point → screen pixel, every frame ───────────── */

/**
 * Projects the marker world points onto the canvas and writes the result
 * straight into the overlay nodes' `transform`.
 *
 * SCREEN SPACE on purpose (user 2026-09-06: the label has to stay readable at
 * any zoom).  A ring drawn as scene geometry is a ring the size of two elements
 * when you zoom in and a dot when you zoom out; an HTML overlay positioned by
 * projecting one point is the same 14 px ring and the same 10 px label at every
 * zoom level.
 *
 * It writes the DOM directly rather than lifting the pixel position into React
 * state: this runs on every frame, and a setState per frame is a render per
 * frame of a component that owns a 100k-triangle mesh.
 */
const MarkerLayer: React.FC<{
  pts: ({ x: number; y: number } | null)[];
  els: React.MutableRefObject<(HTMLDivElement | null)[]>;
}> = ({ pts, els }) => {
  const camera = useThree(s => s.camera);
  const size = useThree(s => s.size);
  useFrame(() => {
    const cam = camera as THREE.OrthographicCamera;
    if (!cam.right || !cam.top || !size.width) return;
    for (let i = 0; i < pts.length; i++) {
      const el = els.current[i];
      if (!el) continue;
      const p = pts[i];
      if (!p) { el.style.visibility = 'hidden'; continue; }
      const nx = ((p.x - cam.position.x) * cam.zoom) / cam.right;
      const ny = ((p.y - cam.position.y) * cam.zoom) / cam.top;
      const px = (nx * 0.5 + 0.5) * size.width;
      const py = (0.5 - ny * 0.5) * size.height;
      // Panned out of frame: hide rather than pin to an edge, which would claim
      // the peak is somewhere it is not.
      const on = px > -40 && px < size.width + 40 && py > -30 && py < size.height + 30;
      el.style.visibility = on ? 'visible' : 'hidden';
      el.style.transform = `translate3d(${px.toFixed(1)}px, ${py.toFixed(1)}px, 0)`;
    }
  });
  return null;
};

/** One ring + crosshair + label, in page pixels.  `left/top: 0` and a transform
 *  the layer above rewrites — so nothing here re-renders while the camera moves. */
const MarkerNode = React.forwardRef<HTMLDivElement, {
  ink: string; text: string; title: string;
}>(({ ink, text, title }, ref) => (
  <div ref={ref} title={title} style={{
    position: 'absolute', left: 0, top: 0, visibility: 'hidden',
    willChange: 'transform', pointerEvents: 'none' }}>
    <div style={{ position: 'absolute', left: -7, top: -7, width: 14, height: 14,
      border: `1.5px solid ${ink}`, borderRadius: '50%',
      boxShadow: '0 0 0 1px rgba(2,6,23,0.85)' }}/>
    <div style={{ position: 'absolute', left: -11, top: -0.5, width: 22, height: 1,
      background: ink, opacity: 0.75 }}/>
    <div style={{ position: 'absolute', left: -0.5, top: -11, width: 1, height: 22,
      background: ink, opacity: 0.75 }}/>
    <div style={{ position: 'absolute', left: 12, top: -8, whiteSpace: 'nowrap',
      fontSize: 10, lineHeight: '14px', fontFamily: 'monospace', color: ink,
      background: 'rgba(2,6,23,0.78)', padding: '0 4px', borderRadius: 2,
      border: `1px solid ${ink}66` }}>
      {text}
    </div>
  </div>
));
MarkerNode.displayName = 'MarkerNode';

/* ── hover: nearest drawn triangle ────────────────────────────────────────── */

/** Uniform-grid nearest-centroid lookup.  Built once per output; a raycast into
 *  100k triangles for a text readout would cost more than the picture. */
function buildIndex(p: FieldProbeData | null | undefined) {
  if (!p || !p.val.length) return null;
  const n = p.val.length;
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  for (let i = 0; i < n; i++) {
    if (p.cx[i] < xmin) xmin = p.cx[i]; if (p.cx[i] > xmax) xmax = p.cx[i];
    if (p.cy[i] < ymin) ymin = p.cy[i]; if (p.cy[i] > ymax) ymax = p.cy[i];
  }
  const span = Math.max(xmax - xmin, ymax - ymin) || 1;
  const cell = span / Math.max(4, Math.ceil(Math.sqrt(n / 4)));
  const nx = Math.max(1, Math.ceil((xmax - xmin) / cell) + 1);
  const key = (ix: number, iy: number) => iy * nx + ix;
  const map = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    const ix = Math.floor((p.cx[i] - xmin) / cell);
    const iy = Math.floor((p.cy[i] - ymin) / cell);
    const k = key(ix, iy);
    const a = map.get(k); if (a) a.push(i); else map.set(k, [i]);
  }
  return (x: number, y: number): number | null => {
    if (x < xmin - cell * 4 || x > xmax + cell * 4
        || y < ymin - cell * 4 || y > ymax + cell * 4) return null;
    const ix0 = Math.floor((x - xmin) / cell);
    const iy0 = Math.floor((y - ymin) / cell);
    let best = -1, bestD = Infinity;
    for (let r = 0; r <= 4; r++) {
      for (let dy = -r; dy <= r; dy++) {
        for (let dx = -r; dx <= r; dx++) {
          if (r > 0 && Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
          const a = map.get(key(ix0 + dx, iy0 + dy));
          if (!a) continue;
          for (const i of a) {
            const d = (p.cx[i] - x) ** 2 + (p.cy[i] - y) ** 2;
            if (d < bestD) { bestD = d; best = i; }
          }
        }
      }
      if (best >= 0) break;         // ring r found something: near enough
    }
    if (best < 0 || bestD > (cell * 3) ** 2) return null;
    return p.val[best];
  };
}

/* ── the colour bar ───────────────────────────────────────────────────────── */

/**
 * Ansys's legend, and for the same reason: a BANDED plot's legend has to show
 * the band EDGES, because "which band is this colour" is the only question the
 * picture asks.  It reads its whole range off the SAME FieldScale the fill bands
 * with, so the two cannot disagree — for every output, in every tab.
 */
const ColourBar: React.FC<{ out: FieldOutput; height: number }> = ({ out, height }) => {
  const scale = out.scale;
  if (!scale) return null;
  const bands = Math.max(1, scale.bands);
  const pal = out.palette ?? jetPalette(bands);
  const H = Math.max(120, height - 24);
  const rowH = H / bands;
  return (
    <Box sx={{ display: 'flex', alignItems: 'stretch', gap: 0.5, pl: 1, pr: 1, py: 1 }}>
      <Box sx={{ display: 'flex', flexDirection: 'column-reverse', height: H,
        width: 14, border: '1px solid var(--line-soft)' }}>
        {Array.from({ length: bands }, (_, k) => {
          const [r, g, b] = pal[Math.min(k, pal.length - 1)] ?? [128, 128, 128];
          return <Box key={k} sx={{ flex: 1, background: `rgb(${r | 0},${g | 0},${b | 0})` }}/>;
        })}
      </Box>
      <Box sx={{ position: 'relative', height: H, minWidth: 62 }}>
        {Array.from({ length: bands + 1 }, (_, k) => (
          <Typography key={k} sx={{
            position: 'absolute', bottom: k * rowH - 5, left: 0,
            fontSize: 8.5, lineHeight: 1, whiteSpace: 'nowrap',
            color: 'var(--text-2)', fontFamily: 'monospace' }}>
            {scale.fmt(scale.edge(k))}{k === bands && scale.unit ? ` ${scale.unit}` : ''}
          </Typography>
        ))}
      </Box>
    </Box>
  );
};

/* ── the viewer ───────────────────────────────────────────────────────────── */

export interface FieldViewerProps {
  /** every quantity this host can show; entries with `geometry: null` are
   *  menu-only stubs (nothing solved for them yet) */
  outputs: FieldOutput[];
  selected: string;
  onSelect: (id: string) => void;
  height?: number;
  /** per-output extras, rendered in the same toolbar row as short controls */
  controls?: React.ReactNode;
  /** host actions on the right of the toolbar (Re-solve, Solve, …) */
  actions?: React.ReactNode;
  /** the case / frame / provenance line under the header */
  contextLabel?: React.ReactNode;
  contextTip?: string;
  contextColor?: string;
  busy?: boolean;
  busyNote?: React.ReactNode;
  error?: string | null;
  /** what to say when the selected output has nothing to draw */
  placeholder?: React.ReactNode;
  /** a different machine refits the camera; an output switch never does */
  fitKey?: string;
}

const FieldViewer: React.FC<FieldViewerProps> = ({
  outputs, selected, onSelect, height = 460, controls, actions,
  contextLabel, contextTip, contextColor, busy, busyNote, error, placeholder,
  fitKey,
}) => {
  const full = useMemo(
    () => outputs.find(o => o.id === selected) ?? outputs[0],
    [outputs, selected]);
  // ── the part tree's state ───────────────────────────────────────────────
  // Held by NAME — a name survives an output switch, a class tag does not (the
  // EM path tags every magnet with its own domain id, and each physics numbers
  // its parts its own way).  Session state per viewer: kept while the user
  // walks the output menu, never persisted (hiding the rotor is a look, not a
  // preference).  `hiddenTags` is the same thing one level down, for the
  // per-item rows the EM domain ids make possible.
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [hiddenTags, setHiddenTags] = useState<ReadonlySet<number>>(() => new Set<number>());
  const parts = useMemo(() => partsOf(full), [full]);
  const present = useMemo(() => new Set(parts.map(p => p.key)), [parts]);
  // Every part this viewer has ever drawn.  A part the CURRENT output does not
  // have (J is windings-only, Demag is magnets-only) stays in the tree greyed
  // out instead of vanishing, so the rows do not reshuffle under the cursor on
  // every output switch — and its hidden/shown state survives with it.
  const [seen, setSeen] = useState<FieldPart[]>([]);
  const allParts = useMemo(() => {
    const by = new Map<string, FieldPart>();
    for (const p of seen) by.set(p.key, p);
    for (const p of parts) by.set(p.key, p);      // current wins: fresh tags
    return [...by.values()].sort((a, b) =>
      PART_GROUP_ORDER.indexOf(a.group) - PART_GROUP_ORDER.indexOf(b.group)
      || a.label.localeCompare(b.label));
  }, [seen, parts]);
  useEffect(() => {
    if (allParts.length !== seen.length) setSeen(allParts);
  }, [allParts, seen.length]);

  // Is anything actually switched off?  When nothing is, the output is handed
  // to the viewer UNTOUCHED — which matters beyond saving a pass: the air
  // domains are not parts (they are never listed in the tree), so a restriction
  // always drops them.  That is right when the user isolates a part and wrong
  // when the tree is simply all-on.
  const anyHidden = useMemo(
    () => parts.some(p => hidden.has(p.key) || p.tags.some(t => hiddenTags.has(t))),
    [parts, hidden, hiddenTags]);
  const visible = useMemo(() => {
    const s = new Set<string>();
    for (const p of parts) if (!hidden.has(p.key)) s.add(p.key);
    return s;
  }, [parts, hidden]);
  // Memoised per (output, selection).  The restriction walks the mesh once;
  // from here down EVERYTHING — the fill, the colour bar, the header, the
  // markers, the hover index, the mesh overlay, Fit — reads the restricted
  // output and so restricts itself for free, which is why this belongs in the
  // viewer and not in three hosts (user 2026-09-06: one interface for every
  // graph).
  const out = useMemo(
    () => restrictToParts(full, anyHidden ? visible : null, hiddenTags),
    [full, anyHidden, visible, hiddenTags]);
  const [fitToken, setFitToken] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  // Viewer-wide, remembered, OFF by default — see the header of this file.
  const [showMax, setShowMax] = useState(() => readFlag('fieldview.max'));
  const [showMesh, setShowMesh] = useState(() => readFlag('fieldview.mesh'));
  const markerEls = useRef<(HTMLDivElement | null)[]>([]);

  const index = useMemo(() => buildIndex(out?.probe), [out?.probe]);
  const onHover = React.useCallback((x: number | null, y: number | null) => {
    if (x === null || y === null || !index) { setHover(null); return; }
    setHover(index(x, y));
  }, [index]);
  // A readout of the PREVIOUS output — or of the parts that were showing before
  // these ones — would be worse than none.
  useEffect(() => { setHover(null); }, [selected, hidden, hiddenTags]);

  // The menu, grouped by domain.  MUI needs a flat child list, so the group
  // headings are interleaved rather than nested.
  const grouped = useMemo(() => {
    const by = new Map<FieldGroup, FieldOutput[]>();
    for (const o of outputs) {
      const a = by.get(o.group); if (a) a.push(o); else by.set(o.group, [o]);
    }
    return GROUP_ORDER.filter(g => by.has(g)).map(g => [g, by.get(g)!] as const);
  }, [outputs]);
  const oneGroup = grouped.length <= 1;

  // ── the tree's model ────────────────────────────────────────────────────
  // The rows ARE the parts of the picture.  Per-item rows (Magnet 3) appear
  // only where the output's class tags tell the items apart — the EM domain ids
  // do, a mechanical part id does not — and the group toggle is what matters
  // anyway, so a machine with more items than the cap keeps the group row alone
  // rather than growing a hundred-row tree (user 2026-09-06: keep it simple).
  const treeModel: TreeModel = useMemo(() => {
    const rows: TreeRow[] = allParts.map(p => {
      const here = present.has(p.key);
      const shown = here && !hidden.has(p.key);
      const kids = (here && p.tags.length > 1 && p.tags.length <= PER_ITEM_ROWS)
        ? p.tags.map((t, i) => ({
            key: `${p.key}#${t}`,
            label: `${cap(p.label)} ${i + 1}`,
            colour: p.colour,
            visible: shown && !hiddenTags.has(t),
          }))
        : undefined;
      return { key: p.key, label: cap(p.label), colour: p.colour,
               visible: shown, present: here, group: kids };
    });
    return {
      rows,
      allVisible: !anyHidden,
      onToggle: (key) => {
        const h = key.lastIndexOf('#');
        if (h > 0) {
          const tag = Number(key.slice(h + 1));
          setHiddenTags(s => { const n = new Set(s); if (!n.delete(tag)) n.add(tag); return n; });
          return;
        }
        setHidden(s => { const n = new Set(s); if (!n.delete(key)) n.add(key); return n; });
      },
      // Isolate means what it means on the 3-D tab: this part, nothing else —
      // and all of its items back, or "isolate the magnets" could show none.
      onIsolate: (key) => {
        setHidden(new Set([...present].filter(k => k !== key)));
        setHiddenTags(new Set());
      },
      onShowAll: () => { setHidden(new Set()); setHiddenTags(new Set()); },
    };
  }, [allParts, present, hidden, hiddenTags, anyHidden]);

  const requestFit = React.useCallback(() => setFitToken(t => t + 1), []);

  const extent = out?.extent ?? null;
  const fmtV = (v: number | null | undefined): string =>
    (v === null || v === undefined || !Number.isFinite(v)) ? '—'
      : (out?.scale ? out.scale.fmt(v) : String(v));

  // Where the displayed quantity peaks and bottoms out.  From `probe` — the raw
  // display-unit value per drawn triangle at its display-space centroid — so a
  // deformed picture marks the DEFORMED position and a percentile-clipped scale
  // cannot move the marker (see `extremaOf`).
  const ext2 = useMemo(() => extremaOf(
    out?.probe, out?.vertexValues,
    (out?.geometry?.getAttribute('position') as { array?: ArrayLike<number> } | undefined)?.array,
  ), [out?.probe, out?.vertexValues, out?.geometry]);
  /** "r 61.0 mm, 17.3°" — how an engineer says where on a cross-section. */
  const where = (e: FieldExtremum): string =>
    `r ${e.r.toFixed(1)} mm, ${e.deg.toFixed(1)}°`;
  const partOf = (e: FieldExtremum): string => {
    const n = (e.cls !== undefined && out?.classLabel) ? out.classLabel(e.cls) : undefined;
    return n ? ` · ${n}` : '';
  };
  const markerText = (tag: string, e: FieldExtremum): string =>
    `${tag} ${fmtV(e.v)}${out?.unit ? ` ${out.unit}` : ''} · ${where(e)}${partOf(e)}`;
  const markers = (showMax && out?.geometry)
    ? [ext2.max, ext2.min] : [null, null];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, minWidth: 0 }}>
      {/* ── toolbar: the output menu, the output's own extras, Fit ───────── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Tooltip title="Which quantity to draw. One picture, one camera — switching does not move the view." placement="top">
          <Select size="small" value={out?.id ?? ''} onChange={(e) => onSelect(String(e.target.value))}
            sx={{ fontSize: 12, height: 30, minWidth: 140,
              '& .MuiSelect-select': { py: 0.5 } }}>
            {grouped.flatMap(([g, list]) => [
              ...(oneGroup ? [] : [
                <ListSubheader key={`h-${g}`} sx={{ fontSize: 9, lineHeight: '18px',
                  color: 'var(--text-4)', bgcolor: 'var(--panel)',
                  textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  {g}
                </ListSubheader>,
              ]),
              ...list.map(o => (
                <MenuItem key={o.id} value={o.id} sx={{ fontSize: 12 }}>{o.menuLabel}</MenuItem>
              )),
            ])}
          </Select>
        </Tooltip>
        {controls}
        <Tooltip title="Frame the whole model again. Scroll to zoom at the cursor, drag to pan, double-click the picture to fit.">
          <Button size="small" onClick={requestFit}
            sx={{ color: '#93c5fd', fontSize: 10, textTransform: 'none',
              minWidth: 0, px: 1, border: '1px solid var(--line-soft)' }}>
            Fit
          </Button>
        </Tooltip>
        {/* Both toggles belong to the VIEWER, so every output in every tab has
            them without a host lifting a finger (user 2026-09-06). */}
        <Tooltip title="Ring the maximum and the minimum of the displayed quantity — value, radius and angle, and the part they sit in. Read off the solved values, not the colours, so a percentile-clipped scale cannot move them.">
          <Button size="small"
            onClick={() => setShowMax(v => { writeFlag('fieldview.max', !v); return !v; })}
            sx={{ color: showMax ? 'var(--text-0)' : '#93c5fd', fontSize: 10,
              textTransform: 'none', minWidth: 0, px: 1,
              border: `1px solid ${showMax ? '#93c5fd' : 'var(--line-soft)'}` }}>
            Max
          </Button>
        </Tooltip>
        <Tooltip title="Draw the solver's triangle edges over the field — the mesh the numbers were actually computed on.">
          <Button size="small"
            onClick={() => setShowMesh(v => { writeFlag('fieldview.mesh', !v); return !v; })}
            sx={{ color: showMesh ? 'var(--text-0)' : '#93c5fd', fontSize: 10,
              textTransform: 'none', minWidth: 0, px: 1,
              border: `1px solid ${showMesh ? '#93c5fd' : 'var(--line-soft)'}` }}>
            Mesh
          </Button>
        </Tooltip>
        <Box sx={{ flex: 1 }} />
        {actions}
      </Box>

      {/* ── header: name, range, the value under the cursor ──────────────── */}
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.75, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
          {out?.label ?? '—'}{out?.unit ? ` — ${out.unit}` : ''}
        </Typography>
        {out?.tip && (
          <Tooltip title={out.tip} placement="top">
            <span style={{ color: 'var(--text-4)', fontSize: 11, cursor: 'help' }}>ⓘ</span>
          </Tooltip>
        )}
        {out?.geometry && (
          <Tooltip title="Extremes of what is actually drawn. The colour bar may stop short of them on purpose — a percentile clip keeps one singular element from flattening the picture." placement="top">
            {/* With Max on the header says WHERE too, in the same words the
                marker does ("max 5408 MPa @ r 61.0 mm, 17.3°").  Gated on the
                toggle so the default line stays one short line — the project's
                no-walls-of-text rule — and so "убрать" removes it everywhere. */}
            <Typography sx={{ fontSize: 11, fontFamily: 'monospace',
              color: 'var(--text-3)', cursor: 'help' }}>
              max {fmtV(out.vMax)}
              {showMax && ext2.max ? ` @ ${where(ext2.max)}` : ''}
              {' · '}min {fmtV(out.vMin)}
              {showMax && ext2.min ? ` @ ${where(ext2.min)}` : ''}
            </Typography>
          </Tooltip>
        )}
        {out?.statText && (
          <Typography sx={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-3)' }}>
            {out.statText}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        {hover !== null && (
          <Typography sx={{ fontSize: 11, fontFamily: 'monospace', color: '#7dd3fc' }}>
            cursor {fmtV(hover)}{out?.unit ? ` ${out.unit}` : ''}
          </Typography>
        )}
      </Box>

      {contextLabel && (
        <Tooltip title={contextTip ?? ''} placement="bottom-start">
          <Typography sx={{ fontSize: 10, cursor: contextTip ? 'help' : 'default',
            color: contextColor ?? 'var(--text-4)' }}>
            {contextLabel}
          </Typography>
        </Tooltip>
      )}

      {error && (
        <Typography sx={{ fontSize: 11, color: '#fca5a5', p: 1,
          border: '1px solid #7f1d1d', borderRadius: 1 }}>
          {error}
        </Typography>
      )}

      {/* ── the picture + its colour bar ─────────────────────────────────── */}
      <Box sx={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto',
        gap: 1, height }}>
        <Box sx={{ position: 'relative', border: '1px solid var(--app-bg)',
          bgcolor: 'var(--panel-2)', minHeight: height, minWidth: 0 }}>
          {/* The Canvas stays mounted even with nothing to draw, so the camera
              (and the user's zoom) survives an output that has not been solved
              yet — unmounting it here is what used to throw the view away. */}
          <Canvas style={{ background: 'var(--panel-2)' }} onCreated={guardCanvas('field viewer')}>
            {/* `manual`: without it drei re-derives the frustum from the canvas
                PIXEL size on every resize (left = −width/2 …), silently undoing
                the fit a few frames later — the picture then sat as a 1 mm/px
                thumbnail in the middle of the panel (user 2026-09-06: "не
                забудь про масштабирование по умолчанию").  View2D owns the
                frustum completely. */}
            <OrthographicCamera makeDefault manual position={[0, 0, 300]} near={0.1} far={5000}/>
            <View2D extent={extent} fitToken={fitToken} fitKey={fitKey}
              onFitRequest={requestFit} onHover={onHover}/>
            <ambientLight intensity={1}/>
            {out && <OutputMesh out={out} mesh={showMesh}/>}
            <MarkerLayer pts={markers} els={markerEls}/>
          </Canvas>

          {/* Max / min markers.  Outside the Canvas (screen-space HTML), inside
              the same positioned box, and pointer-transparent so they never
              take a drag or a wheel event away from the camera. */}
          <Box sx={{ position: 'absolute', inset: 0, overflow: 'hidden',
            pointerEvents: 'none', zIndex: 3 }}>
            {markers[0] && (
              <MarkerNode ref={el => { markerEls.current[0] = el; }} ink={MAX_INK}
                text={markerText('max', markers[0]!)}
                title="Maximum of the displayed quantity, at the element that holds it."/>
            )}
            {markers[1] && (
              <MarkerNode ref={el => { markerEls.current[1] = el; }} ink={MIN_INK}
                text={markerText('min', markers[1]!)}
                title="Minimum of the displayed quantity, at the element that holds it."/>
            )}
          </Box>

          {/* The part tree, over the top-left of the picture — the same panel,
              in the same place, as the 3-D tab's (user 2026-09-06: "используй
              то же самое дерево").  Only when there is a choice to make: one
              part is no tree.  Above the marker layer, under the busy veil. */}
          {allParts.length > 1 && (
            <ComponentTreeView
              model={treeModel}
              style={{ top: 8, left: 8, zIndex: 4, width: 176 }}
              rowsMaxHeight={Math.max(120, height - 70)}
            />
          )}

          {busy && (
            <Box sx={{ position: 'absolute', inset: 0, flexDirection: 'column',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              bgcolor: 'rgba(6,13,23,0.7)', zIndex: 5, gap: 1 }}>
              <CircularProgress size={32}/>
              {busyNote}
            </Box>
          )}
          {!busy && !out?.geometry && (out?.blank || placeholder) && (
            <Box sx={{ position: 'absolute', inset: 0, display: 'flex',
              alignItems: 'center', justifyContent: 'center', px: 2,
              pointerEvents: 'none' }}>
              <Box sx={{ pointerEvents: 'auto', textAlign: 'center' }}>
                {out?.blank
                  ? <Typography sx={{ fontSize: 11, color: '#fbbf24' }}>{out.blank}</Typography>
                  : placeholder}
              </Box>
            </Box>
          )}
        </Box>

        <ColourBar out={out ?? ({ scale: null } as unknown as FieldOutput)} height={height}/>
      </Box>

      {out?.note && (
        <Tooltip title={out.note} placement="bottom-start">
          <Typography sx={{ fontSize: 9, color: 'var(--line)', cursor: 'help',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {out.note}
          </Typography>
        </Tooltip>
      )}
    </Box>
  );
};

export default FieldViewer;
