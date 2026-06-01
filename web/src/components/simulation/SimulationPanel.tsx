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
import SimulationCharts from './SimulationCharts';
import PhysicsDashboard from './PhysicsDashboard';

// NOTE: using port 8001 (new backend with loss calculations)
// Change back to 8000 after restarting the main backend
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

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
    // Copper losses (always available)
    P_cu_total_W?: number;
    R_phase_ohm?: number;
    R_coil_ohm?: number;
    L_turn_mm?: number;
    I_coil_rms_A?: number;
    // Iron / magnet losses (require PINN)
    P_fe_stator_W?: number | null;
    P_fe_rotor_W?: number | null;
    P_mag_eddy_W?: number | null;
    // Power & efficiency
    P_mech_W?: number | null;
    P_input_W?: number | null;
    P_loss_total_W?: number;
    efficiency_pct?: number | null;
    note?: string;
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

// ── winding connection definitions ───────────────────────────────────────────
type ConnectionKey = '4S' | '2P2S' | '4P';
const CONNECTIONS: { key: ConnectionKey; label: string; nP: number; nS: number; desc: string }[] = [
  { key: '4S',   label: '4S',   nP: 1, nS: 4, desc: '4 series — max voltage' },
  { key: '2P2S', label: '2P·2S', nP: 2, nS: 2, desc: '2 parallel × 2 series' },
  { key: '4P',   label: '4P',   nP: 4, nS: 1, desc: '4 parallel — max current' },
];

const SimulationPanel: React.FC = () => {
  // ── server status ─────────────────────────────────────────────────────────
  const [srvStatus, setSrvStatus] = useState<SimStatus | null>(null);
  const [srvErr, setSrvErr]       = useState<string | null>(null);

  // ── geometry (for period + winding calc) ─────────────────────────────────
  const [numPoles,      setNumPoles]      = useState<number>(28);
  const [numSlots,      setNumSlots]      = useState<number>(24);
  const [nWiresPerSlot, setNWiresPerSlot] = useState<number>(14);
  const [nCoilsPerPhase, setNCoilsPerPhase] = useState<number>(4);

  // ── winding connection ────────────────────────────────────────────────────
  const [connection, setConnection] = useState<ConnectionKey>('2P2S');
  const connDef = CONNECTIONS.find(c => c.key === connection)!;

  // ── derived periodicity ───────────────────────────────────────────────────
  const polePairs         = Math.round(numPoles / 2);
  const elecPeriod_deg    = 360 / polePairs;
  const coggingPeriod_deg = 360 / lcm(numSlots, numPoles);

  // ── form state (current = I_phase_rms) ───────────────────────────────────
  const [current,       setCurrent]       = useState(85.0);
  const [frequency,     setFrequency]     = useState(921.67);
  const [rpm,           setRpm]           = useState(3950.0);
  const [rotorAngle,    setRotorAngle]    = useState(0.0);
  const [phaseOffset,   setPhaseOffset]   = useState(0.0);   // γ [deg]
  const [maxSteps,      setMaxSteps]      = useState(10000);
  const [device,        setDevice]        = useState<'cpu' | 'cuda'>('cpu');

  // ── derived winding values ────────────────────────────────────────────────
  const I_coil_rms  = current / connDef.nP;                  // Arms per coil
  const I_coil_peak = I_coil_rms * Math.sqrt(2);             // A peak per coil
  const ampTurns    = nWiresPerSlot * I_coil_rms;            // A·turns per slot

  // ── derived phase currents (for display) ─────────────────────────────────
  // γ=0 ⇒ q-axis (max torque) — add +π/2 so the cos→ phase A peaks at q-axis
  const thetaElec_deg = rotorAngle * polePairs + phaseOffset + 90;
  const thetaElec_rad = thetaElec_deg * Math.PI / 180;
  const I_A = I_coil_peak * Math.cos(thetaElec_rad);
  const I_B = I_coil_peak * Math.cos(thetaElec_rad - 2 * Math.PI / 3);
  const I_C = I_coil_peak * Math.cos(thetaElec_rad + 2 * Math.PI / 3);

  // ── job state ─────────────────────────────────────────────────────────────
  const [jobId,   setJobId]   = useState<string | null>(null);
  const [job,     setJob]     = useState<JobStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── load server status + geometry ─────────────────────────────────────────
  useEffect(() => {
    fetch(`${API}/api/simulation/status`)
      .then(r => r.json())
      .then(d => {
        setSrvStatus(d);
        if (d.operating_point) {
          setCurrent(d.operating_point.max_current ?? 85);
          setFrequency(d.operating_point.frequency_hz ?? 921.67);
          setRpm(d.operating_point.rpm ?? 3950);
        }
      })
      .catch(e => setSrvErr(String(e)));

    // Get geometry for periodicity + winding
    fetch(`${API}/api/config`)
      .then(r => r.json())
      .then(d => {
        const g = d.geometry ?? {};
        if (g.num_poles)        setNumPoles(g.num_poles);
        if (g.num_slots)        setNumSlots(g.num_slots);
        if (g.num_wires_per_slot) setNWiresPerSlot(g.num_wires_per_slot);
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
          max_current:      parseFloat(I_coil_peak.toFixed(2)),  // peak A per coil
          frequency:        frequency,
          rpm:              rpm,
          rotor_angle:      rotorAngle,
          phase_offset_deg: phaseOffset,
          max_steps:        maxSteps,
          device:           device,
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

        {/* Rotor periodicity (moved from right panel) */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Rotor Periodicity
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0.6 }}>
            {[
              { label: 'Pole pairs',        value: polePairs.toString(),                                       sub: `${numPoles} poles / 2` },
              { label: 'Electrical period', value: `${elecPeriod_deg.toFixed(2)}°`,                            sub: `360° / ${polePairs}` },
              { label: 'Cogging period',    value: `${coggingPeriod_deg.toFixed(3)}°`,                         sub: `360° / LCM(${numSlots},${numPoles})` },
              { label: 'Cogging / elec',    value: Math.round(elecPeriod_deg / coggingPeriod_deg).toString(),  sub: 'samples' },
            ].map(item => (
              <Box key={item.label} sx={{ bgcolor: '#0a1628', borderRadius: 1,
                px: 0.8, py: 0.6, border: '1px solid #1e293b' }}>
                <Typography sx={{ fontSize: 8.5, color: '#475569',
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {item.label}
                </Typography>
                <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#93c5fd',
                  fontVariantNumeric: 'tabular-nums', lineHeight: 1.1 }}>
                  {item.value}
                </Typography>
                <Typography sx={{ fontSize: 8.5, color: '#334155',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {item.sub}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Winding connection */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Winding Connection
          </Typography>
          <Typography sx={{ fontSize: 10, color: '#334155', mb: 1.2 }}>
            {nCoilsPerPhase} coils/phase · {nWiresPerSlot} wires/slot
          </Typography>

          {/* Connection buttons */}
          <Box sx={{ display: 'flex', gap: 0.75, mb: 1.5 }}>
            {CONNECTIONS.map(c => (
              <Tooltip key={c.key} title={c.desc} placement="top">
                <Button
                  size="small"
                  variant={connection === c.key ? 'contained' : 'outlined'}
                  onClick={() => setConnection(c.key)}
                  disabled={isRunning}
                  sx={{ flex: 1, fontSize: 11, fontWeight: 700, py: 0.5,
                    textTransform: 'none',
                    ...(connection === c.key ? {} : { color: '#64748b', borderColor: '#334155' })
                  }}
                >
                  {c.label}
                </Button>
              </Tooltip>
            ))}
          </Box>

          {/* Derived values */}
          <Box sx={{ bgcolor: '#0a1628', borderRadius: 1, p: 1.2,
            border: '1px solid #1e293b', display: 'flex', flexDirection: 'column', gap: 0.4 }}>
            {[
              { label: 'Parallel branches',  value: `${connDef.nP}` },
              { label: 'Series coils/branch', value: `${connDef.nS}` },
              { label: 'I coil (RMS)',        value: `${I_coil_rms.toFixed(1)} A` },
              { label: 'I coil (peak) →sim',  value: `${I_coil_peak.toFixed(1)} A`, hi: true },
              { label: 'A·turns / slot',      value: `${ampTurns.toFixed(0)} At` },
            ].map(row => (
              <Box key={row.label} sx={{ display: 'flex', justifyContent: 'space-between' }}>
                <Typography sx={{ fontSize: 10, color: '#475569' }}>{row.label}</Typography>
                <Typography sx={{ fontSize: 11, fontWeight: 600,
                  color: (row as any).hi ? '#4ade80' : '#94a3b8',
                  fontVariantNumeric: 'tabular-nums' }}>
                  {row.value}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Operating point */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1.5 }}>
            Operating Point
          </Typography>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <TextField label="I phase RMS (Arms)" type="number" size="small" fullWidth
              value={current} onChange={e => setCurrent(+e.target.value)}
              inputProps={{ step: 5, min: 0, max: 500 }} disabled={isRunning}
              helperText={`I coil peak = ${I_coil_peak.toFixed(1)} A → sent to solver`}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#3b82f6', mx: 0 } }}/>
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

            {/* Phase offset γ */}
            <TextField
              label="Phase Offset γ (°)"
              type="number" size="small" fullWidth
              value={phaseOffset}
              onChange={e => setPhaseOffset(+e.target.value)}
              inputProps={{ step: 5, min: -180, max: 180 }}
              helperText="0°=q-axis (max torque)  ±90°=d-axis (field weakening)"
              disabled={isRunning}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#475569', mx: 0 } }}
            />

            {/* Instantaneous phase currents */}
            <Box sx={{ bgcolor: '#0a1628', borderRadius: 1, p: 1,
              border: '1px solid #1e293b' }}>
              <Typography sx={{ fontSize: 9, color: '#475569', textTransform: 'uppercase',
                letterSpacing: '0.08em', mb: 0.5 }}>
                Slot currents at θ={rotorAngle}° + γ={phaseOffset}° (peak)
              </Typography>
              {[
                { ph: 'A', val: I_A, color: '#f87171' },
                { ph: 'B', val: I_B, color: '#4ade80' },
                { ph: 'C', val: I_C, color: '#60a5fa' },
              ].map(r => (
                <Box key={r.ph} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.2 }}>
                  <Typography sx={{ fontSize: 10, color: r.color, fontWeight: 700, width: 16 }}>
                    {r.ph}
                  </Typography>
                  {/* bar */}
                  <Box sx={{ flex: 1, height: 6, bgcolor: '#1e293b', borderRadius: 1, overflow: 'hidden' }}>
                    <Box sx={{
                      height: '100%', borderRadius: 1,
                      width: `${Math.abs(r.val) / I_coil_peak * 100}%`,
                      bgcolor: r.val >= 0 ? r.color : '#475569',
                      ml: r.val < 0 ? `${(1 - Math.abs(r.val) / I_coil_peak) * 100}%` : 0,
                    }}/>
                  </Box>
                  <Typography sx={{ fontSize: 10, color: '#94a3b8', fontVariantNumeric: 'tabular-nums', width: 52, textAlign: 'right' }}>
                    {r.val >= 0 ? '+' : ''}{r.val.toFixed(1)} A
                  </Typography>
                </Box>
              ))}
            </Box>
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

        {/* Header + Physics overview card removed by user request.
            • The "2D Magnetostatics / Governing equation / Rotor
              periodicity / Domains" block is dropped entirely.
            • Rotor-periodicity info now lives in the LEFT control
              panel, in a compact 2×2 grid right under the Solver
              badge.  See <Box>{Rotor Periodicity}</Box> above. */}
        <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', p: 2,
          borderRadius: 2, display: 'none' }}>
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
            {job.result.status === 'dry_run' && (
              <Alert severity="info" sx={{ fontSize: 10, mb: 1.5, py: 0.5,
                '& .MuiAlert-message': { py: 0 } }}>
                Dry-run: copper losses computed. Install NVIDIA Modulus for torque, iron &amp; magnet losses, η.
              </Alert>
            )}

            {/* ── Efficiency banner ── */}
            {job.result.efficiency_pct != null && (
              <Box sx={{ textAlign: 'center', py: 1.5, mb: 1.5,
                bgcolor: '#0a2010', borderRadius: 1, border: '1px solid #14532d' }}>
                <Typography sx={{ fontSize: 28, fontWeight: 800,
                  color: job.result.efficiency_pct > 90 ? '#4ade80' : '#fbbf24' }}>
                  {job.result.efficiency_pct.toFixed(1)} %
                </Typography>
                <Typography sx={{ fontSize: 10, color: '#16a34a' }}>efficiency η</Typography>
              </Box>
            )}

            {/* ── Power balance ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#3b82f6',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Power Balance
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="Torque"    value={job.result.torque_Nm.toFixed(4)} unit="N·m"
                   highlight={job.result.torque_Nm !== 0}/>
              <Row label="P mech"   value={job.result.P_mech_W != null ? job.result.P_mech_W.toFixed(0) : '—'} unit="W"
                   highlight={(job.result.P_mech_W ?? 0) > 0}/>
              <Row label="P input"  value={job.result.P_input_W != null ? job.result.P_input_W.toFixed(0) : '—'} unit="W"/>
            </Box>

            {/* ── Losses breakdown ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#ef4444',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Losses
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="Cu (winding)"   value={job.result.P_cu_total_W != null ? job.result.P_cu_total_W.toFixed(1) : '—'} unit="W"/>
              <Row label="Fe stator"      value={job.result.P_fe_stator_W != null ? job.result.P_fe_stator_W.toFixed(1) : '— (need Modulus)'} unit={job.result.P_fe_stator_W != null ? 'W' : ''}/>
              <Row label="Fe rotor"       value={job.result.P_fe_rotor_W  != null ? job.result.P_fe_rotor_W.toFixed(1)  : '— (need Modulus)'} unit={job.result.P_fe_rotor_W  != null ? 'W' : ''}/>
              <Row label="Mag eddy"       value={job.result.P_mag_eddy_W  != null ? job.result.P_mag_eddy_W.toFixed(1)  : '— (need Modulus)'} unit={job.result.P_mag_eddy_W  != null ? 'W' : ''}/>
              <Row label="Total losses"   value={job.result.P_loss_total_W != null ? job.result.P_loss_total_W.toFixed(1) : '—'} unit="W"/>
            </Box>

            {/* ── Winding params ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#475569',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Winding (computed)
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="R phase"    value={job.result.R_phase_ohm != null ? (job.result.R_phase_ohm * 1000).toFixed(2) : '—'} unit="mΩ"/>
              <Row label="L turn"     value={job.result.L_turn_mm != null ? job.result.L_turn_mm.toFixed(1) : '—'} unit="mm"/>
              <Row label="I coil rms" value={job.result.I_coil_rms_A != null ? job.result.I_coil_rms_A.toFixed(1) : '—'} unit="A"/>
            </Box>

            {/* ── Field ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#475569',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Magnetic Field
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
              <Row label="B max"  value={job.result.B_max_T.toFixed(4)}  unit="T"/>
              <Row label="B mean" value={job.result.B_mean_T.toFixed(4)} unit="T"/>
              <Row label="Steps"  value={job.result.training_steps.toString()}/>
            </Box>

            <Divider sx={{ borderColor: '#1e293b', my: 1.5 }}/>

            <Typography sx={{ fontSize: 10, color: '#334155' }}>
              Next steps: open output_dir in ParaView to visualise A_z, B field,
              and H field maps.
            </Typography>
          </Paper>
        )}

        {/* No job yet — show bolt icon */}
        {!job && (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', py: 4 }}>
            <Box sx={{ textAlign: 'center', color: '#1e3a5f' }}>
              <BoltIcon sx={{ fontSize: 48, mb: 1 }}/>
              <Typography sx={{ fontSize: 13, color: '#334155' }}>
                Set operating point and press Run
              </Typography>
            </Box>
          </Box>
        )}

        {/* Analytical SimulationCharts (currents / voltages / losses) removed —
            the FEM transient panel inside PhysicsDashboard below shows all
            three waveforms computed from the actual mesh solve. */}

        {/* ── Physics dashboard — analytical + FEM-comparable ── */}
        <PhysicsDashboard
          rotorAngle_deg={rotorAngle}
          gamma_deg={phaseOffset}
          I_phase_rms={current}
          pinnLosses={job?.result ?? null}
        />

      </Box>
    </Box>
  );
};

export default SimulationPanel;
