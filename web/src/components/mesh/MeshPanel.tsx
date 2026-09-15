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
  Paper, Slider, Switch, TextField, Tooltip, Typography, ToggleButton, ToggleButtonGroup,
} from '@mui/material';

// Per-component mesh-size controls (study mesh-density effect on results).
// Keys MUST match the backend _comp_of() mapping in build_mesh_from_polygons.
const MESH_COMPONENTS: { key: string; label: string }[] = [
  { key: 'stator', label: 'Stator iron' },
  { key: 'rotor',  label: 'Rotor iron' },
  { key: 'magnet', label: 'Magnets' },
  { key: 'outer',  label: 'Outer air' },
];
// Dropped from the grid, scrubbed from stored state on mount:
//  - 'coil' (Windings, mm): superseded by the Wire cell factor below — a stale
//    stored mm value would silently pin the copper with no field showing it;
//  - 'shaft': the geo path cannot honour it (-Y cut chains make the area
//    constraint unsatisfiable), so the request silently swapped the WHOLE
//    build onto the gmsh mesher — losing the template iron and the wire patch
//    over one innocuous field (measured: coil 3024 -> 1008 tris).  The API
//    still accepts both keys for diagnostics.
const DROPPED_MESH_KEYS = ['coil', 'shaft'];
// "Wire cell" — the copper cell size as a FACTOR of the wire height h, carried
// in the SAME componentMesh block (so duty save/restore and the per-die
// settings memory pick it up for free).  1h is the backend default and is
// stored as "no key", keeping the canonical mesh byte-identical.
const WIRE_CELL_KEY = 'coil_rel';
// THE DIE'S MEMORY FOLLOWS THE SCREEN.  It used to be written only on leaving
// a die or saving a duty, so a duty (re)load in between put the OLDER memory
// back over the user's live edits — the wire cell went 2h → 1h by itself
// (user 2026-09-08: "кто опять поменял это, у меня было всегда 2h").  Every
// mesh.* write now refreshes the active die's memory, debounced.
import { rememberDieSettings } from '../../lib/dieSettings';
import { activeDuty } from '../../lib/dutySettings';
let _rememberTimer: ReturnType<typeof setTimeout> | null = null;
function rememberActiveDieSoon(): void {
  if (_rememberTimer) clearTimeout(_rememberTimer);
  _rememberTimer = setTimeout(() => {
    _rememberTimer = null;
    try { const a = activeDuty(); if (a?.die) rememberDieSettings(a.die); }
    catch { /* memory is a convenience, never a blocker */ }
  }, 800);
}
const WIRE_CELL_OPTIONS: { v: number; label: string }[] = [
  { v: 0.5, label: '½h' },
  { v: 1,   label: '1h' },
  { v: 2,   label: '2h' },
];
import SaveIcon from '@mui/icons-material/Save';
import FemMeshViewer3D from './FemMeshViewer3D';
import FemMeshViewer2D from './FemMeshViewer2D';
import { syncActiveMotor } from '../common/motorSettings';
import HelpTip from '../common/HelpTip';
import {
  adoptMeshConfig, configRetryDelayMs, decideMeshSave, MESH_CONFIG_KEYS,
  type MeshConfigKey, type MeshSettings,
} from './meshSaveContract';

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

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

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
  effective_mesh_size_mm?: number;   // what the iron was ACTUALLY meshed at (feature/4 floor)
  feature_floor_mm?: number | null;  // the smallest-feature/4 quality floor
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
  8:  [80,  90,  110, 200],   // outer air — SAME colour as air: it is the same
                              // substance, and two blues implied two materials
                              // (user request)
  9:  [63,  174, 90,  240],   // insulation     green   (Nomex/ceramic)
  10: [217, 138, 58,  240],   // wire enamel    orange  (polyimide)
  44: [239, 68,  68,  240],   // magnet S       red
};
const DOMAIN_NAMES: Record<number, string> = {
  0: 'Air',     1: 'Stator',  2: 'Winding',  3: 'Air gap',
  4: 'Magnet N', 5: 'Rotor',  6: 'Shaft',
  7: 'Band',    8: 'Outer air',
  9: 'Insulation', 10: 'Wire enamel',
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
  { key: 'air_gap',     label: 'Air Gap',     color: 'var(--text-2)' },
  { key: 'rotor_core',  label: 'Rotor Core',  color: '#2563eb' },
  { key: 'magnet',      label: 'Magnets',     color: '#ef4444' },
  { key: 'slot',        label: 'Windings',    color: '#f59e0b' },
  { key: 'shaft',       label: 'Shaft',       color: 'var(--text-3)' },
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
        style={{ borderRadius: 12, border: '1px solid var(--line-soft)', display: 'block' }}
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
      ...ringDots(r.sh, r.ri, nR, nA, 'var(--text-3)', 'shaft'),
      ...ringDots(r.ri, r.ro, nR, nA, '#3b82f6', 'rotor'),
      ...ringDots(r.ro, r.si, nR, nS, '#f59e0b', 'gap'),
      ...ringDots(r.si, r.so, nR, nA, '#2563eb', 'stator'),
    ];
  }, [r, cfg, ringDots]);

  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
      <svg width={SVG_SIZE} height={SVG_SIZE} style={{ background: 'var(--panel-2)', borderRadius: 12 }}>
        {r && <>
          {/* domain annuli */}
          <circle cx={CX} cy={CY} r={r.so} fill="none" stroke="var(--panel)" strokeWidth={1}/>
          <circle cx={CX} cy={CY} r={r.si} fill="none" stroke="var(--line)" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.ro} fill="none" stroke="var(--line)" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.ri} fill="none" stroke="var(--line)" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.sh} fill="none" stroke="var(--panel)" strokeWidth={0.8}/>
          {/* filled regions */}
          <circle cx={CX} cy={CY} r={r.so} fill="var(--panel)22"/>
          <circle cx={CX} cy={CY} r={r.si} fill="var(--panel-2)"/>
          <circle cx={CX} cy={CY} r={r.ri} fill="var(--line-accent)22"/>
          <circle cx={CX} cy={CY} r={r.sh} fill="var(--app-bg)"/>
        </>}
        {dots}
        {/* center cross */}
        <line x1={CX - 6} y1={CY} x2={CX + 6} y2={CY} stroke="var(--line)" strokeWidth={0.8}/>
        <line x1={CX} y1={CY - 6} x2={CX} y2={CY + 6} stroke="var(--line)" strokeWidth={0.8}/>
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
  const [view] = useState<'fem' | 'pinn'>('fem');   // always the real FEM mesh; no view toggle

  // ── Load/save contract (user, 2026-09-07) ────────────────────────────────
  // "захожу в Mesh и опять не сохранено то, что было до этого — там точно
  //  стояло 1/2; почему параметры опять не сохраняются?"
  // Sequence: 09:0x config.yaml mesh.n_sectors: 2 → 09:06:45 the API restarts
  // while the app is open → 09:2x GET /api/mesh/config answers n_sectors 1 /
  // outer_air_factor 1.3, i.e. this panel's CONSTANT defaults → 09:25:41 the
  // user re-sets 1/2 by hand.  The browser pane had lost its localStorage, so
  // the panel painted constants and a save path that was not gated on the
  // config load wrote them to the server.
  // The contract now, in three lines:
  //   1. the SERVER config is the single source of truth; localStorage is only
  //      a cache for instant paint and is overwritten by every successful load;
  //   2. nothing is saved (and no mesh is built) until that load succeeds —
  //      failures retry with backoff, saves stay blocked meanwhile;
  //   3. a PATCH carries ONLY the settings the user changed in this session
  //      (per-setting dirty flags) — a constant default can never reach the
  //      server again.
  const [cfgLoaded,  setCfgLoaded]  = useState(false);   // GET /api/mesh/config answered
  const [dirty, setDirty] = useState<ReadonlySet<MeshConfigKey>>(() => new Set());
  const markDirty = useCallback((k: MeshConfigKey) => {
    setDirty(prev => (prev.has(k) ? prev : new Set(prev).add(k)));
  }, []);

  // ── Persisted mesh settings (survive tab switches) ──────────────────────
  // Hook: each setting reads its initial value from localStorage — a per-browser
  // CACHE for instant paint only, never an authority: the mount load below
  // overwrites it with the server's value.  Default symmetry is Full (full disk)
  // per user request.
  const usePersisted = <T,>(key: string, def: T) => {
    const [v, setV] = useState<T>(() => {
      try {
        const raw = localStorage.getItem(`mesh.${key}`);
        return raw == null ? def : (JSON.parse(raw) as T);
      } catch { return def; }
    });
    useEffect(() => {
      try { localStorage.setItem(`mesh.${key}`, JSON.stringify(v)); } catch {}
      rememberActiveDieSoon();
    }, [key, v]);
    // Re-read after a duty load restores saved mesh settings (same contract
    // as SimulationPanel's twin — see 'sim-settings-restored' there).
    useEffect(() => {
      const onRestore = () => {
        try {
          const raw = localStorage.getItem(`mesh.${key}`);
          if (raw != null) setV(JSON.parse(raw) as T);
        } catch { /* keep current */ }
      };
      window.addEventListener('sim-settings-restored', onRestore);
      return () => window.removeEventListener('sim-settings-restored', onRestore);
    }, [key]);
    return [v, setV] as const;
  };

  const [meshSizeMm,  setMeshSizeMm]  = usePersisted<number>('meshSize',   4.0);
  const [minSizeMm,   setMinSizeMm]   = usePersisted<number>('minSize',    0.3);
  // Fillet-arc resolution (Ansys "Normal Deviation"): max angle per fillet
  // segment. Lower → more segments per rounded corner → smoother fillet + finer
  // mesh there. Wired to n_arc in the geometry (get_2d_polygons).
  // Fillet-arc resolution for the PREVIEW build only. It was a slider, and a
  // misleading one: the transient does not take this parameter at all — its mesh
  // call hard-codes 8 deg — so moving it changed the picture and never the
  // result. Fixed at the solver's own value so preview and solve agree.
  const normalDev = 8.0;
  // 'Surface deviation' control removed: any value >0.01 mm Douglas-Peucker-
  // flattened the rounded rotor-tooth / fillet arcs into straight chords, so
  // the Mesh no longer matched the real geometry. The Mesh now always uses the
  // real geometry (tol 0.005 mm, sent below); density is set by Max/Min size.
  const [rotorAngle,  setRotorAngle]  = usePersisted<number>('rotorAngle', 0.0);
  // ── Solver-domain extensions (Ansys-style) ───────────────────────────────
  const [outerAirFactor, setOuterAirFactor] = usePersisted<number>('outerAir', 1.3);
  const [nSectors,       setNSectors]       = usePersisted<number>('nSectors', 1);   // Full (full disk) by default
  // Air-gap element rows PER SIDE of the slip midline (1-3, default 2). The
  // value persists in config.yaml (loaded below, clamped to the new 1-3 range).
  const [gapLayers,      setGapLayers]      = usePersisted<number>('gapLayers', 2);
  // Bit-identical pole/slot mesh (template-copy): mesh ONE pole + ONE slot and
  // rotate-copy them so every pole/slot is identical → no pole-to-pole mesh
  // variance.  Read by the field & simulation fetches too (mesh.poleCopy).
  const [poleCopy,       setPoleCopy]       = usePersisted<boolean>('poleCopy', false);
  // Structured (concentric-ring) air gap — ALWAYS ON, no longer a choice.
  // The Free/Structured chooser was a lie: every consumer read it as
  // `structuredGap || ironTemplate`, and template iron defaults on, so the belt
  // was forced regardless of what the button showed. Kept as state (not a
  // constant) because the toggles below still set it and the request builders
  // still read it.
  const [structuredGap,  setStructuredGap]  = usePersisted<boolean>('structuredGap', true);
  // (There is no element-order toggle any more. Second-order (P2) elements are
  // the calculation basis — B linear per element, so the torque is smooth like
  // ANSYS instead of carrying the P1 sliding-band staircase, and the mean is
  // energy-consistent. P1 was deleted: it over-read the mean torque ~35 % and
  // its ripple was a mesh artefact, so "off" meant "give me the wrong number".)
  // Deterministic template iron: stator/rotor iron meshed by the structured
  // slot/pole unit templates (real CadQuery contours via tags + node snap)
  // instead of gmsh free triangulation — build-to-build deterministic results.
  // Falls back to gmsh automatically when the topology doesn't fit the units.
  const [ironTemplate,   setIronTemplate]   = usePersisted<boolean>('ironTemplate', true);
  // Geometry-driven mesh: triangulate the REAL CadQuery polygons (every fillet)
  // with a constrained Delaunay instead of warping a tensor template — the mesh
  // conforms to magnet corners, tooth-tip r1 and the V-notch apex by
  // construction.  Full-ring only for now (a 1/N request builds the full disk).
  const [geoMesh,        setGeoMesh]        = usePersisted<boolean>('geoMesh', true);
  // Template halves end exactly ON the iron circles, so ONLY the structured
  // belt meshes the air gap between them.  Keep the two coupled on EVERY render
  // (not just on toggle) — otherwise a persisted {template ON, Free gap} state
  // rebuilds with a black, unmeshed gap.  Runs on mount too.
  useEffect(() => {
    if (ironTemplate && !structuredGap) setStructuredGap(true);
  }, [ironTemplate, structuredGap, setStructuredGap]);
  // Applying a descent design restores ITS eval params: restoreDescentEvalParams
  // (motorStore) writes mesh.nSectors/gapLayers/meshSize/minSize/poleCopy straight
  // to localStorage — but this panel seeded its state ONCE at mount, so it kept
  // DISPLAYING the pre-apply values while every solve already read the new ones
  // (until a reload).  Adopt the event's values live, same pattern as the
  // SimulationPanel listener.  Setters are the usePersisted ones, so state and
  // localStorage stay one value (re-writing the same value is a no-op).
  // Applying a design is a USER action (they pressed Apply), so the adopted
  // values are marked dirty and do get persisted — unlike the mount adoption
  // from the server, which must never trigger a save (2026-09-07 incident).
  useEffect(() => {
    const onEval = (e: Event) => {
      const p = (e as CustomEvent).detail || {};
      if (typeof p.n_sectors    === 'number') { setNSectors(p.n_sectors);       markDirty('n_sectors'); }
      if (typeof p.gap_layers   === 'number') { setGapLayers(p.gap_layers);     markDirty('gap_layers'); }
      if (typeof p.mesh_size_mm === 'number') { setMeshSizeMm(p.mesh_size_mm);  markDirty('mesh_size_mm'); }
      if (typeof p.min_size_mm  === 'number') { setMinSizeMm(p.min_size_mm);    markDirty('min_size_mm'); }
      if (typeof p.pole_copy    === 'boolean') setPoleCopy(p.pole_copy);   // localStorage-only setting
    };
    window.addEventListener('descent-eval-params', onEval as EventListener);
    return () => window.removeEventListener('descent-eval-params', onEval as EventListener);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Loading a motor / duty / stored run writes that snapshot's mesh.* keys and
  // fires 'sim-settings-restored'; the usePersisted hooks above adopt them.
  // That is a USER action (they pressed ▶), and the sweep/optimizer read the
  // mesh block from config.yaml, so the restored values must reach it — mark
  // the config-owned settings dirty.  Restored values are real saved settings,
  // never the constant defaults this gate exists to stop.
  useEffect(() => {
    const onRestore = () => setDirty(prev => {
      const next = new Set(prev);
      for (const k of MESH_CONFIG_KEYS) next.add(k);
      return next;
    });
    window.addEventListener('sim-settings-restored', onRestore);
    return () => window.removeEventListener('sim-settings-restored', onRestore);
  }, []);
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
  // A stored 'coil'/'shaft' value would keep steering solves with no field
  // left to show it (the solve paths read mesh.componentMesh directly) — scrub
  // them from the stored block on mount and whenever a duty restore writes the
  // block back (duties saved before the removal still carry them).
  useEffect(() => {
    const scrub = () => setComponentMesh(prev => {
      if (!DROPPED_MESH_KEYS.some(k => k in prev)) return prev;
      const next = { ...prev };
      for (const k of DROPPED_MESH_KEYS) delete next[k];
      return next;
    });
    scrub();
    window.addEventListener('sim-settings-restored', scrub);
    return () => window.removeEventListener('sim-settings-restored', scrub);
  }, [setComponentMesh]);
  // Wire cell (½h / 1h / 2h).  A FACTOR of each wire's own height, so it stays
  // meaningful after a wire_height edit.
  const wireCell = (componentMesh[WIRE_CELL_KEY] as number | undefined) ?? 1;
  const setWireCell = (v: number) => setComponentMesh(prev => {
    const next = { ...prev };
    if (v === 1) delete next[WIRE_CELL_KEY];   // 1h == the canonical default
    else next[WIRE_CELL_KEY] = v;
    return next;
  });
  // Only positive sizes reach the backend; "{}" means global everywhere.
  const componentMeshJson = JSON.stringify(
    Object.fromEntries(Object.entries(componentMesh).filter(([, v]) => v > 0)));
  // NOTE: the old "Extra fillet smoothing" control was removed — it applied a
  // Shapely buffer on top of the CadQuery fillets, deforming the Mesh geometry
  // away from the Geometry tab.  The Mesh now always uses the native geometry
  // (stator_fillet_mm = 0), identical to the Geometry tab.
  // Display is fixed: the mesh (domain fill + triangle edges + geometry outlines)
  // is ALWAYS shown — no toggles.
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
      structured_gap:    structuredGap ? 'true' : 'false',   // ANSYS-style concentric-ring gap
      iron_template:     ironTemplate ? 'true' : 'false',    // deterministic template iron
      geo_mesh:          geoMesh ? 'true' : 'false',         // geometry-driven CDT (real fillets)
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
      outerAirFactor, gapLayers, nSectors, componentMeshJson, poleCopy, structuredGap,
      ironTemplate, geoMesh]);

  // ── Max-element-size bounds = the feature/4 quality floor ─────────────────
  // Above the floor the solver clamps the iron anyway (those slider positions
  // were dead — the "Max size doesn't change the mesh" report).  Bounding the
  // slider to the floor makes its ENTIRE range live.  Floor comes from the last
  // mesh build (feature_floor_mm); fall back to the old fixed range until then.
  const _floor = (femMesh?.feature_floor_mm && femMesh.feature_floor_mm > 0.4)
    ? femMesh.feature_floor_mm : null;
  const meshMax  = _floor ?? 8;
  const meshMin  = _floor ? Math.max(0.2, +(_floor / 6).toFixed(2)) : 1.5;
  const meshStep = _floor ? Math.max(0.05, +((meshMax - meshMin) / 18).toFixed(2)) : 0.5;
  // Snap a persisted value sitting above the floor down onto it, so the Chip and
  // the transient solve use the size that is ACTUALLY meshed (not a dead 8 mm).
  // NOT marked dirty: this is the mesher clamping the user's value, not the user
  // changing it — machine-driven corrections must never write to the server
  // (2026-09-07 incident).  The clamp still applies to every build.
  useEffect(() => {
    if (_floor && meshSizeMm > _floor + 1e-6) setMeshSizeMm(+_floor.toFixed(2));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [_floor]);

  // ── Load the server config on mount — with retry, and NO fallback ─────────
  // The server config is the single source of truth.  Until it answers:
  //   • the settings are shown in a loading state and cannot be edited,
  //   • no PATCH can fire (decideMeshSave refuses on serverLoaded === false),
  //   • no mesh is built — the old `.catch(() => fetchFemMesh())` built the
  //     preview from CONSTANT defaults while the config was unknown, which is
  //     how a 1/2 machine came up as "Full" on 2026-09-07.
  // A restarting API (09:06:45 that day) is retried at 1, 2, 4, 8, 16, 32, 60 s.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    const load = () => {
      fetch(`${API}/api/mesh/config`)
        .then(async r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(d => {
          if (!alive) return;
          setCfg(d);
          // The PERSISTED FEM mesh settings (config.yaml) WIN over the
          // per-browser localStorage cache — the sliders are then identical in
          // every session and every browser.  A difference here just means the
          // cache is stale; the server wins silently (dev-only log).
          const srv = adoptMeshConfig(d);
          if (import.meta.env.DEV) {
            const cache: MeshSettings = {
              mesh_size_mm: meshSizeMm, min_size_mm: minSizeMm,
              outer_air_factor: outerAirFactor, gap_layers: gapLayers,
              n_sectors: nSectors,
            };
            const diff = Object.entries(srv)
              .filter(([k, v]) => cache[k as MeshConfigKey] !== v)
              .map(([k, v]) => `${k}: ${cache[k as MeshConfigKey]} → ${v}`);
            if (diff.length) console.info('[mesh] server config wins over the local cache —', diff.join(', '));
          }
          if (srv.mesh_size_mm     !== undefined) setMeshSizeMm(srv.mesh_size_mm);
          if (srv.min_size_mm      !== undefined) setMinSizeMm(srv.min_size_mm);
          if (srv.outer_air_factor !== undefined) setOuterAirFactor(srv.outer_air_factor);
          if (srv.gap_layers       !== undefined) setGapLayers(srv.gap_layers);
          // normal_deviation is FIXED at the solver's 8° (see const above) — the
          // setter is gone, and calling it here threw a ReferenceError that aborted
          // this handler mid-way (n_sectors never adopted, the gate never set,
          // initial mesh built by the .catch instead).
          if (srv.n_sectors        !== undefined) setNSectors(srv.n_sectors);
          setCfgLoaded(true);   // editing + saving unlocked ONLY here
          // Build the initial mesh with the JUST-LOADED config values (not the stale
          // defaults) so the displayed mesh matches the Symmetry toggle on first open.
          fetchFemMesh({
            mesh_size_mm:     srv.mesh_size_mm,
            min_size_mm:      srv.min_size_mm,
            normal_deviation: typeof d.normal_deviation === 'number' ? d.normal_deviation : undefined,
            outer_air_factor: srv.outer_air_factor,
            gap_layers:       srv.gap_layers,
            n_sectors:        srv.n_sectors,
          });
        })
        .catch(() => {
          if (!alive) return;
          timer = setTimeout(load, configRetryDelayMs(attempt));
          attempt += 1;
        });
    };
    load();

    fetch(`${API}/api/geometry/summary`)
      .then(r => r.json())
      .then(d => { if (alive) setGeo(d); })
      .catch(() => {});
    return () => { alive = false; if (timer) clearTimeout(timer); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── One-click symmetry switch ───────────────────────────────────────────
  // The Full / 1/2 / 1/4 toggle rebuilds the preview immediately.  Persisting is
  // NOT done here any more: this effect fires for every source of an nSectors
  // change — including the mount adoption and the validity snap below — and it
  // called saveMeshConfig() UNGATED by the config load.  Under React StrictMode
  // the mount effects run twice with the refs preserved, so the second pass took
  // the `symFirstRun.current === false` branch and PATCHed whatever the panel
  // held at that instant: with an empty localStorage, the constant defaults
  // (n_sectors 1, outer_air 1.3) — exactly what the server answered at 09:2x on
  // 2026-09-07 after the 09:06:45 API restart.  The toggle's own onChange now
  // marks the setting dirty and the debounced saver below writes it.
  const symFirstRun = useRef(true);
  useEffect(() => {
    if (!cfgLoaded) return;            // never build from constants
    if (symFirstRun.current) { symFirstRun.current = false; return; }
    fetchFemMesh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfgLoaded, nSectors]);

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
  // If the loaded nSectors is invalid for this motor, snap to Full (always valid).
  // NOT marked dirty — a machine-driven correction, so it changes the preview but
  // never writes to config (2026-09-07: automatic writes are what lost the 1/2).
  useEffect(() => {
    if (geo && !validSectors.includes(nSectors)) {
      setNSectors(validSectors[0]);   // = 1 (Full) — validSectors always includes 1
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
    if (!cfgLoaded) return;            // never build from constants
    if (poleCopyFirstRun.current) { poleCopyFirstRun.current = false; return; }
    fetchFemMesh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfgLoaded, poleCopy]);

  // Auto-rebuild on EVERY mesh-affecting setting, debounced ~450 ms after the
  // last change.  Sliders coalesce a drag into one build; the pipeline
  // toggles (geo mesh / iron template / structured gap) and the per-part
  // element sizes used to re-mesh only on the "Rebuild mesh" button, which
  // made a silently stale preview — the button is gone, this effect is the
  // whole contract now.  First run skipped (mount already builds).
  const densityFirstRun = useRef(true);
  useEffect(() => {
    if (!cfgLoaded) return;            // config unknown → never build from constants
    if (densityFirstRun.current) { densityFirstRun.current = false; return; }
    const id = setTimeout(() => fetchFemMesh(), 450);
    return () => clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfgLoaded, meshSizeMm, minSizeMm, normalDev, gapLayers, outerAirFactor,
      ironTemplate, geoMesh, structuredGap, componentMeshJson]);

  // ── Persist to config.yaml — user changes only ───────────────────────────
  // The body carries ONLY the settings the user moved in this session (dirty
  // flags).  Everything else is omitted, so the server keeps the value this
  // browser adopted from it — a value the panel never learned (empty
  // localStorage + an API restart, 2026-09-07) can no longer be overwritten
  // with a constant default.  normal_deviation is a const here, never a user
  // setting, so it is not sent at all any more.
  const patchMeshConfig = useCallback((patch: Partial<MeshSettings>) => {
    if (Object.keys(patch).length === 0) return;
    fetch(`${API}/api/mesh/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }).catch(() => {});
  }, []);

  // Debounced save of the user's edits.  Two gates, both required: the config
  // must have loaded, and the user must have changed something in this session.
  // No mount/adoption effect can reach this — that is the whole point.
  const pendingSave = useRef<Partial<MeshSettings> | null>(null);
  useEffect(() => {
    if (!cfgLoaded || dirty.size === 0) return;
    const decision = decideMeshSave({
      mesh_size_mm: meshSizeMm, min_size_mm: minSizeMm,
      outer_air_factor: outerAirFactor, gap_layers: gapLayers, n_sectors: nSectors,
    }, dirty, cfgLoaded);
    if (!decision.save) {
      if (import.meta.env.DEV) console.info('[mesh] save skipped —', decision.reason);
      return;
    }
    pendingSave.current = decision.patch;
    const id = setTimeout(() => {
      pendingSave.current = null;
      patchMeshConfig(decision.patch);
      syncActiveMotor();
    }, 700);
    return () => clearTimeout(id);
  }, [patchMeshConfig, cfgLoaded, dirty,
      meshSizeMm, minSizeMm, outerAirFactor, gapLayers, nSectors]);

  // The Mesh tab is NOT keepMounted (App.tsx): leaving it unmounts the panel and
  // clears the 700 ms debounce above.  Flush whatever was still pending, or an
  // edit made just before switching tabs is silently dropped — the same "why are
  // my parameters not saved again?" the user reported on 2026-09-07.
  useEffect(() => () => {
    if (pendingSave.current) { patchMeshConfig(pendingSave.current); pendingSave.current = null; }
  }, [patchMeshConfig]);

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
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden', bgcolor: 'var(--panel-2)' }}>

      {/* ── LEFT: controls ── */}
      <Box sx={{
        width: 320, flexShrink: 0, overflowY: 'auto',
        borderRight: '1px solid var(--line-soft)', p: 2,
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>

        {view === 'fem' && (
          <>
            {/* One short line + tooltip (UI rule).  Shown while the server
                config is unknown: the settings are dimmed and locked, nothing
                is saved and no mesh is built — the state that used to silently
                fall back to constants (2026-09-07). */}
            {!cfgLoaded && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <CircularProgress size={12} sx={{ color: '#3b82f6' }}/>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  Mesh settings: waiting for the API
                </Typography>
                <HelpTip title="These settings live in the server config (motor_config.yaml), which is the single source of truth. The panel shows and saves them only after the API answers — retrying automatically — so a restarting API can never leave factory defaults written over your saved values." />
              </Box>
            )}
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2,
              opacity: cfgLoaded ? 1 : 0.45,
              pointerEvents: cfgLoaded ? 'auto' : 'none' }}>
            {/* mesh_size_mm */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                  Max element size
                  <Tooltip title={`Coarsest triangle edge length in the iron/body. Bounded to the feature/2 quality floor${_floor ? ` = ${_floor.toFixed(2)} mm for this motor (2 elements across the smallest tooth/slot)` : ''}: above it the solver clamps anyway, so the slider stops there and every position actually changes the mesh. Lower it to refine (finer + more accurate, slower).`} placement="right">
                    <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={`${Math.min(meshSizeMm, meshMax).toFixed(1)} mm`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: 'var(--line-accent)', color: '#93c5fd' }}/>
              </Box>
              <Slider
                value={Math.min(meshSizeMm, meshMax)} min={meshMin} max={meshMax} step={meshStep}
                disabled={!cfgLoaded}
                // A user move is the ONLY thing that may be written back to the
                // server config (2026-09-07 rule) — hence the dirty flag here.
                onChange={(_, v) => { setMeshSizeMm(v as number); markDirty('mesh_size_mm'); }}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            {/* min_size_mm — a LEGACY gmsh parameter.  The geometry-driven
                mesh (the solver's default) never reads it: its floor is set
                by the wire outlines + the q20 quality bound.  A live slider
                that provably does nothing reads as a broken mesh pipeline
                (user hit exactly that), so it is disabled while geo mesh is
                on, with the reason on the ⓘ. */}
            <Box sx={{ opacity: geoMesh ? 0.45 : 1 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                  Min element size
                  <Tooltip placement="right" title={geoMesh
                    ? 'Not used by the geometry-driven mesh — its minimum size is set by the wire outlines and the q20 quality bound. This slider only affects the legacy gmsh mesher (Geometry-driven mesh OFF).'
                    : 'Lower bound on triangle size at fillets / thin features (gmsh mesher)'}>
                    <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={geoMesh ? 'not used' : `${minSizeMm.toFixed(2)} mm`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: 'var(--panel)', color: 'var(--text-2)' }}/>
              </Box>
              <Slider
                value={minSizeMm} min={0.1} max={2.0} step={0.05} disabled={geoMesh || !cfgLoaded}
                onChange={(_, v) => { setMinSizeMm(v as number); markDirty('min_size_mm'); }}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            {/* ── PREVIEW ONLY ─────────────────────────────────────────────
                Rotor angle rotates the picture below, nothing else.  The
                transient always starts the rotor at 0 and sweeps a full
                electrical period, so this cannot change a result — it used to
                sit among the solver settings and read like one. */}
            <Box sx={{ borderTop: '1px solid var(--line-soft)', pt: 1.5, mt: 0.5 }}>
              <Typography sx={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
                color: 'var(--text-4)', textTransform: 'uppercase', mb: 1 }}>
                Preview only — does not affect results
              </Typography>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)', display: 'flex',
                  alignItems: 'center', gap: 0.5 }}>
                  Rotor angle
                  <HelpTip title="0…25.71° mech = one electrical period. Rotates the mesh view only — the transient always starts the rotor at 0 and sweeps a full period, so this cannot change a result." />
                </Typography>
                <Chip label={`${rotorAngle.toFixed(1)}°`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: 'var(--panel)', color: 'var(--text-2)' }}/>
              </Box>
              <Slider
                value={rotorAngle} min={0} max={25.71} step={0.5}
                onChange={(_, v) => setRotorAngle(v as number)}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            <Divider sx={{ borderColor: 'var(--panel)' }}/>

            {/* ── Solver-domain section (Ansys-style) ────────────────────── */}
            <Box>
              <Typography sx={{ fontSize: '0.62rem', fontWeight: 700, color: 'var(--text-4)',
                letterSpacing: '0.08em', textTransform: 'uppercase', mb: 0.5 }}>
                Solver Domain
              </Typography>
            </Box>

            {/* Outer air ring factor */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                  Outer air ring
                  <Tooltip title="Extend mesh beyond stator OD so the Dirichlet A=0 far-field BC is applied on air, not iron. 1.0 = off; 1.3 ≈ Ansys Region Padding 30%." placement="right">
                    <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={outerAirFactor <= 1.001 ? 'off'
                  : `×${outerAirFactor.toFixed(2)}`} size="small"
                  sx={{ fontSize: 11, height: 20,
                    bgcolor: outerAirFactor > 1.001 ? 'var(--line-accent)' : 'var(--panel)',
                    color: outerAirFactor > 1.001 ? '#93c5fd' : 'var(--text-2)' }}/>
              </Box>
              <Slider
                value={outerAirFactor} min={1.0} max={2.0} step={0.05} disabled={!cfgLoaded}
                onChange={(_, v) => { setOuterAirFactor(v as number); markDirty('outer_air_factor'); }}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            {/* Air element size — open air / shaft / far-field (same store as
                the Per-part "Outer air" field; 0 = auto coarse) */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                  Air element size
                  <Tooltip title="Element size (mm) for the open air: outer far-field, slot pockets, shaft core. Auto = coarse (≈2× the iron element, ≥3 mm) — the open air carries little flux, so coarse is cheap and safe. Same setting as the Per-part 'Outer air' field below." placement="right">
                    <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={(componentMesh.outer ?? 0) > 0
                  ? `${(componentMesh.outer as number).toFixed(1)} mm` : 'auto'} size="small"
                  sx={{ fontSize: 11, height: 20,
                    bgcolor: (componentMesh.outer ?? 0) > 0 ? 'var(--line-accent)' : 'var(--panel)',
                    color: (componentMesh.outer ?? 0) > 0 ? '#93c5fd' : 'var(--text-2)' }}/>
              </Box>
              <Slider
                value={componentMesh.outer ?? 0} min={0} max={8} step={0.5}
                onChange={(_, v) => {
                  const n = v as number;
                  setCompSize('outer', n > 0 ? String(n) : '');
                }}
                sx={{ color: '#3b82f6' }}
              />
            </Box>

            {/* Air-gap layers — element rows on EACH side of the slip midline */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                  Air-gap fidelity (layers/side)
                  <Tooltip title="The single air-gap fidelity control. Sets BOTH the radial element rows per side of the sliding midline (torque via Maxwell stress) AND the tangential slip-ring node count (eddy-loss accuracy — more nodes = less node-identification jitter). 2 is the sweet spot for mean torque; raise to 4+ for the cleanest eddy/solid losses (slower, ~gap=4 ≈ the retired High-fidelity mode). Drives the mesh preview and the Simulation solve identically." placement="right">
                    <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <Chip label={`${gapLayers.toFixed(0)}/side`} size="small"
                  sx={{ fontSize: 11, height: 20, bgcolor: 'var(--line-accent)', color: '#93c5fd' }}/>
              </Box>
              <Slider
                value={gapLayers} min={1} max={6} step={1} disabled={!cfgLoaded}
                marks onChange={(_, v) => { setGapLayers(v as number); markDirty('gap_layers'); }}
                sx={{ color: '#06b6d4' }}
              />
            </Box>

            {/* Air-gap mesh: free triangles vs ANSYS-style concentric rings (experimental) */}
            <Box>
              <Typography sx={{ fontSize: '0.62rem', fontWeight: 700, color: 'var(--text-4)',
                letterSpacing: '0.08em', textTransform: 'uppercase', mb: 0.5 }}>
                Air-gap mesh
              </Typography>
              {/* Element order is no longer a choice: every solve is P2. The
                  switch that used to sit here selected P1, whose mean torque
                  over-read ~35 % and whose ripple was a mesh staircase. */}
              <Tooltip placement="right" title="Every solve uses second-order (P2) finite elements — the flux density B is linear inside each element instead of piecewise-constant, so the torque is smooth like ANSYS Maxwell (2nd-order) and the mean is energy-consistent. RAW ripple is honest with NO filter (measured ~55x lower non-6k noise floor than the retired P1 basis). Requires the structured belt, which is always on.">
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mt: 0.75 }}>
                  <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>Elements</Typography>
                  <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>P2 (2nd order)</Typography>
                </Box>
              </Tooltip>
              <Tooltip placement="right" title="Stator/rotor iron meshed by structured slot/pole unit templates on the real CadQuery contours (pocket fillets, vent, OD fillet) — build-to-build deterministic mesh and ripple. Works on the full disk and 1/2, 1/4, 1/6 sectors. Requires the Structured (rings) air gap (auto-enabled); falls back to gmsh when the topology doesn't fit the units.">
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mt: 0.75 }}>
                  <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>Template iron (deterministic)</Typography>
                  <Switch size="small" checked={ironTemplate}
                    onChange={(e) => {
                      setIronTemplate(e.target.checked);
                      // template halves end ON the iron circles — only the
                      // structured belt meshes the gap between them
                      if (e.target.checked) setStructuredGap(true);
                      else setGeoMesh(false);   // geo rides the template path
                    }} />
                </Box>
              </Tooltip>
              <Tooltip placement="right" title="Triangulate the REAL CadQuery polygons (every fillet) with a constrained Delaunay instead of warping a tensor template — the mesh conforms to magnet corners, tooth-tip r1 and the V-notch apex by construction. Rides the Template-iron path (auto-enables it). Full disk only for now: a 1/N request builds the full ring.">
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mt: 0.75 }}>
                  <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>Geometry-driven mesh (fillets)</Typography>
                  <Switch size="small" checked={geoMesh}
                    onChange={(e) => {
                      setGeoMesh(e.target.checked);
                      if (e.target.checked) { setIronTemplate(true); setStructuredGap(true); }
                    }} />
                </Box>
              </Tooltip>
            </Box>

            {/* ── Per-component mesh size (mesh-convergence study) ───────── */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                  Per-part element size (mm)
                  <Tooltip title="Target triangle size INSIDE each motor part. Empty = use the global Max size for that part. Set a finer/coarser value per part to study how mesh density changes the simulated torque/losses (mesh-convergence). Applies to both the mesh preview and the Simulation solve. A Windings size here overrides the Wire cell factor below." placement="right">
                    <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
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
                    <Typography sx={{ fontSize: 11, color: 'var(--text-1)', flex: 1 }}>
                      {label}
                    </Typography>
                    <TextField
                      type="text" size="small" placeholder="auto"
                      value={compDraft[key] ?? (componentMesh[key]?.toString() ?? '')}
                      onChange={e => setCompSize(key, e.target.value)}
                      inputProps={{ inputMode: 'decimal', style: {
                        padding: '2px 6px', fontSize: 11, width: 52,
                        color: 'var(--text-0)', textAlign: 'right' } }}
                      sx={{ '& .MuiOutlinedInput-root': { bgcolor: 'var(--app-bg)',
                        '& fieldset': { borderColor: 'var(--panel)' } } }}
                    />
                  </Box>
                ))}
              </Box>
              {/* Wire cell — copper cell size relative to the wire height */}
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.75 }}>
                <Typography sx={{ fontSize: 11, color: 'var(--text-1)', flex: 1 }}>
                  Wire cell
                  <Tooltip placement="right" title="Copper cell size as a multiple of the wire height h. A factor, not mm, so it stays ½h/1h/2h after a wire-height edit.">
                    <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                  </Tooltip>
                </Typography>
                <ToggleButtonGroup
                  value={wireCell} exclusive size="small"
                  onChange={(_, v) => v != null && setWireCell(v as number)}
                  sx={{ '& .MuiToggleButton-root': { py: 0.15, px: 1, fontSize: 11,
                    lineHeight: 1.4, color: 'var(--text-3)', borderColor: 'var(--panel)',
                    textTransform: 'none',
                    '&.Mui-selected': { color: 'var(--text-0)', bgcolor: 'var(--line-accent)',
                      borderColor: '#3b82f6' } } }}>
                  {WIRE_CELL_OPTIONS.map(o => (
                    <ToggleButton key={o.v} value={o.v}>{o.label}</ToggleButton>
                  ))}
                </ToggleButtonGroup>
              </Box>
            </Box>

            {/* Symmetry sectors */}
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                  Symmetry
                </Typography>
              </Box>
              <Tooltip placement="right" title={`Split the motor into N equal wedges (${symSlots} slots / ${symPoles} poles, GCD ${symGcd} → ${validSectors.map(s => s === 1 ? 'Full' : '1/' + s).join(', ')}). ${nSectors > 1 ? `Now: ${symSlots / nSectors} slots + ${symPoles / nSectors} poles per sector, ${(symPoles / nSectors) % 2 === 1 ? 'anti-periodic' : 'periodic'} BC on the radial cuts.` : 'Full 360° — stitched from clean half-sectors (no cuts, no double mesh).'} Saved & used by Simulation + charts.`}>
                <ToggleButtonGroup
                  value={nSectors} exclusive size="small" fullWidth disabled={!cfgLoaded}
                  // The one-click symmetry switch — a user action, so it is dirty
                  // and persisted IMMEDIATELY (this is the setting that was lost
                  // on 2026-09-07: "там точно стояло 1/2").  Waiting for the
                  // 700 ms debounce would lose it again if the user leaves the
                  // tab straight after clicking — the panel unmounts.
                  onChange={(_, v) => {
                    if (v == null) return;
                    setNSectors(v as number);
                    markDirty('n_sectors');
                    if (cfgLoaded) patchMeshConfig({ n_sectors: v as number });
                  }}
                  sx={{ width: '100%',
                    '& .MuiToggleButton-root': { flex: 1, py: 0.3,
                      fontSize: 11, color: 'var(--text-3)', borderColor: 'var(--panel)',
                      textTransform: 'none',
                      '&.Mui-selected': { color: 'var(--text-0)', bgcolor: 'var(--line-accent)',
                        borderColor: '#3b82f6' } } }}>
                  {validSectors.map(s => (
                    <ToggleButton key={s} value={s}>{s === 1 ? 'Full' : `1/${s}`}</ToggleButton>
                  ))}
                </ToggleButtonGroup>
              </Tooltip>
            </Box>

            {/* Periodic (template-copy) mesh toggle */}
            <Box sx={{ mt: 1.5 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
                  Pole/slot mesh
                </Typography>
              </Box>
              <Tooltip placement="right" title="Standard: each pole/slot meshed independently (slightly different node pattern → pole-to-pole mesh variance on the losses). Periodic: mesh ONE pole + ONE slot, rotate-copy them so every pole and slot is BIT-IDENTICAL → that variance is removed. Applies to the Mesh view, field views and Simulation.">
                <ToggleButtonGroup
                  value={poleCopy ? 'periodic' : 'standard'} exclusive size="small" fullWidth
                  onChange={(_, v) => v != null && setPoleCopy(v === 'periodic')}
                  sx={{ width: '100%',
                    '& .MuiToggleButton-root': { flex: 1, py: 0.3, fontSize: 11,
                      color: 'var(--text-3)', borderColor: 'var(--panel)', textTransform: 'none',
                      '&.Mui-selected': { color: 'var(--text-0)', bgcolor: 'var(--line-accent)',
                        borderColor: '#3b82f6' } } }}>
                  <ToggleButton value="standard">Standard</ToggleButton>
                  <ToggleButton value="periodic">Periodic (identical poles)</ToggleButton>
                </ToggleButtonGroup>
              </Tooltip>
            </Box>

            {/* The "Rebuild mesh" button is gone: every setting above
                auto-rebuilds (debounced) and auto-persists — a build
                indicator is all that remains of it. */}
            {femLoading && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5 }}>
                <CircularProgress size={14} />
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  Rebuilding mesh…
                </Typography>
              </Box>
            )}

            {femError && <Alert severity="error" sx={{ fontSize: 11 }}>{femError}</Alert>}

            {femMesh && (
              <Box sx={{ bgcolor: 'var(--panel-2)', borderRadius: 1, p: 1, border: '1px solid var(--line-soft)' }}>
                <Typography sx={{ fontSize: 10, color: 'var(--text-4)', mb: 0.5 }}>Mesh stats</Typography>
                <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>vertices</Typography>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-0)', fontVariantNumeric: 'tabular-nums' }}>
                    {femMesh.n_vertices.toLocaleString()}
                  </Typography>
                </Box>
                <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>triangles</Typography>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-0)', fontVariantNumeric: 'tabular-nums' }}>
                    {femMesh.n_triangles.toLocaleString()}
                  </Typography>
                </Box>
              </Box>
            )}

            <Divider sx={{ borderColor: 'var(--panel)' }}/>
            </Box>
          </>
        )}

        {view === 'pinn' && (
          <>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
            letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            Collocation Points
          </Typography>
          <HelpTip title="PINN samples random points inside each domain. Higher density → better accuracy, slower training." />
        </Box>

        <Divider sx={{ borderColor: 'var(--panel)' }}/>

        {/* n_radial */}
        <Box>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
              Radial layers
              <Tooltip title="Number of concentric circles of sample points per domain" placement="right">
                <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_radial} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: 'var(--line-accent)', color: '#93c5fd' }}/>
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
            <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
              Angular divisions
              <Tooltip title="Points around each radial ring in the main domains" placement="right">
                <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_angular} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: 'var(--line-accent)', color: '#93c5fd' }}/>
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
            <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
              Slot angular divisions
              <Tooltip title="Denser angular sampling inside slots/magnets for accuracy" placement="right">
                <span style={{ color: 'var(--text-4)', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_angular_slots} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: 'var(--line-accent)', color: '#93c5fd' }}/>
          </Box>
          <Slider
            value={cfg.n_angular_slots} min={2} max={64} step={2}
            onChange={(_, v) => setCfg(c => ({ ...c, n_angular_slots: v as number }))}
            sx={{ color: '#f59e0b' }}
          />
        </Box>

        <Divider sx={{ borderColor: 'var(--panel)' }}/>

        {/* Point count table */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Estimated Sample Points
          </Typography>

          {DOMAINS.map(d => (
            <Box key={d.key} sx={{ display: 'flex', alignItems: 'center',
              justifyContent: 'space-between', py: 0.35 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: d.color, flexShrink: 0 }}/>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{d.label}</Typography>
              </Box>
              <Typography sx={{ fontSize: 11, fontWeight: 600, color: 'var(--text-2)', fontVariantNumeric: 'tabular-nums' }}>
                {(pointsPerDomain[d.key] ?? 0).toLocaleString()}
              </Typography>
            </Box>
          ))}

          <Divider sx={{ borderColor: 'var(--panel)', my: 0.75 }}/>
          <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
            <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)' }}>Total</Typography>
            <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-0)', fontVariantNumeric: 'tabular-nums' }}>
              {totalPoints.toLocaleString()}
            </Typography>
          </Box>
        </Box>

        <Divider sx={{ borderColor: 'var(--panel)' }}/>

        <Button
          variant="contained" color="primary" fullWidth
          startIcon={saving ? <CircularProgress size={14} color="inherit"/> : <SaveIcon/>}
          onClick={handleSave}
          // Same rule as the FEM settings: never PATCH a config the panel has
          // not read (this body echoes the loaded block, 2026-09-07 incident).
          disabled={saving || !cfgLoaded}
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
          <Typography variant="h6" sx={{ color: 'var(--text-0)', fontWeight: 700, mb: 0.5 }}>
            {view === 'fem' ? '2-D FEM Triangle Mesh' : 'Collocation Sampling Grid'}
          </Typography>
          <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>
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
        <Paper sx={{ flex: 1, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
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
                    showFill
                    showWire
                    showOutlines
                    showGrid/>
                ) : (
                  <FemMeshViewer2D payload={femMesh as any} showWire/>
                )}
              </Box>
              {!WEBGL_OK && (
                <Box sx={{ position: 'absolute', top: 8, left: 8, zIndex: 4,
                  bgcolor: 'var(--overlay)', px: 1, py: 0.5, borderRadius: 1,
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
                bgcolor: 'var(--overlay)', px: 1, py: 0.5, borderRadius: 1,
                pointerEvents: 'none' }}>
                <Typography sx={{ fontSize: 9, color: 'var(--text-2)' }}>
                  Drag = orbit · Right-drag / Shift+drag = pan · Wheel = zoom
                </Typography>
              </Box>
            </>
          ) : (
            <CollocationPreview cfg={cfg} geo={geo}/>
          )}
        </Paper>

        {/* The backend's mesh `note` is a full paragraph — show only its first
            clause inline and hang the rest off the ⓘ (UI rule: no always-visible
            explanation prose). */}
        {view === 'fem' && femMesh?.note && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, justifyContent: 'center' }}>
            <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>
              {String(femMesh.note).split('.')[0]}
            </Typography>
            <HelpTip title={femMesh.note} />
          </Box>
        )}
      </Box>
    </Box>
  );
};

export default MeshPanel;
