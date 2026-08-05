import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box, Button, Card, CardContent, Chip, CircularProgress, Divider,
  LinearProgress, Table, TableBody, TableCell, TableHead, TableRow, TextField,
  ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import BookmarkAddIcon from '@mui/icons-material/BookmarkAdd';
import HelpTip from '../common/HelpTip';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, ReferenceLine, ScatterChart, Scatter,
} from 'recharts';
import { useMotorStore } from '../../stores/motorStore';

/**
 * One-click optimization.
 *
 * The user types ONE number — the maximum torque ripple they will accept — and
 * presses Run.  Everything else (operating point, objective, variables, eval
 * fidelity) is assembled by the BACKEND from this project's standing
 * conventions, so this card cannot be the place where a run quietly disagrees
 * with them.  It shows what was assembled and what the run will cost, then the
 * live progress, then the result next to the design it started from.
 *
 * The full manual optimizer is not gone — it lives under "Advanced" below.
 */
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

const fmt = (v: unknown, d = 2) =>
  (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—');
const pct = (v: unknown, d = 2) =>
  (typeof v === 'number' && Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '—');

/** "1 h 12 m" / "4 m 30 s" / "18 s" — a duration a person can plan around. */
function humanSeconds(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return '—';
  const t = Math.round(s);
  if (t < 90) return `${t} s`;
  const m = Math.round(t / 60);
  if (m < 90) return `${m} m`;
  return `${Math.floor(m / 60)} h ${m % 60} m`;
}

/** Signed delta with its sign always shown — a "+0.02 Nm" reads as a gain. */
function delta(now?: number, was?: number, d = 3, unit = ''): string {
  if (!Number.isFinite(Number(now)) || !Number.isFinite(Number(was))) return '';
  const x = Number(now) - Number(was);
  return `${x >= 0 ? '+' : ''}${x.toFixed(d)}${unit}`;
}

type Plan = {
  objective: string;
  mode: 'cmaes' | 'screen';
  ripple_max_pct: number;
  ripple_penalty_lambda: number;
  operating_point: { current_a: number; rpm: number; gamma_deg: number };
  eval: Record<string, any>;
  variables: { name: string; x0: number; sigma: number; delta: number; unit: string }[];
  budget_evals: number; population: number; generations: number;
  screen?: { n_screen_evals: number; group_size: number; alphas: number[] };
  cost: {
    s_per_eval: number; s_per_eval_source: string; n_samples: number;
    n_evals_max: number; parallel_workers: number;
    est_wall_seconds: number; est_cpu_seconds: number;
  };
};

const AutoOptimizePanel: React.FC = () => {
  const { connectedToApi, descentState, loadLastDescent, applyDescentBest, cancelDescent } =
    useMotorStore();

  const [maxRipple, setMaxRipple] = useState<number>(() => {
    try { return Math.max(0.1, Number(JSON.parse(localStorage.getItem('auto.maxRipple') ?? '5')) || 5); }
    catch { return 5; }
  });
  // WHICH SEARCH runs it.  Not cosmetic: on the CIANO20 150_35 the population
  // search spent 434 evals without beating its own baseline, and the engineer
  // beat it by hand with the screening method in a dozen solves.  Only the
  // person looking at the machine knows which question is being asked, so the
  // choice is theirs — and it is remembered.
  const [mode, setMode] = useState<'cmaes' | 'screen'>(() => {
    try {
      const v = JSON.parse(localStorage.getItem('auto.mode') ?? '"cmaes"');
      return v === 'screen' ? 'screen' : 'cmaes';
    } catch { return 'cmaes'; }
  });
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);
  const [savedPoint, setSavedPoint] = useState<string | null>(null);
  const startedAt = useRef<number | null>(null);

  const st: any = descentState || {};
  const auto = st.auto || {};
  const isAuto = !!auto.objective;             // this state came from an auto run
  const running = !!st.running;
  const result = st.result;
  const best = st.best?.metrics;
  const base = st.baseline;

  const updRipple = (v: number) => {
    const x = Math.max(0.1, Math.min(100, v || 0));
    setMaxRipple(x);
    try { localStorage.setItem('auto.maxRipple', JSON.stringify(x)); } catch { /* quota */ }
  };

  const updMode = (m: 'cmaes' | 'screen' | null) => {
    if (!m) return;                       // the group must always have a choice
    setMode(m);
    try { localStorage.setItem('auto.mode', JSON.stringify(m)); } catch { /* quota */ }
  };

  // ── Pre-flight: assemble + quote, WITHOUT launching ────────────────────────
  // Project rule: a run says what it costs before it starts.  So the card fetches
  // the plan as soon as the number changes and shows it next to the Run button —
  // the estimate is not an afterthought printed once the FEM is already burning.
  const fetchPlan = useCallback(async (ripple: number, m: string) => {
    if (!connectedToApi) return;
    setPlanBusy(true); setPlanError(null);
    try {
      const r = await fetch(`${API}/api/optimization/auto/plan`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_ripple_pct: ripple, mode: m }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) throw new Error(j?.detail ?? `HTTP ${r.status}`);
      setPlan(j.plan as Plan);
    } catch (e: any) {
      setPlan(null); setPlanError(String(e?.message ?? e));
    } finally {
      setPlanBusy(false);
    }
  }, [connectedToApi]);

  useEffect(() => {
    const t = setTimeout(() => { void fetchPlan(maxRipple, mode); }, 350);
    return () => clearTimeout(t);
  }, [maxRipple, mode, fetchPlan]);

  // ── Live progress ─────────────────────────────────────────────────────────
  // Poll here rather than leaning on the Advanced panel's mirror: this card is
  // usable with Advanced collapsed, and a progress bar that only moves when
  // another component happens to be mounted is worse than no progress bar.
  useEffect(() => {
    if (!connectedToApi) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (!alive) return;
      await loadLastDescent();
      if (!alive) return;
      const on = (useMotorStore.getState().descentState as any)?.running;
      timer = setTimeout(tick, on ? 2000 : 5000);
    };
    timer = setTimeout(tick, 500);
    return () => { alive = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectedToApi]);

  const launch = async () => {
    setLaunchError(null); setApplied(false); setSavedPoint(null);
    startedAt.current = Date.now();
    try {
      const r = await fetch(`${API}/api/optimization/auto`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_ripple_pct: maxRipple, mode }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) throw new Error(j?.detail ?? `HTTP ${r.status}`);
      await loadLastDescent();
    } catch (e: any) {
      setLaunchError(String(e?.message ?? e));
    }
  };

  const savePoint = async () => {
    try {
      const r = await fetch(`${API}/api/optimization/auto/compare_point`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: '' }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) throw new Error(j?.detail ?? `HTTP ${r.status}`);
      setSavedPoint(j?.name ?? 'saved');
      try { window.dispatchEvent(new CustomEvent('compare-points-changed')); } catch { /* SSR */ }
    } catch (e: any) {
      setLaunchError(String(e?.message ?? e));
    }
  };

  // ── ETA from the MEASURED rate of THIS run ────────────────────────────────
  // The pre-flight quote uses the median of past evals; once the run is going,
  // its own elapsed/eval is the better number, so switch to it.
  const nEvals = Number(st.n_evals) || 0;
  const budget = Number(auto.budget_evals) || Number(plan?.budget_evals) || 0;
  const workers = Number(auto.cost?.parallel_workers) || Number(plan?.cost?.parallel_workers) || 1;
  const elapsed = startedAt.current ? (Date.now() - startedAt.current) / 1000 : NaN;
  const liveSPer = (Number.isFinite(elapsed) && nEvals > 1)
    ? (elapsed * workers) / nEvals
    : Number(auto.cost?.s_per_eval) || Number(plan?.cost?.s_per_eval) || NaN;
  const remaining = Math.max(0, budget - nEvals);
  const etaS = Number.isFinite(liveSPer) ? (remaining / Math.max(1, workers)) * liveSPer : NaN;
  const progPct = budget > 0 ? Math.min(99, Math.round((100 * nEvals) / budget)) : 0;

  const rj = auto.rejects || result?.rejects;
  const op = auto.operating_point || plan?.operating_point;
  const cost = plan?.cost;

  return (
    <Card variant="outlined" sx={{ mb: 2, borderColor: 'rgba(34,197,94,0.45)' }}>
      <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
        {/* ── The whole input: one number and a button ── */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <AutoAwesomeIcon sx={{ fontSize: 20, color: '#22c55e' }} />
          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
            One-click optimization
          </Typography>
          <TextField
            label="Max torque ripple" type="number" size="small" value={maxRipple}
            onChange={(e) => updRipple(+e.target.value)}
            disabled={running}
            InputProps={{ endAdornment: <Typography sx={{ fontSize: 12, ml: 0.5 }}>%</Typography> }}
            inputProps={{ min: 0.1, max: 100, step: 0.5, style: { fontSize: 14, width: 64 } }}
            InputLabelProps={{ sx: { fontSize: 12 } }}
          />
          {/* WHICH search. One short label each; the reasoning lives in the
              tooltip, not on the card. */}
          <ToggleButtonGroup exclusive size="small" value={mode} disabled={running}
            onChange={(_e, m) => updMode(m)}
            sx={{ '& .MuiToggleButton-root': { py: 0.35, px: 1, fontSize: 11,
                                               textTransform: 'none' } }}>
            <Tooltip placement="top" title={
              'Explore: CMA-ES draws a whole population per generation and adapts its own '
              + 'covariance. It can leave the basin the current design sits in, which is what '
              + 'you want for a machine nobody has optimised yet — but it needs O(N²) evals '
              + 'before that covariance means anything.'}>
              <ToggleButton value="cmaes">Explore</ToggleButton>
            </Tooltip>
            <Tooltip placement="top" title={
              'Refine: deviate every variable by ±0.2 mm (0.02 for dimensionless ones), rank '
              + 'what actually moves the machine, descend the most influential few, then polish '
              + 'with the rest in groups of four. Starts paying after 2×N evals, and it is the '
              + 'method that beat a 434-eval CMA-ES run by hand on this motor. Local: it finds '
              + 'the nearest optimum, it does not change basin.'}>
              <ToggleButton value="screen">Refine</ToggleButton>
            </Tooltip>
          </ToggleButtonGroup>
          {running ? (
            <Button variant="contained" color="error" size="small" startIcon={<StopIcon />}
              onClick={() => cancelDescent()}>
              Stop
            </Button>
          ) : (
            <Button variant="contained" color="success" size="small" startIcon={<PlayArrowIcon />}
              disabled={!connectedToApi || !plan} onClick={launch}>
              Run
            </Button>
          )}
          <HelpTip title={
            'You give the ripple limit; everything else follows this project\'s standing rules. '
            + 'Operating point: the Simulation tab\'s current settings (check the point there first). '
            + 'Objective: maximum perpendicular distance above the current-only baseline line — '
            + 'a design that beats simply cranking current. Variables: the config sweep whitelist, '
            + 'searched with NO artificial range box: the geometry validator is the only fence, so '
            + 'a knob may leave the range a human would have typed. Every evaluation is a real P2 '
            + 'sliding-band transient; candidates whose cross-section is not buildable, or whose '
            + 'nonlinear solve did not converge, are rejected, not scored.'} />
        </Box>

        {/* ── Cost quote + what was assembled (BEFORE launching) ── */}
        {planError && (
          <Typography color="error" variant="caption" sx={{ display: 'block', mt: 1 }}>
            {planError}
          </Typography>
        )}
        {launchError && (
          <Typography color="error" variant="caption" sx={{ display: 'block', mt: 1 }}>
            {launchError}
          </Typography>
        )}
        {planBusy && !plan && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Assembling the run…
          </Typography>
        )}

        {plan && !running && (
          <Box sx={{ mt: 1.25, display: 'flex', gap: 0.75, flexWrap: 'wrap', alignItems: 'center' }}>
            <Chip size="small" variant="outlined" sx={{ height: 22, fontSize: 10.5 }}
              label={`${fmt(plan.operating_point.current_a, 1)} A · ${fmt(plan.operating_point.rpm, 0)} rpm · γ ${fmt(plan.operating_point.gamma_deg, 1)}°`} />
            <Chip size="small" variant="outlined" sx={{ height: 22, fontSize: 10.5 }}
              label={plan.mode === 'screen'
                ? `${plan.variables.length} vars · screen ${plan.screen?.n_screen_evals ?? 0} evals`
                : `${plan.variables.length} vars · unboxed`} />
            <Chip size="small" variant="outlined" sx={{ height: 22, fontSize: 10.5 }}
              label={`obj: above baseline · ripple ≤ ${fmt(plan.ripple_max_pct, 1)}% (λ ${plan.ripple_penalty_lambda})`} />
            <Chip size="small" variant="outlined" sx={{ height: 22, fontSize: 10.5 }}
              label={`P2 · ${plan.eval.steps_per_period} steps/T · ${plan.eval.n_sectors <= 0 ? 'full ring' : `1/${plan.eval.n_sectors}`}`} />
            {plan.eval.steps_per_period_requested
              && plan.eval.steps_per_period_requested < plan.eval.steps_per_period && (
              <Tooltip placement="top" title={
                `The Simulation tab is set to ${plan.eval.steps_per_period_requested} frames per electrical period. `
                + 'Torque ripple is a high-harmonic quantity: below ~48 frames it aliases, and a run whose '
                + 'constraint IS ripple would then be holding a number it measured wrong. The optimizer uses 48 '
                + 'and pins that back into the Simulation tab when you apply the result, so the re-solve matches.'}>
                <Chip size="small" variant="outlined" sx={{ height: 22, fontSize: 10.5, color: '#f59e0b', borderColor: 'rgba(245,158,11,0.6)' }}
                  label={`frames raised ${plan.eval.steps_per_period_requested} → ${plan.eval.steps_per_period} (anti-aliasing)`} />
              </Tooltip>
            )}
          </Box>
        )}

        {/* The FULL variable list, up front — user request: what exactly the
            run will search, with the starting value and the machine-scaled
            initial step (sigma) per variable. Same data the backend logs at
            run start, so screen and log cannot disagree. */}
        {plan && !running && plan.variables?.length > 0 && (
          <Box sx={{ mt: 1, display: 'grid', gap: '2px 14px',
                     gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
            {plan.variables.map(v => (
              <Box key={v.name} sx={{ display: 'flex', justifyContent: 'space-between',
                                      fontSize: 11, color: 'var(--text-2)',
                                      borderBottom: '1px dashed var(--panel)', py: 0.2 }}>
                <span>{v.name}</span>
                <span style={{ color: 'var(--text-1)', fontVariantNumeric: 'tabular-nums' }}>
                  {fmt(v.x0, v.unit === 'mm' ? 2 : 3)}{v.unit === 'mm' ? ' mm' : ''}
                  <span style={{ color: 'var(--text-4)' }}>
                    {plan.mode === 'screen'
                      ? ` · ±${fmt(v.delta, 2)}`
                      : ` · σ ${fmt(v.sigma, 2)}`}
                  </span>
                </span>
              </Box>
            ))}
          </Box>
        )}

        {cost && !running && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.75 }}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              <strong>Cost:</strong> ≤ {cost.n_evals_max} evals → ≈{' '}
              <strong>{humanSeconds(cost.est_wall_seconds)}</strong>
            </Typography>
            <HelpTip title={`Up to ${cost.n_evals_max} FEM evals × ${fmt(cost.s_per_eval, 1)} s/eval `
              + (cost.s_per_eval_source === 'measured'
                ? `(measured on this machine, ${cost.n_samples} evals)`
                : '(estimated — this machine has not timed an eval yet)')
              + ` → ≈ ${humanSeconds(cost.est_wall_seconds)} wall clock on ${cost.parallel_workers} parallel `
              + `workers (${humanSeconds(cost.est_cpu_seconds)} CPU). The budget is a hard cap, not a guess.`} />
          </Box>
        )}

        {/* ── Live progress ── */}
        {running && (
          <Box sx={{ mt: 1.25 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
              <CircularProgress size={14} />
              <Typography variant="caption" sx={{ fontWeight: 600 }}>
                {st.phase === 'baseline' ? 'baseline + reference line…'
                  : st.phase === 'starting' ? 'starting…'
                  : auto.mode === 'screen'
                    ? (st.phase === 'screening' ? 'screening every variable…'
                      : st.phase === 'polish' ? 'polishing the rest…'
                      : `descending · step ${st.iter ?? 0}`)
                    : `generation ${st.iter ?? 0}/${auto.generations ?? '?'}`}
              </Typography>
              <Chip size="small" variant="outlined" sx={{ height: 20, fontSize: 10 }}
                label={`${nEvals}/${budget || '?'} evals`} />
              {best && (
                <Chip size="small" variant="outlined" color="success" sx={{ height: 20, fontSize: 10 }}
                  label={`best ${fmt(best.T_em_Nm, 3)} N·m · ripple ${fmt(best.T_ripple_pct, 2)}%`} />
              )}
              {Number.isFinite(etaS) && remaining > 0 && (
                <Chip size="small" variant="outlined" sx={{ height: 20, fontSize: 10 }}
                  label={`ETA ≈ ${humanSeconds(etaS)}`} />
              )}
              {rj && rj.rejected > 0 && (
                <Tooltip placement="top" title={
                  `Candidates the fences stopped: ${rj.rejected_geometry} not buildable (geometry validator), `
                  + `${rj.rejected_unconverged} whose nonlinear solve did not converge, `
                  + `${rj.rejected_mesh ?? 0} whose mesh would be pathologically fine (mesh budget — rejected in seconds instead of timing out), `
                  + `${rj.rejected_timeout} timed out, ${rj.rejected_other} other. `
                  + `Before any of that, ${rj.resampled_geometry ?? 0} unbuildable candidates were caught in-process `
                  + `and re-drawn from the same distribution at no eval cost (${rj.prefenced_geometry ?? 0} stayed `
                  + 'unbuildable and took the penalty instead). '
                  + 'The search is unboxed on purpose, so some rejection is expected — but a run fencing most '
                  + 'of what it samples is thrashing, not converging.'}>
                  <Chip size="small" variant="outlined"
                    sx={{ height: 20, fontSize: 10,
                          color: rj.reject_pct > 70 ? '#ef4444' : '#f59e0b',
                          borderColor: rj.reject_pct > 70 ? 'rgba(239,68,68,0.6)' : 'rgba(245,158,11,0.6)' }}
                    label={`${rj.rejected} fenced (${fmt(rj.reject_pct, 0)}%)`} />
                </Tooltip>
              )}
            </Box>
            <LinearProgress
              variant={st.phase === 'baseline' || st.phase === 'starting' ? 'indeterminate' : 'determinate'}
              value={progPct} sx={{ height: 6, borderRadius: 3 }} />
          </Box>
        )}

        {/* What the screening actually measured: which knobs move this machine.
            The most useful thing a screening run produces is not the winner —
            it is the ranked list, because the engineer keeps it after the run.
            One line of chips; the numbers live in the tooltip. */}
        {auto.mode === 'screen' && auto.sensitivity?.rows?.length > 0 && (
          <Box sx={{ mt: 1, display: 'flex', gap: 0.6, flexWrap: 'wrap',
                     alignItems: 'center' }}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Most influential:
            </Typography>
            {auto.sensitivity.rows.slice(0, 6).map((r: any) => {
              const inert = !r.measured
                || Math.abs(r.influence) <= (auto.sensitivity.noise_floor ?? 0);
              const on = (auto.active_set ?? []).includes(r.name);
              return (
                <Tooltip key={r.name} placement="top" title={
                  `${r.name}: moving it by one screening deviation changes the objective by `
                  + `${fmt(r.influence, 5)}; the design wants it to go `
                  + `${r.direction < 0 ? 'DOWN' : 'UP'}. `
                  + (r.one_sided ? 'Only one side was buildable, so this is a one-sided slope. ' : '')
                  + (inert ? 'Under the noise floor — not evidence of anything. ' : '')
                  + `Noise floor ${fmt(auto.sensitivity.noise_floor, 5)}.`}>
                  <Chip size="small" variant={on ? 'filled' : 'outlined'}
                    sx={{ height: 20, fontSize: 10,
                          opacity: inert ? 0.45 : 1,
                          borderColor: on ? 'rgba(34,197,94,0.6)' : undefined }}
                    label={`${r.name} ${r.direction < 0 ? '↓' : '↑'}`} />
                </Tooltip>
              );
            })}
            {auto.active_why && <HelpTip title={String(auto.active_why)} />}
          </Box>
        )}

        {/* Trajectory chart — user request: the run's history must be visible
            HERE, not only inside the collapsed Advanced panel. Same store
            history the DescentPanel charts read (st.history), compact form:
            torque + ripple per accepted eval, with the ripple gate line. */}
        {/* Gated on POINTS as well as history: a CMA-ES generation is 12 evals
            long, so gating on history alone kept the cloud hidden for the first
            ~2 hours of a run that already had a dozen measured designs. */}
        {((st.history || []).length > 1 || (st.points || []).length > 1) && (() => {
          const hist = (st.history || []).map((h: any, i: number) => ({
            // The persisted history rows name the fields T_em_Nm / T_ripple_pct
            // (checked against the live /descent/progress payload — do not
            // guess field names, they burned this chart once already).
            i, torque: h.torque ?? h.T_em_Nm ?? h.T_avg_Nm,
            ripple: h.ripple ?? h.T_ripple_pct,
          })).filter((p: any) => Number.isFinite(p.torque) && Number.isFinite(p.ripple));
          // Every eval with metrics, straight from the backend's `points` cloud.
          const cloud = ((st.points || []) as any[])
            .map((p) => ({ torque: p.torque, ripple: p.ripple, td: p.td,
                           eff: (p.eff ?? 0) * 100, kind: p.kind }))
            .filter((p) => Number.isFinite(p.torque) && Number.isFinite(p.ripple));
          const nUnderGate = cloud.filter((p) => p.ripple <= maxRipple).length;
          const bestPt = best && Number.isFinite(best.T_em_Nm)
            ? [{ ripple: best.T_ripple_pct, torque: best.T_em_Nm }] : [];
          const basePt = base && Number.isFinite(base.T_em_Nm)
            ? [{ ripple: base.T_ripple_pct, torque: base.T_em_Nm }] : [];
          return (
          <Box sx={{ mt: 1.5, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
            {/* Trajectory: what the search did over time */}
            <Box sx={{ flex: 1, minWidth: 320, height: 200 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={hist} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--panel)" strokeDasharray="2 4" />
                  <XAxis dataKey="i" tick={{ fontSize: 10, fill: 'var(--text-2)' }}
                    label={{ value: 'eval', position: 'insideBottomRight', fontSize: 10, fill: 'var(--text-3)' }} />
                  <YAxis yAxisId="t" tick={{ fontSize: 10, fill: 'var(--text-2)' }}
                    width={44} domain={['auto', 'auto']} />
                  <YAxis yAxisId="r" orientation="right" width={40}
                    tick={{ fontSize: 10, fill: 'var(--text-2)' }} domain={[0, 'auto']} />
                  <RTooltip contentStyle={{ background: 'var(--app-bg)',
                    border: '1px solid var(--line-soft)', fontSize: 11 }} />
                  <ReferenceLine yAxisId="r" y={maxRipple} stroke="#f59e0b"
                    strokeDasharray="4 3" label={{ value: `≤${maxRipple}%`, fontSize: 10, fill: '#f59e0b' }} />
                  <Line yAxisId="t" dataKey="torque" name="T [N·m]" dot={false}
                    stroke="#60a5fa" strokeWidth={2} isAnimationActive={false} />
                  <Line yAxisId="r" dataKey="ripple" name="ripple [%]" dot={false}
                    stroke="#f87171" strokeWidth={1.5} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </Box>
            {/* Points cloud: EVERY measured design as torque×ripple, the ripple
                gate as the vertical fence, the current design and the best point
                called out — the user's "график с точками".
                It used to plot `hist` (st.history = one row per GENERATION, i.e.
                the incumbent), so a 62-eval run drew 6 dots and read as "found
                nothing".  It now plots st.points — every eval that produced
                metrics — per the standing rule: publish all points, the user
                filters by ripple on the chart. */}
            <Box sx={{ flex: 1, minWidth: 320, height: 200 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--panel)" strokeDasharray="2 4" />
                  <XAxis type="number" dataKey="ripple" name="ripple"
                    tick={{ fontSize: 10, fill: 'var(--text-2)' }} domain={[0, 'auto']}
                    label={{ value: 'ripple %', position: 'insideBottomRight', fontSize: 10, fill: 'var(--text-3)' }} />
                  <YAxis type="number" dataKey="torque" name="T"
                    tick={{ fontSize: 10, fill: 'var(--text-2)' }} width={44}
                    domain={['auto', 'auto']}
                    label={{ value: 'T [N·m]', angle: -90, position: 'insideLeft', fontSize: 10, fill: 'var(--text-3)' }} />
                  <RTooltip cursor={{ strokeDasharray: '3 3' }}
                    contentStyle={{ background: 'var(--app-bg)',
                      border: '1px solid var(--line-soft)', fontSize: 11 }} />
                  <ReferenceLine x={maxRipple} stroke="#f59e0b" strokeDasharray="4 3"
                    label={{ value: `≤${maxRipple}%`, fontSize: 10, fill: '#f59e0b' }} />
                  <Scatter name="evals" data={cloud} fill="#60a5fa"
                    opacity={0.55} isAnimationActive={false} />
                  <Scatter name="current design" data={basePt} fill="#34d399"
                    shape="diamond" isAnimationActive={false} />
                  <Scatter name="best" data={bestPt} fill="#f87171"
                    shape="star" isAnimationActive={false} />
                </ScatterChart>
              </ResponsiveContainer>
              {/* One line — what the gate line is separating. No text walls. */}
              <Tooltip placement="top" title={
                `Every evaluated design is plotted, including the ones over the ripple gate — `
                + `nothing is hidden server-side. Points left of the ${fmt(maxRipple, 1)}% line `
                + `satisfy the ripple limit; points right of it do not.`}>
                <Typography variant="caption" sx={{ display: 'block', mt: -0.5,
                    color: 'var(--text-3)', fontSize: 10, cursor: 'help' }}>
                  {cloud.length} designs measured · {nUnderGate} under the ripple gate ⓘ
                </Typography>
              </Tooltip>
            </Box>
          </Box>
          );
        })()}

        {st.error && !running && (
          <Typography color="error" variant="caption" sx={{ display: 'block', mt: 1 }}>
            {st.error}
          </Typography>
        )}

        {/* ── Result: the optimized design NEXT TO the one it started from ── */}
        {isAuto && !running && best && base && (
          <Box sx={{ mt: 1.5 }}>
            <Divider sx={{ mb: 1 }} />

            {/* The objective's OWN verdict, in words.
                F is the signed perpendicular distance above the current-only
                baseline line.  F < 0 means the winner does not beat simply
                raising the current — the ripple gate cost more than it bought.
                Leaving that to be inferred from the deltas is how a run that
                answered "no" gets read as "here is your optimized motor". */}
            {/* Verdict is ONE line; the reasoning lives in the tooltip — the
                standing UI rule (no text walls, details on hover). The first
                version was a paragraph and the user rightly objected. */}
            {typeof st.best?.F === 'number' && st.best.F <= 0 && (
              <Tooltip placement="top" title={
                `Below the current-only baseline line: every point on that line is reachable by `
                + `just raising current. The ripple limit ${fmt(auto.max_ripple_pct, 2)}% sits below the `
                + `current design's own ${fmt(base.T_ripple_pct, 2)}%, so the optimizer had to buy ripple `
                + `with torque. Raise the limit, or accept the trade on purpose.`}>
                <Typography variant="caption" sx={{ display: 'inline-block', mb: 1, px: 1, py: 0.4,
                    borderRadius: 1, border: '1px solid #f59e0b', color: '#f59e0b',
                    fontWeight: 700, cursor: 'help', bgcolor: 'rgba(245,158,11,0.08)' }}>
                  ⚠ не лучше текущего (F = {fmt(st.best.F, 3)}) · ⓘ
                </Typography>
              </Tooltip>
            )}
            {typeof st.best?.F === 'number' && st.best.F > 0 && (
              <Tooltip placement="top" title={
                `Above the current-only baseline line — this design beats what simply raising `
                + `the current would give.`}>
                <Typography variant="caption" sx={{ display: 'inline-block', mb: 1, color: '#22c55e',
                    fontWeight: 700, cursor: 'help' }}>
                  ✓ лучше текущего (F = {fmt(st.best.F, 3)}) · ⓘ
                </Typography>
              </Tooltip>
            )}
            <Table size="small" sx={{ mb: 1, width: 'auto', '& td, & th': { py: 0.3, px: 1 } }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontSize: 10, color: 'text.secondary' }}>—</TableCell>
                  <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Torque</TableCell>
                  <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Ripple</TableCell>
                  <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Efficiency</TableCell>
                  <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>N·m/kg</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                <TableRow>
                  <TableCell sx={{ fontSize: 11 }}>Current design</TableCell>
                  <TableCell align="right" sx={{ fontSize: 11 }}>{fmt(base.T_em_Nm, 3)}</TableCell>
                  <TableCell align="right" sx={{ fontSize: 11 }}>{fmt(base.T_ripple_pct, 2)}%</TableCell>
                  <TableCell align="right" sx={{ fontSize: 11 }}>{pct(base.efficiency)}</TableCell>
                  <TableCell align="right" sx={{ fontSize: 11 }}>{fmt(base.torque_per_mass, 3)}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>Optimized</TableCell>
                  <TableCell align="right" sx={{ fontSize: 11, fontWeight: 700 }}>
                    {fmt(best.T_em_Nm, 3)}
                    <Typography component="span" sx={{ fontSize: 10, ml: 0.5, color: 'text.secondary' }}>
                      {delta(best.T_em_Nm, base.T_em_Nm, 3)}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ fontSize: 11, fontWeight: 700,
                    color: (best.T_ripple_pct ?? 1e9) <= (auto.max_ripple_pct ?? 1e9) ? '#22c55e' : '#f59e0b' }}>
                    {fmt(best.T_ripple_pct, 2)}%
                    <Typography component="span" sx={{ fontSize: 10, ml: 0.5, color: 'text.secondary' }}>
                      {delta(best.T_ripple_pct, base.T_ripple_pct, 2)}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ fontSize: 11, fontWeight: 700 }}>
                    {pct(best.efficiency)}
                    <Typography component="span" sx={{ fontSize: 10, ml: 0.5, color: 'text.secondary' }}>
                      {delta((best.efficiency ?? 0) * 100, (base.efficiency ?? 0) * 100, 2, ' pp')}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ fontSize: 11, fontWeight: 700 }}>
                    {fmt(best.torque_per_mass, 3)}
                    <Typography component="span" sx={{ fontSize: 10, ml: 0.5, color: 'text.secondary' }}>
                      {delta(best.torque_per_mass, base.torque_per_mass, 3)}
                    </Typography>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>

            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button variant="outlined" color="success" size="small"
                startIcon={applied ? <CheckCircleIcon /> : <PlayArrowIcon />} disabled={applied}
                onClick={async () => { await applyDescentBest(); setApplied(true); }}>
                {applied ? 'Applied to design' : 'Apply to design'}
              </Button>
              <Button variant="outlined" size="small" startIcon={<BookmarkAddIcon />}
                onClick={savePoint}>
                Save as Compare point
              </Button>
              {(auto.compare_point?.name || savedPoint) && (
                <Typography variant="caption" sx={{ color: '#22c55e' }}>
                  saved as “{savedPoint ?? auto.compare_point?.name}”
                </Typography>
              )}
              {auto.compare_point_error && (
                <Typography variant="caption" color="error">
                  compare point not saved: {auto.compare_point_error}
                </Typography>
              )}
            </Box>

            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.75 }}>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {result?.n_evals ?? nEvals} FEM evals
                {rj ? ` · ${rj.rejected} rejected` : ''}
                {op ? ` · ${fmt(op.current_a, 1)} A / ${fmt(op.rpm, 0)} rpm / γ ${fmt(op.gamma_deg, 1)}°` : ''}
              </Typography>
              <HelpTip title={(rj
                ? `${rj.rejected} candidates rejected by the fences: ${rj.rejected_geometry} not buildable, `
                  + `${rj.rejected_unconverged} unconverged`
                  + `${rj.rejected_mesh ? `, ${rj.rejected_mesh} mesh budget` : ''}`
                  + `${rj.rejected_timeout ? `, ${rj.rejected_timeout} timed out` : ''}. `
                  + `${rj.resampled_geometry ? `${rj.resampled_geometry} more were caught before the FEM and re-drawn at no eval cost. ` : ''}`
                : '')
                + (op ? `Solved at ${fmt(op.current_a, 1)} A / ${fmt(op.rpm, 0)} rpm / γ ${fmt(op.gamma_deg, 1)}°. ` : '')
                + 'The result is filed as a Compare point with its full geometry, metrics and provenance.'} />
            </Box>
          </Box>
        )}
      </CardContent>
    </Card>
  );
};

export default AutoOptimizePanel;
