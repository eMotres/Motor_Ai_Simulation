import React, { useState, useEffect, useRef } from 'react';
import {
  Box, Typography, Button, TextField, Tooltip, Divider, Chip,
  CircularProgress, LinearProgress, Table, TableBody, TableCell, TableHead, TableRow,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, Legend, BarChart, Bar, Cell, ReferenceLine,
  ScatterChart, Scatter, ZAxis, LabelList,
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

type BoundaryFlag = {
  name: string; label: string; value: number; min: number; max: number;
  pinned: 'low' | 'high'; atHard: boolean;
};

/**
 * Variables whose optimum landed within MARGIN of a window edge → the true
 * optimum is probably OUTSIDE the ±deviation window (an "active constraint").
 * atHard = the window is already at the schema's physical min/max, so it's a real
 * limit (stop), not just a too-narrow window (soft → re-centerable by box-walking).
 * Reads only what /descent/progress already returns (best.x + variables).
 */
function boundaryFlags(st: any, schema: any[], margin = 0.05): BoundaryFlag[] {
  const best = st?.best?.x || st?.result?.best?.overrides || {};
  const vars = st?.variables || st?.result?.variables || [];
  const meta = new Map((schema || []).map((p: any) => [p.name, p]));
  const out: BoundaryFlag[] = [];
  for (const v of vars) {
    const lo = Number(v.lo ?? v.min), hi = Number(v.hi ?? v.max);
    const x = Number(best[v.name]);
    if (!Number.isFinite(x) || !(hi > lo)) continue;
    const m = margin * (hi - lo);
    const pinned: 'low' | 'high' | null = x >= hi - m ? 'high' : x <= lo + m ? 'low' : null;
    if (!pinned) continue;
    const p: any = meta.get(v.name);
    const hMin = p?.min, hMax = p?.max;
    const atHard = (pinned === 'high' && hMax != null && hi >= Number(hMax) - 1e-9)
                || (pinned === 'low'  && hMin != null && lo <= Number(hMin) + 1e-9);
    out.push({ name: v.name, label: p?.label || v.name, value: x, min: lo, max: hi, pinned, atHard });
  }
  return out;
}

const DescentPanel: React.FC = () => {
  const {
    sweepConfig, connectedToApi, parameterSchema,
    descentRunning, descentState, descentError,
    runDescent, cancelDescent, applyDescentBest, loadLastDescent,
    updateSweepConstraints,
  } = useMotorStore();

  // Re-hydrate the last run's charts from the backend on mount (survives reload).
  useEffect(() => {
    if (!descentState && connectedToApi) loadLastDescent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectedToApi]);

  // Load-line comparison (baseline vs optimized across current), served as a
  // static file from web/public — efficiency vs torque/mass with current stepped
  // along each curve, so a vertical (same Nm/kg) shows which design is more
  // efficient at the same load.
  const [loadLines, setLoadLines] = useState<Record<string, any[]> | null>(null);
  useEffect(() => {
    fetch('/last_loadline.json').then(r => (r.ok ? r.json() : null))
      .then(d => { if (d && typeof d === 'object' && Object.keys(d).length) setLoadLines(d); })
      .catch(() => {});
  }, []);

  // Seed the rated-duty constraint defaults once if the persisted config predates
  // them, so they show + persist (server-side) without needing a manual edit.
  useEffect(() => {
    if (sweepConfig.ratedTorqueNm == null || sweepConfig.vBusV == null || sweepConfig.modulation == null) {
      updateSweepConstraints({
        ratedTorqueNm: sweepConfig.ratedTorqueNm ?? 30.5,
        vBusV: sweepConfig.vBusV ?? 140,
        modulation: sweepConfig.modulation ?? 'svpwm',
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Distinct from the scatter's own colors (green feasible / red infeasible /
  // blue descent path / yellow best) so the overlaid finalist segments read clearly.
  const LL_PALETTE = ['#f97316', '#a855f7', '#22d3ee', '#ec4899', '#fb923c', '#c084fc'];
  const loadLineDesigns: [string, any[]][] = loadLines
    ? Object.entries(loadLines).filter(([, a]) => Array.isArray(a) && a.length > 0)
    : [];

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
  const [mtpa, setMtpa]           = useState(true);   // optimize γ (MTPA) before the geometry search
  // Box-walking: keep re-centering the ±deviation window on the optimum until
  // every variable settles inside its window (or hits a physical limit).
  const [autoWalk, setAutoWalk]   = useState(false);
  const [maxRounds, setMaxRounds] = useState(5);
  const [round, setRound]         = useState(0);      // 1-based walk round while auto-walking
  const [walking, setWalking]     = useState(false);  // an auto-walk sequence (≥1 round) is in flight
  const walkCancel = useRef(false);
  const localRun = useRef(false);   // true while THIS panel is the one running (its runDescent polls)

  // Live mirror: while a run is in progress server-side (even one started in
  // another tab/browser), poll progress so EVERY open panel shows it live.  Skips
  // when this panel started the run (its own runDescent already polls).
  useEffect(() => {
    if (!connectedToApi) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (!alive) return;
      if (!localRun.current) await loadLastDescent();
      if (!alive) return;
      const running = (useMotorStore.getState().descentState as any)?.running;
      timer = setTimeout(tick, running ? 1500 : 4000);
    };
    timer = setTimeout(tick, 1500);
    return () => { alive = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectedToApi]);

  const activeVars = Object.entries(sweepConfig.variations)
    .filter(([, v]) => v.mode !== 'fixed').map(([n]) => n);
  const rippleMax = sweepConfig.rippleThreshold * 100;
  const op0 = sweepConfig.operatingPoints[0];
  // Rated-duty constraints → usable peak-phase voltage limit from the DC bus + PWM scheme.
  const ratedTorque = sweepConfig.ratedTorqueNm ?? 30.5;
  const vBus = sweepConfig.vBusV ?? 140;
  const modulation = sweepConfig.modulation ?? 'svpwm';
  const MOD_FACTOR: Record<string, number> = { svpwm: 1 / Math.sqrt(3), sine: 0.5, sixstep: 2 / Math.PI };
  const vPeakLimit = vBus > 0 ? vBus * (MOD_FACTOR[modulation] ?? MOD_FACTOR.svpwm) : 1e9;
  // Rated operating point ON each load line: interpolate where T = rated torque,
  // so a diamond marks the duty point on every curve.
  const ratedMarkers = (sweepConfig.ratedTorqueNm && sweepConfig.ratedTorqueNm > 0
    ? loadLineDesigns.map(([name, arr], i) => {
        const pts = (arr || []).filter((p: any) => typeof p.T === 'number')
          .sort((a: any, b: any) => a.T - b.T);
        for (let j = 0; j < pts.length - 1; j++) {
          if (pts[j].T <= ratedTorque && ratedTorque <= pts[j + 1].T) {
            const a = pts[j], b = pts[j + 1], f = (ratedTorque - a.T) / ((b.T - a.T) || 1);
            return { name, color: LL_PALETTE[i % LL_PALETTE.length], z: 6,
                     td: a.td + f * (b.td - a.td), eff: a.eff + f * (b.eff - a.eff),
                     I: Math.round(a.I + f * (b.I - a.I)) };
          }
        }
        return null;
      }).filter(Boolean) as any[]
    : []);

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

  // Boundary-active variables (optimum at a window edge).  Only meaningful for a
  // finished run.  Soft = re-centerable; hard = window already at the schema's
  // physical limit → a genuine constraint (the banner offers a one-click continue
  // for the soft ones; box-walking does it automatically when Auto-walk is on).
  const boundary = (!descentRunning && !walking && (best || st.result))
    ? boundaryFlags(st, parameterSchema) : [];
  const softPinned = boundary.filter((f) => !f.atHard);

  // Live status: what the optimizer is doing right now (so a long run visibly
  // works).  phase comes from the backend (mtpa γ sweep / optimizing); the walk
  // round is frontend-local.
  const phase = st.phase as string | undefined;
  const mtpaG = st.mtpa_gamma_deg;
  const genText = phase === 'mtpa'     ? 'MTPA γ — поиск угла макс. момента…'
                : phase === 'baseline' ? 'расчёт опорной точки (baseline)…'
                : phase === 'starting' ? 'построение сетки…'
                : descentRunning       ? `поколение ${st.iter ?? 0}/${st.max_iters ?? maxIters}`
                : 'готово';
  const statusText = ((walking && autoWalk) ? `Раунд ${round}/${maxRounds} · ` : '') + genText;
  const busyIndeterminate = phase === 'mtpa' || phase === 'baseline' || phase === 'starting';
  const genPct = Math.min(100, Math.round(100 * (Number(st.iter) || 0) / Math.max(1, Number(st.max_iters) || maxIters)));
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

  const runOneRound = () =>
    runDescent({ rippleMax, maxIters, wEff, wTd, steps, algorithm, nSectors,
                 targetTorque: ratedTorque, vPeakLimit, optimizeGamma: mtpa });

  // Box-walking: optimize; if a variable pins to its window edge and Auto-walk is
  // on, re-center the window on the optimum (Apply best → the ±deviation window
  // follows the geometry) and re-run — until every variable settles inside its
  // window, hits a physical (schema) limit, or maxRounds.  Auto-walk off → a
  // single round, then the boundary banner offers a manual one-click continue.
  const launch = async () => {
    setApplied(false);
    walkCancel.current = false;
    localRun.current = true;
    setWalking(true);
    const cap = autoWalk ? Math.max(1, maxRounds) : 1;
    try {
      for (let r = 0; r < cap; r++) {
        setRound(r + 1);
        await runOneRound();
        if (walkCancel.current) break;
        const soft = boundaryFlags(useMotorStore.getState().descentState, parameterSchema)
          .filter((f) => !f.atHard);
        if (!autoWalk || soft.length === 0 || r + 1 >= cap) break;
        await applyDescentBest();   // re-center the window on the optimum for the next round
      }
    } finally {
      setWalking(false);
      setRound(0);
      localRun.current = false;
    }
  };

  // Manual "continue in the same direction": re-center on the optimum, run once more.
  const continueWalk = async () => { await applyDescentBest(); await launch(); };
  const stopWalk = () => { walkCancel.current = true; cancelDescent(); };

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

        {/* ── Rated-duty constraints ─────────────────────────────────────── */}
        <Tooltip title="Rated shaft torque to optimize at (Nm). Each geometry is evaluated at the current that delivers THIS torque (not a fixed current). 0 = use the operating-point current." placement="top">
          <TextField label="T rated, Nm" type="number" size="small" value={ratedTorque}
            onChange={e => updateSweepConstraints({ ratedTorqueNm: Math.max(0, +e.target.value || 0) })}
            inputProps={{ min: 0, step: 0.5, style: { fontSize: 11, padding: '3px 6px', width: 52 } }}
            InputLabelProps={{ sx: { fontSize: 10 } }} />
        </Tooltip>
        <Tooltip title="Inverter DC-bus voltage (V). Usable peak phase = bus × modulation factor; designs whose V_peak exceeds it are penalised. 0 = no limit." placement="top">
          <TextField label="V bus" type="number" size="small" value={vBus}
            onChange={e => updateSweepConstraints({ vBusV: Math.max(0, +e.target.value || 0) })}
            inputProps={{ min: 0, step: 1, style: { fontSize: 11, padding: '3px 6px', width: 48 } }}
            InputLabelProps={{ sx: { fontSize: 10 } }} />
        </Tooltip>
        <Tooltip title="PWM scheme → usable peak phase = V_bus × (SVPWM 0.577 / Sine 0.5 / Six-step 0.637)." placement="top">
          <ToggleButtonGroup exclusive size="small" value={modulation}
            onChange={(_, m) => m && updateSweepConstraints({ modulation: m })} sx={{ height: 26 }}>
            <ToggleButton value="svpwm"   sx={{ px: 0.7, fontSize: 10 }}>SVPWM</ToggleButton>
            <ToggleButton value="sine"    sx={{ px: 0.7, fontSize: 10 }}>Sine</ToggleButton>
            <ToggleButton value="sixstep" sx={{ px: 0.7, fontSize: 10 }}>6-step</ToggleButton>
          </ToggleButtonGroup>
        </Tooltip>
        <Tooltip title="Computed usable peak phase voltage limit = V_bus × modulation factor. The optimizer penalises any design whose V_peak exceeds it." placement="top">
          <Typography variant="caption" sx={{ color: '#93c5fd', fontSize: 10, whiteSpace: 'nowrap' }}>
            V_peak ≤ {vPeakLimit < 1e8 ? `${vPeakLimit.toFixed(0)} V` : '—'}
          </Typography>
        </Tooltip>
        <Tooltip title="Optimize the load angle γ (MTPA) for the starting geometry BEFORE the geometry search (a quick parallel γ sweep runs first), so the whole run uses the best phase." placement="top">
          <ToggleButton value="mtpa" selected={mtpa} size="small"
            onChange={() => setMtpa(m => !m)} sx={{ px: 1, py: 0, height: 26, fontSize: 10 }}>
            MTPA γ
          </ToggleButton>
        </Tooltip>
        <Tooltip title="Auto-walk (box-walking): if a variable ends at the edge of its ±deviation window, re-center the window on the optimum and re-optimize — repeat until every variable settles inside its window, hits a physical (schema) limit, or the round cap. Off = one run, then boundary variables are flagged for a manual one-click continue." placement="top">
          <ToggleButton value="autowalk" selected={autoWalk} size="small"
            onChange={() => setAutoWalk(a => !a)} sx={{ px: 1, py: 0, height: 26, fontSize: 10 }}>
            Auto-walk
          </ToggleButton>
        </Tooltip>
        {autoWalk && (
          <TextField label="max rounds" type="number" size="small" value={maxRounds}
            onChange={(e) => setMaxRounds(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
            sx={{ width: 88 }} inputProps={{ min: 1, max: 20, style: { fontSize: 11 } }}
            InputLabelProps={{ style: { fontSize: 10 } }} />
        )}

        {(descentRunning || walking) ? (
          <Button variant="contained" color="error" size="small" startIcon={<StopIcon />}
            onClick={stopWalk}>
            {walking && autoWalk ? `Stop · round ${round}/${maxRounds}` : `Stop ${st.iter ?? 0}/${st.max_iters ?? maxIters}`}
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

      {/* Live progress: phase headline + bar (so a long FEM run visibly works) */}
      {(descentRunning || walking) && (
        <Box sx={{ mb: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <CircularProgress size={14} />
            <Typography variant="caption" sx={{ fontWeight: 600 }}>{statusText}</Typography>
            {mtpaG != null && (
              <Chip size="small" color="info" variant="outlined"
                label={`γ = ${Number(mtpaG).toFixed(0)}°`} sx={{ height: 18, fontSize: 10 }} />
            )}
          </Box>
          <LinearProgress variant={busyIndeterminate ? 'indeterminate' : 'determinate'} value={genPct}
            sx={{ height: 6, borderRadius: 3 }} />
        </Box>
      )}

      {/* Progress chips: iters / evals / MTPA γ / best */}
      {(descentRunning || walking || history.length > 0) && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
          <Chip size="small" variant="outlined"
            label={`iter ${st.iter ?? 0}/${st.max_iters ?? maxIters}`} sx={{ height: 20, fontSize: 10 }} />
          <Chip size="small" variant="outlined"
            label={`${st.n_evals ?? 0} FEM evals`} sx={{ height: 20, fontSize: 10 }} />
          {mtpaG != null && (
            <Chip size="small" variant="outlined"
              label={`MTPA γ = ${Number(mtpaG).toFixed(0)}°`} sx={{ height: 20, fontSize: 10 }} />
          )}
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
          {loadLineDesigns.some(([, a]) => a.length > 1) && (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '12px', mb: 0.5 }}>
              <Typography variant="caption" color="text.secondary">load-lines · ◆ rated {ratedTorque} N·m:</Typography>
              {loadLineDesigns.map(([name], i) => (
                <Typography key={name} variant="caption" sx={{ color: LL_PALETTE[i % LL_PALETTE.length] }}>● {name}</Typography>
              ))}
            </Box>
          )}
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
                {loadLineDesigns.map(([name, arr], i) => (
                  <Scatter key={'ll-' + name} name={name}
                    data={arr.map((p: any) => ({ td: p.td, eff: p.eff, z: 1, I: p.I }))}
                    fill={LL_PALETTE[i % LL_PALETTE.length]}
                    line={{ stroke: LL_PALETTE[i % LL_PALETTE.length], strokeWidth: 2.5 }} lineType="joint" isAnimationActive={false}>
                    <LabelList dataKey="I" position="top" formatter={(v: any) => `${v}A`} fill={LL_PALETTE[i % LL_PALETTE.length]} fontSize={9} />
                  </Scatter>
                ))}
                {ratedMarkers.map((rp: any) => (
                  <Scatter key={'rated-' + rp.name} name={`◆ ${rp.name} @ ${ratedTorque} Nm`}
                    data={[{ td: rp.td, eff: rp.eff, z: rp.z, I: rp.I }]}
                    fill={rp.color} shape="diamond" isAnimationActive={false}>
                    <LabelList dataKey="I" position="right" formatter={(v: any) => `${v}A`} fill={rp.color} fontSize={9} />
                  </Scatter>
                ))}
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

      {/* Boundary-active variables → flag + one-click box-walk continue */}
      {boundary.length > 0 && (
        <Box sx={{ mb: 1.5, p: 1, borderRadius: 1, border: '1px solid',
                   borderColor: softPinned.length ? '#f59e0b' : '#22c55e',
                   bgcolor: softPinned.length ? 'rgba(245,158,11,0.08)' : 'rgba(34,197,94,0.08)' }}>
          <Typography variant="caption" sx={{ display: 'block', fontWeight: 700, mb: 0.5,
                      color: softPinned.length ? '#f59e0b' : '#22c55e' }}>
            {softPinned.length
              ? `⚠ ${boundary.length} переменн${boundary.length === 1 ? 'ая' : 'ых'} на границе диапазона`
              : '✓ упёрлись в физический предел — оптимум найден'}
          </Typography>
          {boundary.map((f) => (
            <Typography key={f.name} variant="caption"
              sx={{ display: 'block', fontSize: 10.5, color: 'text.secondary' }}>
              <strong>{f.label}</strong>: {f.value.toFixed(2)} {f.pinned === 'high' ? '↑' : '↓'}{' '}
              край [{f.min.toFixed(2)} … {f.max.toFixed(2)}]
              {f.atHard
                ? <span style={{ color: '#ef4444' }}> · физ. предел</span>
                : <span style={{ color: '#f59e0b' }}> · можно сдвинуть окно</span>}
            </Typography>
          ))}
          {softPinned.length > 0 && (
            <Button variant="outlined" color="warning" size="small" sx={{ mt: 0.75 }}
              startIcon={<TrendingDownIcon />} disabled={!connectedToApi}
              onClick={continueWalk}>
              Сдвинуть окно и продолжить ({softPinned.length})
            </Button>
          )}
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
