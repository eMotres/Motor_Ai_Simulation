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
import * as THREE from 'three';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

// ── types ─────────────────────────────────────────────────────────────────
interface FemPayload {
  n_vertices:    number;
  n_triangles:   number;
  vertices:      [number, number][];                  // metres
  triangles:     [number, number, number][];
  domain_per_tri: number[];
  A_z_per_node:  number[];                            // Wb/m
  Bmag_per_tri:  number[];
  extent:        [number, number, number, number];
  outlines:      { domain: number; loops: [number, number][][] }[];

  T_em_Nm:       number;
  P_cu_W:        number;
  P_fe_W:        number;
  P_mag_eddy_W:  number;
  P_loss_total_W:number;
  P_mech_W:      number;
  efficiency:    number;
  freq_Hz:       number;
  rpm:           number;

  n_sectors:     number;
  symmetry_mult: number;
  solve_time_s:  number;
  total_time_s:  number;

  A_z_min:       number;
  A_z_max:       number;
  B_mag_max:     number;
}

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
type FieldMode = 'Az' | 'Bmag';

const N_BANDS = 20;           // # of discrete colour bands / iso-A levels

/** Extract iso-A_z contour line segments via per-triangle linear
 *  interpolation (marching-segments on tri's).
 *  For each iso level L, find triangles where A_min ≤ L ≤ A_max, then
 *  locate the two edges that L crosses and emit one line segment between
 *  the two intersection points.  Output is a flat positions array
 *  (XYZ pairs per segment) plus per-vertex RGB matching the band colour. */
function buildIsoLines(
  vertices: [number, number][],
  triangles: [number, number, number][],
  A_z_per_node: number[],
  A_min: number,
  A_max: number,
  nLevels: number,
  S: number,        // metres → mm
  z: number,        // depth for visibility
): { positions: Float32Array; colors: Float32Array } {
  const range = Math.max(A_max - A_min, 1e-12);
  const pos: number[] = [];
  const col: number[] = [];
  for (let k = 1; k < nLevels; k++) {
    const t  = k / nLevels;
    const L  = A_min + t * range;
    const [cr, cg, cb] = jet01(t);
    const r = cr / 255, g = cg / 255, b = cb / 255;
    for (let ti = 0; ti < triangles.length; ti++) {
      const [a, b1, c] = triangles[ti];
      const Aa = A_z_per_node[a];
      const Ab = A_z_per_node[b1];
      const Ac = A_z_per_node[c];
      const lo = Math.min(Aa, Ab, Ac);
      const hi = Math.max(Aa, Ab, Ac);
      if (L < lo || L > hi) continue;
      // Find the two edges where the value crosses L
      const ix: [number, number][] = [];
      const ed: [[number, number, number], [number, number, number]] = [
        [a, b1, Aa - L],
        [b1, c, Ab - L],
        [c, a, Ac - L],
      ] as any;
      for (let e = 0; e < 3; e++) {
        const [i0, i1] = [ed[e][0] as number, ed[e][1] as number];
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
        col.push(r, g, b, r, g, b);
      }
    }
  }
  return {
    positions: new Float32Array(pos),
    colors:    new Float32Array(col),
  };
}

const FieldMesh: React.FC<{ payload: FemPayload; mode: FieldMode }>
  = ({ payload, mode }) => {
  // Fill geometry — per-vertex Ansys-style banded rainbow for A_z, or
  // per-triangle flat jet for |B|.
  const fillGeo = useMemo(() => {
    const { vertices, triangles, A_z_per_node, A_z_min, A_z_max,
            Bmag_per_tri, B_mag_max } = payload;
    const S = 1000;

    if (mode === 'Az') {
      const range = Math.max(A_z_max - A_z_min, 1e-12);
      const positions = new Float32Array(vertices.length * 3);
      const colors    = new Float32Array(vertices.length * 3);
      for (let i = 0; i < vertices.length; i++) {
        positions[3 * i]     = vertices[i][0] * S;
        positions[3 * i + 1] = vertices[i][1] * S;
        positions[3 * i + 2] = 0;
        const t = (A_z_per_node[i] - A_z_min) / range;
        const [r, g, b] = jetBands(t, N_BANDS);
        colors[3 * i]     = r / 255;
        colors[3 * i + 1] = g / 255;
        colors[3 * i + 2] = b / 255;
      }
      const indices = new Uint32Array(triangles.length * 3);
      for (let i = 0; i < triangles.length; i++) {
        indices[3 * i]     = triangles[i][0];
        indices[3 * i + 1] = triangles[i][1];
        indices[3 * i + 2] = triangles[i][2];
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      g.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
      g.setIndex(new THREE.BufferAttribute(indices, 1));
      return g;
    }

    // |B| — flat per-triangle jet, clamp 2 T.
    const vmax = Math.min(B_mag_max || 0.001, 2.0);
    const nTri = triangles.length;
    const positions = new Float32Array(nTri * 3 * 3);
    const colors    = new Float32Array(nTri * 3 * 3);
    let p = 0; let c = 0;
    for (let i = 0; i < nTri; i++) {
      const tt = triangles[i];
      const t  = Math.min(1, Math.max(0, Bmag_per_tri[i] / vmax));
      const [rr, gg, bb] = jet01(t);
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
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    g.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
    return g;
  }, [payload, mode]);

  // Iso-A contour lines (only in A_z mode) — gives the classic Ansys
  // "flux line" pattern on top of the filled bands.
  const isoGeo = useMemo(() => {
    if (mode !== 'Az') return null;
    const { vertices, triangles, A_z_per_node, A_z_min, A_z_max } = payload;
    const { positions, colors } = buildIsoLines(
      vertices, triangles, A_z_per_node,
      A_z_min, A_z_max, N_BANDS, 1000, 0.5,
    );
    if (positions.length === 0) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    g.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
    return g;
  }, [payload, mode]);

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
          <lineBasicMaterial vertexColors linewidth={1}/>
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
interface Props { gamma_deg?: number; rotor_angle_deg?: number; }

const FemFieldChart: React.FC<Props> = ({ gamma_deg = 0, rotor_angle_deg = 0 }) => {
  const [payload, setPayload] = useState<FemPayload | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error,   setError]   = useState<string | null>(null);
  const [mode,    setMode]    = useState<FieldMode>('Az');
  const controlsRef = useRef<any>(null);

  const fetchFem = () => {
    setLoading(true); setError(null);
    const qs = new URLSearchParams({
      rotor_angle_deg:   String(rotor_angle_deg),
      gamma_deg:         String(gamma_deg),
      mesh_size_mm:      String(readMeshSetting('meshSize',    4.0)),
      min_size_mm:       String(readMeshSetting('minSize',     0.3)),
      outer_air_factor:  String(readMeshSetting('outerAir',    1.3)),
      motion_band:       String(readMeshSetting('motionBand',  true)),
      band_thickness_mm: String(readMeshSetting('bandThickness', 0.4)),
      n_sectors:         String(readMeshSetting('nSectors',    4)),
      stator_fillet_mm:  String(readMeshSetting('statorFillet', 0.0)),
    }).toString();
    fetch(`${API}/api/simulation/physics/fem_field2d?${qs}`)
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        return r.json();
      })
      .then((d: FemPayload) => { setPayload(d); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  };

  useEffect(() => { fetchFem();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gamma_deg, rotor_angle_deg]);

  return (
    <Paper sx={{ bgcolor: '#0b1220', border: '1px solid #1e293b', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Box>
          <Typography sx={{ fontSize: 13, color: '#cbd5e1', fontWeight: 700 }}>
            Magnetic potential A<sub>z</sub> — real scikit-fem solve
            <Tooltip title="scikit-fem 2-D magnetostatics on the same mesh the Mesh tab renders. Uses the mesh-tab Solver-Domain settings (Symmetry, motion band, outer-air ring). Torque and iron / magnet losses are multiplied by n_sectors so the values represent the full motor." placement="top">
              <span style={{ color: '#475569', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </Typography>
          <Typography sx={{ fontSize: 10, color: '#475569' }}>
            {payload
              ? `${payload.n_triangles.toLocaleString()} triangles · solve ${payload.solve_time_s}s · total ${payload.total_time_s}s · ×${payload.symmetry_mult} symmetry mult`
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
          </ToggleButtonGroup>
          <Button size="small" startIcon={<RefreshIcon fontSize="small"/>}
            onClick={fetchFem} disabled={loading}
            sx={{ color: '#93c5fd', fontSize: 11, textTransform: 'none' }}>
            Re-solve
          </Button>
        </Box>
      </Box>

      {error && (
        <Typography sx={{ fontSize: 11, color: '#fca5a5', p: 1,
          border: '1px solid #7f1d1d', borderRadius: 1 }}>
          {error}
        </Typography>
      )}

      <Box sx={{ display: 'grid',
        gridTemplateColumns: 'minmax(0, 1fr) auto 200px',
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
            </Canvas>
          )}
        </Box>

        {/* Colour bar */}
        {payload && (
          mode === 'Az'
            ? <ColorBar vmin={payload.A_z_min}
                         vmax={payload.A_z_max}
                         unit="Wb/m"
                         lut={(t) => jetBands(t, N_BANDS)}/>
            : <ColorBar vmin={0}
                         vmax={Math.min(payload.B_mag_max, 2.0)}
                         unit="T" lut={jet01}/>
        )}

        {/* Stats sidebar */}
        {payload && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.3,
            p: 1, bgcolor: '#060d17', border: '1px solid #0f172a',
            borderRadius: 1, fontSize: 11 }}>
            <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#475569',
              letterSpacing: '0.08em', textTransform: 'uppercase', mb: 0.5 }}>
              Physics (full motor)
            </Typography>
            <StatRow label="Torque T_em" value={`${payload.T_em_Nm.toFixed(2)} N·m`}
              sub={`Maxwell stress (×${payload.symmetry_mult})`}/>
            <StatRow label="Mech power" value={`${(payload.P_mech_W / 1000).toFixed(2)} kW`}
              sub={`@${payload.rpm} rpm`}/>

            <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#475569',
              letterSpacing: '0.08em', textTransform: 'uppercase', mt: 1, mb: 0.5 }}>
              Losses
            </Typography>
            <StatRow label="Copper" value={`${payload.P_cu_W.toFixed(0)} W`}
              sub="I²R (3-phase)"/>
            <StatRow label="Iron (stator+rotor)" value={`${payload.P_fe_W.toFixed(0)} W`}
              sub={`Steinmetz @ ${payload.freq_Hz} Hz (×${payload.symmetry_mult})`}/>
            <StatRow label="Magnet eddy" value={`${payload.P_mag_eddy_W.toFixed(0)} W`}
              sub={`(×${payload.symmetry_mult})`}/>
            <StatRow label="Total loss" value={`${(payload.P_loss_total_W / 1000).toFixed(2)} kW`}/>
            <StatRow label="Efficiency η" value={`${(payload.efficiency * 100).toFixed(1)} %`}/>

            <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#475569',
              letterSpacing: '0.08em', textTransform: 'uppercase', mt: 1, mb: 0.5 }}>
              Numerics
            </Typography>
            <StatRow label="Mesh vertices" value={payload.n_vertices.toLocaleString()}/>
            <StatRow label="Mesh triangles" value={payload.n_triangles.toLocaleString()}/>
            <StatRow label="|B|_max" value={`${payload.B_mag_max.toFixed(2)} T`}/>
            <StatRow label="A_z range" value={`[${(payload.A_z_min*1000).toFixed(2)}, ${(payload.A_z_max*1000).toFixed(2)}]`}
              sub="mWb/m"/>
          </Box>
        )}
      </Box>

      <Typography sx={{ fontSize: 9, color: '#334155', mt: 0.5 }}>
        Same mesh + Solver-Domain settings as the Mesh tab (read from
        localStorage). With Symmetry &gt; 1, anti-periodic BC on the radial
        cuts isn't yet enforced — torque and field shape in the sector are
        approximate; copper loss is exact.
      </Typography>
    </Paper>
  );
};

export default FemFieldChart;
