import React, { useState, useEffect } from 'react';
import {
  Box, Typography, Button, TextField, Tooltip, Divider, Chip,
  CircularProgress, Table, TableBody, TableCell, TableHead, TableRow,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, Legend, BarChart, Bar, Cell, ReferenceLine,
  ScatterChart, Scatter, ZAxis,
} from 'recharts';
import { useMotorStore } from '../../stores/motorStore';

/**
 * Gradient / coordinate descent panel.
 *
 * Fixes the operating point (Sweep "Point 1" current + rpm) and varies every
 * active whitelisted geometry variable.  Each iteration perturbs every variable
 * ±step (central finite difference) to estimate the gradient of the cost
 *
 *     cost = −(eff/eff0)^w_eff · (td/td0)^w_td + λ·max(0, ripple − ripple_max)
 *
 * then steps downhill with a backtracking line search.  Maximises efficiency ×
 * torque-density while holding ripple ≤ the threshold.
 */
const fmtPct  = (v?: number) => (v == null ? '—' : `${(v * 100).toFixed(2)}%`);
const fmtNum  = (v?: number, d = 2) => (v == null ? '—' : v.toFixed(d));

const DescentPanel: React.FC = () => {
  const {
    sweepConfig, connectedToApi,
    descentRunning, descentState, descentError,
    runDescent, cancelDescent, applyDescentBest, loadLastDescent,
  } = useMotorStore();

  // Re-hydrate the last run's charts from the backend on mount (survives reload).
  useEffect(() => {
    if (!descentState && connectedToApi) loadLastDescent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectedToApi]);

  const [maxIters, setMaxIters] = useState(10);
  const [wEff, setWEff] = useState(1);
  const [wTd, setWTd]   = useState(1);
  const [steps, setSteps] = useState(24);
  const [applied, setApplied] = useState(false);
  // Algorithm: CMA-ES (derivative-free, noise-robust, default) vs the original
  // finite-difference gradient descent.  Symmetry: full disk (−1, accurate
  // ripple, default) vs ¼ sector (4, ~3× faster — good for quick debugging).
  const [algorithm, setAlgorithm] = useState<'cmaes' | 'gradient'>('cmaes');
  const [nSectors, setNSectors]   = useState<-1 | 4>(-1);

  const activeVars = Object.entries(sweepConfig.variations)
    .filter(([, v]) => v.mode !== 'fixed').map(([n]) => n);
  const rippleMax = sweepConfig.rippleThreshold * 100;
  const op0 = sweepConfig.operatingPoints[0];

  const st: any = descentState || {};
  const base = st.baseline;
  const cur  = st.current;
  const best = st.best?.metrics;
  const history: any[] = st.history || [];
  const chartData = history.map((h: any) => ({ ...h, eff_pct: (h.efficiency ?? 0) * 100 }));

  // Descent direction per variable (−∂cost/∂var): >0 → optimizer raises the
  // variable, <0 → lowers it.  Visualises what the optimizer is doing right now.
  const grad: Record<string, number> = st.grad || {};
  const variables: any[] = st.variables || [];
  const gradData = variables
    .map((v: any) => ({ name: v.name, dir: -(grad[v.name] ?? 0) }))
    .filter((d: any) => Number.isFinite(d.dir) && d.dir !== 0)
    .sort((a: any, b: any) => Math.abs(b.dir) - Math.abs(a.dir))
    .slice(0, 12);

  // 2-D objective-space projection: X = torque/mass (Nm/kg), Y = efficiency (%).
  // Every evaluated design is a point, split by the ripple constraint; the
  // accepted iterates form the descent trajectory.
  const points: any[] = st.points || [];
  const toXY = (p: any) => ({ td: p.td, eff: (p.eff ?? 0) * 100, ripple: p.ripple, z: 1 });
  const feasiblePts   = points.filter((p) => p.td != null && p.ripple != null && p.ripple <= rippleMax).map(toXY);
  const infeasiblePts = points.filter((p) => p.td != null && p.ripple != null && p.ripple >  rippleMax).map(toXY);
  const trajPts = history
    .filter((h: any) => h.torque_per_mass != null)
    .map((h: any) => ({ td: h.torque_per_mass, eff: (h.efficiency ?? 0) * 100, iter: h.iter, z: 2 }));
  const bestPt = best?.torque_per_mass != null
    ? [{ td: best.torque_per_mass, eff: (best.efficiency ?? 0) * 100, z: 6 }] : [];

  const launch = () => {
    setApplied(false);
    runDescent({ rippleMax, maxIters, wEff, wTd, steps, algorithm, nSectors });
  };

  const row = (label: string, m: any, cost?: number) => (
    <TableRow>
      <TableCell sx={{ fontWeight: 600, fontSize: 11 }}>{label}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.T_ripple_pct, 2)}%</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtPct(m?.efficiency)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.torque_per_mass, 3)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.T_em_Nm, 1)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.mass_total_kg, 2)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{cost == null ? '—' : cost.toFixed(4)}</TableCell>
    </TableRow>
  );

  return (
    <Box sx={{ mt: 4 }}>
      <Divider sx={{ mb: 2 }} />

      {/* Header / controls */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1 }}>
        <TrendingDownIcon sx={{ fontSize: 18 }} />
        <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 180 }}>
          {algorithm === 'cmaes' ? 'CMA-ES' : 'Gradient descent'} — max efficiency × torque/mass
        </Typography>

        <Tooltip title="Optimization algorithm. CMA-ES: derivative-free, noise-robust evolution strategy (recommended). Gradient: the original finite-difference descent." placement="top">
          <ToggleButtonGroup exclusive size="small" value={algorithm}
            onChange={(_, a) => a && setAlgorithm(a)} sx={{ height: 26 }}>
            <ToggleButton value="cmaes"    sx={{ px: 1, fontSize: 10 }}>CMA-ES</ToggleButton>
            <ToggleButton value="gradient" sx={{ px: 1, fontSize: 10 }}>Gradient</ToggleButton>
          </ToggleButtonGroup>
        </Tooltip>
        <Tooltip title="FEM symmetry for every evaluation. Full disk = accurate ripple (recommended). ¼ sector = ~3× faster but over-reports ripple ~2.7× (good for quick algorithm debugging, not final numbers)." placement="top">
          <ToggleButtonGroup exclusive size="small" value={nSectors}
            onChange={(_, n) => n != null && setNSectors(n)} sx={{ height: 26 }}>
            <ToggleButton value={-1} sx={{ px: 1, fontSize: 10 }}>Full</ToggleButton>
            <ToggleButton value={4}  sx={{ px: 1, fontSize: 10 }}>¼</ToggleButton>
          </ToggleButtonGroup>
        </Tooltip>

        <Tooltip title="Descent iterations. Each: ±step per variable (gradient) + line search." placement="top">
          <TextField label="iters" type="number" size="small" value={maxIters}
            onChange={e => setMaxIters(Math.max(1, Math.min(40, Math.round(+e.target.value) || 1)))}
            inputProps={{ min: 1, max: 40, style: { fontSize: 11, padding: '3px 6px', width: 40 } }}
            InputLabelProps={{ sx: { fontSize: 10 } }} />
        </Tooltip>
        <Tooltip title="FEM frames per period. Higher → cleaner ripple gradient, but slower." placement="top">
          <TextField label="steps/T" type="number" size="small" value={steps}
            onChange={e => setSteps(Math.max(8, Math.min(72, Math.round(+e.target.value) || 24)))}
            inputProps={{ min: 8, max: 72, style: { fontSize: 11, padding: '3px 6px', width: 40 } }}
            InputLabelProps={{ sx: { fontSize: 10 } }} />
        </Tooltip>
        <Tooltip title="Efficiency weight in the objective (exponent on eff/eff₀)." placement="top">
          <TextField label="w·eff" type="number" size="small" value={wEff}
            onChange={e => setWEff(Math.max(0, +e.target.value || 0))}
            inputProps={{ min: 0, step: 0.5, style: { fontSize: 11, padding: '3px 6px', width: 40 } }}
            InputLabelProps={{ sx: { fontSize: 10 } }} />
        </Tooltip>
        <Tooltip title="Torque-density weight (exponent on td/td₀)." placement="top">
          <TextField label="w·Nm/kg" type="number" size="small" value={wTd}
            onChange={e => setWTd(Math.max(0, +e.target.value || 0))}
            inputProps={{ min: 0, step: 0.5, style: { fontSize: 11, padding: '3px 6px', width: 44 } }}
            InputLabelProps={{ sx: { fontSize: 10 } }} />
        </Tooltip>

        {descentRunning ? (
          <Button variant="contained" color="error" size="small" startIcon={<StopIcon />}
            onClick={() => cancelDescent()}>
            Stop {st.iter ?? 0}/{st.max_iters ?? maxIters}
          </Button>
        ) : (
          <Button variant="contained" size="small" startIcon={<PlayArrowIcon />}
            disabled={activeVars.length === 0 || !connectedToApi}
            onClick={launch}>
            Run descent
          </Button>
        )}
      </Box>

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
        Fixed operating point: <strong>{op0.current_a} A @ {op0.rpm} rpm, γ={op0.gamma_deg ?? 0}°</strong> · variables:{' '}
        <strong>{activeVars.length}</strong> · ripple ≤ <strong>{rippleMax.toFixed(1)}%</strong>{' '}
        (Torque Ripple Constraint slider). Only whitelisted variables are varied.
      </Typography>

      {descentError && (
        <Typography color="error" variant="caption" sx={{ display: 'block', mb: 1 }}>
          Descent error: {descentError}
        </Typography>
      )}

      {/* Progress line */}
      {(descentRunning || history.length > 0) && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
          {descentRunning && <CircularProgress size={14} />}
          <Chip size="small" variant="outlined"
            label={`iter ${st.iter ?? 0}/${st.max_iters ?? maxIters}`} sx={{ height: 20, fontSize: 10 }} />
          <Chip size="small" variant="outlined"
            label={`${st.n_evals ?? 0} FEM evals`} sx={{ height: 20, fontSize: 10 }} />
          {best && (
            <Chip size="small" color="success" variant="outlined"
              label={`best F = ${st.best?.F?.toFixed?.(4) ?? '—'}`} sx={{ height: 20, fontSize: 10 }} />
          )}
        </Box>
      )}

      {/* Metrics table: baseline / current / best */}
      {base && (
        <Table size="small" sx={{ mb: 2, '& td, & th': { py: 0.4, px: 1 } }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ fontSize: 10, color: 'text.secondary' }}>—</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Ripple</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Eff</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Nm/kg</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>T (Nm)</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Mass</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>cost</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {row('Baseline', base, history[0]?.cost ?? 0)}
            {cur && row('Current', cur)}
            {best && row('Best', best, st.best?.cost)}
          </TableBody>
        </Table>
      )}

      {/* Convergence + metric trajectories */}
      {chartData.length > 1 && (
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mb: 2 }}>
          {/* Convergence: figure of merit F ↑ and ripple (constraint) */}
          <Box sx={{ flex: 1, minWidth: 320 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              Convergence — F = eff×Nm/kg (↑, green) · ripple (orange, limit in red)
            </Typography>
            <Box sx={{ height: 200 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
                  <XAxis dataKey="iter" tick={{ fontSize: 10 }} />
                  <YAxis yAxisId="l" tick={{ fontSize: 10 }} width={42} domain={['auto', 'auto']} />
                  <YAxis yAxisId="r" orientation="right" tick={{ fontSize: 10 }} width={34} />
                  <RTooltip contentStyle={{ fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <ReferenceLine yAxisId="l" y={1} stroke="#888" strokeDasharray="4 4" />
                  <ReferenceLine yAxisId="r" y={rippleMax} stroke="#ef4444" strokeDasharray="4 4" />
                  <Line yAxisId="l" type="monotone" dataKey="F" name="F" stroke="#22c55e" dot={{ r: 2 }} strokeWidth={2} isAnimationActive={false} />
                  <Line yAxisId="r" type="monotone" dataKey="T_ripple_pct" name="Ripple %" stroke="#f59e0b" dot={{ r: 2 }} strokeWidth={2} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </Box>
          </Box>

          {/* Metric trajectories: torque density + efficiency */}
          <Box sx={{ flex: 1, minWidth: 320 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              Metrics — torque/mass (blue) · efficiency % (purple)
            </Typography>
            <Box sx={{ height: 200 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
                  <XAxis dataKey="iter" tick={{ fontSize: 10 }} />
                  <YAxis yAxisId="l" tick={{ fontSize: 10 }} width={42} domain={['auto', 'auto']} />
                  <YAxis yAxisId="r" orientation="right" tick={{ fontSize: 10 }} width={42} domain={['auto', 'auto']} />
                  <RTooltip contentStyle={{ fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Line yAxisId="l" type="monotone" dataKey="torque_per_mass" name="Nm/kg" stroke="#3b82f6" dot={{ r: 2 }} strokeWidth={2} isAnimationActive={false} />
                  <Line yAxisId="r" type="monotone" dataKey="eff_pct" name="Eff %" stroke="#a855f7" dot={{ r: 2 }} strokeWidth={2} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </Box>
          </Box>
        </Box>
      )}

      {/* 2-D objective-space projection: efficiency (Y) vs torque/mass (X) */}
      {(feasiblePts.length + infeasiblePts.length) > 1 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
            Objective space — efficiency vs torque/mass ·{' '}
            <span style={{ color: '#22c55e' }}>feasible</span> /{' '}
            <span style={{ color: '#ef4444' }}>ripple&gt;limit</span> ·{' '}
            <span style={{ color: '#3b82f6' }}>descent path</span> ·{' '}
            <span style={{ color: '#fbbf24' }}>★ best</span>
          </Typography>
          <Box sx={{ height: 460, width: '100%' }}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 24, left: 8, bottom: 18 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
                <XAxis type="number" dataKey="td" name="Nm/kg" domain={['auto', 'auto']} tick={{ fontSize: 10 }}
                  label={{ value: 'Torque / mass  (Nm/kg)', position: 'insideBottom', offset: -10, fontSize: 11 }} />
                <YAxis type="number" dataKey="eff" name="Eff %" domain={['auto', 'auto']} tick={{ fontSize: 10 }} width={48}
                  label={{ value: 'Efficiency %', angle: -90, position: 'insideLeft', fontSize: 11 }} />
                <ZAxis type="number" dataKey="z" range={[10, 150]} />
                <RTooltip cursor={{ strokeDasharray: '3 3' }}
                  formatter={(v: any, n: any) => [typeof v === 'number' ? v.toFixed(3) : v, n]} />
                <Scatter name="ripple>limit" data={infeasiblePts} fill="#ef4444" fillOpacity={0.35} isAnimationActive={false} />
                <Scatter name="feasible" data={feasiblePts} fill="#22c55e" fillOpacity={0.55} isAnimationActive={false} />
                <Scatter name="descent path" data={trajPts} fill="#3b82f6"
                  line={{ stroke: '#3b82f6', strokeWidth: 1.5 }} lineType="joint" isAnimationActive={false} />
                <Scatter name="★ best" data={bestPt} fill="#fbbf24" shape="star" isAnimationActive={false} />
              </ScatterChart>
            </ResponsiveContainer>
          </Box>
        </Box>
      )}

      {/* Descent direction per variable (−∂cost/∂var) — the optimizer at work */}
      {gradData.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
            Descent direction (−∂cost/∂var): <span style={{ color: '#22c55e' }}>green ↑ raises</span>{' '}
            the variable, <span style={{ color: '#ef4444' }}>red ↓ lowers</span> it — length = sensitivity
          </Typography>
          <Box sx={{ height: Math.max(120, gradData.length * 24) }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart layout="vertical" data={gradData} margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
                <XAxis type="number" tick={{ fontSize: 9 }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={130} />
                <RTooltip contentStyle={{ fontSize: 11 }} formatter={(v: any) => [Number(v).toExponential(2), 'dir']} />
                <ReferenceLine x={0} stroke="#888" />
                <Bar dataKey="dir" isAnimationActive={false}>
                  {gradData.map((d: any, i: number) => (
                    <Cell key={i} fill={d.dir >= 0 ? '#22c55e' : '#ef4444'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Box>
        </Box>
      )}

      {/* Apply best */}
      {best && !descentRunning && (
        <Button
          variant="outlined" color="success" size="small"
          startIcon={applied ? <CheckCircleIcon /> : <PlayArrowIcon />}
          disabled={applied}
          onClick={async () => { await applyDescentBest(); setApplied(true); }}
        >
          {applied ? 'Applied to geometry' : 'Apply best to geometry'}
        </Button>
      )}
    </Box>
  );
};

export default DescentPanel;
