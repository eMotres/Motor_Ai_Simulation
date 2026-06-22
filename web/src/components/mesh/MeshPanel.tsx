/**
 * Mesh tab — FEM triangle mesh viewer + PINN collocation settings.
 *
 * Left  : parameter sliders (n_radial, n_angular, n_angular_slots)
 *         + mesh density slider for the FEM mesh
 * Right : either the live SVG collocation preview OR the real FEM triangle
 *         mesh fetched from /api/simulation/mesh/build2d, drawn on canvas
 *         with one color per domain.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Divider,
  Paper, Slider, TextField, Tooltip, Typography, ToggleButton, ToggleButtonGroup,
} from '@mui/material';

// Per-component mesh-size controls (study mesh-density effect on results).
// Keys MUST match the backend _comp_of() mapping in build_mesh_from_polygons.
const MESH_COMPONENTS: { key: string; label: string }[] = [
  { key: 'stator', label: 'Stator iron' },
  { key: 'rotor',  label: 'Rotor iron' },
  { key: 'magnet', label: 'Magnets' },
  { key: 'coil',   label: 'Windings' },
  { key: 'shaft',  label: 'Shaft' },
  { key: 'outer',  label: 'Outer air' },
];
import SaveIcon from '@mui/icons-material/Save';
import RefreshIcon from '@mui/icons-material/Refresh';
import FemMeshViewer3D from './FemMeshViewer3D';
import FemMeshViewer2D from './FemMeshViewer2D';
import { syncActiveMotor } from '../common/motorSettings';

// WebGL is unavailable in some embedded / sandboxed browser panels
// ("GL_VENDOR = Disabled, Sandboxed = yes") → the 3-D (WebGL) viewer renders
// black there.  Detect once and fall back to the Canvas2D viewer so the mesh
// is always visible.
const WEBGL_OK = (() => {
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch { return false; }
})();
import Viewcube from '../viewer3d/Viewcube';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ── FEM mesh types ────────────────────────────────────────────────────────────
interface FemMesh {
  n_vertices: number;
  n_triangles: number;
  vertices: [number, number][];   // metres
  triangles: [number, number, number][];
  domain_per_tri: number[];
  domain_counts: Record<string, number>;
  extent: [number, number, number, number];
  mesh_size_mm: number;
  note: string;
}

// Domain id → color (matches the field map convention)
const DOMAIN_RGBA: Record<number, [number, number, number, number]> = {
  0:  [80,  90,  110, 200],   // air            slate-ish
  1:  [72,  85,  99,  240],   // stator         slate
  2:  [251, 191, 36,  240],   // coil           amber
  3:  [56,  189, 248, 200],   // air gap        sky
  4:  [59,  130, 246, 240],   // magnet N       blue
  5:  [55,  68,  82,  230],   // rotor          dark slate
  6:  [180, 180, 190, 210],   // shaft          grey
  7:  [115, 217, 204, 230],   // band           teal
  8:  [56,  102, 140, 200],   // outer air      deep blue
  9:  [63,  174, 90,  240],   // slot liner     green   (Nomex/ceramic)
  10: [217, 138, 58,  240],   // wire enamel    orange  (polyimide)
  44: [239, 68,  68,  240],   // magnet S       red
};
const DOMAIN_NAMES: Record<number, string> = {
  0: 'Air',     1: 'Stator',  2: 'Winding',  3: 'Air gap',
  4: 'Magnet N', 5: 'Rotor',  6: 'Shaft',
  7: 'Band',    8: 'Outer air',
  9: 'Slot liner', 10: 'Wire enamel',
  44: 'Magnet S',
};

// ── types ─────────────────────────────────────────────────────────────────────
interface MeshCfg {
  n_radial:        number;
  n_angular:       number;
  n_angular_slots: number;
}

interface MotorGeo {
  stator_outer_radius: number;
  stator_inner_radius: number;
  rotor_outer_radius:  number;
  rotor_inner_radius:  number;
  num_slots:           number;
  num_poles:           number;
  shaft_radius?:       number;
}

// ── domain list (mirrors geometry_2d.py) ─────────────────────────────────────
const DOMAINS = [
  { key: 'stator_core', label: 'Stator Core', color: '#3b82f6' },
  { key: 'air_gap',     label: 'Air Gap',     color: '#94a3b8' },
  { key: 'rotor_core',  label: 'Rotor Core',  color: '#2563eb' },
  { key: 'magnet',      label: 'Magnets',     color: '#ef4444' },
  { key: 'slot',        label: 'Windings',    color: '#f59e0b' },
  { key: 'shaft',       label: 'Shaft',       color: '#64748b' },
];

// Estimate collocation points per domain (mirrors batch_size_interior in solver)
function estimatePoints(cfg: MeshCfg): Record<string, number> {
  const ring  = cfg.n_radial * cfg.n_angular;
  const slot  = cfg.n_radial * cfg.n_angular_slots;
  return {
    stator_core: ring,
    air_gap:     Math.round(ring * 0.5),
    rotor_core:  ring,
    magnet:      Math.round(slot * 1.5),
    slot:        slot,
    shaft:       Math.round(ring * 0.3),
  };
}

// ── FEM mesh canvas preview ──────────────────────────────────────────────────
interface FemMeshCanvasProps {
  mesh: FemMesh | null;
  loading: boolean;
  size?: number;
  fillDomains?: boolean;   // fill triangles with domain color
  showEdges?: boolean;     // draw triangle edges
}

const FemMeshCanvas: React.FC<FemMeshCanvasProps> = ({
  mesh, loading, size = 520, fillDomains = true, showEdges = true,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext('2d');
    if (!ctx) return;
    c.width = size;
    c.height = size;

    // Background
    ctx.fillStyle = '#060d17';
    ctx.fillRect(0, 0, size, size);

    if (!mesh || mesh.n_triangles === 0) return;

    const [xMin, xMax, yMin, yMax] = mesh.extent;
    const span = Math.max(xMax - xMin, yMax - yMin) * 1.05;
    const cx = (xMin + xMax) / 2;
    const cy = (yMin + yMax) / 2;
    const scale = size / span;
    const m2px = (x: number) => (x - cx) * scale + size / 2;
    const m2py = (y: number) => size / 2 - (y - cy) * scale;

    const v = mesh.vertices;
    const tris = mesh.triangles;
    const doms = mesh.domain_per_tri;

    // Fill pass
    if (fillDomains) {
      for (let i = 0; i < tris.length; i++) {
        const [a, b, c2] = tris[i];
        const rgba = DOMAIN_RGBA[doms[i]] ?? [40, 40, 50, 255];
        ctx.fillStyle = `rgba(${rgba[0]},${rgba[1]},${rgba[2]},${rgba[3] / 255})`;
        ctx.beginPath();
        ctx.moveTo(m2px(v[a][0]), m2py(v[a][1]));
        ctx.lineTo(m2px(v[b][0]), m2py(v[b][1]));
        ctx.lineTo(m2px(v[c2][0]), m2py(v[c2][1]));
        ctx.closePath();
        ctx.fill();
      }
    }

    // Edges pass — light, single colour
    if (showEdges) {
      ctx.strokeStyle = 'rgba(255,255,255,0.18)';
      ctx.lineWidth = 0.4;
      ctx.beginPath();
      for (let i = 0; i < tris.length; i++) {
        const [a, b, c2] = tris[i];
        const ax = m2px(v[a][0]), ay = m2py(v[a][1]);
        const bx = m2px(v[b][0]), by = m2py(v[b][1]);
        const cx2 = m2px(v[c2][0]), cy2 = m2py(v[c2][1]);
        ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
        ctx.moveTo(bx, by); ctx.lineTo(cx2, cy2);
        ctx.moveTo(cx2, cy2); ctx.lineTo(ax, ay);
      }
      ctx.stroke();
    }
  }, [mesh, size, fillDomains, showEdges]);

  return (
    <Box sx={{ position: 'relative', display: 'flex', justifyContent: 'center',
      alignItems: 'center' }}>
      {loading && (
        <Box sx={{ position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          bgcolor: 'rgba(6,13,23,0.65)', borderRadius: 2, zIndex: 2 }}>
          <CircularProgress size={32} sx={{ color: '#3b82f6' }}/>
        </Box>
      )}
      <canvas
        ref={canvasRef}
        width={size}
        height={size}
        style={{ borderRadius: 12, border: '1px solid #1e293b', display: 'block' }}
      />
    </Box>
  );
};

// ── SVG collocation preview ──────────────────────────────────────────────────
const SVG_SIZE = 420;
const CX = SVG_SIZE / 2;
const CY = SVG_SIZE / 2;

interface PreviewProps {
  cfg: MeshCfg;
  geo: MotorGeo | null;
}

const CollocationPreview: React.FC<PreviewProps> = ({ cfg, geo }) => {
  const scale = useMemo(() => {
    if (!geo) return 1;
    return (SVG_SIZE * 0.44) / geo.stator_outer_radius;
  }, [geo]);

  const r = useMemo(() => {
    if (!geo) return null;
    return {
      so: geo.stator_outer_radius * scale,
      si: geo.stator_inner_radius * scale,
      ro: geo.rotor_outer_radius  * scale,
      ri: geo.rotor_inner_radius  * scale,
      sh: (geo.shaft_radius ?? geo.rotor_inner_radius * 0.55) * scale,
    };
  }, [geo, scale]);

  // Generate radial rings between r_min and r_max with n_r layers, n_phi points each
  const ringDots = useCallback((
    r_min: number, r_max: number, n_r: number, n_phi: number, color: string, key: string
  ) => {
    const dots: React.ReactElement[] = [];
    for (let i = 0; i < n_r; i++) {
      const radius = r_min + ((r_max - r_min) / Math.max(n_r - 1, 1)) * i;
      for (let j = 0; j < n_phi; j++) {
        const phi = (2 * Math.PI / n_phi) * j;
        const x = CX + radius * Math.cos(phi);
        const y = CY + radius * Math.sin(phi);
        dots.push(
          <circle key={`${key}-${i}-${j}`} cx={x} cy={y} r={1.2}
            fill={color} opacity={0.65}/>
        );
      }
    }
    return dots;
  }, []);

  const dots = useMemo(() => {
    if (!r) return null;
    const nR = Math.max(2, Math.min(cfg.n_radial,  20));
    const nA = Math.max(8, Math.min(cfg.n_angular, 128));
    const nS = Math.max(4, Math.min(cfg.n_angular_slots, 32));
    return [
      ...ringDots(r.sh, r.ri, nR, nA, '#64748b', 'shaft'),
      ...ringDots(r.ri, r.ro, nR, nA, '#3b82f6', 'rotor'),
      ...ringDots(r.ro, r.si, nR, nS, '#f59e0b', 'gap'),
      ...ringDots(r.si, r.so, nR, nA, '#2563eb', 'stator'),
    ];
  }, [r, cfg, ringDots]);

  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
      <svg width={SVG_SIZE} height={SVG_SIZE} style={{ background: '#060d17', borderRadius: 12 }}>
        {r && <>
          {/* domain annuli */}
          <circle cx={CX} cy={CY} r={r.so} fill="none" stroke="#1e293b" strokeWidth={1}/>
          <circle cx={CX} cy={CY} r={r.si} fill="none" stroke="#334155" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.ro} fill="none" stroke="#334155" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.ri} fill="none" stroke="#334155" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.sh} fill="none" stroke="#1e293b" strokeWidth={0.8}/>
          {/* filled regions */}
          <circle cx={CX} cy={CY} r={r.so} fill="#1e293b22"/>
          <circle cx={CX} cy={CY} r={r.si} fill="#0a1628"/>
          <circle cx={CX} cy={CY} r={r.ri} fill="#1e3a5f22"/>
          <circle cx={CX} cy={CY} r={r.sh} fill="#0f172a"/>
        </>}
        {dots}
        {/* center cross */}
        <line x1={CX - 6} y1={CY} x2={CX + 6} y2={CY} stroke="#334155" strokeWidth={0.8}/>
        <line x1={CX} y1={CY - 6} x2={CX} y2={CY + 6} stroke="#334155" strokeWidth={0.8}/>
      </svg>
    </Box>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────
const MeshPanel: React.FC = () => {
  const [cfg,     setCfg]     = useState<MeshCfg>({ n_radial: 10, n_angular: 64, n_angular_slots: 8 });
  const [geo,     setGeo]     = useState<MotorGeo | null>(null);
  const [saving,  setSaving]  = useState(false);
  const [saved,   setSaved]   = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  // ── FEM mesh state ─────────────────────────────────────────────────────────
  const [view,        setView]        = useState<'fem' | 'pinn'>('fem');
  // ── Persisted mesh settings (survive tab switches) ──────────────────────
  // Hook: each setting reads its initial value from localStorage and writes
  // back on every change.  Default symmetry is 1/4 per user request.
  const usePersisted = <T,>(key: string, def: T) => {
    const [v, setV] = useState<T>(() => {
      try {
        const raw = localStorage.getItem(`mesh.${key}`);
        return raw == null ? def : (JSON.parse(raw) as T);
      } catch { return def; }
    });
    useEffect(() => {
      try { localStorage.setItem(`mesh.${key}`, JSON.stringify(v)); } catch {}
    }, [key, v]);
    return [v, setV] as const;
  };

  const [meshSizeMm,  setMeshSizeMm]  = usePersisted<number>('meshSize',   4.0);
  const [minSizeMm,   setMinSizeMm]   = usePersisted<number>('minSize',    0.3);
  // Fillet-arc resolution (Ansys "Normal Deviation"): max angle per fillet
  // segment. Lower → more segments per rounded corner → smoother fillet + finer
  // mesh there. Wired to n_arc in the geometry (get_2d_polygons).
  const [normalDev,   setNormalDev]   = usePersisted<number>('normalDev',  6.0);
  // 'Surface deviation' control removed: any value >0.01 mm Douglas-Peucker-
  // flattened the rounded rotor-tooth / fillet arcs into straight chords, so
  // the Mesh no longer matched the real geometry. The Mesh now always uses the
  // real geometry (tol 0.005 mm, sent below); density is set by Max/Min size.
  const [rotorAngle,  setRotorAngle]  = usePersisted<number>('rotorAngle', 0.0);
  // ── Solver-domain extensions (Ansys-style) ───────────────────────────────
  const [outerAirFactor, setOuterAirFactor] = usePersisted<number>('outerAir', 1.3);
  const [nSectors,       setNSectors]       = usePersisted<number>('nSectors', 4);   // 1/4 by default
  // Air-gap element rows PER SIDE of the slip midline (1-3, default 2). The
  // value persists in config.yaml (loaded below, clamped to the new 1-3 range).
  const [gapLayers,      setGapLayers]      = usePersisted<number>('gapLayers', 2);
  // Bit-identical pole/slot mesh (template-copy): mesh ONE pole + ONE slot and
  // rotate-copy them so every pole/slot is identical → no pole-to-pole mesh
  // variance.  Read by the field & simulation fetches too (mesh.poleCopy).
  const [poleCopy,       setPoleCopy]       = usePersisted<boolean>('poleCopy', false);
  // ── Per-component mesh size (study mesh-density effect on results) ─────────
  // {comp: target element size mm}. Empty/0 → use the global size for that part.
  // Persisted under 'mesh.componentMesh' so the Simulation tab's solve reads the
  // SAME sizes (see readComponentMesh() in the simulation store).
  const [componentMesh, setComponentMesh] =
    usePersisted<Record<string, number>>('componentMesh', {});
  // Raw text being typed per field.  The numeric store (componentMesh) only
  // keeps positive values, so a controlled type="number" input ate any sub-1
  // entry: typing "0.5" fires onChange with "0" first → parses to 0 → key
  // deleted → field snaps back to empty, making a value like Wire-Width/2
  // (~0.5 mm) impossible to enter.  Hold the raw string here and commit to
  // componentMesh only when it parses to > 0; the field shows the draft.
  const [compDraft, setCompDraft] = useState<Record<string, string>>({});
  const setCompSize = (k: string, raw: string) => {
    const clean = raw.replace(/[^0-9.]/g, '');
    setCompDraft(prev => ({ ...prev, [k]: clean }));
    const v = parseFloat(clean);
    setComponentMesh(prev => {
      const next = { ...prev };
      if (clean === '' || !isFinite(v) || v <= 0) delete next[k];
      else next[k] = v;
      return next;
    });
  };
  const resetCompSizes = () => { setComponentMesh({}); setCompDraft({}); };
  // Only positive sizes reach the backend; "{}" means global everywhere.
  const componentMeshJson = JSON.stringify(
    Object.fromEntries(Object.entries(componentMesh).filter(([, v]) => v > 0)));
  // NOTE: the old "Extra fillet smoothing" control was removed — it applied a
  // Shapely buffer on top of the CadQuery fillets, deforming the Mesh geometry
  // away from the Geometry tab.  The Mesh now always uses the native geometry
  // (stator_fillet_mm = 0), identical to the Geometry tab.
  const [showEdges,    setShowEdges]    = useState<boolean>(true);
  const [showOutlines, setShowOutlines] = useState<boolean>(true);
  const [fillDomains,  setFillDomains]  = useState<boolean>(true);
  // Sliding-band TWO-mesh view (feature/sliding-band-fem branch).  When on,
  // fetches /mesh/build2d_sliding_band which returns the stator and rotor
  // meshes concatenated — moving the rotor_angle slider rigidly rotates
  // the rotor half without touching the stator triangulation.
  // Which mesh the tab DRAWS:
  //   true  → the REAL sliding-band solver mesh (stator + rotor meshed
  //           separately, iron clamped to 2 mm, gap floor 0.1 mm, 1008-node
  //           slip ring) — exactly what computes T(t)/V(t)/losses.
  //   false → the single-mesh viewer/static mesh (what the static field +
  //           torque sweep solve on).
  // Default = solver mesh, so "Mesh" shows what actually runs.  (New
  // localStorage key so the default takes effect even for older sessions.)
  // The Mesh tab always renders the REAL transient (sliding-band) solver mesh.
  // The static single-mesh view was retired — we work only with the transient.
  const solverMesh = true;
  const [femMesh,     setFemMesh]     = useState<FemMesh | null>(null);
  const [femLoading,  setFemLoading]  = useState<boolean>(false);
  const [femError,    setFemError]    = useState<string | null>(null);

  // Monotonic build id: only the LATEST build's result is applied, so a stale build
  // (e.g. the premature mount build with default settings) can't overwrite the
  // config-correct one — that mismatch is what showed a 1/4 mesh under a "Full"
  // toggle on first open.  `over` lets the mount build use the just-loaded config
  // values directly, before the async setState has propagated to these closures.
  const buildSeq = useRef(0);
  const fetchFemMesh = useCallback((over?: Partial<{
    mesh_size_mm: number; min_size_mm: number; normal_deviation: number;
    outer_air_factor: number; gap_layers: number; n_sectors: number;
  }>) => {
    const mySeq = ++buildSeq.current;
    setFemLoading(true);
    setFemError(null);
    const _ms = (over?.mesh_size_mm     ?? meshSizeMm).toString();
    const _mn = (over?.min_size_mm      ?? minSizeMm).toString();
    const _nd = (over?.normal_deviation ?? normalDev).toString();
    const _oa = (over?.outer_air_factor ?? outerAirFactor).toString();
    const _gl = (over?.gap_layers       ?? gapLayers).toString();
    const _ns = (over?.n_sectors        ?? nSectors).toString();
    const base = solverMesh
      ? `${API}/api/simulation/mesh/build2d_sliding_band`
      : `${API}/api/simulation/mesh/build2d`;
    const qs = new URLSearchParams(solverMesh ? {
      rotor_angle_deg:   rotorAngle.toString(),
      mesh_size_mm:      _ms,
      min_size_mm:       _mn,
      surface_deviation: '0.005',     // real geometry — no flattening
      normal_deviation:  _nd,
      outer_air_factor:  _oa,
      gap_layers:        _gl,
      n_sectors:         _ns,
      stator_fillet_mm:  '0',          // native geometry — no extra smoothing
      component_mesh:    componentMeshJson,
      pole_copy:         poleCopy ? 'true' : 'false',
    } : {
      mesh_size_mm:        _ms,
      min_size_mm:         _mn,
      surface_deviation:   '0.005',   // real geometry — no flattening
      normal_deviation:    _nd,
      rotor_angle_deg:     rotorAngle.toString(),
      outer_air_factor:    _oa,
      gap_layers:          _gl,
      n_sectors:           _ns,
      stator_fillet_mm:    '0',          // native geometry — no extra smoothing
      component_mesh:      componentMeshJson,
    }).toString();
    fetch(`${base}?${qs}`)
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        return r.json();
      })
      .then((d: FemMesh) => { if (mySeq !== buildSeq.current) return; setFemMesh(d); setFemLoading(false); })
      .catch(e => { if (mySeq !== buildSeq.current) return; setFemError(String(e)); setFemLoading(false); });
  }, [solverMesh, meshSizeMm, minSizeMm, normalDev, rotorAngle,
      outerAirFactor, gapLayers, nSectors, componentMeshJson, poleCopy]);

  // Gate: don't persist mesh settings back to config until the mount config-load
  // has populated state — otherwise a slow/interrupted load would let the
  // debounced save write STALE localStorage into config (a clobber).
  const meshReady = useRef(false);
  // Load current config + geometry on mount
  useEffect(() => {
    fetch(`${API}/api/mesh/config`)
      .then(r => r.json())
      .then(d => {
        setCfg(d);
        // Load the PERSISTED FEM mesh settings (config.yaml) → these win over the
        // per-browser localStorage so the sliders are identical in every session.
        if (typeof d.mesh_size_mm     === 'number') setMeshSizeMm(d.mesh_size_mm);
        if (typeof d.min_size_mm      === 'number') setMinSizeMm(d.min_size_mm);
        if (typeof d.outer_air_factor === 'number') setOuterAirFactor(d.outer_air_factor);
        // gap_layers changed meaning (now per-side, 1-3): clamp legacy values.
        if (typeof d.gap_layers       === 'number') setGapLayers(Math.min(3, Math.max(1, Math.round(d.gap_layers))));
        if (typeof d.normal_deviation === 'number') setNormalDev(d.normal_deviation);
        if (typeof d.n_sectors        === 'number') setNSectors(d.n_sectors);
        meshReady.current = true;     // saves allowed only AFTER config is loaded
        // Build the initial mesh with the JUST-LOADED config values (not the stale
        // defaults) so the displayed mesh matches the Symmetry toggle on first open.
        fetchFemMesh({
          mesh_size_mm:     typeof d.mesh_size_mm     === 'number' ? d.mesh_size_mm : undefined,
          min_size_mm:      typeof d.min_size_mm      === 'number' ? d.min_size_mm : undefined,
          normal_deviation: typeof d.normal_deviation === 'number' ? d.normal_deviation : undefined,
          outer_air_factor: typeof d.outer_air_factor === 'number' ? d.outer_air_factor : undefined,
          gap_layers:       typeof d.gap_layers       === 'number' ? Math.min(3, Math.max(1, Math.round(d.gap_layers))) : undefined,
          n_sectors:        typeof d.n_sectors        === 'number' ? d.n_sectors : undefined,
        });
      })
      .catch(() => { fetchFemMesh(); });   // config load failed → build with current defaults

    fetch(`${API}/api/geometry/summary`)
      .then(r => r.json())
      .then(d => setGeo(d))
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── One-click symmetry switch (PERSISTENT) ──────────────────────────────
  // When the Full / 1/2 / 1/4 toggle changes, rebuild AND persist to the
  // backend config (config.yaml) via rebuildMesh → saveMeshConfig.  This makes
  // the symmetry choice permanent across reloads/sessions, and — because the
  // Simulation field view and the charts all read the same `mesh.nSectors`
  // setting — they automatically use the SAME symmetry as the mesh.
  const symFirstRun = useRef(true);
  useEffect(() => {
    if (symFirstRun.current) { symFirstRun.current = false; return; }
    rebuildMesh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nSectors]);

  // ── Valid symmetry sectors for THIS motor ───────────────────────────────
  // A sector model is only valid for divisors of GCD(slots, poles): each wedge
  // must hold a whole number of slots AND poles.  e.g. 12s/14p → GCD 2 → {1,2}
  // (only Full or 1/2); 24s/28p → GCD 4 → {1,2,4}.  Offering an invalid sector
  // (e.g. 1/4 of a 12-slot motor) builds a broken cut mesh.
  const _gcd = (a: number, b: number): number => (b === 0 ? a : _gcd(b, a % b));
  const symSlots = geo?.num_slots ?? 24;
  const symPoles = geo?.num_poles ?? 28;
  const symGcd = Math.max(1, _gcd(Math.round(symSlots), Math.round(symPoles)));
  const validSectors = [1, 2, 3, 4, 6, 8, 12].filter(s => symGcd % s === 0);
  // If the persisted nSectors is invalid for this motor, snap to the finest valid.
  useEffect(() => {
    if (geo && !validSectors.includes(nSectors)) {
      setNSectors(validSectors[validSectors.length - 1]);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geo, symGcd]);

  // ── Standard / Periodic (pole-copy) switch ──────────────────────────────
  // Toggling it must REBUILD the displayed mesh immediately, like the symmetry
  // switch — otherwise the change silently does nothing until the next "Rebuild
  // mesh" click (the bug the user hit: "no difference").  poleCopy is NOT a
  // config.yaml field (it lives in localStorage and is read by the field/sim
  // fetches too), so we just re-fetch the Mesh-tab mesh here.
  const poleCopyFirstRun = useRef(true);
  useEffect(() => {
    if (poleCopyFirstRun.current) { poleCopyFirstRun.current = false; return; }
    fetchFemMesh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poleCopy]);

  // Persist the Mesh-tab settings to config.yaml — ONLY on the explicit
  // "Rebuild mesh" click (rebuildMesh below), not on every slider move. This
  // makes them permanent + reused in all later sessions.
  const saveMeshConfig = useCallback(() => {
    fetch(`${API}/api/mesh/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mesh_size_mm: meshSizeMm, min_size_mm: minSizeMm,
        outer_air_factor: outerAirFactor, gap_layers: gapLayers,
        normal_deviation: normalDev, n_sectors: nSectors,
      }),
    }).catch(() => {});
  }, [meshSizeMm, minSizeMm, outerAirFactor, gapLayers, normalDev, nSectors]);

  // "Rebuild mesh" button → persist the settings, then rebuild.
  const rebuildMesh = useCallback(() => {
    saveMeshConfig();
    fetchFemMesh();
  }, [saveMeshConfig, fetchFemMesh]);

  // Persist mesh settings to config.yaml on ANY change (debounced), not just on
  // the explicit Rebuild click — config wins on mount, so it MUST stay current
  // or a change made without pressing Rebuild would be lost on reload.
  useEffect(() => {
    if (!meshReady.current) return;   // skip until the mount config-load populated state
    const id = setTimeout(() => { saveMeshConfig(); syncActiveMotor(); }, 700);
    return () => clearTimeout(id);
  }, [saveMeshConfig]);

  const totalPoints = useMemo(() => {
    const pts = estimatePoints(cfg);
    return Object.values(pts).reduce((a, b) => a + b, 0);
  }, [cfg]);

  const pointsPerDomain = useMemo(() => estimatePoints(cfg), [cfg]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/mesh/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      });
      if (!r.ok) throw new Error(await r.text());
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden', bgcolor: '#060d17' }}>

      {/* ── LEFT: controls ── */}
      <Box sx={{
        width: 320, flexShrink: 0, overflowY: 'auto',
        borderRight: '1px solid #1e293b', p: 2,
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>

        {/* ── View toggle ── */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 0.75 }}>
            View
          </Typography>
          <ToggleButtonGroup
            value={view} exclusive size="small"
            onChange={(_, v) => v && setView(v as 'fem' | 'pinn')}
            sx={{ width: '100%',
              '& .MuiToggleButton-root': { flex: 1, py: 0.5, fontSize: 11,
                color: '#64748b', borderColor: '#1e293b', textTransform: 'none',
                '&.Mui-selected': { color: '#e2e8f0', bgcolor: '#1e3a5f',
                  borderColor: '#3b82f6' } } }}>
            <ToggleButton value="fem">FEM mesh (real)</ToggleButton>
          </ToggleButtonGroup>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {view === 'fem' && (
          <>
            <Box>
              <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
                letterSpacing: '0.1em', textTransform: 'uppercase', mb: 0.5 }}>
                FEM Triangle Mesh
              </Typography>
              <Typography sx={{ fontSize: 11, color: '#334155' }}>
                Conforming mesh of the real CadQuery cross-section (gmsh OCC).
                Used by the scikit-fem 2-D magnetostatics solver.
              </Typography>
            </Box>

            {/* mesh_size_mm */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
                  Max element size
                  <Tooltip title="Triangle edge length in open regions (mesh_size_mm)" placement="right">
                    <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={`${meshSizeMm.toFixed(1)} mm`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
              </Box>
              <Slider
                value={meshSizeMm} min={1.5} max={8} step={0.5}
                onChange={(_, v) => setMeshSizeMm(v as number)}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            {/* min_size_mm */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
                  Min element size
                  <Tooltip title="Lower bound on triangle size at fillets / thin features" placement="right">
                    <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={`${minSizeMm.toFixed(2)} mm`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: '#1e293b', color: '#94a3b8' }}/>
              </Box>
              <Slider
                value={minSizeMm} min={0.1} max={2.0} step={0.05}
                onChange={(_, v) => setMinSizeMm(v as number)}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            {/* Normal deviation — fillet-arc resolution (max angle per segment) */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
                  Normal deviation
                  <Tooltip title="Fillet-arc accuracy (Ansys Normal Deviation): max angle per segment of a rounded corner. Lower → more segments per fillet → smoother arc + finer mesh on the fillet. Drives the fillet vertex density (n_arc) in the geometry." placement="right">
                    <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={`${normalDev.toFixed(1)}°`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: '#1e293b', color: '#94a3b8' }}/>
              </Box>
              <Slider
                value={normalDev} min={2.0} max={20.0} step={0.5}
                onChange={(_, v) => setNormalDev(v as number)}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            {/* rotor angle */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>Rotor angle</Typography>
                <Chip label={`${rotorAngle.toFixed(1)}°`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: '#1e293b', color: '#94a3b8' }}/>
              </Box>
              <Slider
                value={rotorAngle} min={0} max={25.71} step={0.5}
                onChange={(_, v) => setRotorAngle(v as number)}
                sx={{ color: '#3b82f6' }}
              />
              <Typography sx={{ fontSize: 9, color: '#334155' }}>
                0…25.71° mech = one electrical period
              </Typography>
            </Box>

            <Divider sx={{ borderColor: '#1e293b' }}/>

            {/* ── Solver-domain section (Ansys-style) ────────────────────── */}
            <Box>
              <Typography sx={{ fontSize: '0.62rem', fontWeight: 700, color: '#475569',
                letterSpacing: '0.08em', textTransform: 'uppercase', mb: 0.5 }}>
                Solver Domain
              </Typography>
            </Box>

            {/* Outer air ring factor */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
                  Outer air ring
                  <Tooltip title="Extend mesh beyond stator OD so the Dirichlet A=0 far-field BC is applied on air, not iron. 1.0 = off; 1.3 ≈ Ansys Region Padding 30%." placement="right">
                    <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={outerAirFactor <= 1.001 ? 'off'
                  : `×${outerAirFactor.toFixed(2)}`} size="small"
                  sx={{ fontSize: 11, height: 20,
                    bgcolor: outerAirFactor > 1.001 ? '#1e3a5f' : '#1e293b',
                    color: outerAirFactor > 1.001 ? '#93c5fd' : '#94a3b8' }}/>
              </Box>
              <Slider
                value={outerAirFactor} min={1.0} max={2.0} step={0.05}
                onChange={(_, v) => setOuterAirFactor(v as number)}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            {/* Air-gap layers — element rows on EACH side of the slip midline */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
                  Air-gap layers (per side)
                  <Tooltip title="Element rows between the rotating slip midline and the iron on EACH side (stator and rotor). The sliding band splits the gap at the midline, so '2' = 2 rows you see between the midline and the stator (and 2 toward the rotor). 2 is the mesh-independent sweet spot for Maxwell-stress torque; 3 is plenty — more adds cost without accuracy." placement="right">
                    <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={`${gapLayers.toFixed(0)}/side`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
              </Box>
              <Slider
                value={gapLayers} min={1} max={3} step={1}
                marks onChange={(_, v) => setGapLayers(v as number)}
                sx={{ color: '#06b6d4' }}
              />
            </Box>

            {/* ── Per-component mesh size (mesh-convergence study) ───────── */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
                  Per-part element size (mm)
                  <Tooltip title="Target triangle size INSIDE each motor part. Empty = use the global Max size for that part. Set a finer/coarser value per part to study how mesh density changes the simulated torque/losses (mesh-convergence). Applies to both the mesh preview and the Simulation solve." placement="right">
                    <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                {Object.keys(componentMesh).length > 0 && (
                  <Chip label="reset" size="small" onClick={resetCompSizes}
                    sx={{ fontSize: 10, height: 18, bgcolor: '#3f1d1d', color: '#fca5a5',
                          cursor: 'pointer' }}/>
                )}
              </Box>
              <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0.6 }}>
                {MESH_COMPONENTS.map(({ key, label }) => (
                  <Box key={key} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Typography sx={{ fontSize: 11, color: '#cbd5e1', flex: 1 }}>
                      {label}
                    </Typography>
                    <TextField
                      type="text" size="small" placeholder="auto"
                      value={compDraft[key] ?? (componentMesh[key]?.toString() ?? '')}
                      onChange={e => setCompSize(key, e.target.value)}
                      inputProps={{ inputMode: 'decimal', style: {
                        padding: '2px 6px', fontSize: 11, width: 52,
                        color: '#e2e8f0', textAlign: 'right' } }}
                      sx={{ '& .MuiOutlinedInput-root': { bgcolor: '#0f172a',
                        '& fieldset': { borderColor: '#1e293b' } } }}
                    />
                  </Box>
                ))}
              </Box>
            </Box>

            {/* Symmetry sectors */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
                  Symmetry
                  <Tooltip title={`Split the motor into N equal wedges. ${symSlots} slots / ${symPoles} poles → GCD = ${symGcd} → valid models: ${validSectors.map(s => s === 1 ? 'Full' : '1/' + s).join(', ')} (each wedge needs whole slots AND poles). Anti-periodic BC on the radial cuts when poles-per-sector is odd.`} placement="right">
                    <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
              </Box>
              <ToggleButtonGroup
                value={nSectors} exclusive size="small" fullWidth
                onChange={(_, v) => v != null && setNSectors(v as number)}
                sx={{ width: '100%',
                  '& .MuiToggleButton-root': { flex: 1, py: 0.3,
                    fontSize: 11, color: '#64748b', borderColor: '#1e293b',
                    textTransform: 'none',
                    '&.Mui-selected': { color: '#e2e8f0', bgcolor: '#1e3a5f',
                      borderColor: '#3b82f6' } } }}>
                {validSectors.map(s => (
                  <ToggleButton key={s} value={s}>{s === 1 ? 'Full' : `1/${s}`}</ToggleButton>
                ))}
              </ToggleButtonGroup>
              {nSectors > 1 ? (
                <Typography sx={{ fontSize: 9, color: '#334155', mt: 0.5 }}>
                  {symSlots / nSectors} slots + {symPoles / nSectors} poles per sector ·
                  {(symPoles / nSectors) % 2 === 1 ? ' anti-periodic' : ' periodic'} BC on radial
                  cuts ({symPoles / nSectors} poles = {(symPoles / nSectors) % 2 === 1 ? 'odd' : 'even'})
                </Typography>
              ) : (
                <Typography sx={{ fontSize: 9, color: '#334155', mt: 0.5 }}>
                  Full 360° — stitched from clean half-sectors (no cuts, no double mesh).
                  Saved permanently &amp; used by Simulation + charts.
                </Typography>
              )}
            </Box>

            {/* Periodic (template-copy) mesh toggle */}
            <Box sx={{ mt: 1.5 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
                  Pole/slot mesh
                  <Tooltip title="Standard: each pole/slot meshed independently (slightly different node pattern → pole-to-pole mesh variance on the losses). Periodic: mesh ONE pole + ONE slot, rotate-copy them so every pole and slot is BIT-IDENTICAL → that variance is removed. Applies to the Mesh view, field views and Simulation." placement="right">
                    <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
              </Box>
              <ToggleButtonGroup
                value={poleCopy ? 'periodic' : 'standard'} exclusive size="small" fullWidth
                onChange={(_, v) => v != null && setPoleCopy(v === 'periodic')}
                sx={{ width: '100%',
                  '& .MuiToggleButton-root': { flex: 1, py: 0.3, fontSize: 11,
                    color: '#64748b', borderColor: '#1e293b', textTransform: 'none',
                    '&.Mui-selected': { color: '#e2e8f0', bgcolor: '#1e3a5f',
                      borderColor: '#3b82f6' } } }}>
                <ToggleButton value="standard">Standard</ToggleButton>
                <ToggleButton value="periodic">Periodic (identical poles)</ToggleButton>
              </ToggleButtonGroup>
              {poleCopy && (
                <Typography sx={{ fontSize: 9, color: '#34d399', mt: 0.5 }}>
                  Every pole + slot is a bit-identical rotated copy → zero pole-to-pole
                  mesh variance. Also drives the field views &amp; Simulation.
                </Typography>
              )}
            </Box>

            {/* Display toggles */}
            <Box>
              <Typography sx={{ fontSize: '0.62rem', fontWeight: 700, color: '#475569',
                letterSpacing: '0.08em', textTransform: 'uppercase', mb: 0.5 }}>
                Display
              </Typography>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
                {[
                  { label: 'Fill triangles by domain',  val: fillDomains,  set: setFillDomains  },
                  { label: 'Show CadQuery outlines',    val: showOutlines, set: setShowOutlines },
                  { label: 'Show triangle edges',       val: showEdges,    set: setShowEdges    },
                ].map(({ label, val, set }) => (
                  <Box key={label} onClick={() => set(!val)}
                    sx={{ display: 'flex', alignItems: 'center', gap: 1, cursor: 'pointer',
                      p: 0.5, borderRadius: 1,
                      bgcolor: val ? '#1e293b' : 'transparent',
                      border: '1px solid', borderColor: val ? '#334155' : 'transparent' }}>
                    <Box sx={{ width: 12, height: 12, borderRadius: 0.5, flexShrink: 0,
                      bgcolor: val ? '#3b82f6' : '#1e293b',
                      border: '1px solid #334155' }}/>
                    <Typography sx={{ fontSize: 11,
                      color: val ? '#e2e8f0' : '#475569' }}>{label}</Typography>
                  </Box>
                ))}
              </Box>
            </Box>

            <Button
              variant="outlined" fullWidth
              startIcon={femLoading ? <CircularProgress size={14} color="inherit"/> : <RefreshIcon/>}
              onClick={rebuildMesh}
              disabled={femLoading}
              sx={{ py: 0.8, fontSize: 11, color: '#3b82f6', borderColor: '#1e3a5f' }}
            >
              {femLoading ? 'Building…' : 'Rebuild mesh'}
            </Button>

            {femError && <Alert severity="error" sx={{ fontSize: 11 }}>{femError}</Alert>}

            {femMesh && (
              <Box sx={{ bgcolor: '#0a1628', borderRadius: 1, p: 1, border: '1px solid #1e293b' }}>
                <Typography sx={{ fontSize: 10, color: '#475569', mb: 0.5 }}>Mesh stats</Typography>
                <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Typography sx={{ fontSize: 11, color: '#94a3b8' }}>vertices</Typography>
                  <Typography sx={{ fontSize: 11, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
                    {femMesh.n_vertices.toLocaleString()}
                  </Typography>
                </Box>
                <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Typography sx={{ fontSize: 11, color: '#94a3b8' }}>triangles</Typography>
                  <Typography sx={{ fontSize: 11, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
                    {femMesh.n_triangles.toLocaleString()}
                  </Typography>
                </Box>
              </Box>
            )}

            <Divider sx={{ borderColor: '#1e293b' }}/>
          </>
        )}

        {view === 'pinn' && (
          <>
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 0.5 }}>
            Collocation Points
          </Typography>
          <Typography sx={{ fontSize: 11, color: '#334155' }}>
            PINN samples random points inside each domain. Higher density →
            better accuracy, slower training.
          </Typography>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* n_radial */}
        <Box>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
              Radial layers
              <Tooltip title="Number of concentric circles of sample points per domain" placement="right">
                <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_radial} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
          </Box>
          <Slider
            value={cfg.n_radial} min={2} max={30} step={1}
            onChange={(_, v) => setCfg(c => ({ ...c, n_radial: v as number }))}
            sx={{ color: '#3b82f6' }}
          />
        </Box>

        {/* n_angular */}
        <Box>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
              Angular divisions
              <Tooltip title="Points around each radial ring in the main domains" placement="right">
                <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_angular} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
          </Box>
          <Slider
            value={cfg.n_angular} min={8} max={256} step={8}
            onChange={(_, v) => setCfg(c => ({ ...c, n_angular: v as number }))}
            sx={{ color: '#3b82f6' }}
          />
        </Box>

        {/* n_angular_slots */}
        <Box>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
              Slot angular divisions
              <Tooltip title="Denser angular sampling inside slots/magnets for accuracy" placement="right">
                <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_angular_slots} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
          </Box>
          <Slider
            value={cfg.n_angular_slots} min={2} max={64} step={2}
            onChange={(_, v) => setCfg(c => ({ ...c, n_angular_slots: v as number }))}
            sx={{ color: '#f59e0b' }}
          />
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Point count table */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Estimated Sample Points
          </Typography>

          {DOMAINS.map(d => (
            <Box key={d.key} sx={{ display: 'flex', alignItems: 'center',
              justifyContent: 'space-between', py: 0.35 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: d.color, flexShrink: 0 }}/>
                <Typography sx={{ fontSize: 11, color: '#64748b' }}>{d.label}</Typography>
              </Box>
              <Typography sx={{ fontSize: 11, fontWeight: 600, color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                {(pointsPerDomain[d.key] ?? 0).toLocaleString()}
              </Typography>
            </Box>
          ))}

          <Divider sx={{ borderColor: '#1e293b', my: 0.75 }}/>
          <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
            <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#94a3b8' }}>Total</Typography>
            <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
              {totalPoints.toLocaleString()}
            </Typography>
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        <Button
          variant="contained" color="primary" fullWidth
          startIcon={saving ? <CircularProgress size={14} color="inherit"/> : <SaveIcon/>}
          onClick={handleSave}
          disabled={saving}
          sx={{ py: 1.1, fontWeight: 700, letterSpacing: 1 }}
        >
          {saving ? 'SAVING…' : saved ? 'SAVED ✓' : 'SAVE TO CONFIG'}
        </Button>

        {error && <Alert severity="error" sx={{ fontSize: 11 }}>{error}</Alert>}
          </>
        )}
      </Box>

      {/* ── RIGHT: preview ── */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', p: 3, gap: 2,
        overflow: 'auto' }}>

        <Box>
          <Typography variant="h6" sx={{ color: '#e2e8f0', fontWeight: 700, mb: 0.5 }}>
            {view === 'fem' ? '2-D FEM Triangle Mesh' : 'Collocation Sampling Grid'}
          </Typography>
          <Typography sx={{ fontSize: 12, color: '#475569' }}>
            {view === 'fem'
              ? 'Conforming triangle mesh of the actual CadQuery cross-section. Each colour = one motor domain.'
              : 'Live preview of point distribution used during PINN training. Each coloured ring corresponds to one domain.'}
          </Typography>
        </Box>

        {/* Legend */}
        {view === 'fem' && femMesh && (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
            {Object.entries(femMesh.domain_counts).map(([name, count]) => {
              // find rgba from DOMAIN_NAMES → id
              const id = Number(Object.keys(DOMAIN_NAMES).find(
                k => DOMAIN_NAMES[Number(k)].toLowerCase() === name.replace('_', ' ').toLowerCase()
                  || (name === 'magnet_N' && Number(k) === 4)
                  || (name === 'magnet_S' && Number(k) === 44)
                  || (name === 'airgap'   && Number(k) === 3)
                  || (name === 'coil'     && Number(k) === 2)
              ));
              const rgba = DOMAIN_RGBA[id] ?? [100, 100, 100, 255];
              return (
                <Chip key={name} label={`${DOMAIN_NAMES[id] ?? name} · ${count.toLocaleString()}`}
                  size="small" sx={{
                    fontSize: 10, height: 20,
                    bgcolor: `rgba(${rgba[0]},${rgba[1]},${rgba[2]},0.18)`,
                    color: `rgb(${rgba[0]},${rgba[1]},${rgba[2]})`,
                    border: `1px solid rgba(${rgba[0]},${rgba[1]},${rgba[2]},0.5)`,
                  }}/>
              );
            })}
          </Box>
        )}
        {view === 'pinn' && (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
            {DOMAINS.map(d => (
              <Chip key={d.key} label={d.label} size="small" sx={{
                fontSize: 10, height: 20,
                bgcolor: `${d.color}20`, color: d.color,
                border: `1px solid ${d.color}40`,
              }}/>
            ))}
          </Box>
        )}

        {/* Preview pane */}
        <Paper sx={{ flex: 1, bgcolor: '#060d17', border: '1px solid #1e293b',
          borderRadius: 2, overflow: 'hidden', display: 'flex',
          alignItems: 'center', justifyContent: 'center', minHeight: 540,
          position: 'relative' }}>
          {view === 'fem' ? (
            <>
              {femLoading && (
                <Box sx={{ position: 'absolute', inset: 0, display: 'flex',
                  alignItems: 'center', justifyContent: 'center',
                  bgcolor: 'rgba(6,13,23,0.65)', zIndex: 5 }}>
                  <CircularProgress size={32} sx={{ color: '#3b82f6' }}/>
                </Box>
              )}
              {/* position:absolute + inset:0 gives r3f a CONCRETE pixel-sized
                  parent (the relative Paper) — a height:100% chain against a
                  flex/align-center parent can resolve to 0 at the moment r3f
                  measures, leaving the canvas stuck at the 300×150 default. */}
              <Box sx={{ position: 'absolute', inset: 0 }}>
                {WEBGL_OK ? (
                  <FemMeshViewer3D payload={femMesh as any}
                    showFill={fillDomains}
                    showWire={showEdges}
                    showOutlines={showOutlines}
                    showGrid/>
                ) : (
                  <FemMeshViewer2D payload={femMesh as any} showWire={showEdges}/>
                )}
              </Box>
              {!WEBGL_OK && (
                <Box sx={{ position: 'absolute', top: 8, left: 8, zIndex: 4,
                  bgcolor: 'rgba(10,22,40,0.8)', px: 1, py: 0.5, borderRadius: 1,
                  pointerEvents: 'none' }}>
                  <Typography sx={{ fontSize: 9, color: '#fbbf24' }}>
                    2D view (WebGL unavailable in this window) · wheel = zoom · drag = pan
                  </Typography>
                </Box>
              )}
              {/* Orientation cube + XYZ axes — same component as Geometry */}
              <Viewcube/>
              {/* Help text overlay */}
              <Box sx={{ position: 'absolute', left: 8, bottom: 8, zIndex: 4,
                bgcolor: 'rgba(10,22,40,0.7)', px: 1, py: 0.5, borderRadius: 1,
                pointerEvents: 'none' }}>
                <Typography sx={{ fontSize: 9, color: '#94a3b8' }}>
                  Drag = orbit · Right-drag / Shift+drag = pan · Wheel = zoom
                </Typography>
              </Box>
            </>
          ) : (
            <CollocationPreview cfg={cfg} geo={geo}/>
          )}
        </Paper>

        {view === 'fem' && femMesh && (
          <Typography sx={{ fontSize: 10, color: '#475569', textAlign: 'center' }}>
            {femMesh.note}
          </Typography>
        )}
      </Box>
    </Box>
  );
};

export default MeshPanel;
