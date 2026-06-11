/**
 * FemFieldChart — Real scikit-fem A_z field on the motor mesh.
 *
 * Fetches /api/simulation/physics/fem_field2d (uses the SAME mesh as the
 * Mesh tab — settings live in localStorage under mesh.*) and renders:
 *   • the triangle mesh, with each vertex coloured by A_z via a diverging
 *     RdBu colormap (classic FEA flux-potential look)
 *   • CadQuery boundary overlay (smooth outlines per domain)
 *   • a sidebar with torque, copper / iron / magnet-eddy losses, efficiency
 *     — already multiplied by n_sectors so the values represent the FULL
 *     motor (1/4 model × 4, etc.).
 *
 * Interactive: orbit / pan / zoom via the same OrthographicCamera +
 * OrbitControls combo used by FemMeshViewer3D.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Box, Paper, Typography, CircularProgress, Button, Tooltip,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, OrthographicCamera } from '@react-three/drei';
import Viewcube from '../viewer3d/Viewcube';
import { ViewcubeNavigation, CameraSync } from '../viewer3d/MotorScene';
import * as THREE from 'three';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

// ── types ─────────────────────────────────────────────────────────────────
import type { FemPayload } from './fem-types';
export type { FemPayload } from './fem-types';

// ── colour maps ───────────────────────────────────────────────────────────
// Viridis — sequential, no near-white midpoint, reads cleanly on dark canvas.
function viridis01(t: number): [number, number, number] {
  const c0 = [68,  1,   84];
  const c1 = [59,  82,  139];
  const c2 = [33,  145, 140];
  const c3 = [94,  201, 98];
  const c4 = [253, 231, 37];
  const pts = [c0, c1, c2, c3, c4];
  const s = Math.max(0, Math.min(1, t)) * (pts.length - 1);
  const i = Math.min(Math.floor(s), pts.length - 2);
  const f = s - i;
  return [
    pts[i][0] + f * (pts[i + 1][0] - pts[i][0]),
    pts[i][1] + f * (pts[i + 1][1] - pts[i][1]),
    pts[i][2] + f * (pts[i + 1][2] - pts[i][2]),
  ];
}
// Classic Ansys-style rainbow LUT (blue → cyan → green → yellow → red).
function jet01(t: number): [number, number, number] {
  const x = Math.max(0, Math.min(1, t));
  const r = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * x - 3))) * 255;
  const g = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * x - 2))) * 255;
  const b = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * x - 1))) * 255;
  return [r, g, b];
}

// Discrete-banded rainbow — quantize t into N levels so the fill renders
// as Ansys-style colour patches instead of a smooth gradient.
function jetBands(t: number, n: number = 20): [number, number, number] {
  // Quantise to band centres so the colour matches the iso-line at the band edge.
  const tq = (Math.floor(t * n) + 0.5) / n;
  return jet01(tq);
}

// ── helpers: read mesh params persisted by MeshPanel ──────────────────────
function readMeshSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

// ── R3F mesh component ────────────────────────────────────────────────────
type FieldMode = 'Az' | 'Bmag' | 'J' | 'Demag';

// Diverging blue→green→red colormap for signed J_z (Ansys "J" style:
// red = +max, blue = −max, green = 0).
function jetSigned(t: number): [number, number, number] {
  // t ∈ [-1, +1].  Build 11 stops mirroring the Ansys J legend:
  //   −1 .. −0.6 blue family,   −0.6 .. −0.2 cyan,   −0.2 .. +0.2 green,
  //   +0.2 .. +0.6 yellow,      +0.6 .. +1 red.
  const u = Math.max(-1, Math.min(1, t));
  if (u <= -0.6) return [0, 0, 255];                // dark blue
  if (u <= -0.4) return [0, 80, 255];               // blue
  if (u <= -0.2) return [0, 200, 230];              // cyan
  if (u <= -0.05) return [80, 230, 80];             // bright green
  if (u <= +0.05) return [40, 200, 40];             // green (zero)
  if (u <= +0.2) return [220, 240, 30];             // yellow-green
  if (u <= +0.4) return [255, 220, 0];              // yellow
  if (u <= +0.6) return [255, 120, 0];              // orange
  return [255, 0, 0];                                // red
}

const N_BANDS = 20;           // # of discrete colour bands / iso-A levels

/** Extract iso-A_z contour line segments via per-triangle linear
 *  interpolation (marching-segments on tris).
 *  For each iso level L, find triangles where A_min ≤ L ≤ A_max, then
 *  locate the two edges that L crosses and emit one line segment.
 *  Returns just positions — colour is applied uniformly at draw time. */
function buildIsoLines(
  vertices: [number, number][],
  triangles: [number, number, number][],
  domain_per_tri: number[],
  A_z_per_node: number[],
  A_min: number,
  A_max: number,
  nLevels: number,
  S: number,        // metres → mm
  z: number,        // depth for visibility
): Float32Array {
  const DOM_OUTER = 8;
  // LINEAR distribution of iso-levels — required so the lines have
  // UNIFORM spacing inside each magnet (where A_z varies linearly with
  // the local coordinate of constant ∇A_z = constant B).  Log-spaced
  // levels would cluster lines around A=0, making them look "denser at
  // the middle of the magnet" — the artefact the user pointed out.
  const range = Math.max(A_max - A_min, 1e-12);
  const pos: number[] = [];
  for (let k = 1; k < nLevels; k++) {
    const t  = k / nLevels;
    const L  = A_min + t * range;
    for (let ti = 0; ti < triangles.length; ti++) {
      if (domain_per_tri[ti] === DOM_OUTER) continue;
      const [a, b1, c] = triangles[ti];
      const Aa = A_z_per_node[a];
      const Ab = A_z_per_node[b1];
      const Ac = A_z_per_node[c];
      const lo = Math.min(Aa, Ab, Ac);
      const hi = Math.max(Aa, Ab, Ac);
      if (L < lo || L > hi) continue;
      const ix: [number, number][] = [];
      const ed: number[][] = [[a, b1], [b1, c], [c, a]];
      for (let e = 0; e < 3; e++) {
        const i0 = ed[e][0], i1 = ed[e][1];
        const f0 = A_z_per_node[i0] - L;
        const f1 = A_z_per_node[i1] - L;
        if (f0 * f1 > 0 || (f0 === 0 && f1 === 0)) continue;
        const denom = (f0 - f1);
        const u = denom === 0 ? 0.5 : f0 / denom;
        const x = vertices[i0][0] + u * (vertices[i1][0] - vertices[i0][0]);
        const y = vertices[i0][1] + u * (vertices[i1][1] - vertices[i0][1]);
        ix.push([x, y]);
        if (ix.length === 2) break;
      }
      if (ix.length === 2) {
        pos.push(ix[0][0] * S, ix[0][1] * S, z,
                 ix[1][0] * S, ix[1][1] * S, z);
      }
    }
  }
  return new Float32Array(pos);
}

const FieldMesh: React.FC<{ payload: FemPayload; mode: FieldMode }>
  = ({ payload, mode }) => {
  // Fill geometry — per-vertex Ansys-style banded rainbow for A_z, or
  // per-triangle flat jet for |B|.  We SKIP DOM_OUTER (8) triangles so
  // the outer far-field air ring (visible only for the BC) doesn't eat
  // most of the canvas with a uniform near-zero band.
  const fillGeo = useMemo(() => {
    const { vertices, triangles, domain_per_tri,
            A_z_per_node, A_z_min, A_z_max,
            Bmag_per_tri, B_mag_max } = payload;
    const S = 1000;
    const DOM_OUTER = 8;

    // ── helper: percentile of an array (in-place sort) ───────────────
    const pctl = (arr: number[], p: number): number => {
      if (!arr.length) return 0;
      const a = Float64Array.from(arr).sort();
      const i = Math.max(0, Math.min(a.length - 1,
        Math.floor((p / 100) * (a.length - 1))));
      return a[i];
    };

    if (mode === 'Az') {
      // LINEAR mapping of A_z → colormap (no compression) so the iso-line
      // density inside each magnet stays UNIFORM — A_z varies linearly
      // with position inside a uniformly-magnetised magnet, and the user
      // expects identical band spacing across the whole magnet body.
      const interior = new Set<number>();
      for (let ti = 0; ti < triangles.length; ti++) {
        if (domain_per_tri[ti] === DOM_OUTER) continue;
        for (const vi of triangles[ti]) interior.add(vi);
      }
      const interiorAbs: number[] = [];
      interior.forEach(vi => interiorAbs.push(Math.abs(A_z_per_node[vi])));
      const amax = Math.max(pctl(interiorAbs, 99), 1e-12);
      const lo = -amax, hi = +amax;
      const range = 2 * amax;
      const compress = (a: number): number => {
        // Linear normalisation A_z → [-1, +1]
        return Math.max(-1, Math.min(1, a / amax));
      };

      // Only triangles NOT in DOM_OUTER get filled
      const keep = triangles.map((_, ti) => domain_per_tri[ti] !== DOM_OUTER);
      const positions = new Float32Array(vertices.length * 3);
      const colors    = new Float32Array(vertices.length * 3);
      for (let i = 0; i < vertices.length; i++) {
        positions[3 * i]     = vertices[i][0] * S;
        positions[3 * i + 1] = vertices[i][1] * S;
        positions[3 * i + 2] = 0;
        // map A_z → [-1, 1] via signed log, then to [0, 1] for jetBands
        const tc = compress(A_z_per_node[i]);
        const t  = 0.5 + 0.5 * tc;
        const [r, g, b] = jetBands(t, N_BANDS);
        colors[3 * i]     = r / 255;
        colors[3 * i + 1] = g / 255;
        colors[3 * i + 2] = b / 255;
      }
      const indexArr: number[] = [];
      for (let i = 0; i < triangles.length; i++) {
        if (!keep[i]) continue;
        indexArr.push(triangles[i][0], triangles[i][1], triangles[i][2]);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      g.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
      g.setIndex(new THREE.BufferAttribute(new Uint32Array(indexArr), 1));
      // Stash the symmetric percentile-clipped range so isoGeo and the
      // colour bar can re-use the EXACT same scale.
      (g as any).userData = { Az_lo: lo, Az_hi: hi };
      return g;
    }
    // NOTE: lo/hi defined here is referenced by isoGeo + colour bar via
    // fillGeo.userData (see below).

    if (mode === 'J') {
      // J_z mode — Ansys "J [A/m²]" style.  Coil triangles get a diverging
      // blue→green→red colormap based on signed J_z, scaled to the 99-pct
      // absolute value across all coil cells in the current frame.  Iron
      // / magnet / air cells are not coloured.
      const jz = payload.J_z_per_tri ?? [];
      const dom = domain_per_tri;
      const DOM_COIL = 2;
      const nTri = triangles.length;
      let vmaxAbs = 1e-12;
      const sample: number[] = [];
      for (let i = 0; i < nTri; i++) {
        if (dom[i] !== DOM_COIL) continue;
        if (i < jz.length) sample.push(Math.abs(jz[i]));
      }
      if (sample.length) {
        const sorted = Float64Array.from(sample).sort();
        const k = Math.floor(0.99 * (sorted.length - 1));
        vmaxAbs = Math.max(sorted[k], 1e-12);
      }
      const positions = new Float32Array(nTri * 3 * 3);
      const colors    = new Float32Array(nTri * 3 * 3);
      let p = 0, c = 0;
      for (let i = 0; i < nTri; i++) {
        if (dom[i] !== DOM_COIL) continue;
        const tt = triangles[i];
        const v  = i < jz.length ? jz[i] : 0;
        const tNorm = Math.max(-1, Math.min(1, v / vmaxAbs));
        const [rr, gg, bb] = jetSigned(tNorm);
        for (const vi of tt) {
          positions[p++] = vertices[vi][0] * S;
          positions[p++] = vertices[vi][1] * S;
          positions[p++] = 0;
          colors[c++] = rr / 255;
          colors[c++] = gg / 255;
          colors[c++] = bb / 255;
        }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position',
        new THREE.BufferAttribute(positions.subarray(0, p), 3));
      g.setAttribute('color',
        new THREE.BufferAttribute(colors.subarray(0, c), 3));
      (g as any).userData = { J_z_vmax: vmaxAbs };
      return g;
    }

    if (mode === 'Demag') {
      // Demag %: show ONLY magnet triangles, coloured by the IRREVERSIBLE
      // demagnetisation  % = (1 − Br_factor)·100  — blue = 0 % (safe),
      // red = 100 % (fully demagnetised).  Non-magnet cells drop out so the
      // magnet geometry stands alone on the canvas.
      const dc = (payload as any).demag_coef_per_tri as number[] | undefined;
      const dom = domain_per_tri;
      const DOM_MAG_N = 4, DOM_MAG_S = 44;
      const nTri = triangles.length;
      const positions = new Float32Array(nTri * 3 * 3);
      const colors    = new Float32Array(nTri * 3 * 3);
      let p = 0, c = 0;
      for (let i = 0; i < nTri; i++) {
        if (dom[i] !== DOM_MAG_N && dom[i] !== DOM_MAG_S) continue;
        const tt = triangles[i];
        const coef = dc ? Math.max(0, Math.min(1, dc[i])) : 1;
        const [rr, gg, bb] = jetBands(1 - coef, 11);   // colour by % lost (red = demagnetised)
        for (const vi of tt) {
          positions[p++] = vertices[vi][0] * S;
          positions[p++] = vertices[vi][1] * S;
          positions[p++] = 0;
          colors[c++] = rr / 255;
          colors[c++] = gg / 255;
          colors[c++] = bb / 255;
        }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position',
        new THREE.BufferAttribute(positions.subarray(0, p), 3));
      g.setAttribute('color',
        new THREE.BufferAttribute(colors.subarray(0, c), 3));
      return g;
    }

    // |B| — SMOOTH, nodal-averaged jet (like Ansys).  B = ∇A_z is constant per
    // P1 triangle, so a flat per-triangle fill looks faceted ("scary").  Instead
    // we average each triangle's |B| onto its 3 nodes (AREA-WEIGHTED) and colour
    // PER-VERTEX, exactly like the A_z field — Three.js then interpolates the
    // colour smoothly across every triangle → a continuous gradient, no facets.
    // vmax = 95-percentile of the interior |B|, hard-capped at 1.8 T (iron
    // saturation knee) so 1/4-sector sharp-corner spikes don't squash the LUT.
    const nV = vertices.length;
    const bSum = new Float64Array(nV);
    const wSum = new Float64Array(nV);
    const interiorB: number[] = [];
    for (let ti = 0; ti < triangles.length; ti++) {
      if (domain_per_tri[ti] === DOM_OUTER) continue;
      const [ia, ib, ic] = triangles[ti];
      const ax = vertices[ia][0], ay = vertices[ia][1];
      const bx = vertices[ib][0], by = vertices[ib][1];
      const cx = vertices[ic][0], cy = vertices[ic][1];
      const area = Math.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) * 0.5 || 1e-12;
      const bm = Bmag_per_tri[ti];
      interiorB.push(bm);
      bSum[ia] += bm * area; wSum[ia] += area;
      bSum[ib] += bm * area; wSum[ib] += area;
      bSum[ic] += bm * area; wSum[ic] += area;
    }
    const vmaxPct = pctl(interiorB, 95);
    const vmax = Math.min(Math.max(vmaxPct, 0.05), 1.8);

    const keep = triangles.map((_, ti) => domain_per_tri[ti] !== DOM_OUTER);
    const positions = new Float32Array(nV * 3);
    const colors    = new Float32Array(nV * 3);
    for (let i = 0; i < nV; i++) {
      positions[3 * i]     = vertices[i][0] * S;
      positions[3 * i + 1] = vertices[i][1] * S;
      positions[3 * i + 2] = 0;
      const bnode = wSum[i] > 0 ? bSum[i] / wSum[i] : 0;
      const t = Math.min(1, Math.max(0, bnode / vmax));
      const [rr, gg, bb] = jet01(t);          // continuous → smooth gradient
      colors[3 * i]     = rr / 255;
      colors[3 * i + 1] = gg / 255;
      colors[3 * i + 2] = bb / 255;
    }
    const indexArr: number[] = [];
    for (let i = 0; i < triangles.length; i++) {
      if (!keep[i]) continue;
      indexArr.push(triangles[i][0], triangles[i][1], triangles[i][2]);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    g.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(indexArr), 1));
    (g as any).userData = { Bmag_vmax: vmax };
    return g;
  }, [payload, mode]);

  // Iso-A contour lines (only in A_z mode) — drawn as crisp DARK lines on
  // top of the rainbow fill, like the "flux lines" in FEMM / FEMAG.  We
  // emit them at finer level than the colour bands (N_BANDS × 3) so the
  // line pattern is dense enough to read everywhere — even in regions
  // where A_z varies slowly (outer air, stator iron).
  const isoGeo = useMemo(() => {
    if (mode !== 'Az') return null;
    const { vertices, triangles, domain_per_tri, A_z_per_node } = payload;
    // Pick the same symmetric 2/98 range the fill uses so contour lines
    // are visible across the whole motor (stator iron included), not just
    // inside the magnets where A_z peaks.
    const ud = (fillGeo as any).userData ?? {};
    const lo = ud.Az_lo ?? payload.A_z_min;
    const hi = ud.Az_hi ?? payload.A_z_max;
    // More iso lines than fill bands — gives a denser FEMM/Ansys-style
    // flux-line pattern in the iron + airgap regions.
    const positions = buildIsoLines(
      vertices, triangles, domain_per_tri, A_z_per_node,
      lo, hi, N_BANDS * 2, 1000, 1.0,
    );
    if (positions.length === 0) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    return g;
  }, [payload, mode, fillGeo]);

  // Outlines for crisp boundaries on top of the field
  const outGeo = useMemo(() => {
    const S = 1000;
    const Z = 0.8;
    const arr: number[] = [];
    for (const entry of payload.outlines ?? []) {
      for (const loop of entry.loops) {
        if (loop.length < 2) continue;
        for (let i = 0; i < loop.length; i++) {
          const a = loop[i];
          const b = loop[(i + 1) % loop.length];
          arr.push(a[0] * S, a[1] * S, Z, b[0] * S, b[1] * S, Z);
        }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(arr), 3));
    return g;
  }, [payload]);

  return (
    <group>
      <mesh geometry={fillGeo}>
        <meshBasicMaterial vertexColors side={THREE.DoubleSide}/>
      </mesh>
      {isoGeo && (
        <lineSegments geometry={isoGeo}>
          <lineBasicMaterial color={0x0b1220} transparent opacity={0.85}/>
        </lineSegments>
      )}
      <lineSegments geometry={outGeo}>
        <lineBasicMaterial color={0x0f172a} transparent opacity={0.55}/>
      </lineSegments>
    </group>
  );
};

const FitView: React.FC<{
  payload: FemPayload | null;
  controlsRef: React.MutableRefObject<any>;
}> = ({ payload, controlsRef }) => {
  const { camera, size, gl } = useThree();
  useEffect(() => {
    if (!payload || size.width === 0) return;
    if (!(camera as any).isOrthographicCamera) return;
    const cam = camera as THREE.OrthographicCamera;
    const [xmin, xmax, ymin, ymax] = payload.extent;
    const cx = (xmin + xmax) * 0.5 * 1000;
    const cy = (ymin + ymax) * 0.5 * 1000;
    const r  = Math.max(xmax - xmin, ymax - ymin) * 1000 * 0.55;
    const aspect = size.width / size.height;
    cam.left   = -r * aspect; cam.right  =  r * aspect;
    cam.top    =  r;          cam.bottom = -r;
    cam.zoom = 1.0;
    cam.position.set(cx, cy, 300);
    cam.lookAt(cx, cy, 0);
    cam.updateProjectionMatrix();
    if (controlsRef.current) {
      controlsRef.current.target.set(cx, cy, 0);
      controlsRef.current.update();
    }
    gl.render(camera as any, camera as any);
  }, [payload, size.width, size.height, camera, gl, controlsRef]);
  return null;
};

// ── colour-bar component (vertical) ───────────────────────────────────────
const ColorBar: React.FC<{
  vmin: number; vmax: number; unit: string; lut: (t: number) => [number, number, number];
}> = ({ vmin, vmax, unit, lut }) => {
  const ticks = useMemo(() => {
    const n = 5;
    return Array.from({ length: n }, (_, i) => vmin + (vmax - vmin) * (i / (n - 1)));
  }, [vmin, vmax]);
  const stops = Array.from({ length: 11 }, (_, k) => {
    const [r, g, b] = lut(k / 10);
    return `rgb(${r|0},${g|0},${b|0}) ${(k * 10).toFixed(0)}%`;
  });
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
      pl: 1, pr: 1, py: 1 }}>
      <Box sx={{
        width: 12, height: 200,
        background: `linear-gradient(to top, ${stops.join(', ')})`,
        border: '1px solid #1e293b',
      }}/>
      <Box sx={{ display: 'flex', flexDirection: 'column-reverse',
        justifyContent: 'space-between', height: 200 }}>
        {ticks.map((t, i) => (
          <Typography key={i} sx={{ fontSize: 9, color: '#94a3b8',
            fontFamily: 'monospace', lineHeight: 1 }}>
            {t.toFixed(2)} {unit}
          </Typography>
        ))}
      </Box>
    </Box>
  );
};

// ── stats sidebar ─────────────────────────────────────────────────────────
const StatRow: React.FC<{ label: string; value: string; sub?: string }> = ({
  label, value, sub,
}) => (
  <Box sx={{ display: 'flex', justifyContent: 'space-between',
    py: 0.4, borderBottom: '1px solid #0f172a' }}>
    <Box>
      <Typography sx={{ fontSize: 10, color: '#64748b' }}>{label}</Typography>
      {sub && <Typography sx={{ fontSize: 9, color: '#334155' }}>{sub}</Typography>}
    </Box>
    <Typography sx={{ fontSize: 11, color: '#cbd5e1', fontFamily: 'monospace' }}>
      {value}
    </Typography>
  </Box>
);

// ── main component ────────────────────────────────────────────────────────
interface Props {
  gamma_deg?: number;
  rotor_angle_deg?: number;
  I_phase_rms?: number;
  onPayload?: (p: FemPayload) => void;
  /**
   * If provided, the chart skips its own /fem_field2d fetch and renders the
   * supplied payload instead.  Used by the FemAnimationViewer to feed
   * per-step frames into the same rendering pipeline (mesh + iso lines +
   * |B| / Demag modes all work transparently with frame data).
   */
  payloadOverride?: FemPayload | null;
  /** Optional extra info line under the header (e.g. "Step 5 / 12  ·  rotor 6.4°"). */
  subHeader?: string;
  /** Hide the "Re-solve" button — useful when an external playback widget
   *  is in charge of (re)fetching frames. */
  hideRefresh?: boolean;
}

const FemFieldChart: React.FC<Props> = ({ gamma_deg = 0, rotor_angle_deg = 0,
                                          I_phase_rms, onPayload,
                                          payloadOverride, subHeader,
                                          hideRefresh }) => {
  const [fetchedPayload, setPayload] = useState<FemPayload | null>(null);
  const payload = payloadOverride ?? fetchedPayload;   // override wins
  const [loading, setLoading] = useState<boolean>(false);
  const [error,   setError]   = useState<string | null>(null);
  const [mode,    setMode]    = useState<FieldMode>('Az');
  const controlsRef = useRef<any>(null);

  const fetchFem = () => {
    if (payloadOverride) return;   // parent owns the data
    setLoading(true); setError(null);
    const comp = JSON.stringify(readMeshSetting<Record<string, number>>('componentMesh', {}));
    // Transient-only policy: the separate "Eddy" static solve was retired —
    // the magnetostatic field view is just the per-frame field the transient
    // sweeps; losses/torque come from the sliding-band transient.
    const base = `${API}/api/simulation/physics/fem_field2d`;
    const params: Record<string, string> = {
      rotor_angle_deg:   String(rotor_angle_deg),
      gamma_deg:         String(gamma_deg),
      mesh_size_mm:      String(readMeshSetting('meshSize',    4.0)),
      min_size_mm:       String(readMeshSetting('minSize',     0.3)),
      outer_air_factor:  String(readMeshSetting('outerAir',    1.3)),
      motion_band:       String(readMeshSetting('motionBand',  true)),
      band_thickness_mm: String(readMeshSetting('bandThickness', 0.4)),
      n_sectors:         String(readMeshSetting('nSectors',    4)),
      stator_fillet_mm:  '0',   // native geometry — extra smoothing removed
      component_mesh:    comp,
    };
    if (I_phase_rms !== undefined) {
      params.I_phase_rms = String(I_phase_rms);
    }
    const qs = new URLSearchParams(params).toString();
    fetch(`${base}?${qs}`)
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        return r.json();
      })
      .then((d: FemPayload) => {
        setPayload(d); setLoading(false);
        if (onPayload) onPayload(d);
      })
      .catch(e => { setError(String(e)); setLoading(false); });
  };

  useEffect(() => {
    if (payloadOverride) {
      // External owner — drop loading flag and forward upstream
      setLoading(false); setError(null);
      if (onPayload) onPayload(payloadOverride);
      return;
    }
    fetchFem();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gamma_deg, rotor_angle_deg, I_phase_rms, payloadOverride]);

  return (
    <Paper sx={{ bgcolor: '#0b1220', border: '1px solid #1e293b', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Box>
          <Typography sx={{ fontSize: 13, color: '#cbd5e1', fontWeight: 700 }}>
            Magnetic potential A<sub>z</sub> — real scikit-fem solve
            <Tooltip title="2-D magnetostatic field at the current rotor angle — the same per-frame field the sliding-band transient sweeps. Torque + losses are ×n_sectors for the full motor." placement="top">
              <span style={{ color: '#475569', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </Typography>
          <Typography sx={{ fontSize: 10, color: '#475569' }}>
            {payload
              ? (subHeader
                   ? subHeader
                   : `${payload.n_triangles.toLocaleString()} triangles · ×${payload.symmetry_mult} symmetry · solve ${payload.solve_time_s}s`)
              : 'Solving…'}
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <ToggleButtonGroup value={mode} exclusive size="small"
            onChange={(_, v) => v && setMode(v as FieldMode)}
            sx={{
              '& .MuiToggleButton-root': { py: 0.2, px: 1.2, fontSize: 11,
                color: '#64748b', borderColor: '#1e293b', textTransform: 'none',
                '&.Mui-selected': { color: '#e2e8f0', bgcolor: '#1e3a5f',
                  borderColor: '#3b82f6' }}}}>
            <ToggleButton value="Az">A<sub>z</sub></ToggleButton>
            <ToggleButton value="Bmag">|B|</ToggleButton>
            <ToggleButton value="J">J</ToggleButton>
            <ToggleButton value="Demag">Demag</ToggleButton>
          </ToggleButtonGroup>
          {!hideRefresh && (
            <Button size="small" startIcon={<RefreshIcon fontSize="small"/>}
              onClick={fetchFem} disabled={loading}
              sx={{ color: '#93c5fd', fontSize: 11, textTransform: 'none' }}>
              Re-solve
            </Button>
          )}
        </Box>
      </Box>

      {error && (
        <Typography sx={{ fontSize: 11, color: '#fca5a5', p: 1,
          border: '1px solid #7f1d1d', borderRadius: 1 }}>
          {error}
        </Typography>
      )}

      <Box sx={{ display: 'grid',
        gridTemplateColumns: 'minmax(0, 1fr) auto',
        gap: 1, height: 460 }}>
        {/* Canvas */}
        <Box sx={{ position: 'relative', border: '1px solid #0f172a',
          bgcolor: '#060d17', minHeight: 460 }}>
          {loading && (
            <Box sx={{ position: 'absolute', inset: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              bgcolor: 'rgba(6,13,23,0.7)', zIndex: 5 }}>
              <CircularProgress size={32}/>
            </Box>
          )}
          {payload && (
            <Canvas style={{ background: '#060d17' }}>
              <OrthographicCamera makeDefault position={[0, 0, 300]}
                near={0.1} far={5000}/>
              <FitView payload={payload} controlsRef={controlsRef}/>
              <ambientLight intensity={1}/>
              <FieldMesh payload={payload} mode={mode}/>
              <OrbitControls ref={controlsRef} enableDamping={false}
                enableRotate enablePan enableZoom zoomSpeed={1.2}/>
              {/* Drive + follow the overlay Viewcube (same as Geometry). */}
              <CameraSync controlsRef={controlsRef}/>
              <ViewcubeNavigation controlsRef={controlsRef}/>
            </Canvas>
          )}
          {/* Orientation cube + XYZ axes — same component as Geometry */}
          {payload && <Viewcube/>}
        </Box>

        {/* Colour bar — recompute the same 2/98 percentile-based vmin/vmax
            the fill geometry uses, so the bar labels line up with the
            actual rendered colours. */}
        {payload && (() => {
          const DOM_OUTER = 8;
          const dom  = payload.domain_per_tri;
          const tris = payload.triangles;
          const pct = (arr: number[], p: number) => {
            if (!arr.length) return 0;
            const a = Float64Array.from(arr).sort();
            const i = Math.max(0, Math.min(a.length - 1,
              Math.floor((p / 100) * (a.length - 1))));
            return a[i];
          };
          if (mode === 'Bmag') {
            const Bs: number[] = [];
            for (let ti = 0; ti < tris.length; ti++) {
              if (dom[ti] === DOM_OUTER) continue;
              Bs.push(payload.Bmag_per_tri[ti]);
            }
            const vmax = Math.min(Math.max(pct(Bs, 95), 0.05), 1.8);
            return <ColorBar vmin={0} vmax={vmax} unit="T"
              lut={(t) => jet01(t)}/>;
          }
          if (mode === 'Demag') {
            return <ColorBar vmin={0} vmax={100} unit="Demag %"
              lut={(t) => jetBands(t, 11)}/>;
          }
          if (mode === 'J') {
            // Signed J_z bar — find vmax across coil triangles only.
            const DOM_COIL = 2;
            const jz = payload.J_z_per_tri ?? [];
            const Js: number[] = [];
            for (let ti = 0; ti < tris.length; ti++) {
              if (dom[ti] !== DOM_COIL) continue;
              if (ti < jz.length) Js.push(Math.abs(jz[ti]));
            }
            const vmax = pct(Js, 99) || 1;
            // Display in A/mm² for readability (matches Ansys legend scale).
            return <ColorBar vmin={-vmax} vmax={+vmax}
              unit="A/m²"
              lut={(t) => jetSigned(2 * t - 1)}/>;
          }
          // A_z mode — 2/98 percentile of interior node values, symmetrised.
          const used = new Set<number>();
          for (let ti = 0; ti < tris.length; ti++) {
            if (dom[ti] === DOM_OUTER) continue;
            for (const vi of tris[ti]) used.add(vi);
          }
          const As: number[] = [];
          used.forEach(vi => As.push(payload.A_z_per_node[vi]));
          const lo = pct(As, 2);
          const hi = pct(As, 98);
          const amax = Math.max(Math.abs(lo), Math.abs(hi), 1e-12);
          return (
            <ColorBar vmin={-amax} vmax={+amax} unit="Wb/m"
              lut={(t) => jetBands(t, N_BANDS)}/>
          );
        })()}

      </Box>

      {/* Solver diagnostics strip — only the mesh/field numerics that are
          NOT already in the top summary table.  Sits BELOW the full-width
          field chart as a compact horizontal row. */}
      {payload && (
        <Box sx={{ display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 1, mt: 1 }}>
          {[
            { label: 'Mesh vertices',  value: payload.n_vertices.toLocaleString() },
            { label: 'Mesh triangles', value: payload.n_triangles.toLocaleString() },
            { label: '|B|_max',        value: `${payload.B_mag_max.toFixed(2)} T` },
            { label: 'A_z range',      value: `[${(payload.A_z_min*1000).toFixed(2)}, ${(payload.A_z_max*1000).toFixed(2)}] mWb/m` },
          ].map(s => (
            <Box key={s.label} sx={{ p: 1, bgcolor: '#060d17',
              border: '1px solid #0f172a', borderRadius: 1 }}>
              <Typography sx={{ fontSize: 9, color: '#475569',
                textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {s.label}
              </Typography>
              <Typography sx={{ fontSize: 13, fontWeight: 700, color: '#cbd5e1' }}>
                {s.value}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      <Typography sx={{ fontSize: 9, color: '#334155', mt: 0.5 }}>
        Same mesh + Solver-Domain settings as the Mesh tab (read from
        localStorage). Sector mode uses anti-periodic Dirichlet BC on the
        radial cuts so torque, |B| and flux linkages are physically correct
        and multiplied by n_sectors to represent the full motor.
      </Typography>
      {payload && payload.demag_report && payload.demag_report.length > 0 && (
        <Box sx={{ mt: 0.5, p: 0.75, border: '1px solid', borderRadius: 1,
          borderColor: payload.demag_report.some(r => r.demagnetised)
            ? '#dc2626' : '#fbbf24',
          bgcolor: '#0f0a05' }}>
          <Typography sx={{ fontSize: 10, fontWeight: 700,
            color: payload.demag_report.some(r => r.demagnetised)
              ? '#fca5a5' : '#fde68a', mb: 0.5 }}>
            {payload.demag_report.some(r => r.demagnetised)
              ? '⛔ MAGNET DEMAGNETISATION'
              : '⚠ Magnets approaching demag knee'}
          </Typography>
          {payload.demag_report.map((r, i) => (
            <Typography key={i} sx={{ fontSize: 9, color: '#fcd34d',
              fontFamily: 'monospace' }}>
              mag[{r.magnet_index}]: H_min = {r.H_min_kA_per_m} kA/m
              (knee {r.H_knee_kA_per_m} kA/m, {(r.knee_proximity*100).toFixed(0)}%)
              {r.demagnetised && '  → IRREVERSIBLE LOSS'}
            </Typography>
          ))}
        </Box>
      )}
    </Paper>
  );
};

export default FemFieldChart;
