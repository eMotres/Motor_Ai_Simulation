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
import { useMotorStore } from '../../stores/motorStore';
import { geoSignature } from '../common/geoSig';
export type { FemPayload } from './fem-types';

// ── ONE renderer for every view ───────────────────────────────────────────
// Geometry, colour ramp, banding and the legend's range all come out of
// ./fieldView — see the header there for why they had to stop being seven
// hand-written copies.  Nothing in this file may build a field colour of its
// own: that is exactly how the views drifted apart.
import {
  buildFieldView, bandColor, BAND_VERT, BAND_FRAG, N_BANDS,
  type FieldView, type FieldScale,
} from './fieldView';

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
// Only J⟳ (eddy-current crowding) needs the time-coupled σ(−∂A/∂t+U) solve.
//
// It does NOT necessarily need a NEW one: the Simulation run keeps its own last
// frame server-side (mesh + A + B + Jeddy + cycle-averaged loss density), so when
// the run solved this operating point with the coupled eddy on, J⟳ and Loss are
// replayed from it instantly.  Only a miss (different operating point / mesh /
// frame count, coupled eddy off in the run, or a back-end restart) runs a solve
// here — and the header then says so.  Loss otherwise falls back to the
// single-frame analytic density (fast, like |B|).
const EDDY_MODES = new Set<FieldMode>(['Jeddy']);

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

const FieldMesh: React.FC<{
  payload: FemPayload; mode: FieldMode; view: FieldView; showFlux?: boolean;
}> = ({ payload, mode, view, showFlux }) => {
  // The fill geometry + its scale are built ONCE, by the parent, through
  // fieldView.buildFieldView — the same object feeds this mesh and the colour
  // bar, so the legend cannot describe a range the picture does not use.  Every
  // mode goes through it: nodal smoothing within material class, ~11 bands
  // quantised per pixel, iso-lines on the band edges.
  const fillGeo = view.geometry;

  // FLUX lines (A_z view only).  Every view already gets iso-lines for free —
  // the shader draws one on each band edge, which IS an iso-level of whatever
  // is being plotted.  A_z gets a second, DENSER set (2× the bands) on top,
  // because for the vector potential the iso-lines are the flux lines and they
  // are the point of the picture, not a decoration on it.
  const isoGeo = useMemo(() => {
    if (mode !== 'Az' || !view.scale) return null;
    const { vertices, triangles, domain_per_tri, A_z_per_node } = payload;
    // EXACTLY the range the fill bands over (view.scale is in mWb/m) — so the
    // lines land on the band edges instead of near them.
    const positions = buildIsoLines(
      vertices, triangles, domain_per_tri, A_z_per_node,
      view.scale.vmin * 1e-3, view.scale.vmax * 1e-3, N_BANDS * 2, 1000, 1.0,
    );
    if (positions.length === 0) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    return g;
  }, [payload, mode, view]);

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
      {fillGeo && (
        <mesh geometry={fillGeo}>
          {/* EVERY view: the interpolated scalar is banded per PIXEL and the
              band edge is drawn as an iso-line.  Quantising at the vertices
              instead would let the GPU blend two band colours across each
              triangle — blotches, not bands. */}
          <shaderMaterial
            key={`band-${mode}`}
            side={THREE.DoubleSide}
            vertexShader={BAND_VERT}
            fragmentShader={BAND_FRAG}
            uniforms={{ uBands: { value: view.scale?.bands ?? N_BANDS },
                        uIso:   { value: 0.85 } }}
          />
        </mesh>
      )}
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

// ── banded colour bar (vertical) ──────────────────────────────────────────
// Ansys's legend, and for the same reason: a BANDED plot's legend has to show
// the band EDGES, because "which band is this colour" is the only question the
// picture asks.  A smooth gradient bar under a banded fill is a legend for a
// different plot.  It reads its whole range off the SAME FieldScale the fill
// bands with, so the two cannot disagree.
const ColorBar: React.FC<{ scale: FieldScale }> = ({ scale }) => {
  const { bands, unit, fmt } = scale;
  const H = 220;
  const rowH = H / bands;
  return (
    <Box sx={{ display: 'flex', alignItems: 'stretch', gap: 0.5,
      pl: 1, pr: 1, py: 1 }}>
      <Box sx={{ display: 'flex', flexDirection: 'column-reverse',
        height: H, width: 14, border: '1px solid var(--line-soft)' }}>
        {Array.from({ length: bands }, (_, k) => {
          const [r, g, b] = bandColor(k, bands);
          return <Box key={k} sx={{ flex: 1,
            background: `rgb(${r | 0},${g | 0},${b | 0})` }}/>;
        })}
      </Box>
      {/* One label per band EDGE (bands+1 of them), aligned with the band
          boundaries rather than spread evenly over the bar. */}
      <Box sx={{ position: 'relative', height: H, minWidth: 62 }}>
        {Array.from({ length: bands + 1 }, (_, k) => (
          <Typography key={k} sx={{
            position: 'absolute', bottom: k * rowH - 5, left: 0,
            fontSize: 8.5, lineHeight: 1, whiteSpace: 'nowrap',
            color: 'var(--text-2)', fontFamily: 'monospace' }}>
            {fmt(scale.edge(k))}{k === bands ? ` ${unit}` : ''}
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
  // TRUE only once the snapshot probe has come back empty and this view is
  // actually running its own transient — so the spinner never claims to be
  // "fetching" while it is solving, or the other way round.
  const [eddySolving, setEddySolving] = useState<boolean>(false);
  const [eddyErr,     setEddyErr]     = useState<string | null>(null);
  // Loss view: the Simulation run's OWN cycle-averaged loss-density map, if that
  // run's snapshot matches this operating point.  Probed without solving
  // (snapshot_only); a miss leaves the single-frame analytic map in place.
  const [lossSnap,    setLossSnap]    = useState<FemPayload | null>(null);
  const [lossProbing, setLossProbing] = useState<boolean>(false);
  const [lossProbed,  setLossProbed]  = useState<boolean>(false);
  const [logLoss,     setLogLoss]     = useState<boolean>(true);   // log W/m³ map
  // Loss view: one shared W/m³ axis (default, comparable) vs each material
  // scaled to its own range (readable inside the weakest component, but a
  // colour no longer means one number).  Default OFF — the shared axis is the
  // number; this is a reading aid and the note under the view says so.
  const [perMat,      setPerMat]      = useState<boolean>(false);
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
  // Signature of the machine this view is drawing.  The fetch interceptor sends
  // exactly these numbers as `geo=` on every request, so when they change the
  // picture on screen is of a different motor and has to be re-fetched.  Same
  // construction TransientCharts uses to flag a stale run — one definition of
  // "the geometry changed" for both panels.
  const storeGeometry = useMotorStore(s => s.geometry);
  const geoSig = useMemo(
    () => geoSignature(storeGeometry as Record<string, unknown>), [storeGeometry]);
  const isEddy = !payloadOverride && EDDY_MODES.has(mode);
  const isThermal = !payloadOverride && mode === 'Temp';
  const isLoss = !payloadOverride && mode === 'Loss';
  const payload = payloadOverride
    ?? (isThermal ? thermalPayload
       : isEddy ? eddyPayload
       : (isLoss && lossSnap) ? lossSnap
       : fetchedPayload);
  const busy = isThermal ? thermalLoading
             : isEddy ? eddyLoading
             : (isLoss && lossProbing) ? true
             : loading;
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

  // THE field view — geometry AND colour scale, for whichever mode is showing.
  // Built once, here, and handed to both the 3-D mesh and the colour bar; the
  // two used to derive their ranges independently and could disagree about
  // what a colour meant.
  const fieldView: FieldView = useMemo(
    () => buildFieldView(payload, mode, { logLoss, eqTemp,
                                          perMaterialLoss: perMat }),
    [payload, mode, logLoss, eqTemp, perMat]);

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
  }, [gamma_deg, rotor_angle_deg, I_phase_rms, payloadOverride, geoSig]);

  // ── the picture depends on the GEOMETRY, so the geometry is a dependency ──
  // `geoSig` above is in that list, and this is the whole fix for "I edited the
  // geometry, saved it, re-ran, and every field view still shows the old
  // motor".  The back end was innocent: its keys already discriminate (a
  // config-side edit changes cfg_fingerprint, a per-request `geo=` is appended
  // to the field cache key).  What went wrong was up here — this view only ever
  // re-fetched on γ / rotor angle / current and on the `sim-design-applied`
  // event, and the Geometry tab's save path (motorStore.updateGeometryViaApi)
  // does not fire that event: only applying a design from Sweep / Compare does.
  // So the store's geometry became the new machine, every request would have
  // carried it — and no request was made.  The stale payload stayed on screen,
  // and Re-run Simulation did not help either: `sim-transient-done` only drops
  // the eddy/loss snapshots, so Loss then fell back to this same stale payload.
  //
  // Keying on the geometry itself rather than on an event covers EVERY way the
  // machine can change — Geometry tab, Sweep apply, Compare apply, a preset,
  // a catalog load — including the ones that do not exist yet.

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

  // The Simulation tab's own run settings.  The multi-frame views are asked for
  // with EXACTLY these so the backend can recognise the request as the run it
  // already solved and hand back that run's field instead of solving again.
  // (Same localStorage keys the Simulation panel writes and TransientCharts
  // sends — one source, or the keys would never match.)
  const simSteps    = () => Number(readSimSetting('stepsPP', 24)) || 24;
  const simCoilTemp = () => Number(readSimSetting('coilTemp', 120.0));
  const simEddy     = () => readSimSetting<boolean>('eddyCoupled', true) !== false;

  /** Query shared by the J⟳ / Loss requests and the run itself.  `eddy` and the
   *  frame count differ per caller, everything else is the run's own settings. */
  const multiFrameParams = (eddy: boolean, steps: number): Record<string, string> => ({
    gamma_deg:        String(gamma_deg),
    I_phase_rms:      String(eddyCurrent),
    n_steps_per_period: String(steps),
    n_periods:          '1',
    eddy:             String(eddy),
    rotor_eddy:       'true',
    // Demag and coil temperature change the SOLVE, so they are part of what
    // makes a request "the same run" — omitting them meant the view asked for a
    // motor the simulation never solved and could never be served from it.
    demag:            String(demagOn),
    coil_temp_c:      String(simCoilTemp()),
    mesh_size_mm:     String(readMeshSetting('meshSize', 4.0)),
    min_size_mm:      String(readMeshSetting('minSize',  0.3)),
    outer_air_factor: String(readMeshSetting('outerAir', 1.3)),
    n_sectors:        String(readMeshSetting('nSectors', 1)),
    component_mesh:   JSON.stringify(readMeshSetting<Record<string, number>>('componentMesh', {})),
    pole_copy:        String(readMeshSetting('poleCopy', false)),
    iron_template:    String(readMeshSetting('ironTemplate', true)),
    geo_mesh:         String(readMeshSetting('geoMesh', true)),
    structured_gap:   String(readMeshSetting('structuredGap', false) || readMeshSetting('ironTemplate', true)),
    airgap_macro:     String(readMeshSetting('harmonicGap', false)),
    gap_layers:       String(readMeshSetting('gapLayers', 2)),
  });

  const fetchEddy = () => {
    if (payloadOverride) return;
    setEddyLoading(true); setEddyErr(null); setEddySolving(false);
    // SAME endpoint as the A_z / |B| / J views, with the SAME mesh-pipeline
    // toggles. It used to be a separate /fem_eddy_field2d whose defaults left
    // out template iron, the geo mesh and the structured belt, so the J⟳ view
    // solved a DIFFERENT mesh and drew a visibly different outline next to the
    // A_z view. Every mesh setting must stay in step with fetchFem — that
    // is the whole reason the two share one endpoint now.
    const base = `${API}/api/simulation/physics/fem_field2d`;
    const coupled = simEddy();
    // STEP 1 — ask whether the Simulation run already solved this exact thing
    // (snapshot_only: the backend answers from its store or says no; it never
    // starts a solve behind this request).  Two steps rather than one so the
    // spinner can tell the truth: "fetching" and "solving" are different waits,
    // and a single request that might do either has to guess which to claim.
    // With the coupled eddy off in the run there is nothing to find, so skip it.
    const askJ = (relaxed: boolean) => fetch(
      `${base}?${new URLSearchParams({
          ...multiFrameParams(true, simSteps()), snapshot_only: 'true',
          latest_run_field: relaxed ? 'true' : 'false',
        }).toString()}`, { cache: 'no-store' })
      .then(r => (r.ok ? r.json() : { no_snapshot: true }))
      .catch(() => ({ no_snapshot: true }));
    // Exact key, then the same-machine fallback (see the Loss probe below).
    const probe: Promise<FemPayload> = coupled
      ? askJ(false).then((d: any) => (d && d.no_snapshot) ? askJ(true) : d)
      : Promise.resolve({ no_snapshot: true } as FemPayload);
    probe.then((d: FemPayload) => {
      if (d && !d.no_snapshot && d.vertices) {         // the run's own field
        setEddyPayload(tileFullRing(d)); setEddyLoading(false);
        return;
      }
      // STEP 2 — nothing to replay: solve it here, at the cheap 10 frames /
      // period this view always used (≥9 keeps the de-jitter savgol and still
      // resolves the 6f loss harmonic).  The payload labels itself as an
      // on-demand solve, and the spinner now says so too.
      setEddySolving(true);
      return fetch(`${base}?${new URLSearchParams(multiFrameParams(true, 10)).toString()}`,
                   { cache: 'no-store' })
        .then(async r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
          return r.json();
        })
        .then((d2: FemPayload) => {
          setEddyPayload(tileFullRing(d2));
          setEddyLoading(false); setEddySolving(false);
        });
    }).catch(e => { setEddyErr(String(e)); setEddyLoading(false); setEddySolving(false); });
  };

  /** Loss view: ask ONLY for the simulation run's own cycle-averaged map
   *  (snapshot_only → the backend never starts a solve for this).  A hit
   *  replaces the single-frame analytic estimate with the real transient map;
   *  a miss leaves the analytic one, which is what this view showed before. */
  const probeLossSnapshot = () => {
    if (payloadOverride) return;
    setLossProbing(true);
    const base = `${API}/api/simulation/physics/fem_field2d`;
    const P = multiFrameParams(simEddy(), simSteps());
    const ask = (relaxed: boolean) => fetch(
      `${base}?${new URLSearchParams({
        ...P, snapshot_only: 'true',
        latest_run_field: relaxed ? 'true' : 'false',
      }).toString()}`, { cache: 'no-store' })
      .then(async r => (r.ok ? r.json() : { no_snapshot: true }));
    // EXACT key first.  On a miss, ask for the last run of the SAME machine —
    // the backend crosses an operating-point / mesh difference but never a
    // geometry or material one, and it reports every difference in
    // `source_label`, which the header prints.  Three separate one-field
    // spelling mismatches have sent this view to its single-frame analytic
    // fallback (whose magnet term is ZERO), so a labelled real map beats an
    // unlabelled fake one.
    ask(false)
      .then((d: FemPayload) => (d && d.no_snapshot) ? ask(true) : d)
      .then((d: FemPayload) => {
        setLossSnap(d && d.no_snapshot ? null : tileFullRing(d));
        setLossProbing(false); setLossProbed(true);
      })
      .catch(() => { setLossSnap(null); setLossProbing(false); setLossProbed(true); });
  };

  // γ / I changed → the cached eddy solve is stale (rotor angle does NOT
  // matter — the eddy run sweeps a whole period — so we don't invalidate on it).
  useEffect(() => {
    setEddyPayload(null); setEddyErr(null);
    setLossSnap(null); setLossProbed(false);
    // geoSig: a different machine invalidates these just as surely as a
    // different operating point does — and the thermal solve is fed by them.
  }, [gamma_deg, I_phase_rms, mode, geoSig]);   // Loss (fast) vs J⟳ (coupled) need different solves

  // A finished simulation run has just produced a field snapshot → drop what
  // these views are holding so the next look comes from the RUN, not from a
  // solve of an older operating point.
  useEffect(() => {
    const onRun = () => {
      setEddyPayload(null); setEddyErr(null);
      setLossSnap(null); setLossProbed(false);
    };
    window.addEventListener('sim-transient-done', onRun);
    return () => window.removeEventListener('sim-transient-done', onRun);
  }, []);

  // Lazily fetch the first time a J⟳ view is shown (from the run's snapshot when
  // it matches, otherwise a solve here).
  useEffect(() => {
    if (isEddy && !eddyPayload && !eddyLoading && !eddyErr) fetchEddy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEddy, eddyPayload, eddyLoading, eddyErr]);

  // Loss: probe the run's snapshot once per operating point (no solve).
  useEffect(() => {
    if (isLoss && !lossSnap && !lossProbed && !lossProbing) probeLossSnapshot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoss, lossSnap, lossProbed, lossProbing]);

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
              ? (lossSnap
                   ? <>Loss density — W/m³ (simulation run, cycle-averaged)</>
                   : <>Loss density — W/m³ (single frame, analytic)</>)
              : mode === 'Jeddy'
                ? <>Current density J — coupled eddy solve (proximity)</>
                : <>Magnetic potential A<sub>z</sub> — real scikit-fem solve</>}
            <Tooltip title={(isEddy || isLoss)
              ? "Time-coupled eddy-current solve over a full electrical period. J⟳ shows the real current density σ(−∂A/∂t+U) crowding toward the slot mouth (proximity effect); Loss is the cycle-averaged dissipation density [W/m³] — iron Bertotti + copper DC+AC + magnet eddy, normalised so the map integrates to the reported component losses. When the last Simulation run solved this same operating point (with the coupled eddy solve on), these views replay THAT run's final frame — no second solve. Otherwise they compute here, and the line below says so."
              : "2-D magnetostatic field at the current rotor angle — the same per-frame field the sliding-band transient sweeps. Torque + losses are ×n_sectors for the full motor."} placement="top">
              <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </Typography>
          {/* One SHORT visible line (user rule: no walls of text in the web —
              details live in the tooltip).  Visible: where the picture came
              from + the operating current.  Hover ⓘ: mesh size, solve time,
              per-component provenance and the colour-scale semantics. */}
          <Tooltip placement="bottom-start" title={payload ? (
              `${payload.n_triangles.toLocaleString()} triangles · ×${payload.symmetry_mult} symmetry`
              + `${payload.from_transient ? '' : ` · solve ${payload.solve_time_s}s`}`
              + `${payload.source_label ? ` · ${payload.source_label}` : ''}`
              + `${fieldView.scale ? ` — ${fieldView.scale.note} · ${fieldView.scale.bands} bands` : ''}`
            ) : ''}>
            <Typography sx={{ fontSize: 10, cursor: payload ? 'help' : 'default',
                              color: payload?.from_transient ? '#38bdf8' : 'var(--text-4)' }}>
              {payload
                ? (subHeader
                     ? subHeader
                     : `${payload.from_transient
                            ? ('from last simulation run'
                               // A relaxed match is the user's MOTOR but not the
                               // panel's numbers.  That has to be visible, not
                               // hover-only; the diff itself is in the tooltip.
                               + ((payload as any).from_transient_relaxed
                                    ? ' (≠ panel)' : ''))
                            : 'computed on demand'}`
                       + `${(isEddy || isLoss) ? ` · @ ${eddyCurrent.toFixed(0)} A` : ''}`
                       + ' · ⓘ')
                : (isEddy
                     ? (eddySolving
                          ? 'No matching simulation run — solving this view on demand…'
                          : 'Looking for the last simulation run\'s field…')
                     : isLoss ? 'Checking the last simulation run\'s loss map…'
                     : 'Solving…')}
            </Typography>
          </Tooltip>
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
                title="Coupled eddy-current density σ(−∂A/∂t+U) — the proximity crowding the uniform 'J' view cannot show. Instant when the last Simulation run solved this operating point with the coupled eddy solve on (it replays that run's final frame); otherwise it runs a 10-frame transient here (~25 s) and says so.">
                J⟳
              </ToggleButton>
            )}
            {!payloadOverride && (
              <ToggleButton value="Loss"
                title="Ansys-style loss-density map [W/m³]. Uses the last Simulation run's own cycle-averaged map when it matches this operating point; otherwise the single-frame analytic estimate (fast). The header says which one you are looking at.">
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
          {mode === 'Loss' && (
            <Button size="small" onClick={() => setPerMat(v => !v)}
              title={'Colour scale span. "shared" is the honest one: one W/m³ '
                + 'axis for the whole cross-section, so copper (≈4e7 W/m³ here) '
                + 'and the magnets (≈2e6) are directly comparable — and the '
                + 'magnets sit low because they ARE low. "per-material" '
                + 'rescales every material to its own range so the structure '
                + 'inside the weakest one is readable; a colour then means a '
                + 'different number in each material and levels cannot be '
                + 'compared across them.'}
              sx={{ color: perMat ? '#fbbf24' : '#93c5fd', fontSize: 10,
                textTransform: 'none', minWidth: 0, px: 1,
                border: '1px solid var(--line-soft)' }}>
              {perMat ? 'per-material' : 'shared'}
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
              onClick={isThermal ? fetchThermal
                       : isEddy ? fetchEddy
                       // Loss: re-check the run's snapshot AND refresh the
                       // single-frame map it falls back to.
                       : isLoss ? (() => { setLossSnap(null); setLossProbed(false); fetchFem(); })
                       : fetchFem}
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
              {(isEddy || isThermal || isLoss) && (
                <Typography sx={{ fontSize: 11, color: 'var(--text-2)', textAlign: 'center', maxWidth: 320 }}>
                  {isThermal
                    ? 'Running thermal solve (EM losses + conduction, ~25 s)…'
                    : isLoss
                      ? 'Looking for the last simulation run\'s loss map (no solve)…'
                      : !eddySolving
                        // Still the snapshot probe — a lookup, not a solve.
                        ? 'Looking for the last simulation run\'s eddy field…'
                        : (simEddy()
                            ? 'The last simulation run does not cover this operating '
                              + 'point, so this view is solving its own 10-frame eddy '
                              + 'transient (~25 s).'
                            : 'Coupled eddy solve is OFF in the Simulation run, so this '
                              + 'view has to solve its own 10-frame eddy transient (~25 s). '
                              + 'Turn it on in the Simulation panel to get this instantly.')}
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
              <FieldMesh payload={payload} mode={mode} view={fieldView} showFlux={showFlux}/>
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

        {/* Colour bar — the SAME FieldScale object the fill bands with, so
            the labels are the band edges of the picture beside them and can
            never drift from it (they used to be recomputed here, with a
            second copy of the percentile code). */}
        {fieldView.scale && <ColorBar scale={fieldView.scale}/>}

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
          Real current density σ(−∂A/∂t+U) from the eddy solve — current crowds
          toward the slot opening (proximity). Compare with the uniform
          magnetostatic &quot;J&quot;.
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
