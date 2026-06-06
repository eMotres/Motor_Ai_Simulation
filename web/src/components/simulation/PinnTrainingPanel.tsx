/**
 * Modulus PINN training panel — watch the GPU PINN train live and converge to
 * the FEM torque, with a GPU-temperature readout so an overheating laptop is
 * obvious.  The PINN runs in WSL (CUDA + physicsnemo-sym); this panel drives the
 * backend bridge (/api/simulation/pinn/{start,progress,stop}).
 */
import React from 'react';
import {
  Box, Typography, Button, TextField, LinearProgress, Chip, Tooltip, Alert,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTip,
  ReferenceLine, ResponsiveContainer, Legend,
} from 'recharts';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface Hist { step: number; torque: number; b_max?: number; }
interface Prog {
  running: boolean; step: number; max_steps: number;
  torque_pinn?: number; torque_fem?: number; b_max?: number;
  gpu_temp?: number | null; cooling?: boolean; done?: boolean; crashed?: boolean;
  sec?: number; history?: Hist[];
}

const PinnTrainingPanel: React.FC = () => {
  const [steps, setSteps] = React.useState(3000);
  const [prog, setProg] = React.useState<Prog | null>(null);
  const [busy, setBusy] = React.useState(false);
  const pollRef = React.useRef<number | null>(null);
  const [fieldTs, setFieldTs] = React.useState<number>(() => Date.now()); // cache-bust key
  const [fieldOk, setFieldOk] = React.useState(true);
  const [geomTs, setGeomTs] = React.useState<number>(() => Date.now());
  const [geomOk, setGeomOk] = React.useState(true);
  const [vsTs, setVsTs] = React.useState<number>(() => Date.now());
  const [vsOk, setVsOk] = React.useState(true);
  const wasRunning = React.useRef(false);

  const poll = React.useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/simulation/pinn/progress`);
      const d: Prog = await r.json();
      setProg(d);
      if (!d.running && pollRef.current) {
        clearInterval(pollRef.current); pollRef.current = null; setBusy(false);
      }
    } catch { /* backend down — keep last */ }
  }, []);

  const ensurePoll = React.useCallback(() => {
    if (!pollRef.current) pollRef.current = window.setInterval(poll, 1500);
  }, [poll]);

  const start = async () => {
    setBusy(true);
    try {
      await fetch(`${API}/api/simulation/pinn/start`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ steps }),
      });
    } catch { setBusy(false); return; }
    ensurePoll(); poll();
  };
  const stop = async () => {
    try { await fetch(`${API}/api/simulation/pinn/stop`, { method: 'POST' }); } catch { /**/ }
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setBusy(false); poll();
  };

  // initial fetch + resume polling if a run is already in flight
  React.useEffect(() => {
    poll();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [poll]);
  React.useEffect(() => {
    if (prog?.running) { setBusy(true); ensurePoll(); }
  }, [prog?.running, ensurePoll]);
  // refresh the Modulus field image whenever a run finishes (running → not running)
  React.useEffect(() => {
    if (wasRunning.current && !prog?.running) { setFieldTs(Date.now()); setFieldOk(true); }
    wasRunning.current = !!prog?.running;
  }, [prog?.running]);

  const t = prog?.gpu_temp ?? null;
  const tempColor = t == null ? '#64748b' : t >= 82 ? '#ef4444' : t >= 70 ? '#f59e0b' : '#22c55e';
  const pct = prog && prog.max_steps ? Math.min(100, 100 * prog.step / prog.max_steps) : 0;
  const Tp = prog?.torque_pinn ?? 0;
  const Tf = prog?.torque_fem ?? 24.87;
  const dT = prog?.torque_pinn != null ? Tp - Tf : null;
  const hist = prog?.history ?? [];

  const statusChip = () => {
    if (prog?.cooling) return <Chip size="small" label="COOLING — GPU hot" sx={{ bgcolor: '#7c2d12', color: '#fed7aa', height: 20, fontSize: 11 }} />;
    if (prog?.running) return <Chip size="small" color="primary" label="TRAINING" sx={{ height: 20, fontSize: 11 }} />;
    if (prog?.done) return <Chip size="small" color="success" label="DONE" sx={{ height: 20, fontSize: 11 }} />;
    if (prog?.crashed) return <Chip size="small" color="error" label="STOPPED / CRASHED" sx={{ height: 20, fontSize: 11 }} />;
    return <Chip size="small" variant="outlined" label="IDLE" sx={{ height: 20, fontSize: 11 }} />;
  };

  return (
    <Box sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, p: 2, bgcolor: 'rgba(99,102,241,0.04)' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 700, flex: 1 }}>
          ⚛ Modulus PINN (GPU) — physics validation vs FEM
        </Typography>
        {statusChip()}
        <Tooltip title="Live GPU temperature (read from nvidia-smi). Green &lt;70, amber 70–82, red ≥82 °C — the trainer auto-pauses above 82 °C." placement="top">
          <Chip size="small" label={`GPU ${t == null ? '—' : t + '°C'}`}
            sx={{ height: 20, fontSize: 11, color: '#fff', bgcolor: tempColor }} />
        </Tooltip>
      </Box>

      {(prog?.cooling || (t != null && t >= 82)) && (
        <Alert severity="warning" sx={{ mb: 1.5, py: 0.25, fontSize: 12 }}>
          <b>GPU too hot ({t ?? '?'} °C) — training PAUSED by the thermal guard.</b>{' '}
          It resumes only below ~72 °C. Your GPU idles hot, so cool it first
          (AC power + max fans, clean vents, Afterburner temp-limit / undervolt),
          otherwise training can't progress.
        </Alert>
      )}

      {/* GEOMETRY DIAGNOSTIC — PINN now samples the SAME real polygons as the FEM */}
      {geomOk && (
        <Box sx={{ mb: 2, border: '1px solid', borderColor: 'rgba(34,197,94,0.4)', borderRadius: 1, p: 1, bgcolor: 'rgba(34,197,94,0.06)' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
            <Typography variant="caption" sx={{ fontWeight: 700, color: '#86efac' }}>
              ✓ Geometry — Modulus (PINN) = FEM (unified)
            </Typography>
            <Chip size="small" label="real polygons" sx={{ height: 18, fontSize: 10, bgcolor: 'rgba(34,197,94,0.22)', color: '#bbf7d0' }} />
            <Button size="small" variant="text" sx={{ minWidth: 0, fontSize: 11, py: 0 }}
              onClick={() => { setGeomOk(true); setGeomTs(Date.now()); }}>
              ↻ refresh
            </Button>
          </Box>
          <Box
            component="img"
            src={`${API}/api/simulation/pinn/geometry?t=${geomTs}`}
            alt="PINN vs FEM geometry"
            onError={() => setGeomOk(false)}
            sx={{ width: '100%', display: 'block', borderRadius: 1, border: '1px solid', borderColor: 'divider', bgcolor: '#0f172a' }}
          />
          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 0.5 }}>
            <b>Left</b> = the PINN now rejection-samples collocation points <b>inside the real polygons</b>{' '}
            (<code>pinn_geom.json</code>, exported from <code>get_2d_polygons</code>). <b>Right</b> = the FEM polygons
            filled. They <b>match</b> — same trapezoid spoke magnets, real coils, T-tooth iron. The PINN now solves the
            same motor as the FEM. (Next: fix the magnet <i>source</i> formula, then re-train.)
          </Typography>
        </Box>
      )}

      {/* PINN vs FEM comparison — |B|, air-gap B_r/B_φ, capability table */}
      {vsOk && (
        <Box sx={{ mb: 2, border: '1px solid', borderColor: 'divider', borderRadius: 1, p: 1, bgcolor: 'rgba(96,165,250,0.05)' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
            <Typography variant="caption" sx={{ fontWeight: 700, color: '#93c5fd' }}>
              Modulus PINN vs FEM — fields &amp; capabilities
            </Typography>
            <Button size="small" variant="text" sx={{ minWidth: 0, fontSize: 11, py: 0 }}
              onClick={() => { setVsOk(true); setVsTs(Date.now()); }}>
              ↻ refresh
            </Button>
          </Box>
          <Box component="img" src={`${API}/api/simulation/pinn/vs_fem?t=${vsTs}`}
            alt="PINN vs FEM" onError={() => setVsOk(false)}
            sx={{ width: '100%', display: 'block', borderRadius: 1, border: '1px solid', borderColor: 'divider', bgcolor: '#0f172a' }} />
          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 0.5 }}>
            Same geometry. FEM teeth <b>saturate</b> (~1.8 T, BH curve) and concentrate flux → strong air-gap B_φ →
            full torque. The PINN is <b>linear</b> (μ_r=500, no BH/saturation/demag/loss) → diffuse |B|~1 T, B_φ ~2×
            weaker → torque 2.68 vs ≈24.9 N·m. Without the missing physics the PINN can't reproduce the real picture.
          </Typography>
        </Box>
      )}

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5, flexWrap: 'wrap' }}>
        <TextField label="steps" type="number" size="small" value={steps} disabled={busy}
          onChange={e => setSteps(Math.max(150, Math.min(50000, Math.round(+e.target.value) || 3000)))}
          inputProps={{ style: { fontSize: 12, padding: '5px 8px', width: 64 } }} />
        {prog?.running ? (
          <Button size="small" variant="contained" color="error" startIcon={<StopIcon />} onClick={stop}>
            Stop
          </Button>
        ) : (
          <Button size="small" variant="contained" startIcon={<PlayArrowIcon />} onClick={start} disabled={busy}>
            Train PINN
          </Button>
        )}
        <Typography variant="caption" color="text.secondary">
          step {prog?.step ?? 0}/{prog?.max_steps ?? 0}{prog?.sec ? ` · ${prog.sec}s` : ''}
        </Typography>
      </Box>

      <LinearProgress variant="determinate" value={pct} sx={{ height: 6, borderRadius: 3, mb: 1.5 }} />

      {/* metrics */}
      <Box sx={{ display: 'flex', gap: 3, mb: 1.5, flexWrap: 'wrap' }}>
        <Box>
          <Typography variant="caption" color="text.secondary">Torque PINN</Typography>
          <Typography variant="h6" sx={{ lineHeight: 1.1, color: dT != null && Math.abs(dT) < 2 ? '#22c55e' : '#fbbf24' }}>
            {Tp.toFixed(2)} <Typography component="span" variant="caption">N·m</Typography>
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">FEM (target)</Typography>
          <Typography variant="h6" sx={{ lineHeight: 1.1, color: '#60a5fa' }}>
            {Tf.toFixed(2)} <Typography component="span" variant="caption">N·m</Typography>
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">Δ vs FEM</Typography>
          <Typography variant="h6" sx={{ lineHeight: 1.1 }}>
            {dT == null ? '—' : `${dT > 0 ? '+' : ''}${dT.toFixed(2)}`}
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">|B|max</Typography>
          <Typography variant="h6" sx={{ lineHeight: 1.1 }}>
            {(prog?.b_max ?? 0).toFixed(2)} <Typography component="span" variant="caption">T</Typography>
          </Typography>
        </Box>
      </Box>

      {/* torque convergence chart */}
      <Box sx={{ height: 160 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={hist} margin={{ top: 6, right: 12, bottom: 4, left: -6 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="2 4" />
            <XAxis dataKey="step" tick={{ fontSize: 10, fill: '#94a3b8' }} />
            <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} />
            <RTip contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', fontSize: 11 }} />
            <ReferenceLine y={Tf} stroke="#60a5fa" strokeDasharray="4 3"
              label={{ value: 'FEM', fill: '#60a5fa', fontSize: 10, position: 'insideTopRight' }} />
            <Line type="monotone" dataKey="torque" name="PINN torque" stroke="#fbbf24"
              dot={{ r: 2 }} strokeWidth={1.5} isAnimationActive={false} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
          </LineChart>
        </ResponsiveContainer>
      </Box>

      <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 0.5 }}>
        The PINN learns Maxwell's equations directly (no FEM data); the yellow line should climb toward the blue FEM
        target as the residual drops. Trains in cooled chunks; auto-pauses if the GPU exceeds 82 °C.
      </Typography>

      {/* Modulus field dump — A_z (real/imag) + |B|, full disk via 90° anti-periodic symmetry */}
      {fieldOk && (
        <Box sx={{ mt: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Typography variant="caption" sx={{ fontWeight: 700 }}>
              Modulus PINN field — A_z (real / imag) &amp; |B|
            </Typography>
            <Chip size="small" label="neural net" sx={{ height: 18, fontSize: 10, bgcolor: 'rgba(99,102,241,0.25)', color: '#c7d2fe' }} />
            <Button size="small" variant="text" sx={{ minWidth: 0, fontSize: 11, py: 0 }}
              onClick={() => { setFieldOk(true); setFieldTs(Date.now()); }}>
              ↻ refresh
            </Button>
          </Box>
          <Box
            component="img"
            src={`${API}/api/simulation/pinn/field?t=${fieldTs}`}
            alt="Modulus PINN field"
            onError={() => setFieldOk(false)}
            sx={{ width: '100%', display: 'block', borderRadius: 1, border: '1px solid', borderColor: 'divider', bgcolor: '#fff' }}
          />
          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 0.5 }}>
            Direct output of the trained network (90° sector folded to the full disk via the anti-periodic BC).
            Auto-refreshes when a run finishes. The 28-pole + slot structure is correct; the amplitude (|B|) is still
            building toward the FEM solution.
          </Typography>
        </Box>
      )}
    </Box>
  );
};

export default PinnTrainingPanel;
