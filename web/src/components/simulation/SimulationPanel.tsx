/**
 * Simulation tab — 2D Magnetostatics PINN (NVIDIA Modulus)
 *
 * Layout:
 *   Left  — operating-point controls + run button
 *   Right — status / results / log
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box, Typography, TextField, Button, Chip, Divider,
  LinearProgress, Alert, Tooltip, IconButton, Paper,
  CircularProgress,
} from '@mui/material';
import PlayArrowIcon    from '@mui/icons-material/PlayArrow';
import StopIcon         from '@mui/icons-material/Stop';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import CheckCircleIcon  from '@mui/icons-material/CheckCircle';
import ErrorIcon        from '@mui/icons-material/Error';
import BoltIcon         from '@mui/icons-material/Bolt';

const API = 'http://localhost:8000';

// ── types ─────────────────────────────────────────────────────────────────────
interface SimStatus {
  modulus_available: boolean;
  operating_point: {
    max_current: number;
    frequency_hz: number;
    rpm: number;
    Br_magnet_T: number;
  };
  solver: string;
}

interface JobStatus {
  job_id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  progress: number;
  result?: {
    torque_Nm: number;
    B_max_T: number;
    B_mean_T: number;
    training_steps: number;
    output_dir: string;
    status?: string;
    modulus_available?: boolean;
  };
  error?: string;
  elapsed_s?: number;
}

// ── small helpers ─────────────────────────────────────────────────────────────
const Row: React.FC<{ label: string; value: string; unit?: string; highlight?: boolean }> = ({
  label, value, unit, highlight,
}) => (
  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', py: 0.4 }}>
    <Typography sx={{ fontSize: 11, color: '#64748b' }}>{label}</Typography>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <Typography sx={{ fontSize: 12, fontWeight: 600, color: highlight ? '#4ade80' : '#e2e8f0' }}>
        {value}
      </Typography>
      {unit && <Typography sx={{ fontSize: 10, color: '#475569' }}>{unit}</Typography>}
    </Box>
  </Box>
);

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────
// ── helpers ───────────────────────────────────────────────────────────────────
function gcd(a: number, b: number): number { return b === 0 ? a : gcd(b, a % b); }
function lcm(a: number, b: number): number { return (a * b) / gcd(a, b); }

const SimulationPanel: React.FC = () => {
  // ── server status ─────────────────────────────────────────────────────────
  const [srvStatus, setSrvStatus] = useState<SimStatus | null>(null);
  const [srvErr, setSrvErr]       = useState<string | null>(null);

  // ── geometry (for period calculation) ────────────────────────────────────
  const [numPoles, setNumPoles] = useState<number>(28);
  const [numSlots, setNumSlots] = useState<number>(24);

  // ── derived periodicity ───────────────────────────────────────────────────
  // Electrical period = one pole pair in mechanical degrees
  const polePairs      = Math.round(numPoles / 2);
  const elecPeriod_deg = 360 / polePairs;                         // e.g. 25.71°
  // Cogging period = smallest repeating unit of T(θ)
  const coggingPeriod_deg = 360 / lcm(numSlots, numPoles);        // e.g. 2.14°

  // ── form state ────────────────────────────────────────────────────────────
  const [current,     setCurrent]     = useState(10.0);
  const [frequency,   setFrequency]   = useState(50.0);
  const [rpm,         setRpm]         = useState(2000.0);
  const [rotorAngle,  setRotorAngle]  = useState(0.0);
  const [maxSteps,    setMaxSteps]    = useState(10000);
  const [device,      setDevice]      = useState<'cpu' | 'cuda'>('cpu');

  // ── job state ─────────────────────────────────────────────────────────────
  const [jobId,    setJobId]    = useState<string | null>(null);
  const [job,      setJob]      = useState<JobStatus | null>(null);
  const [polling,  setPolling]  = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── load server status + geometry ─────────────────────────────────────────
  useEffect(() => {
    fetch(`${API}/api/simulation/status`)
      .then(r => r.json())
      .then(d => {
        setSrvStatus(d);
        if (d.operating_point) {
          setCurrent(d.operating_point.max_current ?? 10);
          setFrequency(d.operating_point.frequency_hz ?? 50);
          setRpm(d.operating_point.rpm ?? 2000);
        }
      })
      .catch(e => setSrvErr(String(e)));

    fetch(`${API}/api/geometry/summary`)
      .then(r => r.json())
      .then(d => {
        if (d.num_poles) setNumPoles(d.num_poles);
        if (d.num_slots) setNumSlots(d.num_slots);
      })
      .catch(() => {});
  }, []);

  // ── polling ───────────────────────────────────────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setPolling(false);
  }, []);

  useEffect(() => {
    if (!jobId || !polling) return;
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/simulation/result/${jobId}`);
        const d: JobStatus = await r.json();
        setJob(d);
        if (d.status === 'done' || d.status === 'error') stopPolling();
      } catch { /* ignore transient */ }
    }, 1500);
    return stopPolling;
  }, [jobId, polling, stopPolling]);

  // ── run ───────────────────────────────────────────────────────────────────
  const handleRun = async () => {
    setJob(null);
    try {
      const r = await fetch(`${API}/api/simulation/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          max_current:  current,
          frequency:    frequency,
          rpm:          rpm,
          rotor_angle:  rotorAngle,
          max_steps:    maxSteps,
          device:       device,
        }),
      });
      const d: JobStatus = await r.json();
      setJobId(d.job_id);
      setJob(d);
      setPolling(true);
    } catch (e) {
      setSrvErr(String(e));
    }
  };

  const isRunning = job?.status === 'queued' || job?.status === 'running';

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden', bgcolor: '#060d17' }}>

      {/* ── LEFT: controls ── */}
      <Box sx={{
        width: 320, flexShrink: 0, overflowY: 'auto',
        borderRight: '1px solid #1e293b', p: 2,
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>

        {/* Solver badge */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Solver
          </Typography>
          {srvStatus ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              {srvStatus.modulus_available
                ? <Chip icon={<BoltIcon sx={{ fontSize: 13 }}/>} label="NVIDIA Modulus"
                    size="small" color="success" sx={{ fontSize: 10 }} />
                : <Chip label="Dry-run (no Modulus)" size="small" color="warning"
                    sx={{ fontSize: 10 }} />
              }
              <Tooltip title={srvStatus.solver}>
                <InfoOutlinedIcon sx={{ fontSize: 14, color: '#475569', cursor: 'help' }}/>
              </Tooltip>
            </Box>
          ) : (
            <Chip label="Connecting…" size="small" sx={{ fontSize: 10 }}/>
          )}
          {srvStatus && (
            <Typography sx={{ fontSize: 10, color: '#334155', mt: 0.5 }}>
              Br = {srvStatus.operating_point.Br_magnet_T.toFixed(2)} T
              &nbsp;(from materials config)
            </Typography>
          )}
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Operating point */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1.5 }}>
            Operating Point
          </Typography>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <TextField label="Peak Current (A)" type="number" size="small" fullWidth
              value={current} onChange={e => setCurrent(+e.target.value)}
              inputProps={{ step: 1, min: 0, max: 500 }} disabled={isRunning}/>
            <TextField label="Frequency (Hz)" type="number" size="small" fullWidth
              value={frequency} onChange={e => setFrequency(+e.target.value)}
              inputProps={{ step: 10, min: 1, max: 2000 }} disabled={isRunning}/>
            <TextField label="Speed (rpm)" type="number" size="small" fullWidth
              value={rpm} onChange={e => setRpm(+e.target.value)}
              inputProps={{ step: 100, min: 0 }} disabled={isRunning}/>
            <TextField
              label={`Rotor Angle (°)  — period: ${elecPeriod_deg.toFixed(2)}°`}
              type="number" size="small" fullWidth
              value={rotorAngle}
              onChange={e => {
                const v = +e.target.value;
                // wrap into [0, elecPeriod_deg)
                setRotorAngle(parseFloat((((v % elecPeriod_deg) + elecPeriod_deg) % elecPeriod_deg).toFixed(3)));
              }}
              inputProps={{ step: parseFloat((elecPeriod_deg / 12).toFixed(2)), min: 0, max: elecPeriod_deg }}
              helperText={`0 … ${elecPeriod_deg.toFixed(2)}° = 360°/${polePairs} pole pairs`}
              disabled={isRunning}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#475569', mx: 0 } }}
            />
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* PINN training settings */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1.5 }}>
            PINN Training
          </Typography>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <TextField label="Training steps" type="number" size="small" fullWidth
              value={maxSteps} onChange={e => setMaxSteps(+e.target.value)}
              inputProps={{ step: 1000, min: 100, max: 200000 }} disabled={isRunning}/>

            {/* Device toggle */}
            <Box sx={{ display: 'flex', gap: 1 }}>
              {(['cpu', 'cuda'] as const).map(d => (
                <Button key={d} size="small" variant={device === d ? 'contained' : 'outlined'}
                  onClick={() => setDevice(d)} disabled={isRunning}
                  sx={{ flex: 1, fontSize: 11, textTransform: 'uppercase', py: 0.5 }}>
                  {d}
                </Button>
              ))}
            </Box>
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Run button */}
        <Button
          variant="contained" color="primary" fullWidth
          startIcon={isRunning ? <CircularProgress size={14} color="inherit"/> : <PlayArrowIcon/>}
          onClick={handleRun}
          disabled={isRunning}
          sx={{ py: 1.2, fontWeight: 700, letterSpacing: 1 }}
        >
          {isRunning ? 'RUNNING…' : 'RUN SIMULATION'}
        </Button>

        {srvErr && (
          <Alert severity="error" sx={{ fontSize: 11 }}>{srvErr}</Alert>
        )}
      </Box>

      {/* ── RIGHT: results ── */}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 3, display: 'flex', flexDirection: 'column', gap: 3 }}>

        {/* Header */}
        <Box>
          <Typography variant="h6" sx={{ color: '#e2e8f0', fontWeight: 700, mb: 0.5 }}>
            2D Magnetostatics
          </Typography>
          <Typography sx={{ fontSize: 12, color: '#475569' }}>
            Physics-Informed Neural Network · NVIDIA Modulus Sym ·
            ∇·(ν ∇A<sub>z</sub>) = −J
          </Typography>
        </Box>

        {/* Physics overview card */}
        <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', p: 2, borderRadius: 2 }}>
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1.5,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Governing Equation
          </Typography>
          <Box sx={{ fontFamily: 'monospace', fontSize: 12, color: '#94a3b8', lineHeight: 2 }}>
            <Box>∂/∂x(ν ∂A_z/∂x) + ∂/∂y(ν ∂A_z/∂y) = −J_z</Box>
            <Box sx={{ color: '#475569', fontSize: 10, mt: 0.5 }}>
              ν = reluctivity = 1/(μ₀ μᵣ) &nbsp;|&nbsp;
              B_x = ∂A_z/∂y &nbsp;|&nbsp;
              B_y = −∂A_z/∂x
            </Box>
          </Box>

          <Divider sx={{ borderColor: '#1e293b', my: 1.5 }}/>

          {/* Periodicity info */}
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Rotor Periodicity
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, mb: 1.5 }}>
            {[
              { label: 'Pole pairs',       value: polePairs.toString(),                    sub: `${numPoles} poles / 2` },
              { label: 'Electrical period', value: `${elecPeriod_deg.toFixed(2)}°`,        sub: `360° / ${polePairs}` },
              { label: 'Cogging period',    value: `${coggingPeriod_deg.toFixed(3)}°`,     sub: `360° / LCM(${numSlots},${numPoles})` },
              { label: 'Cogging per elec', value: Math.round(elecPeriod_deg / coggingPeriod_deg).toString(), sub: 'samples for full curve' },
            ].map(item => (
              <Box key={item.label} sx={{ bgcolor: '#0f1e35', borderRadius: 1, p: 1,
                border: '1px solid #1e293b' }}>
                <Typography sx={{ fontSize: 9, color: '#475569', textTransform: 'uppercase',
                  letterSpacing: '0.08em' }}>{item.label}</Typography>
                <Typography sx={{ fontSize: 14, fontWeight: 700, color: '#93c5fd',
                  fontVariantNumeric: 'tabular-nums' }}>{item.value}</Typography>
                <Typography sx={{ fontSize: 9, color: '#334155' }}>{item.sub}</Typography>
              </Box>
            ))}
          </Box>
          <Alert severity="info" sx={{ fontSize: 10, py: 0.5, mb: 1.5,
            '& .MuiAlert-message': { py: 0 } }}>
            Full T(θ) curve needs {Math.round(elecPeriod_deg / coggingPeriod_deg)} points × one simulation each,
            or one parametric PINN with θ as input.
          </Alert>

          <Divider sx={{ borderColor: '#1e293b', my: 1.5 }}/>

          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Domains
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
            {[
              { name: 'Stator Core', color: '#3b82f6',  pde: 'μᵣ = 5000' },
              { name: 'Air Gap',     color: '#94a3b8',  pde: 'μᵣ = 1' },
              { name: 'Rotor Core',  color: '#2563eb',  pde: 'μᵣ = 5000' },
              { name: 'Magnets',     color: '#ef4444',  pde: 'Br = 1.2 T' },
              { name: 'Windings',    color: '#f59e0b',  pde: 'J = ±J_peak' },
              { name: 'Shaft',       color: '#64748b',  pde: 'μᵣ = 1000' },
            ].map(d => (
              <Tooltip key={d.name} title={`PDE: ${d.pde}`} placement="top">
                <Chip label={d.name} size="small" sx={{
                  fontSize: 10, height: 20,
                  bgcolor: `${d.color}18`, color: d.color,
                  border: `1px solid ${d.color}44`,
                  cursor: 'help',
                }}/>
              </Tooltip>
            ))}
          </Box>
        </Paper>

        {/* Job progress */}
        {job && (
          <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', p: 2, borderRadius: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
              <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#475569',
                textTransform: 'uppercase', letterSpacing: 1 }}>
                Job {job.job_id}
              </Typography>
              {job.status === 'done'  && <CheckCircleIcon sx={{ fontSize: 16, color: '#4ade80' }}/>}
              {job.status === 'error' && <ErrorIcon       sx={{ fontSize: 16, color: '#f87171' }}/>}
              {isRunning && <CircularProgress size={14} sx={{ color: '#3b82f6' }}/>}
            </Box>

            <Box sx={{ display: 'flex', gap: 1, mb: 1.5 }}>
              <Chip
                label={job.status.toUpperCase()}
                size="small"
                sx={{ fontSize: 10,
                  bgcolor: job.status === 'done' ? '#14532d' : job.status === 'error' ? '#7f1d1d' : '#1e3a5f',
                  color:   job.status === 'done' ? '#4ade80' : job.status === 'error' ? '#f87171' : '#93c5fd',
                }}
              />
              {job.elapsed_s != null && (
                <Chip label={`${job.elapsed_s.toFixed(1)}s`} size="small"
                  sx={{ fontSize: 10, bgcolor: '#1e293b', color: '#64748b' }}/>
              )}
            </Box>

            <LinearProgress
              variant="determinate"
              value={job.progress * 100}
              sx={{ borderRadius: 1, height: 4, bgcolor: '#1e293b',
                '& .MuiLinearProgress-bar': {
                  bgcolor: job.status === 'error' ? '#ef4444' : '#3b82f6',
                }
              }}
            />

            {job.error && (
              <Alert severity="error" sx={{ mt: 1.5, fontSize: 11 }}>{job.error}</Alert>
            )}
          </Paper>
        )}

        {/* Results */}
        {job?.result && (
          <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', p: 2, borderRadius: 2 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#475569',
              textTransform: 'uppercase', letterSpacing: 1, mb: 1.5 }}>
              Results
            </Typography>

            {job.result.status === 'dry_run' && (
              <Alert severity="info" sx={{ fontSize: 11, mb: 1.5 }}>
                Dry-run mode — install NVIDIA Modulus to get real results.
                Geometry domains and PDEs are assembled correctly.
              </Alert>
            )}

            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.2 }}>
              <Row label="Torque"          value={job.result.torque_Nm.toFixed(4)}  unit="N·m" highlight={job.result.torque_Nm !== 0}/>
              <Row label="B max"           value={job.result.B_max_T.toFixed(4)}    unit="T"/>
              <Row label="B mean"          value={job.result.B_mean_T.toFixed(4)}   unit="T"/>
              <Row label="Training steps"  value={job.result.training_steps.toString()}/>
              <Row label="Output dir"      value={job.result.output_dir}/>
            </Box>

            <Divider sx={{ borderColor: '#1e293b', my: 1.5 }}/>

            <Typography sx={{ fontSize: 10, color: '#334155' }}>
              Next steps: open output_dir in ParaView to visualise A_z, B field,
              and H field maps.
            </Typography>
          </Paper>
        )}

        {/* No job yet */}
        {!job && (
          <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Box sx={{ textAlign: 'center', color: '#1e3a5f' }}>
              <BoltIcon sx={{ fontSize: 48, mb: 1 }}/>
              <Typography sx={{ fontSize: 13, color: '#334155' }}>
                Set operating point and press Run
              </Typography>
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
};

export default SimulationPanel;
