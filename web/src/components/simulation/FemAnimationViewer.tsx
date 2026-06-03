/**
 * FemAnimationViewer — Plays back ONE electrical period of the transient
 * FEM solve as an animated field map.
 *
 * Calls /api/simulation/physics/fem_transient?include_frames=true once
 * (~90 s for a coarse mesh), caches the resulting N frames, then lets the
 * user scrub through them via a slider OR auto-play.  Each frame is a
 * complete FEM payload (mesh + A_z + |B| + demag) at a different rotor
 * angle, so the rotor IS visibly rotating through the period.
 *
 * The actual rendering is delegated to <FemFieldChart payloadOverride=…/>
 * so all three field modes (A_z / |B| / Demag), iso-lines and the colour
 * bar work identically to the static field viewer.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Box, Paper, Typography, Slider, IconButton, Button, CircularProgress,
  ToggleButtonGroup, ToggleButton, Tooltip,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PauseIcon from '@mui/icons-material/Pause';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import DownloadIcon from '@mui/icons-material/Download';
import FemFieldChart from './FemFieldChart';
import type { FemPayload } from './fem-types';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface FrameRaw {
  step_idx:        number;
  time_s:          number;
  rotor_angle_deg: number;
  T_em_Nm:         number;
  n_vertices:      number;
  n_triangles:     number;
  vertices:        [number, number][];
  triangles:       [number, number, number][];
  domain_per_tri:  number[];
  A_z_per_node:    number[];
  Bmag_per_tri:    number[];
  J_z_per_tri?:    number[];
  demag_coef_per_tri?: number[];
  extent:          [number, number, number, number];
  A_z_min:         number;
  A_z_max:         number;
  B_mag_max:       number;
  // Only on frame[0]:
  outlines?:       { domain: number; loops: [number, number][][] }[];
  symmetry_mult?:  number;
  n_sectors?:      number;
  // On every frame ≥ 1: rotor / magnet outlines at the current rotation.
  outlines_rotor?: { domain: number; loops: [number, number][][] }[];
}

interface TransientFramePayload {
  n_steps_per_period: number;
  n_periods:          number;
  T_period_s:         number;
  f_elec_Hz:          number;
  rpm:                number;
  T_avg_Nm:           number;
  T_ripple_pct:       number;
  V_peak:             number;
  P_mech_avg_W:       number;
  frames:             FrameRaw[];
  n_frames_returned:  number;
}

function readMeshSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

interface Props {
  gamma_deg?:    number;
  I_phase_rms?:  number;
  n_frames?:     number;        // animation keyframes (default 12)
  onPayload?:    (p: FemPayload) => void;   // forwarded from inner viewer
  // Ticks when the user presses "Run Simulation"; the animation only
  // (re)solves on that signal, never on raw gamma/current edits.
  runNonce?:     number;
  // "Start fresh" → backend discards cached frames before recomputing.
  fresh?:        boolean;
}

const FemAnimationViewer: React.FC<Props> = ({
  gamma_deg = 0, I_phase_rms = 85, n_frames: n_frames_default = 12, onPayload,
  runNonce = 0, fresh = false,
}) => {
  const [data,    setData]    = useState<TransientFramePayload | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error,   setError]   = useState<string | null>(null);
  const [idx,     setIdx]     = useState<number>(0);
  const [playing, setPlaying] = useState<boolean>(false);
  const [fps,     setFps]     = useState<number>(8);
  const [n_frames, setNFrames] = useState<number>(n_frames_default);
  const abortRef = useRef<AbortController | null>(null);

  // "Stop Simulation" (left panel) → abort the in-flight animation fetch
  // and ask the backend to cancel THIS run_id.
  useEffect(() => {
    const onStop = () => {
      abortRef.current?.abort();
      fetch(`${API}/api/simulation/physics/fem_transient/cancel?run_id=${runNonce}`,
        { method: 'POST' }).catch(() => {});
      setLoading(false);
    };
    window.addEventListener('sim:stop', onStop);
    return () => window.removeEventListener('sim:stop', onStop);
  }, [runNonce]);

  // ── fetch ──────────────────────────────────────────────────────────────
  const runAnimation = () => {
    setLoading(true); setError(null); setData(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const params: Record<string, string> = {
      n_steps_per_period: String(n_frames),
      n_periods:          '1.0',
      gamma_deg:          String(gamma_deg),
      I_phase_rms:        String(I_phase_rms),
      mesh_size_mm:       String(readMeshSetting('meshSize',    4.0)),
      min_size_mm:        String(readMeshSetting('minSize',     0.3)),
      outer_air_factor:   String(readMeshSetting('outerAir',    1.3)),
      motion_band:        String(readMeshSetting('motionBand',  true)),
      band_thickness_mm:  String(readMeshSetting('bandThickness', 0.4)),
      n_sectors:          String(readMeshSetting('nSectors',    4)),
      stator_fillet_mm:   '0',   // native geometry — extra smoothing removed
      include_frames:     'true',
      n_frames:           String(n_frames),
      run_id:             String(runNonce),
      fresh:              String(fresh),
    };
    // Auto-retry against transient backend hiccups (uvicorn supervisor
    // sometimes respawns the worker mid-request during a heavy FEM solve).
    const attempt = async (i = 0): Promise<void> => {
      try {
        const r = await fetch(`${API}/api/simulation/physics/fem_transient?${new URLSearchParams(params)}`,
          { signal: ctrl.signal });
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        const d: TransientFramePayload = await r.json();
        if (!d.frames || d.frames.length === 0) {
          throw new Error('No frames in response');
        }
        setData(d); setIdx(0); setLoading(false); setError(null);
      } catch (e: any) {
        const msg = String(e);
        if (ctrl.signal.aborted || /abort/i.test(msg)) { setLoading(false); return; }
        const isNetwork = /Failed to fetch|NetworkError|TypeError/i.test(msg);
        if (isNetwork && i < 4) {
          setError(`Backend hiccup — retrying (attempt ${i+2}/5)…`);
          setTimeout(() => attempt(i+1), 2000);
        } else {
          setError(msg); setLoading(false);
        }
      }
    };
    attempt();
  };

  // ── playback animation ─────────────────────────────────────────────────
  useEffect(() => {
    if (!playing || !data || data.frames.length < 2) return;
    const periodMs = 1000 / Math.max(1, fps);
    const tick = window.setInterval(() => {
      setIdx(i => (i + 1) % data.frames.length);
    }, periodMs);
    return () => window.clearInterval(tick);
  }, [playing, data, fps]);

  // Keep the keyframe count synced with the left-panel "Steps" field so
  // the animation and the transient charts hit the same backend cache key
  // (one FEM sweep, not two).
  useEffect(() => { setNFrames(n_frames_default); }, [n_frames_default]);

  // ── run ONLY when "Run Simulation" is pressed (runNonce ticks) ─────────
  // runNonce starts at 0 → nothing happens on mount; the user sets the
  // operating point + mesh settings first, then launches one solve.
  useEffect(() => {
    if (runNonce > 0) runAnimation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runNonce]);

  // ── build a FemPayload-shaped object for FemFieldChart ─────────────────
  const currentFrame = data?.frames[idx];
  const baseFrame = data?.frames[0];      // outlines / symmetry_mult live here
  const payloadForChart: FemPayload | null = useMemo(() => {
    if (!currentFrame || !baseFrame || !data) return null;
    return {
      n_vertices:      currentFrame.n_vertices,
      n_triangles:     currentFrame.n_triangles,
      vertices:        currentFrame.vertices,
      triangles:       currentFrame.triangles,
      domain_per_tri:  currentFrame.domain_per_tri,
      A_z_per_node:    currentFrame.A_z_per_node,
      Bmag_per_tri:    currentFrame.Bmag_per_tri,
      extent:          currentFrame.extent,
      // Static outlines (stator + coils + airgap + shaft) are baked
      // ONCE on frame[0].  Rotor-attached outlines (rotor body +
      // magnets) come per-frame from outlines_rotor so they always
      // align with the rotated mesh fill.
      outlines: [
        // Stator / coils / airgap / shaft / outer — always from frame[0]
        ...((baseFrame.outlines ?? []).filter(
          o => o.domain !== 4 && o.domain !== 44 && o.domain !== 5,
        )),
        // Rotor + magnets — from current frame (rotated); frame[0] still
        // has them in its `outlines` array.
        ...((idx === 0
              ? (baseFrame.outlines ?? []).filter(
                  o => o.domain === 4 || o.domain === 44 || o.domain === 5,
                )
              : (currentFrame.outlines_rotor ?? []))),
      ] as any,

      T_em_Nm:         currentFrame.T_em_Nm,
      // The transient runs at constant current, so per-frame Cu loss is constant.
      P_cu_W:          0,    // hidden in subHeader display anyway
      P_fe_W:          0,
      P_mag_eddy_W:    0,
      P_loss_total_W:  0,
      P_mech_W:        data.P_mech_avg_W,
      efficiency:      0,
      freq_Hz:         data.f_elec_Hz,
      rpm:             data.rpm,

      n_sectors:       baseFrame.n_sectors ?? 1,
      symmetry_mult:   baseFrame.symmetry_mult ?? 1,
      solve_time_s:    0,
      total_time_s:    0,

      A_z_min:         currentFrame.A_z_min,
      A_z_max:         currentFrame.A_z_max,
      B_mag_max:       currentFrame.B_mag_max,
      demag_coef_per_tri: currentFrame.demag_coef_per_tri,
      J_z_per_tri:     currentFrame.J_z_per_tri,
    } as FemPayload;
  }, [currentFrame, baseFrame, data]);

  // ── UI ─────────────────────────────────────────────────────────────────
  const t_ms_now  = currentFrame ? (currentFrame.time_s * 1000) : 0;
  const T_period  = data ? data.T_period_s * 1000 : 0;
  const rotorDeg  = currentFrame?.rotor_angle_deg ?? 0;
  const T_em_now  = currentFrame?.T_em_Nm ?? 0;
  const subHdr = data && currentFrame
    ? `frame ${idx + 1}/${data.frames.length}  ·  t = ${t_ms_now.toFixed(3)} ms / ${T_period.toFixed(3)} ms  ·  rotor = ${rotorDeg.toFixed(2)}°  ·  T_em = ${T_em_now.toFixed(2)} N·m`
    : undefined;

  return (
    <Paper sx={{ bgcolor: '#0b1220', border: '1px solid #1e293b', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: 1 }}>
        <Box>
          <Typography sx={{ fontSize: 13, color: '#cbd5e1', fontWeight: 700 }}>
            Field Animation — rotor through one electrical period
            <Tooltip title="Runs the transient FEM N_frames times (one solve per keyframe), capturing the full field map at each rotor angle. Use the slider to scrub through the period — rotor moves in real geometry, fields update with it. Each fetch takes ~n_frames × 3 s for mesh_size_mm=4." placement="top">
              <span style={{ color: '#475569', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </Typography>
          <Typography sx={{ fontSize: 10, color: '#475569' }}>
            {data
              ? `${data.frames.length} keyframes  ·  period ${(data.T_period_s*1000).toFixed(3)} ms  ·  f_elec ${data.f_elec_Hz.toFixed(1)} Hz  ·  rpm ${data.rpm}`
              : 'Idle — click "Run animation" to compute'}
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button size="small" variant="contained" disableElevation
            onClick={runAnimation} disabled={loading}
            startIcon={loading ? <CircularProgress size={14}/> : <RestartAltIcon fontSize="small"/>}
            sx={{ textTransform: 'none', fontSize: 11, bgcolor: '#1e3a5f',
              '&:hover': { bgcolor: '#2c5282' }}}>
            {loading ? 'Running FEM…' : (data ? 'Re-run animation' : 'Run animation')}
          </Button>
        </Box>
      </Box>

      {error && (
        <Typography sx={{ fontSize: 11, color: '#fca5a5', p: 1,
          border: '1px solid #7f1d1d', borderRadius: 1 }}>
          {error}
        </Typography>
      )}

      {/* Field map (delegated to FemFieldChart with payloadOverride).  The
          loader runs ~22 s for 4 frames, ~90 s for 12 — until then we show
          a spinner so the layout doesn't jump when the data arrives. */}
      {!payloadForChart && loading && (
        <Box sx={{ display: 'flex', alignItems: 'center',
          justifyContent: 'center', minHeight: 460,
          border: '1px solid #0f172a', bgcolor: '#060d17' }}>
          <Box sx={{ textAlign: 'center' }}>
            <CircularProgress size={40}/>
            <Typography sx={{ mt: 1, fontSize: 12, color: '#475569' }}>
              Solving FEM for {n_frames} keyframes…
            </Typography>
          </Box>
        </Box>
      )}
      {payloadForChart && (
        <FemFieldChart
          gamma_deg={gamma_deg}
          rotor_angle_deg={rotorDeg}
          I_phase_rms={I_phase_rms}
          payloadOverride={payloadForChart}
          subHeader={subHdr}
          hideRefresh
          onPayload={onPayload}
        />
      )}

      {/* Playback controls — slider + play / pause + FPS */}
      {data && data.frames.length > 1 && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2,
          px: 1, py: 0.5, bgcolor: '#060d17',
          border: '1px solid #0f172a', borderRadius: 1 }}>
          <IconButton size="small" onClick={() => setPlaying(p => !p)}
            sx={{ color: '#93c5fd' }}>
            {playing ? <PauseIcon fontSize="small"/> : <PlayArrowIcon fontSize="small"/>}
          </IconButton>
          <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
            <Slider
              size="small"
              value={idx}
              min={0}
              max={data.frames.length - 1}
              step={1}
              onChange={(_, v) => setIdx(v as number)}
              sx={{
                color: '#3b82f6',
                '& .MuiSlider-thumb': { width: 14, height: 14 },
              }}
            />
            <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
              <Typography sx={{ fontSize: 9, color: '#64748b', fontFamily: 'monospace' }}>
                t = {t_ms_now.toFixed(3)} ms
              </Typography>
              <Typography sx={{ fontSize: 9, color: '#64748b', fontFamily: 'monospace' }}>
                rotor = {rotorDeg.toFixed(2)}°  ·  θ_e = {(rotorDeg * 14).toFixed(0)}°
              </Typography>
            </Box>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Typography sx={{ fontSize: 10, color: '#64748b' }}>FPS</Typography>
            <ToggleButtonGroup value={fps} exclusive size="small"
              onChange={(_, v) => v && setFps(v as number)}
              sx={{
                '& .MuiToggleButton-root': { py: 0, px: 0.8, fontSize: 10,
                  color: '#64748b', borderColor: '#1e293b', textTransform: 'none',
                  '&.Mui-selected': { color: '#e2e8f0', bgcolor: '#1e3a5f',
                    borderColor: '#3b82f6' }}}}>
              <ToggleButton value={2}>2</ToggleButton>
              <ToggleButton value={4}>4</ToggleButton>
              <ToggleButton value={8}>8</ToggleButton>
              <ToggleButton value={16}>16</ToggleButton>
            </ToggleButtonGroup>
          </Box>
        </Box>
      )}

      <Typography sx={{ fontSize: 9, color: '#334155' }}>
        Each keyframe is a separate FEM solve at the actual rotor angle — magnets,
        rotor body and air-gap field reflect the physical state at that moment.
        Stator iron + slot positions stay fixed.  Iso-lines and the colour-bar
        rescale per frame to the local A_z range.
      </Typography>
    </Paper>
  );
};

export default FemAnimationViewer;
