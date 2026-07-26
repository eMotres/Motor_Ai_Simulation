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
  ToggleButton, ToggleButtonGroup, TextField, Select, MenuItem,
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
import { tileFullRing } from './fem-types';
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

// Discrete-banded rainbow for the LEGEND (and anything drawn on the CPU).
// The field fill itself bands in the fragment shader below — see BAND_FRAG.
function bandLut(t: number, n: number): [number, number, number] {
  return jet01((Math.floor(Math.max(0, Math.min(1, t)) * n) + 0.5) / n);
}

// Ansys-style banded fill.  The band edges must be evaluated PER PIXEL from the
// interpolated SCALAR — quantising at the vertices and letting the GPU blend the
// resulting RGB mixes neighbouring band colours and turns the field into
// blotches.  So the shader carries the scalar through and quantises in the
// fragment stage, which puts every band edge exactly on an iso-line.
const BAND_VERT = `
  attribute float aVal;
  varying float vVal;
  void main() {
    vVal = aVal;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const BAND_FRAG = `
  uniform float uBands;          // 0 = continuous
  varying float vVal;
  vec3 jet(float x) {
    x = clamp(x, 0.0, 1.0);
    return vec3(clamp(1.5 - abs(4.0 * x - 3.0), 0.0, 1.0),
                clamp(1.5 - abs(4.0 * x - 2.0), 0.0, 1.0),
                clamp(1.5 - abs(4.0 * x - 1.0), 0.0, 1.0));
  }
  void main() {
    float t = clamp(vVal, 0.0, 1.0);
    if (uBands > 0.5) t = (floor(t * uBands) + 0.5) / uBands;   // band centre
    gl_FragColor = vec4(jet(t), 1.0);
  }
`;
// Bands per mode — mirrors what the legend draws.
const MODE_BANDS: Record<string, number> = { Az: 20, Bmag: 13, Demag: 11 };

/**
 * Colour range for the Demag map: [worst, best] remaining Br over the magnet
 * triangles only. Auto-ranged at BOTH ends — with the top pinned at 100 % a
 * magnet that sits between 15 % and 31 % gets the bottom sixth of the palette
 * and reads as one flat colour. Shared by the fill and the legend so the two
 * cannot drift apart. Degenerate spans are widened around their centre.
 */
function demagRange(
  dc: number[] | undefined, dom: number[] | Int32Array,
  nTri: number, magN: number, magS: number,
): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < nTri; i++) {
    if (dom[i] !== magN && dom[i] !== magS) continue;
    const cc = dc ? Math.max(0, Math.min(1, dc[i])) : 1;
    if (cc < lo) lo = cc;
    if (cc > hi) hi = cc;
  }
  if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
  if (hi - lo < 0.02) {                       // uniform map: widen, stay in [0,1]
    const mid = 0.5 * (lo + hi);
    lo = Math.max(0, mid - 0.01);
    hi = Math.min(1, Math.max(lo + 0.02, mid + 0.01));
  }
  return [lo, hi];
}
// ── helpers: read mesh params persisted by MeshPanel ──────────────────────
function readMeshSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}
// Simulation-tab settings live under `sim.*` (single source the Simulation panel
// writes).  The thermal solve reads rpm from here so the air-gap conductivity
// tracks the operating speed you set in Simulation.
function readSimSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`sim.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

// ── R3F mesh component ────────────────────────────────────────────────────
type FieldMode = 'Az' | 'Bmag' | 'J' | 'Jeddy' | 'Loss' | 'Demag' | 'Temp';
// Modes powered by the (slow) time-coupled eddy-current transient rather than
// the fast magnetostatic snapshot: J⟳ (real current crowding / proximity) and
// Loss (Ansys-style W/m³ density map).  Selecting either lazily runs the eddy
// solve (~25 s) and caches its last-frame field + cycle-averaged loss density.
// Only J⟳ (eddy-current crowding) needs the slow time-coupled solve.  Loss now
// comes from the single magnetostatic frame (analytic loss density) like |B|.
const EDDY_MODES = new Set<FieldMode>(['Jeddy']);

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

const FieldMesh: React.FC<{ payload: FemPayload; mode: FieldMode; logLoss: boolean; eqTemp?: boolean; showFlux?: boolean }>
  = ({ payload, mode, logLoss, eqTemp = true, showFlux }) => {
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

    if (mode === 'Temp') {
      // Temperature field [°C] — nodal colour on the thermal SOLID sub-mesh
      // (this payload carries its own vertices/triangles; outer air + gap are
      // already removed, so render EVERY triangle).  Ansys-style rainbow
      // (blue→red).  A motor under steady cooling is a tight hot PLATEAU — most
      // of the structure sits in a few °C — so a linear scale maps it all to one
      // colour.  Default = histogram-EQUALISED: colour by each node's RANK among
      // all nodes, so the full blue→red spectrum lands on the real distribution
      // (the vivid Fusion look).  eqTemp=false → faithful linear scale.
      const Tn = payload.temperature_per_node ?? [];
      const tmin = payload.T_min ?? (Tn.length ? Math.min(...Tn) : 0);
      const tmax = payload.T_max ?? (Tn.length ? Math.max(...Tn) : 1);
      const rng = Math.max(tmax - tmin, 1e-6);
      // rank fraction per node (histogram equalisation)
      const N = Tn.length;
      const rankFrac = new Float64Array(N);
      if (eqTemp && N > 1) {
        const order = Array.from({ length: N }, (_, i) => i).sort((a, b) => (Tn[a] - Tn[b]));
        for (let r = 0; r < N; r++) rankFrac[order[r]] = r / (N - 1);
      }
      const positions = new Float32Array(vertices.length * 3);
      const colors = new Float32Array(vertices.length * 3);
      for (let i = 0; i < vertices.length; i++) {
        positions[3 * i] = vertices[i][0] * S;
        positions[3 * i + 1] = vertices[i][1] * S;
        positions[3 * i + 2] = 0;
        const t = eqTemp ? rankFrac[i] : (((Tn[i] ?? tmin) - tmin) / rng);
        const [r, g2, b] = jet01(t);
        colors[3 * i] = r / 255; colors[3 * i + 1] = g2 / 255; colors[3 * i + 2] = b / 255;
      }
      const indexArr: number[] = [];
      for (let i = 0; i < triangles.length; i++)
        indexArr.push(triangles[i][0], triangles[i][1], triangles[i][2]);
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      g.setIndex(new THREE.BufferAttribute(new Uint32Array(indexArr), 1));
      (g as any).userData = { T_lo: tmin, T_hi: tmax };
      return g;
    }

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
      const vals      = new Float32Array(vertices.length);   // scalar for the shader
      for (let i = 0; i < vertices.length; i++) {
        positions[3 * i]     = vertices[i][0] * S;
        positions[3 * i + 1] = vertices[i][1] * S;
        positions[3 * i + 2] = 0;
        // map A_z → [-1, 1] via signed log, then to [0, 1] for the colour map.
        // CONTINUOUS: these are VERTEX colours that the GPU interpolates across
        // each triangle, so quantising here banded the nodes and then blended the
        // bands — neither clean iso-bands nor a smooth field, just blotches.  The
        // iso-lines are drawn separately and still mark the levels.
        const tc = compress(A_z_per_node[i]);
        const t  = 0.5 + 0.5 * tc;
        vals[i] = Math.max(0, Math.min(1, t));
        const [r, g, b] = jet01(vals[i]);
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
      g.setAttribute('aVal',     new THREE.BufferAttribute(vals, 1));
      g.setIndex(new THREE.BufferAttribute(new Uint32Array(indexArr), 1));
      // Stash the symmetric percentile-clipped range so isoGeo and the
      // colour bar can re-use the EXACT same scale.
      (g as any).userData = { Az_lo: lo, Az_hi: hi };
      return g;
    }
    // NOTE: lo/hi defined here is referenced by isoGeo + colour bar via
    // fillGeo.userData (see below).

    if (mode === 'Loss') {
      // Ansys-style "Total Loss" density [W/m³]: a flat per-triangle rainbow
      // fill over every non-air element, coloured by the cycle-averaged loss
      // density (iron Bertotti + copper DC+proximity + magnet eddy).  The
      // density spans orders of magnitude (copper ≫ iron ≫ air-gap), so the
      // default is a LOG map (logLoss) — every component stays legible; a
      // linear map matches Ansys's default look but buries the iron.
      // SMOOTH, nodal-averaged loss density (like Ansys) — the same per-vertex
      // gradient trick the |B| view uses.  loss is constant per P1 element, so a
      // flat per-triangle fill looks faceted; we area-weight each element's loss
      // onto its 3 nodes and colour PER-VERTEX so Three.js interpolates a smooth
      // gradient.  (Minor softening across material edges; the part outlines
      // drawn on top keep the boundaries crisp.)
      const ld = payload.loss_density_per_tri ?? [];
      const dom = domain_per_tri;
      const nTri = triangles.length;
      const nV = vertices.length;
      const lSum = new Float64Array(nV);
      const wSum = new Float64Array(nV);
      const posv: number[] = [];
      for (let ti = 0; ti < nTri; ti++) {
        if (dom[ti] === DOM_OUTER) continue;
        const v = ti < ld.length ? ld[ti] : 0;
        if (v > 0) posv.push(v);
        const [ia, ib, ic] = triangles[ti];
        const ax = vertices[ia][0], ay = vertices[ia][1];
        const bx = vertices[ib][0], by = vertices[ib][1];
        const cx = vertices[ic][0], cy = vertices[ic][1];
        const area = Math.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) * 0.5 || 1e-12;
        lSum[ia] += v * area; wSum[ia] += area;
        lSum[ib] += v * area; wSum[ib] += area;
        lSum[ic] += v * area; wSum[ic] += area;
      }
      const vmax = Math.max(pctl(posv, 99.5), 1e-3);
      const vminPos = Math.max(pctl(posv, 5), vmax * 1e-4);   // log-floor
      const lgMax = Math.log10(vmax), lgMin = Math.log10(vminPos);
      const keep = triangles.map((_, ti) => dom[ti] !== DOM_OUTER);
      const positions = new Float32Array(nV * 3);
      const colors    = new Float32Array(nV * 3);
      for (let i = 0; i < nV; i++) {
        positions[3 * i]     = vertices[i][0] * S;
        positions[3 * i + 1] = vertices[i][1] * S;
        positions[3 * i + 2] = 0;
        const v = wSum[i] > 0 ? lSum[i] / wSum[i] : 0;
        let t: number;
        if (logLoss) {
          t = v <= 0 ? 0 : (Math.log10(v) - lgMin) / Math.max(lgMax - lgMin, 1e-9);
        } else {
          t = v / vmax;
        }
        const [rr, gg, bb] = jet01(Math.max(0, Math.min(1, t)));
        colors[3 * i]     = rr / 255;
        colors[3 * i + 1] = gg / 255;
        colors[3 * i + 2] = bb / 255;
      }
      const indexArr: number[] = [];
      for (let i = 0; i < nTri; i++) {
        if (!keep[i]) continue;
        indexArr.push(triangles[i][0], triangles[i][1], triangles[i][2]);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      g.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
      g.setIndex(new THREE.BufferAttribute(new Uint32Array(indexArr), 1));
      (g as any).userData = { loss_vmax: vmax, loss_vmin: vminPos };
      return g;
    }

    if (mode === 'J' || mode === 'Jeddy') {
      // J_z mode — Ansys "J [A/m²]" style.  Coil triangles get a diverging
      // blue→green→red colormap based on signed J_z, scaled to the 99-pct
      // absolute value across all coil cells in the current frame.  Iron
      // / magnet / air cells are not coloured.  In 'Jeddy' (eddy-solve) mode
      // the J is the REAL current density σ(−∂A/∂t+U) — it crowds towards the
      // slot opening (proximity effect); the magnetostatic 'J' is uniform.
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
      // Demag map, ANSYS-style: show ONLY magnet triangles, coloured by the % of
      // Br REMAINING  = Br_factor·100.  100 % = full strength (the magnet body) →
      // RED (top of the legend); the demagnetised corners fall to the low end →
      // BLUE.  The legend AUTO-RANGES to the real span [min remaining … 100 %]
      // instead of a fixed 0-100, so a few-% demag is actually visible.
      const dc = (payload as any).demag_coef_per_tri as number[] | undefined;
      const dom = domain_per_tri;
      const DOM_MAG_N = 4, DOM_MAG_S = 44;
      const nTri = triangles.length;
      // Auto-range BOTH ends to the real span [worst … best remaining].  Pinning
      // the top at 100 % wastes the whole upper half of the palette whenever no
      // element is still at full strength — a magnet sitting between 15 % and
      // 31 % rendered as one flat blue.
      const [coefLo, coefHi] = demagRange(dc, dom, nTri, DOM_MAG_N, DOM_MAG_S);
      const coefSpan = coefHi - coefLo;
      // SMOOTH, nodal-averaged — same treatment as |B| below, and what Ansys
      // shows.  Br_factor is constant per element, so a flat per-triangle fill
      // renders a corner-localised loss as hard-edged blocks; averaging onto the
      // NODES (area-weighted) and colouring per-vertex lets the GPU interpolate
      // across each triangle, so the gradient into the demagnetised corner reads
      // the way the physics actually varies.
      const dSum = new Float64Array(vertices.length);
      const dW   = new Float64Array(vertices.length);
      for (let i = 0; i < nTri; i++) {
        if (dom[i] !== DOM_MAG_N && dom[i] !== DOM_MAG_S) continue;
        const [ia, ib, ic] = triangles[i];
        const ax = vertices[ia][0], ay = vertices[ia][1];
        const bx = vertices[ib][0], by = vertices[ib][1];
        const cx = vertices[ic][0], cy = vertices[ic][1];
        const area = Math.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) * 0.5 || 1e-12;
        const cc = dc ? Math.max(0, Math.min(1, dc[i])) : 1;
        dSum[ia] += cc * area; dW[ia] += area;
        dSum[ib] += cc * area; dW[ib] += area;
        dSum[ic] += cc * area; dW[ic] += area;
      }
      const positions = new Float32Array(nTri * 3 * 3);
      const colors    = new Float32Array(nTri * 3 * 3);
      const vals = new Float32Array(nTri * 3);             // scalar for the shader
      let p = 0, c = 0, q = 0;
      for (let i = 0; i < nTri; i++) {
        if (dom[i] !== DOM_MAG_N && dom[i] !== DOM_MAG_S) continue;
        for (const vi of triangles[i]) {
          const coefV = dW[vi] > 0 ? dSum[vi] / dW[vi] : 1;
          // top of the span = best remaining → red;  bottom = worst → blue.
          const tCol = Math.max(0, Math.min(1, (coefV - coefLo) / coefSpan));
          vals[q++] = tCol;
          const [rr, gg, bb] = jet01(tCol);   // continuous: the vertex colours are
                                              // what get interpolated, so banding
                                              // here would quantise the gradient
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
      g.setAttribute('aVal', new THREE.BufferAttribute(vals.subarray(0, q), 1));
      return g;
    }

    // |B| — SMOOTH, nodal-averaged jet (like Ansys).  B = ∇A_z is constant per
    // P1 triangle, so a flat per-triangle fill looks faceted ("scary").  Instead
    // we average each triangle's |B| onto its 3 nodes (AREA-WEIGHTED) and colour
    // PER-VERTEX, exactly like the A_z field — Three.js then interpolates the
    // colour smoothly across every triangle (blue→red, continuous — banding the
    // vertex colours quantised the nodes and then blended the bands, which read
    // as blotches rather than a field).
    // vmax = 99.5-percentile of the interior |B|, capped at 4 T — wide enough to
    // show the real air-gap/tooth-tip field (~3.3 T, like Ansys), while the
    // percentile keeps 1/4-sector sharp-corner spikes from squashing the LUT.
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
    const vmaxPct = pctl(interiorB, 99.5);
    const vmax = Math.min(Math.max(vmaxPct, 0.05), 4.0);

    const keep = triangles.map((_, ti) => domain_per_tri[ti] !== DOM_OUTER);
    const positions = new Float32Array(nV * 3);
    const colors    = new Float32Array(nV * 3);
    const vals      = new Float32Array(nV);                  // scalar for the shader
    for (let i = 0; i < nV; i++) {
      positions[3 * i]     = vertices[i][0] * S;
      positions[3 * i + 1] = vertices[i][1] * S;
      positions[3 * i + 2] = 0;
      const bnode = wSum[i] > 0 ? bSum[i] / wSum[i] : 0;
      const t = Math.min(1, Math.max(0, bnode / vmax));
      vals[i] = t;
      const [rr, gg, bb] = jet01(t);
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
    g.setAttribute('aVal',     new THREE.BufferAttribute(vals, 1));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(indexArr), 1));
    (g as any).userData = { Bmag_vmax: vmax };
    return g;
  }, [payload, mode, logLoss, eqTemp]);

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

  // Heat-flux arrows (Temp view) — q = -k∇T per element, drawn from each
  // sampled element centroid, length ∝ |q| (clamped).  Shows heat flowing from
  // the hot winding outward to the cooled housing.
  const fluxGeo = useMemo(() => {
    if (mode !== 'Temp' || !showFlux) return null;
    const flux = payload.heat_flux_per_tri ?? [];
    const fmag = payload.flux_mag_per_tri ?? [];
    if (!flux.length) return null;
    const S = 1000;
    const { vertices, triangles, extent } = payload;
    const pos = Float64Array.from(fmag.filter(v => v > 0)).sort();
    const qref = pos.length ? pos[Math.floor(0.9 * (pos.length - 1))] : 1;
    const span = Math.max(extent[1] - extent[0], extent[3] - extent[2]) * S;
    const Lmax = span * 0.035;
    const step = Math.max(1, Math.floor(triangles.length / 500));
    const arr: number[] = [];
    for (let ti = 0; ti < triangles.length; ti += step) {
      const f = flux[ti]; if (!f) continue;
      const m = fmag[ti] || Math.hypot(f[0], f[1]);
      if (m <= 1e-9) continue;
      const [a, b, c] = triangles[ti];
      const cx = (vertices[a][0] + vertices[b][0] + vertices[c][0]) / 3 * S;
      const cy = (vertices[a][1] + vertices[b][1] + vertices[c][1]) / 3 * S;
      const len = Math.min(1, m / qref) * Lmax;
      const ux = f[0] / m, uy = f[1] / m;
      const ex = cx + ux * len, ey = cy + uy * len;
      arr.push(cx, cy, 1.4, ex, ey, 1.4);                         // shaft
      const hb = len * 0.32;
      arr.push(ex, ey, 1.4, ex - hb * (ux + uy), ey - hb * (uy - ux), 1.4);  // barb
      arr.push(ex, ey, 1.4, ex - hb * (ux - uy), ey - hb * (uy + ux), 1.4);  // barb
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(arr), 3));
    return g;
  }, [payload, mode, showFlux]);

  return (
    <group>
      <mesh geometry={fillGeo}>
        {/* Modes that carry the raw scalar band it per-pixel (Ansys look); the
            rest keep the CPU-side vertex colours they already compute. */}
        {fillGeo?.getAttribute('aVal') ? (
          <shaderMaterial
            key={`band-${mode}`}
            side={THREE.DoubleSide}
            vertexShader={BAND_VERT}
            fragmentShader={BAND_FRAG}
            uniforms={{ uBands: { value: MODE_BANDS[mode] ?? 0 } }}
          />
        ) : (
          <meshBasicMaterial vertexColors side={THREE.DoubleSide}/>
        )}
      </mesh>
      {isoGeo && (
        <lineSegments geometry={isoGeo}>
          <lineBasicMaterial color={0x0b1220} transparent opacity={0.85}/>
        </lineSegments>
      )}
      {fluxGeo && (
        <lineSegments geometry={fluxGeo}>
          <lineBasicMaterial color={0xe2e8f0} transparent opacity={0.7}/>
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
  vmin: number; vmax: number; unit: string;
  lut: (t: number) => [number, number, number];
  log?: boolean;                     // log-spaced tick labels (fill is still 0..1 LUT)
  fmt?: (v: number) => string;       // custom number format (e.g. large W/m³)
  tickValues?: number[];             // explicit tick labels (bottom→top), e.g. quantiles for an equalised scale
}> = ({ vmin, vmax, unit, lut, log = false, fmt, tickValues }) => {
  const linTicks = useMemo(() => {
    const n = 5;
    if (log && vmin > 0 && vmax > 0) {
      const a = Math.log10(vmin), b = Math.log10(vmax);
      return Array.from({ length: n },
        (_, i) => Math.pow(10, a + (b - a) * (i / (n - 1))));
    }
    return Array.from({ length: n }, (_, i) => vmin + (vmax - vmin) * (i / (n - 1)));
  }, [vmin, vmax, log]);
  const ticks = (tickValues && tickValues.length) ? tickValues : linTicks;
  const f = fmt ?? ((v: number) => v.toFixed(2));
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
        border: '1px solid var(--line-soft)',
      }}/>
      <Box sx={{ display: 'flex', flexDirection: 'column-reverse',
        justifyContent: 'space-between', height: 200 }}>
        {ticks.map((t, i) => (
          <Typography key={i} sx={{ fontSize: 9, color: 'var(--text-2)',
            fontFamily: 'monospace', lineHeight: 1 }}>
            {f(t)} {unit}
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
    py: 0.4, borderBottom: '1px solid var(--app-bg)' }}>
    <Box>
      <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{label}</Typography>
      {sub && <Typography sx={{ fontSize: 9, color: 'var(--line)' }}>{sub}</Typography>}
    </Box>
    <Typography sx={{ fontSize: 11, color: 'var(--text-1)', fontFamily: 'monospace' }}>
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
  const [loading, setLoading] = useState<boolean>(false);
  const [error,   setError]   = useState<string | null>(null);
  const [mode,    setMode]    = useState<FieldMode>('Az');
  // Eddy-solve payload (J⟳ / Loss views) — separate from the fast magnetostatic
  // one, lazily fetched on first selection and re-fetched when γ / I change.
  const [eddyPayload, setEddyPayload] = useState<FemPayload | null>(null);
  const [eddyLoading, setEddyLoading] = useState<boolean>(false);
  const [eddyErr,     setEddyErr]     = useState<string | null>(null);
  const [logLoss,     setLogLoss]     = useState<boolean>(true);   // log W/m³ map
  const [eqTemp,      setEqTemp]      = useState<boolean>(true);   // histogram-equalised temp colours
  // Thermal (Temp view) — a steady-state conduction solve fed by the eddy
  // losses; lazily fetched, re-run when γ/I or the cooling inputs change.
  const [thermalPayload, setThermalPayload] = useState<FemPayload | null>(null);
  const [thermalLoading, setThermalLoading] = useState<boolean>(false);
  const [thermalErr,     setThermalErr]     = useState<string | null>(null);
  const [ambientT, setAmbientT] = useState<number>(40);     // °C — ambient air / coolant INLET temp
  const [coolMode, setCoolMode] = useState<'air' | 'liquid'>('air');
  const [airSpeed, setAirSpeed] = useState<number>(10);     // m/s — air blow speed (→ h)
  const [fluid,    setFluid]    = useState<string>('water'); // liquid coolant
  const [tOut,     setTOut]     = useState<number>(60);     // °C — liquid target OUTLET temp (= housing)
  const [coolOpen,  setCoolOpen]  = useState(false);   // cooling dropdown open
  const [coolHover, setCoolHover] = useState(false);   // cooling tooltip hover-intent
  const [showFlux, setShowFlux] = useState<boolean>(true);
  const isEddy = !payloadOverride && EDDY_MODES.has(mode);
  const isThermal = !payloadOverride && mode === 'Temp';
  const payload = payloadOverride
    ?? (isThermal ? thermalPayload : isEddy ? eddyPayload : fetchedPayload);
  const busy = isThermal ? thermalLoading : isEddy ? eddyLoading : loading;
  const errMsg = isThermal ? thermalErr : isEddy ? eddyErr : error;
  // The static solver always computes a demag map (a check at full Br), but if
  // the user has demag modelling OFF the map (all 0 % at no-load) is just
  // confusing — only offer the Demag view when demag is actually enabled.
  const demagOn = (() => {
    try { return JSON.parse(localStorage.getItem('sim.demag') || 'false') === true; }
    catch { return false; }
  })();
  useEffect(() => { if (!demagOn && mode === 'Demag') setMode('Az'); }, [demagOn, mode]);
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
      n_sectors:         String(readMeshSetting('nSectors',    1)),
      stator_fillet_mm:  '0',   // native geometry — extra smoothing removed
      component_mesh:    comp,
      // The field view now runs the sliding-band solver for one frame; pass the
      // demag flag so it computes the irreversible-demag %-map when modelling is on.
      demag:             String(demagOn),
      // Bit-identical pole/slot mesh (Mesh-tab toggle) — keep the field view
      // consistent with the mesh/sim the user is verifying.
      pole_copy:         String(readMeshSetting('poleCopy', false)),
      // SAME mesh pipeline as the transient (Mesh-tab toggles) — the field
      // view must show the exact mesh the simulation solves on.
      iron_template:     String(readMeshSetting('ironTemplate', true)),
      geo_mesh:          String(readMeshSetting('geoMesh', true)),
      structured_gap:    String(readMeshSetting('structuredGap', false) || readMeshSetting('ironTemplate', true)),
      airgap_macro:      String(readMeshSetting('harmonicGap', false)),
      gap_layers:        String(readMeshSetting('gapLayers', 2)),
    };
    if (I_phase_rms !== undefined) {
      params.I_phase_rms = String(I_phase_rms);
    }
    const qs = new URLSearchParams(params).toString();
    fetch(`${base}?${qs}`, { cache: 'no-store' })
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        return r.json();
      })
      .then((d: FemPayload) => {
        const full = tileFullRing(d);   // sector solve → full-ring display
        setPayload(full); setLoading(false);
        if (onPayload) onPayload(full);
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

  // Assigning a different magnet/steel changes the solve but NOT the URL, so this
  // view has no way to notice on its own — re-fetch on the same event a material
  // change fires.  Without it the map kept showing the previous material, and a
  // manual Re-solve only re-read the browser's cached response for that URL.
  useEffect(() => {
    const onApplied = () => { if (!payloadOverride) fetchFem(); };
    window.addEventListener('sim-design-applied', onApplied);
    return () => window.removeEventListener('sim-design-applied', onApplied);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payloadOverride, mode, gamma_deg, I_phase_rms]);

  // Use the ACTUAL operating-point current — no silent substitution (a hidden
  // fallback to 120 A made the no-load Loss view show full I²R copper loss, which
  // read as "copper loss at I=0").  At I=0 the map now honestly shows the no-load
  // losses: iron + magnet eddy + the small copper eddy/proximity the spinning
  // magnets induce in the windings — there is NO I²R.
  const eddyCurrent = (I_phase_rms !== undefined && I_phase_rms > 0) ? I_phase_rms : 0;
  const eddyNoLoad = !(eddyCurrent > 0);

  const fetchEddy = () => {
    if (payloadOverride) return;
    setEddyLoading(true); setEddyErr(null);
    const comp = JSON.stringify(readMeshSetting<Record<string, number>>('componentMesh', {}));
    const base = `${API}/api/simulation/physics/fem_eddy_field2d`;
    const qs = new URLSearchParams({
      gamma_deg:        String(gamma_deg),
      I_phase_rms:      String(eddyCurrent),
      // The Loss/J⟳ views run a full nonlinear transient; the loss-density MAP is a
      // VISUAL and does not need the dashboard's 24 frames.  10 frames / 1 period
      // (≥9 keeps the de-jitter savgol; still resolves the 6f loss harmonic) ≈ 2.4×
      // fewer nonlinear solves → ~2.4× faster, and the result caches so a repeat
      // view of the same operating point is instant.
      n_steps_per_period: '10',
      n_periods:          '1',
      mesh_size_mm:     String(readMeshSetting('meshSize', 4.0)),
      min_size_mm:      String(readMeshSetting('minSize',  0.3)),
      outer_air_factor: String(readMeshSetting('outerAir', 1.3)),
      n_sectors:        String(readMeshSetting('nSectors', 1)),
      component_mesh:   comp,
      pole_copy:        String(readMeshSetting('poleCopy', false)),
      // Loss only needs the reconstructed loss-density map (no coupled solve) → FAST.
      // The J⟳ current-crowding view needs the true time-coupled eddy currents.
      coupled:          String(mode === 'Jeddy'),
    }).toString();
    fetch(`${base}?${qs}`, { cache: 'no-store' })
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        return r.json();
      })
      .then((d: FemPayload) => { setEddyPayload(tileFullRing(d)); setEddyLoading(false); })
      .catch(e => { setEddyErr(String(e)); setEddyLoading(false); });
  };

  // γ / I changed → the cached eddy solve is stale (rotor angle does NOT
  // matter — the eddy run sweeps a whole period — so we don't invalidate on it).
  useEffect(() => {
    setEddyPayload(null); setEddyErr(null);
  }, [gamma_deg, I_phase_rms, mode]);   // Loss (fast) vs J⟳ (coupled) need different solves

  // Lazily run the (slow) eddy solve the first time a J⟳ / Loss view is shown.
  useEffect(() => {
    if (isEddy && !eddyPayload && !eddyLoading && !eddyErr) fetchEddy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEddy, eddyPayload, eddyLoading, eddyErr]);

  // ── Thermal (Temp view) ───────────────────────────────────────────────────
  const thermalCurrent = (I_phase_rms !== undefined && I_phase_rms > 0) ? I_phase_rms : 0;
  const fetchThermal = () => {
    if (payloadOverride) return;
    setThermalLoading(true); setThermalErr(null);
    const comp = JSON.stringify(readMeshSetting<Record<string, number>>('componentMesh', {}));
    const qs = new URLSearchParams({
      cooling_mode:     coolMode,
      ambient_temp:     String(ambientT),           // air ambient / coolant inlet
      air_speed_mps:    String(coolMode === 'air' ? airSpeed : 0),
      fluid:            fluid,
      fluid_temp_in_c:  String(ambientT),           // liquid inlet = T₀
      fluid_temp_out_c: String(tOut),               // liquid outlet (= housing); flow derived
      gamma_deg:        String(gamma_deg),
      I_phase_rms:      String(thermalCurrent),
      rpm:              String(readSimSetting('rpm', 0)),
      mesh_size_mm:     String(readMeshSetting('meshSize', 4.0)),
      min_size_mm:      String(readMeshSetting('minSize',  0.3)),
      outer_air_factor: String(readMeshSetting('outerAir', 1.3)),
      n_sectors:        String(readMeshSetting('nSectors', 1)),
      component_mesh:   comp,
    }).toString();
    fetch(`${API}/api/simulation/physics/thermal_field2d?${qs}`)
      .then(async r => { if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`); return r.json(); })
      .then((d: FemPayload) => { setThermalPayload(tileFullRing(d)); setThermalLoading(false); })
      .catch(e => { setThermalErr(String(e)); setThermalLoading(false); });
  };
  // γ / I / cooling changed → cached thermal solve is stale.
  useEffect(() => { setThermalPayload(null); setThermalErr(null); },
    [gamma_deg, I_phase_rms, ambientT, coolMode, airSpeed, fluid, tOut]);
  useEffect(() => {
    if (isThermal && !thermalPayload && !thermalLoading && !thermalErr) fetchThermal();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isThermal, thermalPayload, thermalLoading, thermalErr]);

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Box>
          <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
            {mode === 'Temp'
              ? <>Temperature — °C (steady-state thermal solve)</>
              : mode === 'Loss'
              ? <>Loss density — W/m³ (eddy-current transient)</>
              : mode === 'Jeddy'
                ? <>Current density J — eddy solve (proximity)</>
                : <>Magnetic potential A<sub>z</sub> — real scikit-fem solve</>}
            <Tooltip title={isEddy
              ? "Time-coupled eddy-current solve over a full electrical period. J⟳ shows the real current density σ(−∂A/∂t+U) crowding toward the slot mouth (proximity effect); Loss is the cycle-averaged dissipation density [W/m³] — iron Bertotti + copper DC+AC + magnet eddy, normalised so the map integrates to the reported component losses."
              : "2-D magnetostatic field at the current rotor angle — the same per-frame field the sliding-band transient sweeps. Torque + losses are ×n_sectors for the full motor."} placement="top">
              <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </Typography>
          <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>
            {payload
              ? (subHeader
                   ? subHeader
                   : `${payload.n_triangles.toLocaleString()} triangles · ×${payload.symmetry_mult} symmetry · solve ${payload.solve_time_s}s${isEddy ? ` · eddy @ ${eddyCurrent.toFixed(0)} A` : ''}`)
              : (isEddy ? 'Running eddy-current transient (~25 s)…' : 'Solving…')}
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <ToggleButtonGroup value={mode} exclusive size="small"
            onChange={(_, v) => v && setMode(v as FieldMode)}
            sx={{
              '& .MuiToggleButton-root': { py: 0.2, px: 1.2, fontSize: 11,
                color: 'var(--text-3)', borderColor: 'var(--panel)', textTransform: 'none',
                '&.Mui-selected': { color: 'var(--text-0)', bgcolor: 'var(--line-accent)',
                  borderColor: '#3b82f6' }}}}>
            <ToggleButton value="Az">A<sub>z</sub></ToggleButton>
            <ToggleButton value="Bmag">|B|</ToggleButton>
            <ToggleButton value="J">J</ToggleButton>
            {!payloadOverride && (
              <ToggleButton value="Jeddy"
                title="Eddy-solve current density — shows the proximity crowding the uniform 'J' view can't (slow, ~25 s)">
                J⟳
              </ToggleButton>
            )}
            {!payloadOverride && (
              <ToggleButton value="Loss"
                title="Ansys-style loss-density map [W/m³] (slow, ~25 s)">
                Loss
              </ToggleButton>
            )}
            {!payloadOverride && (
              <ToggleButton value="Temp"
                title="Steady-state temperature map — solves heat conduction from the EM losses (slow, ~25 s)">
                Temp
              </ToggleButton>
            )}
            {demagOn && <ToggleButton value="Demag">Demag</ToggleButton>}
          </ToggleButtonGroup>
          {mode === 'Loss' && (
            <Button size="small" onClick={() => setLogLoss(v => !v)}
              title="Toggle log / linear colour scale"
              sx={{ color: '#93c5fd', fontSize: 10, textTransform: 'none',
                minWidth: 0, px: 1, border: '1px solid var(--line-soft)' }}>
              {logLoss ? 'log' : 'lin'}
            </Button>
          )}
          {mode === 'Temp' && (
            <Button size="small" onClick={() => setEqTemp(v => !v)}
              title="Colour scale: Equalised spreads the full blue→red spectrum over the temperature distribution (vivid, non-linear); Linear is a true °C scale."
              sx={{ color: '#93c5fd', fontSize: 10, textTransform: 'none',
                minWidth: 0, px: 1, border: '1px solid var(--line-soft)' }}>
              {eqTemp ? 'equalised' : 'linear'}
            </Button>
          )}
          {isThermal && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
              {/* cooling mode: air (blow speed) | liquid (in/out temps) */}
              <Tooltip title="Cooling method at the housing"
                open={coolHover && !coolOpen}
                onOpen={() => setCoolHover(true)} onClose={() => setCoolHover(false)}>
                <Select size="small" value={coolMode}
                  onChange={(e) => setCoolMode(e.target.value as 'air' | 'liquid')}
                  open={coolOpen} onOpen={() => setCoolOpen(true)} onClose={() => setCoolOpen(false)}
                  sx={{ fontSize: 11, '& .MuiSelect-select': { py: 0.5 } }}>
                  <MenuItem sx={{ fontSize: 11 }} value="air">Air cooling</MenuItem>
                  <MenuItem sx={{ fontSize: 11 }} value="liquid">Liquid cooling</MenuItem>
                </Select>
              </Tooltip>

              {coolMode === 'air' ? (
                <>
                  <Tooltip title="Ambient air temperature (°C)">
                    <TextField size="small" type="number" label="Air °C" value={ambientT}
                      onChange={(e) => setAmbientT(Number(e.target.value))}
                      sx={{ width: 76, '& .MuiInputBase-input': { fontSize: 11, py: 0.5 },
                        '& .MuiInputLabel-root': { fontSize: 11 } }} />
                  </Tooltip>
                  <Tooltip title="Blow speed over the housing → convection h (Churchill–Bernstein). 0 = still air (natural).">
                    <Select size="small" value={airSpeed} onChange={(e) => setAirSpeed(Number(e.target.value))}
                      sx={{ fontSize: 11, '& .MuiSelect-select': { py: 0.5 } }}>
                      <MenuItem sx={{ fontSize: 11 }} value={0}>Still air (0 m/s)</MenuItem>
                      <MenuItem sx={{ fontSize: 11 }} value={2}>2 m/s</MenuItem>
                      <MenuItem sx={{ fontSize: 11 }} value={5}>5 m/s</MenuItem>
                      <MenuItem sx={{ fontSize: 11 }} value={10}>10 m/s</MenuItem>
                      <MenuItem sx={{ fontSize: 11 }} value={20}>20 m/s</MenuItem>
                      <MenuItem sx={{ fontSize: 11 }} value={30}>30 m/s</MenuItem>
                    </Select>
                  </Tooltip>
                </>
              ) : (
                <>
                  <Tooltip title="Coolant">
                    <Select size="small" value={fluid} onChange={(e) => setFluid(String(e.target.value))}
                      sx={{ fontSize: 11, '& .MuiSelect-select': { py: 0.5 } }}>
                      <MenuItem sx={{ fontSize: 11 }} value="water">Water</MenuItem>
                      <MenuItem sx={{ fontSize: 11 }} value="water_glycol_50">Glycol 50%</MenuItem>
                      <MenuItem sx={{ fontSize: 11 }} value="ethylene_glycol">Ethylene glycol</MenuItem>
                      <MenuItem sx={{ fontSize: 11 }} value="oil">Oil</MenuItem>
                    </Select>
                  </Tooltip>
                  <Tooltip title="Coolant inlet temperature (°C)">
                    <TextField size="small" type="number" label="In °C" value={ambientT}
                      onChange={(e) => setAmbientT(Number(e.target.value))}
                      sx={{ width: 70, '& .MuiInputBase-input': { fontSize: 11, py: 0.5 },
                        '& .MuiInputLabel-root': { fontSize: 11 } }} />
                  </Tooltip>
                  <Tooltip title="Coolant outlet temperature (°C) — the housing is held at this temp; the flow rate is computed from inlet↔outlet ΔT.">
                    <TextField size="small" type="number" label="Out °C" value={tOut}
                      onChange={(e) => setTOut(Number(e.target.value))}
                      sx={{ width: 70, '& .MuiInputBase-input': { fontSize: 11, py: 0.5 },
                        '& .MuiInputLabel-root': { fontSize: 11 } }} />
                  </Tooltip>
                  {(payload as any)?.cooling?.flow_lpm != null && (() => {
                    const lpm = Number((payload as any).cooling.flow_lpm);
                    const txt = lpm >= 1 ? `${lpm.toFixed(2)} L/min` : `${(lpm * 1000).toFixed(0)} mL/min`;
                    return (
                      <Tooltip title="Flow rate required to carry the losses with the chosen inlet↔outlet ΔT (computed automatically). Small losses need very little flow.">
                        <Typography sx={{ fontSize: 11, color: '#7dd3fc', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
                          → {txt}
                        </Typography>
                      </Tooltip>
                    );
                  })()}
                </>
              )}

              <Button size="small" onClick={() => setShowFlux(v => !v)}
                title="Toggle heat-flux arrows"
                sx={{ color: showFlux ? 'var(--text-0)' : 'var(--text-3)', fontSize: 10, textTransform: 'none',
                  minWidth: 0, px: 1, border: '1px solid var(--line-soft)' }}>
                flux
              </Button>
            </Box>
          )}
          {!hideRefresh && (
            <Button size="small" startIcon={<RefreshIcon fontSize="small"/>}
              onClick={isThermal ? fetchThermal : isEddy ? fetchEddy : fetchFem}
              disabled={busy}
              sx={{ color: '#93c5fd', fontSize: 11, textTransform: 'none' }}>
              Re-solve
            </Button>
          )}
        </Box>
      </Box>

      {errMsg && (
        <Typography sx={{ fontSize: 11, color: '#fca5a5', p: 1,
          border: '1px solid #7f1d1d', borderRadius: 1 }}>
          {errMsg}
        </Typography>
      )}

      <Box sx={{ display: 'grid',
        gridTemplateColumns: 'minmax(0, 1fr) auto',
        gap: 1, height: 460 }}>
        {/* Canvas */}
        <Box sx={{ position: 'relative', border: '1px solid var(--app-bg)',
          bgcolor: 'var(--panel-2)', minHeight: 460 }}>
          {busy && (
            <Box sx={{ position: 'absolute', inset: 0, flexDirection: 'column',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              bgcolor: 'rgba(6,13,23,0.7)', zIndex: 5, gap: 1 }}>
              <CircularProgress size={32}/>
              {(isEddy || isThermal) && (
                <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
                  {isThermal
                    ? 'Running thermal solve (EM losses + conduction, ~25 s)…'
                    : 'Running eddy-current transient (~25 s)…'}
                </Typography>
              )}
            </Box>
          )}
          {payload && (
            <Canvas style={{ background: 'var(--panel-2)' }}>
              <OrthographicCamera makeDefault position={[0, 0, 300]}
                near={0.1} far={5000}/>
              <FitView payload={payload} controlsRef={controlsRef}/>
              <ambientLight intensity={1}/>
              <FieldMesh payload={payload} mode={mode} logLoss={logLoss} eqTemp={eqTemp} showFlux={showFlux}/>
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
          if (mode === 'Temp') {
            // Equalised scale → label the bar at the temperature QUANTILES (the
            // °C at each 25% of the colour range), so a colour still reads as a
            // real temperature even though the spacing is non-linear.
            const Ts = (payload.temperature_per_node ?? []).slice().sort((a, b) => a - b);
            const q = (f: number) => Ts.length ? Ts[Math.min(Ts.length - 1, Math.round(f * (Ts.length - 1)))] : 0;
            const qticks = Ts.length ? [q(0), q(0.25), q(0.5), q(0.75), q(1)] : undefined;
            return <ColorBar vmin={payload.T_min ?? 0} vmax={payload.T_max ?? 1}
              unit="°C" lut={(t) => jet01(t)} fmt={(v) => v.toFixed(0)}
              tickValues={eqTemp ? qticks : undefined}/>;
          }
          if (mode === 'Bmag') {
            const Bs: number[] = [];
            for (let ti = 0; ti < tris.length; ti++) {
              if (dom[ti] === DOM_OUTER) continue;
              Bs.push(payload.Bmag_per_tri[ti]);
            }
            const vmax = Math.min(Math.max(pct(Bs, 99.5), 0.05), 4.0);
            return <ColorBar vmin={0} vmax={vmax * 1000} unit="mT"
              fmt={(v) => v.toFixed(0)} lut={(t) => bandLut(t, MODE_BANDS.Bmag)}/>;
          }
          if (mode === 'Demag') {
            // % of Br REMAINING, auto-ranged at BOTH ends to the real span: the
            // best magnet element sets the top → red, the worst the bottom →
            // blue.  Must use the SAME range helper as the fill or the legend
            // stops describing the colours.
            const dc = (payload as any).demag_coef_per_tri as number[] | undefined;
            const [lo, hi] = demagRange(dc, dom, tris.length, 4, 44);
            return <ColorBar vmin={lo * 100} vmax={hi * 100} unit="% Br"
              fmt={(v) => v.toFixed(1)} lut={(t) => bandLut(t, MODE_BANDS.Demag)}/>;
          }
          if (mode === 'Loss') {
            // Loss-density bar [W/m³] — log or linear to match the fill.
            const ld = payload.loss_density_per_tri ?? [];
            const posv: number[] = [];
            for (let ti = 0; ti < tris.length; ti++) {
              if (dom[ti] === DOM_OUTER) continue;
              const v = ti < ld.length ? ld[ti] : 0;
              if (v > 0) posv.push(v);
            }
            const vmax = Math.max(pct(posv, 99.5), 1e-3);
            const vminPos = Math.max(pct(posv, 5), vmax * 1e-4);
            const fmtWm3 = (v: number) =>
              v >= 1e6 ? `${(v / 1e6).toFixed(1)}M`
              : v >= 1e3 ? `${(v / 1e3).toFixed(0)}k`
              : v.toFixed(0);
            return <ColorBar vmin={logLoss ? vminPos : 0} vmax={vmax}
              unit="W/m³" log={logLoss} fmt={fmtWm3} lut={(t) => jet01(t)}/>;
          }
          if (mode === 'J' || mode === 'Jeddy') {
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
              lut={(t) => bandLut(t, MODE_BANDS.Az)}/>
          );
        })()}

      </Box>

      {/* Solver diagnostics strip — only the mesh/field numerics that are
          NOT already in the top summary table.  Sits BELOW the full-width
          field chart as a compact horizontal row. */}
      {payload && (
        <Box sx={{ display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 1, mt: 1 }}>
          {(isThermal
            ? [
                { label: 'Winding T_max', value: payload.components?.winding ? `${payload.components.winding.max} °C` : '—' },
                { label: 'Magnet T_max',  value: payload.components?.magnet ? `${payload.components.magnet.max} °C` : '—' },
                { label: 'Stator T_max',  value: payload.components?.stator ? `${payload.components.stator.max} °C` : '—' },
                { label: 'Hot-spot',      value: `${(payload.T_max ?? 0).toFixed(0)} °C` },
              ]
            : isEddy
            ? [
                { label: 'Copper loss', value: `${(payload.P_cu_W ?? 0).toFixed(0)} W` },
                { label: 'Iron loss',   value: `${(payload.P_fe_W ?? 0).toFixed(0)} W` },
                { label: 'Magnet eddy', value: `${(payload.P_mag_eddy_W ?? 0).toFixed(1)} W` },
                { label: 'Efficiency',  value: `${((payload.efficiency ?? 0) * 100).toFixed(1)} %` },
              ]
            : [
                { label: 'Mesh vertices',  value: payload.n_vertices.toLocaleString() },
                { label: 'Mesh triangles', value: payload.n_triangles.toLocaleString() },
                { label: '|B|_max',        value: `${payload.B_mag_max.toFixed(2)} T` },
                { label: 'A_z range',      value: `[${(payload.A_z_min*1000).toFixed(2)}, ${(payload.A_z_max*1000).toFixed(2)}] mWb/m` },
              ]
          ).map(s => (
            <Box key={s.label} sx={{ p: 1, bgcolor: 'var(--panel-2)',
              border: '1px solid var(--app-bg)', borderRadius: 1 }}>
              <Typography sx={{ fontSize: 9, color: 'var(--text-4)',
                textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {s.label}
              </Typography>
              <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-1)' }}>
                {s.value}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      {isEddy ? (
        <Typography sx={{ fontSize: 9, color: 'var(--text-3)', mt: 0.5 }}>
          {mode === 'Loss'
            ? `Cycle-averaged loss density [W/m³] from the eddy-current transient — iron Bertotti + copper (DC I²R + AC proximity) + magnet eddy, each normalised so the map integrates to the reported component losses. ${logLoss ? 'Log' : 'Linear'} scale (toggle top-right).`
            : 'Real current density σ(−∂A/∂t+U) from the eddy solve — current crowds toward the slot opening (proximity). Compare with the uniform magnetostatic "J".'}
          {eddyNoLoad && ' No winding current (I=0): the copper loss shown is ONLY eddy/proximity induced by the spinning magnets (concentrated near the slot opening) — there is no I²R. Set a load current to see I²R copper loss and current crowding.'}
        </Typography>
      ) : (
        <Typography sx={{ fontSize: 9, color: 'var(--line)', mt: 0.5 }}>
          Same mesh + Solver-Domain settings as the Mesh tab (read from
          localStorage). Sector mode uses anti-periodic Dirichlet BC on the
          radial cuts so torque, |B| and flux linkages are physically correct
          and multiplied by n_sectors to represent the full motor.
        </Typography>
      )}
      {/* Demag warning banner removed — the per-magnet knee report over-flagged
          (the demag model over-derates sharp corners); the demag % map above is
          the honest per-element view. */}
    </Paper>
  );
};

export default FemFieldChart;
