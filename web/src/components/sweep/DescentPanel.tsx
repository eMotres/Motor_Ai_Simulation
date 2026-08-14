import React, { useState, useEffect, useRef } from 'react';
import {
  Box, Typography, Button, TextField, Tooltip, Divider, Chip,
  CircularProgress, LinearProgress, Table, TableBody, TableCell, TableHead, TableRow,
  ToggleButton, ToggleButtonGroup, Slider,
} from '@mui/material';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import HelpTip from '../common/HelpTip';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, Legend, BarChart, Bar, Cell, ReferenceLine,
  ScatterChart, Scatter, ZAxis, LabelList,
} from 'recharts';
import Scatter3D from './Scatter3D';
import { useMotorStore } from '../../stores/motorStore';
import { appliedSaveLine } from '../../lib/appliedAutoSave';

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
/** Top of the chart's ripple-trim slider, in per cent.  Nothing above this is
 *  a machine anyone would build, so the track spends its whole length on the
 *  range where candidates are actually chosen. */
const RIPPLE_SLIDER_MAX = 30;

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

// A small labelled group so the toolbar reads as organised sections instead of
// one long cramped row.  Module-level (stable identity) so inputs don't remount.
const Group: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
    <Typography sx={{ fontSize: 9, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.6, fontWeight: 700 }}>
      {label}
    </Typography>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>{children}</Box>
  </Box>
);

const DescentPanel: React.FC<{ chartsOnly?: boolean }> = ({ chartsOnly = false }) => {
  const {
    sweepConfig, connectedToApi, parameterSchema, materials,
    descentRunning, descentState, descentError,
    baselineLine, baselineBusy, baselineError, computeBaselineLine,
    runDescent, cancelDescent, applyDescentBest, applyDescentPoint, loadLastDescent,
    updateSweepConstraints, lastOptSnapshot, setLastOptSnapshot, appliedSave,
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
  // Load-lines (current-sweep overlay) are a disambiguation tool — OFF by default,
  // shown on demand after a run when two candidates are too close to call.
  const [showLoadLines, setShowLoadLines] = useState(false);
  const [view3d, setView3d] = useState(false);   // 2-D ScatterChart vs 3-D (ripple on z)
  // On-chart ripple cut: post-hoc DISPLAY filter (null = show all). Independent of
  // the search constraint (rippleMax, which steers the optimizer) — drag to visually
  // trim high-ripple points off the chart without re-running.
  const [displayCut, setDisplayCut] = useState<number | null>(null);
  // User-picked scatter point (click-to-select) — when set, "Apply" applies THIS
  // design instead of the optimiser's auto-best.
  const [selectedPt, setSelectedPt] = useState<any | null>(null);
  // Objective-chart Y (efficiency) scaling: Auto re-fits to the meaningful designs
  // as the run progresses; Manual fixes a range (the low infeasible points are not
  // informative, so cut them).
  const [yMode, setYMode] = useState<'auto' | 'manual'>('auto');
  const [yMin, setYMin] = useState(90);
  const [yMax, setYMax] = useState(97);
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
  const [wTd, setWTd]   = useState(1);   // reward torque/mass too (default); 0 = efficiency only → design ignores mass
  // Ripple-gate enforcement weight in the optimizer cost (0 = off → ripple is only
  // trimmed on the chart). Persisted: forgetting a set λ on reload would silently
  // relaunch unconstrained runs.
  // Ripple suppression is ON by default (λ=25, "soft pressure").  The backend
  // now ESCALATES λ across box-walk rounds (penalty continuation) until ripple
  // is under the gate, so a moderate start is all that's needed.  One-time
  // migration seeds the default for users whose localStorage predates this
  // (was hard-'0'); after that the field is fully user-controlled (0 = off).
  const [rippleLam, setRippleLam] = useState<number>(() => {
    try {
      const raw = localStorage.getItem('opt.rippleLam');
      if (raw == null || localStorage.getItem('opt.rippleLamV2') == null) {
        localStorage.setItem('opt.rippleLamV2', '1');
        const seeded = raw == null ? 25 : Math.max(0, Number(JSON.parse(raw)) || 0) || 25;
        localStorage.setItem('opt.rippleLam', JSON.stringify(seeded));
        return seeded;
      }
      return Math.max(0, Number(JSON.parse(raw)) || 0);
    } catch { return 25; }
  });
  const updRippleLam = (v: number) => {
    const x = Math.max(0, v || 0);
    setRippleLam(x);
    try { localStorage.setItem('opt.rippleLam', JSON.stringify(x)); } catch { /* ignore */ }
  };
  // Line-to-line voltage THD gate (FOC waveform quality) — same opt-in penalty
  // pattern as ripple; persisted for the same reason.
  const [thdLam, setThdLam] = useState<number>(() => {
    try { return Math.max(0, Number(JSON.parse(localStorage.getItem('opt.thdLam') ?? '0')) || 0); }
    catch { return 0; }
  });
  const updThdLam = (v: number) => {
    const x = Math.max(0, v || 0);
    setThdLam(x);
    try { localStorage.setItem('opt.thdLam', JSON.stringify(x)); } catch { /* ignore */ }
  };
  const [thdMax, setThdMax] = useState<number>(() => {
    try { return Math.max(0, Number(JSON.parse(localStorage.getItem('opt.thdMax') ?? '5')) || 5); }
    catch { return 5; }
  });
  const updThdMax = (v: number) => {
    const x = Math.max(0, v || 0);
    setThdMax(x);
    try { localStorage.setItem('opt.thdMax', JSON.stringify(x)); } catch { /* ignore */ }
  };
  // Objective mode. 'baseline_line' (default): maximise the signed PERPENDICULAR
  // DISTANCE above the "current-only" line — two sims of the START geometry (at I
  // and I·(1+bump)) set the eff/(T/mass) weights automatically from the line slope,
  // so a design above the line beats just cranking current. 'product': legacy
  // efficiency^w_eff · (T/mass)^w_td.
  const [objective, setObjective] = useState<'baseline_line' | 'product'>('baseline_line');
  const [currentBump, setCurrentBump] = useState(10);   // 2nd baseline current = I·(1+pct/100)
  // FEM frames/period = the Simulation tab's "Steps per period" (single source) —
  // so the optimizer evaluates ripple at the SAME resolution you simulate at.
  const [steps] = useState(() => {
    try { return Math.max(8, Math.min(180, Number(JSON.parse(localStorage.getItem('sim.stepsPP') ?? '72')) || 72)); }
    catch { return 72; }
  });
  const [applied, setApplied] = useState(false);
  // Algorithm: CMA-ES (derivative-free, noise-robust, default) vs the original
  // finite-difference gradient descent.
  const [algorithm, setAlgorithm] = useState<'cmaes' | 'gradient'>('cmaes');
  // FEM symmetry = the Mesh tab's sector count (single source) — the optimizer
  // builds the disk EXACTLY like Simulation does, so optimizer ripple ==
  // Simulation ripple (no opt↔Sim mismatch).  Set it on the Mesh tab; read-only here.
  const nSectors = (() => {
    try { return Math.max(1, Math.round(Number(JSON.parse(localStorage.getItem('mesh.nSectors') ?? '1')) || 1)); }
    catch { return 4; }
  })();
  // Box-walking: keep re-centering the ±deviation window on the optimum until
  // every variable settles inside its window (or hits a physical limit).
  // Default ON (per Vadim): a variable pinned at its window edge auto-extends /
  // walks the window server-side and keeps optimizing instead of stalling.
  const [autoWalk, setAutoWalk]   = useState(true);
  const [surrogateSeed, setSurrogateSeed] = useState(false);  // Bayesian warm-start from accumulated evals
  const [maxRounds, setMaxRounds] = useState(5);   // box-walking round cap (server-side auto-walk)
  const [snoozeKey, setSnoozeKey] = useState('');  // signature of a dismissed change-set
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
  const boundary = (!descentRunning && (best || st.result))
    ? boundaryFlags(st, parameterSchema) : [];
  const softPinned = boundary.filter((f) => !f.atHard);

  // Live status: what the optimizer is doing right now (so a long run visibly
  // works).  phase + walk round both come from the backend (server-side box-walking).
  const phase = st.phase as string | undefined;
  const walkRound = Number(st.walk_round) || 1;
  const walkRounds = Number(st.walk_rounds) || 1;
  const genText = phase === 'baseline' ? 'baseline solve…'
                : phase === 'starting' ? 'building mesh…'
                : descentRunning       ? `generation ${st.iter ?? 0}/${st.max_iters ?? maxIters}`
                : 'done';
  const statusText = ((walkRounds > 1) ? `Round ${walkRound}/${walkRounds} · ` : '') + genText;
  const busyIndeterminate = phase === 'baseline' || phase === 'starting';
  // Eval-based progress so the bar visibly creeps WITHIN a generation — CMA-ES
  // only bumps `iter` once a whole generation of ~λ evals finishes, so at full
  // disk the bar would otherwise sit flat for ~15 min.  λ ≈ 4 + 3·ln(N) (the CMA
  // default population), N = number of optimized variables.
  const _lambda = 4 + Math.floor(3 * Math.log(Math.max(2, activeVars.length)));
  const genPct = Math.min(99, Math.round(100 * Math.max(0, (Number(st.n_evals) || 0) - 1)
                                          / Math.max(1, (Number(st.max_iters) || maxIters) * _lambda)));
  // Auto-expand info feed (server): window edges GROWN in place (gradient path)
  // or WALKED between rounds (CMA) because the optimum was pinned there.  Show
  // the latest state per variable+side so the user sees where the search went
  // beyond the ranges they drew.
  const rangeEvents: any[] = Array.isArray((st as any).range_events) ? (st as any).range_events : [];
  const rangeSummary = (() => {
    const latest = new Map<string, any>();
    for (const e of rangeEvents) latest.set(`${e.name}:${e.side}`, e);
    return Array.from(latest.values());
  })();

  // ── Re-optimize suggestion: what changed since the last optimization? ────────
  // Snapshot the inputs at launch (in `launch`), then diff the live inputs against
  // it.  Tracks persisted/reactive inputs (survives reload).  Winding connection is
  // backend-only config → a follow-up.
  const op0_ = (sweepConfig.operatingPoints?.[0] || {}) as any;
  const _varsSig = Object.entries(sweepConfig.variations || {})
    .filter(([, v]: any) => v.mode !== 'fixed')
    .map(([n, v]: any) => `${n}:${v.mode}:${Number(v.min)}-${Number(v.max)}`).sort().join(' | ');
  const optInputs: Record<string, any> = {
    'Steel': materials?.stator_core ?? '—',
    'Magnet': materials?.magnet ?? '—',
    'Speed (rpm)': op0_.rpm,
    'Load angle γ': op0_.gamma_deg ?? 0,
    'Ripple limit (%)': +((sweepConfig.rippleThreshold ?? 0) * 100).toFixed(2),
    'Ripple penalty λ': rippleLam,
    'THD penalty λ': thdLam,
    'THD limit (%)': thdMax,
    'Variables / windows': _varsSig,
  };
  const changes = (lastOptSnapshot && (best || st.result))
    ? Object.keys(optInputs)
        .filter((k) => String(optInputs[k]) !== String((lastOptSnapshot as any)[k]))
        .map((k) => ({ label: k, prev: (lastOptSnapshot as any)[k], now: optInputs[k] }))
    : [];
  const changesKey = JSON.stringify(changes.map((c) => [c.label, c.now]));
  const showChanges = changes.length > 0 && !descentRunning && changesKey !== snoozeKey;
  const gradData = variables
    .map((v: any) => ({ name: v.name, dir: -(grad[v.name] ?? 0) }))
    .filter((d: any) => Number.isFinite(d.dir) && d.dir !== 0)
    .sort((a: any, b: any) => Math.abs(b.dir) - Math.abs(a.dir))
    .slice(0, 12);

  // 2-D objective-space projection: X = torque/mass (Nm/kg), Y = efficiency (%).
  // Every evaluated design is a point, split by the ripple constraint; the
  // accepted iterates form the descent trajectory.
  const points: any[] = st.points || [];
  // Ripple is NOT a search constraint — the optimizer maximises efficiency ×
  // torque-density (2 criteria), and ripple is trimmed purely on the chart: the
  // slider hides points whose ripple exceeds the cut (effCut defaults to the data
  // max = show all, then drags down to trim visually).
  const _dataMaxRipple = points.reduce((m: number, p: any) => Math.max(m, p.ripple ?? 0), 0);
  // THE SCALE STOPS AT 30 %, not at the worst point in the cloud.  A free search
  // (stage 1, no ripple gate) throws up designs at 400 %+ ripple, and letting
  // them set the slider's span made the whole useful range — everything under
  // ~15 % — the first 3 % of the track: unusable for picking a candidate, while
  // the chart stayed full of machines nobody would build.  Above the cap the
  // points are simply out; the count next to the slider says how many.
  const sliderMax = Math.min(Math.max(Math.ceil(_dataMaxRipple), 10), RIPPLE_SLIDER_MAX);
  const effCut = displayCut ?? sliderMax;
  const toXY = (p: any) => ({ td: p.td, eff: (p.eff ?? 0) * 100, ripple: p.ripple, z: 1,
                              torque: p.torque, mass: p.mass,
                              // Pareto flags from the backend (optimization/pareto.py):
                              // computed against THIS run's baseline at THIS run's
                              // operating point — never across runs.
                              dominates: !!p.dominates, front: !!p.pareto_front,
                              overrides: p.overrides, current_a: p.current_a, gamma_deg: p.gamma_deg });
  // Display fence against non-physical points persisted before the backend's
  // output sanity gate existed (seen live: td = -7.7e9, eff = 0 — one such
  // point stretched the axis to 3e8 and crushed the real cloud into a sliver).
  // Bounds are orders-of-magnitude loose: this hides broken numbers, not bad
  // designs.
  const physical = (p: any) =>
    Number.isFinite(p.td) && p.td > 0 && p.td < 5e3 &&
    Number.isFinite(p.eff ?? 0) && (p.eff ?? 0) > 0 && (p.eff ?? 0) <= 1;
  // One series of every evaluated design; the slider visually cuts by ripple.
  const shownPts = points.filter((p) => physical(p) && p.ripple != null && p.ripple <= effCut).map(toXY);
  // ── Pareto-dominance, next to F ──────────────────────────────────────────
  // F (the perpendicular distance above the baseline line) is ~pure efficiency
  // on this machine, so "F ≤ 0" and "found nothing better" are NOT the same
  // statement.  The backend flags every candidate that is at least as good as
  // the run's own baseline on torque, ripple AND efficiency — at the run's own
  // operating point — and those are drawn apart from the cloud so they can be
  // clicked and applied.
  const pareto: any = st.pareto || null;
  const domPts = shownPts.filter((p: any) => p.dominates);
  const restPts = shownPts.filter((p: any) => !p.dominates);
  // Unfiltered count — gates the scatter section so the on-chart ripple slider
  // stays mounted even when the cut hides most points (else dragging down would
  // unmount the slider itself).
  const totalValidPts = points.filter((p: any) => physical(p) && p.ripple != null).length;
  // What the two filters are each hiding, so the label can say it instead of
  // leaving the user to read a shrinking count as "the run found nothing".
  const hiddenByCut = totalValidPts - shownPts.length;
  const nonPhysicalPts = points.length - totalValidPts;
  const trajPts = history
    .filter((h: any) => h.torque_per_mass != null)
    .map((h: any) => ({ td: h.torque_per_mass, eff: (h.efficiency ?? 0) * 100, ripple: h.T_ripple_pct, iter: h.iter, z: 2 }));
  // The ★ best carries its geometry + operating point too, so it is click-selectable
  // and "Apply picked point" works on it exactly like any other scatter point.
  const _bestX = st.best?.x || st.result?.best?.overrides;
  const _bestG = (typeof st.mtpa_gamma_deg === 'number') ? st.mtpa_gamma_deg
               : (typeof best?.gamma_deg === 'number' ? best.gamma_deg : undefined);
  const bestPt = best?.torque_per_mass != null
    ? [{ td: best.torque_per_mass, eff: (best.efficiency ?? 0) * 100, ripple: best.T_ripple_pct, z: 6,
         overrides: _bestX, current_a: best.current_a, gamma_deg: _bestG }] : [];

  // ── Baseline (current-only) line A–B ── from a run (st.baseline_line) OR the
  // standalone "Draw baseline" button (baselineLine). Computed here so the axes
  // can frame it even before any optimization point exists.
  const bline: any = st.baseline_line || st.result?.baseline_line || baselineLine || null;
  const blA = bline ? { td: bline.td_a, eff: (bline.eff_a ?? 0) * 100, tag: `${Math.round(bline.current_a)}A` } : null;
  const blB = bline ? { td: bline.td_b, eff: (bline.eff_b ?? 0) * 100, tag: `${Math.round(bline.current_b)}A` } : null;

  // Y (efficiency) axis domain: Manual = fixed [yMin, yMax]; Auto = fit to the
  // MEANINGFUL designs (descent path + best + feasible), padded — re-zooms as the
  // run progresses and drops the uninformative low points (clipped via allowDataOverflow).
  // Auto-fit BOTH axes to the bounding box of the DISPLAYED points (the ripple-cut
  // slider decides which are shown): scale to the extreme X (Nm/kg) and Y (η) so
  // every visible point fits with a little margin — nothing clipped, and it re-fits
  // live as the ripple slider hides/shows points.
  const _objShown = [...shownPts, ...trajPts, ...bestPt, ...(blA && blB ? [blA, blB] : [])];
  const _fitDomain = (vals: number[], padFrac: number, padMin: number, clampLo?: number):
      [number | string, number | string] => {
    const v = vals.filter((x) => Number.isFinite(x));
    if (!v.length) return ['auto', 'auto'];
    const lo = Math.min(...v), hi = Math.max(...v);
    const pad = Math.max(padMin, (hi - lo) * padFrac);
    let dlo = lo - pad;
    if (clampLo !== undefined) dlo = Math.max(clampLo, dlo);
    return [+dlo.toFixed(2), +(hi + pad).toFixed(2)];
  };
  const yDomain: [number | string, number | string] =
    yMode === 'manual' ? [yMin, yMax] : _fitDomain(_objShown.map((p: any) => p.eff), 0.06, 0.3);
  const xDomain: [number | string, number | string] =
    _fitDomain(_objShown.map((p: any) => p.td), 0.06, 0.05, 0);

  // Baseline-line chart geometry — A/B markers + a segment extended across the
  // visible X-range (bline/blA/blB are computed above so the axes can frame them).
  const blMarkers = (blA && blB) ? [{ ...blA, z: 5 }, { ...blB, z: 5 }] : [];
  const _xN = (v: number | string, fb: number) => (typeof v === 'number' ? v : fb);
  const _blSeg = (blA && blB && Math.abs(blB.td - blA.td) > 1e-9)
    ? (() => {
        const slope = (blB.eff - blA.eff) / (blB.td - blA.td);
        const yAt = (x: number) => blA.eff + slope * (x - blA.td);
        const xLo = _xN(xDomain[0], Math.min(blA.td, blB.td));
        const xHi = _xN(xDomain[1], Math.max(blA.td, blB.td));
        return [{ x: xLo, y: yAt(xLo) }, { x: xHi, y: yAt(xHi) }];
      })()
    : null;
  // Auto-weights from the slope: efficiency is weighted by the T/mass GAINED per
  // +current (Nm/kg); T/mass by the efficiency LOST per +current (%-points).
  const blWeffNmKg = bline ? (bline.w_eff ?? 0) : 0;         // ΔT/mass  (weight on η)
  const blWtdPct   = bline ? (bline.w_td ?? 0) * 100 : 0;     // ΔEff %   (weight on T/mass)

  // Box-walking now runs SERVER-SIDE: with Auto-walk on we hand the backend
  // auto_expand + maxRounds and it re-centers the window + re-runs round after round
  // on its own (the tab can be closed — the live mirror reattaches). runDescent
  // resolves when the whole sequence finishes.
  const launch = async () => {
    setApplied(false);
    setLastOptSnapshot(optInputs);   // snapshot the inputs this run launches with
    localRun.current = true;
    try {
      await runDescent({ rippleMax, maxIters, wEff, wTd, steps, algorithm, nSectors,
                         targetTorque: 0, vPeakLimit: 1e9, optimizeGamma: false,   // fixed Simulation current, no voltage limit
                         autoExpand: autoWalk, maxRounds, surrogateSeed,
                         objective, currentBumpPct: currentBump,
                         ripplePenaltyLambda: rippleLam,
                         thdPenaltyLambda: thdLam, thdMaxPct: thdMax });
    } finally {
      localRun.current = false;
    }
  };

  // Manual "continue in the same direction": re-center on the optimum, run once more
  // (Auto-walk off → a single backend round).
  const continueWalk = async () => { await applyDescentBest(); await launch(); };
  // Change-diff actions: warm-start re-uses continueWalk (apply best → relaunch on the
  // new inputs); "from scratch" relaunches from the current geometry; dismiss snoozes.
  const reoptScratch = () => { void launch(); };
  const dismissChanges = () => setSnoozeKey(changesKey);

  const row = (label: string, m: any, cost?: number) => (
    <TableRow>
      <TableCell sx={{ fontWeight: 600, fontSize: 11 }}>{label}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.T_ripple_pct, 2)}%</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>
        {m?.THD_LL_pct == null ? '—' : `${fmtNum(m.THD_LL_pct, 2)}%`}
      </TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtPct(m?.efficiency)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.torque_per_mass, 3)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.T_em_Nm, 1)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.current_a, 1)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{fmtNum(m?.mass_total_kg, 2)}</TableCell>
      <TableCell align="right" sx={{ fontSize: 11 }}>{cost == null ? '—' : cost.toFixed(4)}</TableCell>
    </TableRow>
  );

  return (
    <Box sx={{ mt: chartsOnly ? 1 : 4 }}>
      {!chartsOnly && <Divider sx={{ mb: 2 }} />}

      {/* chartsOnly: the Optimize tab shows ONLY the auto card by default, but
          the user wants the classic charts (metrics table, convergence, the
          objective cloud with the baseline line) back under it — WITHOUT the
          manual controls. Everything from the header through the toolbars is
          skipped; the data sections below read the same store the auto run
          writes, so they visualise auto runs too. */}
      {!chartsOnly && (<>
      {/* ── Header: title + Run ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
        <TrendingDownIcon sx={{ fontSize: 18 }} />
        <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 160 }}>
          Geometry optimizer — maximise efficiency × torque/mass
        </Typography>
        {descentRunning ? (
          <Button variant="contained" color="error" size="small" startIcon={<StopIcon />}
            onClick={() => cancelDescent()}>
            {walkRounds > 1 ? `Stop · round ${walkRound}/${walkRounds}` : `Stop ${st.iter ?? 0}/${st.max_iters ?? maxIters}`}
          </Button>
        ) : (
          <Button variant="contained" size="small" startIcon={<PlayArrowIcon />}
            disabled={activeVars.length === 0 || !connectedToApi}
            onClick={launch}>
            Run descent
          </Button>
        )}
      </Box>

      {/* ── Controls grouped into labelled sections ── */}
      <Box sx={{ display: 'flex', gap: 2.5, flexWrap: 'wrap', alignItems: 'flex-start', mb: 1.5 }}>
        <Group label="Algorithm">
          <Tooltip title="Optimization algorithm. CMA-ES: derivative-free, noise-robust evolution strategy (recommended). Gradient: the original finite-difference descent." placement="top">
            <ToggleButtonGroup exclusive size="small" value={algorithm}
              onChange={(_, a) => a && setAlgorithm(a)} sx={{ height: 26 }}>
              <ToggleButton value="cmaes"    sx={{ px: 1, fontSize: 10 }}>CMA-ES</ToggleButton>
              <ToggleButton value="gradient" sx={{ px: 1, fontSize: 10 }}>Gradient</ToggleButton>
            </ToggleButtonGroup>
          </Tooltip>
          <Tooltip title="FEM symmetry — taken from the Mesh tab (sectors), single source. Read-only here so the optimizer builds the disk the SAME way Simulation does → optimizer ripple == Simulation ripple." placement="top">
            <Chip size="small" variant="outlined"
              label={`sym: ${nSectors <= 1 ? 'Full disk' : `1/${nSectors}`} · Mesh`}
              sx={{ height: 26, fontSize: 10 }} />
          </Tooltip>
          <Tooltip title="Descent iterations / CMA-ES generations." placement="top">
            <TextField label="iters" type="number" size="small" value={maxIters}
              onChange={e => setMaxIters(Math.max(1, Math.min(40, Math.round(+e.target.value) || 1)))}
              inputProps={{ min: 1, max: 40, style: { fontSize: 11, padding: '3px 6px', width: 40 } }}
              InputLabelProps={{ sx: { fontSize: 10 } }} />
          </Tooltip>
          <Tooltip title="FEM frames per electrical period — taken from the Simulation tab (Steps per period), single source. Read-only here so the optimizer evaluates ripple at the resolution you simulate at." placement="top">
            <TextField label="steps/T (sim)" type="number" size="small" value={steps} disabled
              inputProps={{ style: { fontSize: 11, padding: '3px 6px', width: 40 } }}
              InputLabelProps={{ sx: { fontSize: 10 } }} />
          </Tooltip>
        </Group>

        <Group label="Objective">
          <Tooltip title="How designs are scored. 'Above baseline' (recommended): two sims of the START geometry — at I and I·(1+bump) — define the 'current-only' trade-off line; the optimizer maximises the PERPENDICULAR DISTANCE above it, so the efficiency vs T/mass weights come from the line's slope automatically (no manual guessing). 'η × T/mass': the legacy weighted product." placement="top">
            <ToggleButtonGroup exclusive size="small" value={objective}
              onChange={(_, v) => v && setObjective(v)} sx={{ height: 26 }}>
              <ToggleButton value="baseline_line" sx={{ px: 1, fontSize: 10 }}>Above baseline</ToggleButton>
              <ToggleButton value="product"       sx={{ px: 1, fontSize: 10 }}>η × T/mass</ToggleButton>
            </ToggleButtonGroup>
          </Tooltip>
          <Tooltip title={`Ripple suppression — starting penalty weight λ (ON by default = 25). Adds cost += λ·max(0, ripple% − limit%)/100 against the limit (${rippleMax.toFixed(1)}% from Operating points). With Auto-walk on, the backend ESCALATES λ (×2 per round, up to 16× this start) until ripple falls under the limit, and keeps optimizing while ripple is still over — so this start value only sets how gently it begins: ~25 soft, 100 aggressive. 0 = OFF (ripple only trimmed on the chart, never felt by the optimizer).`} placement="top">
            <TextField label="ripple λ" type="number" size="small" value={rippleLam}
              onChange={e => updRippleLam(+e.target.value)}
              inputProps={{ min: 0, step: 5, style: { fontSize: 11, padding: '3px 6px', width: 44 } }}
              InputLabelProps={{ sx: { fontSize: 10 } }}
              sx={rippleLam > 0 ? { '& .MuiOutlinedInput-notchedOutline': { borderColor: '#f59e0b' } } : undefined} />
          </Tooltip>
          <Tooltip title={`Voltage-quality gate for sinusoidal FOC drives: line-to-line THD of the phase voltage (non-triplen harmonics 5/7/11/13… — triplens cancel in wye and are excluded). λ > 0 adds cost += λ·max(0, THD_LL% − limit%)/100, steering the optimizer toward a sinusoidal back-EMF: ~25 = soft, 100 = hard wall. 0 = report only. CIANO-S target: THD_LL < 5%.`} placement="top">
            <TextField label="THD λ" type="number" size="small" value={thdLam}
              onChange={e => updThdLam(+e.target.value)}
              inputProps={{ min: 0, step: 5, style: { fontSize: 11, padding: '3px 6px', width: 44 } }}
              InputLabelProps={{ sx: { fontSize: 10 } }}
              sx={thdLam > 0 ? { '& .MuiOutlinedInput-notchedOutline': { borderColor: '#22d3ee' } } : undefined} />
          </Tooltip>
          {thdLam > 0 && (
            <Tooltip title="THD_LL limit [%] the penalty enforces." placement="top">
              <TextField label="THD ≤ %" type="number" size="small" value={thdMax}
                onChange={e => updThdMax(+e.target.value)}
                inputProps={{ min: 0, step: 0.5, style: { fontSize: 11, padding: '3px 6px', width: 44 } }}
                InputLabelProps={{ sx: { fontSize: 10 } }} />
            </Tooltip>
          )}
          {objective === 'baseline_line' ? (
            <>
              <Tooltip title="The 2nd baseline sim runs at I·(1+this%). Larger = a longer baseline arm (steadier slope) but a bigger efficiency drop to span. 10% is a good default." placement="top">
                <TextField label="+% I" type="number" size="small" value={currentBump}
                  onChange={e => setCurrentBump(Math.max(1, Math.min(50, Math.round(+e.target.value) || 10)))}
                  inputProps={{ min: 1, max: 50, style: { fontSize: 11, padding: '3px 6px', width: 44 } }}
                  InputLabelProps={{ sx: { fontSize: 10 } }} />
              </Tooltip>
              <Tooltip title="Run the 2 baseline sims now (at I and +X% I) and draw the current-only line on the chart below — see the reference before launching the full optimization." placement="top">
                <span>
                  <Button size="small" variant="outlined"
                    onClick={() => computeBaselineLine({ currentBumpPct: currentBump, steps, nSectors })}
                    disabled={!connectedToApi || baselineBusy || descentRunning}
                    startIcon={baselineBusy ? <CircularProgress size={12} color="inherit" /> : undefined}
                    sx={{ textTransform: 'none', fontSize: 10, height: 26 }}>
                    {baselineBusy ? 'Computing…' : 'Draw baseline'}
                  </Button>
                </span>
              </Tooltip>
            </>
          ) : (
            <>
              <Tooltip title="Efficiency weight (exponent on eff/eff₀)." placement="top">
                <TextField label="w·eff" type="number" size="small" value={wEff}
                  onChange={e => setWEff(Math.max(0, +e.target.value || 0))}
                  inputProps={{ min: 0, step: 0.5, style: { fontSize: 11, padding: '3px 6px', width: 40 } }}
                  InputLabelProps={{ sx: { fontSize: 10 } }} />
              </Tooltip>
              <Tooltip title="Torque-density weight (exponent on td/td₀). 0 = ignore mass." placement="top">
                <TextField label="w·Nm/kg" type="number" size="small" value={wTd}
                  onChange={e => setWTd(Math.max(0, +e.target.value || 0))}
                  inputProps={{ min: 0, step: 0.5, style: { fontSize: 11, padding: '3px 6px', width: 44 } }}
                  InputLabelProps={{ sx: { fontSize: 10 } }} />
              </Tooltip>
            </>
          )}
        </Group>

        <Group label="Search options">
          <Tooltip title="Auto-walk (box-walking): if a variable ends at the edge of its ±deviation window, the server re-centers the window on the optimum and re-optimizes — round after round, until every variable settles inside its window, hits a physical (schema) limit, or the round cap. Runs FULLY on the backend: you can close the tab and it finishes on its own. Off = one run, then boundary variables are flagged for a manual one-click continue." placement="top">
            <ToggleButton value="autowalk" selected={autoWalk} size="small"
              onChange={() => setAutoWalk(a => !a)} sx={{ px: 1, py: 0, height: 26, fontSize: 10 }}>
              Auto-walk
            </ToggleButton>
          </Tooltip>
          <Tooltip title="Surrogate (Bayesian) warm-start: seed the search from the geometry a RandomForest surrogate predicts best over ALL past evaluations (learned, not fixed). The more you optimize, the smarter the start → fewer FEM solves. Falls back to the current geometry until ~20 evals are logged." placement="top">
            <ToggleButton value="surrogate" selected={surrogateSeed} size="small"
              onChange={() => setSurrogateSeed(s => !s)} sx={{ px: 1, py: 0, height: 26, fontSize: 10 }}>
              Surrogate seed
            </ToggleButton>
          </Tooltip>
          {autoWalk && (
            <TextField label="max rounds" type="number" size="small" value={maxRounds}
              onChange={(e) => setMaxRounds(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
              sx={{ width: 88 }} inputProps={{ min: 1, max: 20, style: { fontSize: 11 } }}
              InputLabelProps={{ style: { fontSize: 10 } }} />
          )}
        </Group>
      </Box>

      {/* Run summary — structured data only; the explanation lives in the ⓘ tooltip. */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1.5, flexWrap: 'wrap' }}>
        <Chip size="small" variant="outlined" sx={{ height: 20, fontSize: 10 }}
          label={`${op0.current_a} A · ${op0.rpm} rpm · γ ${op0.gamma_deg ?? 0}°`} />
        <Chip size="small" variant="outlined" sx={{ height: 20, fontSize: 10 }}
          label={`${activeVars.length} vars`} />
        <Chip size="small" variant="outlined" sx={{ height: 20, fontSize: 10 }}
          label={objective === 'baseline_line' ? 'obj: above baseline' : 'obj: η × T/mass'} />
        <Chip size="small" variant="outlined"
          label={rippleLam > 0 ? `ripple ≤ ${rippleMax.toFixed(1)}% · λ ${rippleLam}` : 'ripple: chart-only'}
          sx={{ height: 20, fontSize: 10,
                ...(rippleLam > 0 ? { color: '#f59e0b', borderColor: 'rgba(245,158,11,0.6)' } : {}) }} />
        {thdLam > 0 && (
          <Chip size="small" variant="outlined"
            label={`THD_LL ≤ ${thdMax}% · λ ${thdLam}`}
            sx={{ height: 20, fontSize: 10, color: '#22d3ee',
                  borderColor: 'rgba(34,211,238,0.6)' }} />
        )}
        <HelpTip title={`Operating point — from Simulation (single source). Objective: ${objective === 'baseline_line'
            ? `max perpendicular distance above the current-only line (2 sims of the start geometry @ I and +${currentBump}% I set the η vs T/mass weights from the line slope)`
            : 'max (η/η₀)^w·eff × (T/mass ÷ (T/mass)₀)^w·Nm/kg'}. Ripple: ${rippleLam > 0
            ? `enforced in the cost above ${rippleMax.toFixed(1)}% (λ=${rippleLam})`
            : 'NOT constrained — trimmed on the chart only; set ripple λ > 0 to enforce the limit'}. Only whitelisted geometry variables are varied.`} />
      </Box>

      {descentError && (
        <Typography color="error" variant="caption" sx={{ display: 'block', mb: 1 }}>
          Descent error: {descentError}
        </Typography>
      )}
      {baselineError && (
        <Typography color="error" variant="caption" sx={{ display: 'block', mb: 1 }}>
          Baseline error: {baselineError}
        </Typography>
      )}

      {/* Inputs changed since the last optimization → comparison + warm-start re-run */}
      {showChanges && (
        <Box sx={{ mb: 1.5, p: 1, borderRadius: 1, border: '1px solid #f59e0b',
                   bgcolor: 'rgba(245,158,11,0.08)' }}>
          <Typography variant="caption" sx={{ display: 'block', fontWeight: 700, color: '#f59e0b', mb: 0.5 }}>
            ⚠ {changes.length} input{changes.length === 1 ? '' : 's'} changed since the last optimization — re-optimize?
          </Typography>
          <Table size="small" sx={{ mb: 0.75, width: 'auto',
            '& td, & th': { py: 0.2, px: 1, borderColor: 'rgba(245,158,11,0.2)' } }}>
            <TableHead><TableRow>
              <TableCell sx={{ fontSize: 10, color: 'text.secondary' }}>Parameter</TableCell>
              <TableCell sx={{ fontSize: 10, color: 'text.secondary' }}>Previous</TableCell>
              <TableCell sx={{ fontSize: 10, color: 'text.secondary' }}>Now</TableCell>
            </TableRow></TableHead>
            <TableBody>
              {changes.map((c) => (
                <TableRow key={c.label}>
                  <TableCell sx={{ fontSize: 11 }}>{c.label}</TableCell>
                  <TableCell sx={{ fontSize: 11, color: 'text.secondary' }}>
                    {c.label === 'Variables / windows' ? '(set)' : String(c.prev)}
                  </TableCell>
                  <TableCell sx={{ fontSize: 11, color: '#fbbf24', fontWeight: 600 }}>
                    {c.label === 'Variables / windows' ? '(changed)' : String(c.now)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <Button variant="contained" color="warning" size="small" startIcon={<PlayArrowIcon />}
              disabled={!connectedToApi || !best} onClick={continueWalk}>
              Re-optimize (warm-start)
            </Button>
            {best && (
              <HelpTip title={`Warm-start re-uses the last best geometry (η ${fmtPct(best.efficiency)}); the rated current is re-derived for the new conditions (γ from Simulation).`} />
            )}
            <Button variant="outlined" size="small" disabled={!connectedToApi} onClick={reoptScratch}>
              From scratch
            </Button>
            <Button variant="text" size="small" color="inherit" onClick={dismissChanges}>
              Dismiss
            </Button>
          </Box>
        </Box>
      )}

      {/* Live progress: phase headline + bar (so a long FEM run visibly works) */}
      {descentRunning && (
        <Box sx={{ mb: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <CircularProgress size={14} />
            <Typography variant="caption" sx={{ fontWeight: 600 }}>{statusText}</Typography>
          </Box>
          <LinearProgress variant={busyIndeterminate ? 'indeterminate' : 'determinate'} value={genPct}
            sx={{ height: 6, borderRadius: 3 }} />
        </Box>
      )}

      {/* Auto-expand feed: which variable windows the server extended (gradient)
          or walked (CMA) because the optimum was pinned at their edge.
          Structured chips (one per variable), explanation in the ⓘ tooltip. */}
      {rangeSummary.length > 0 && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.6, mb: 1, flexWrap: 'wrap' }}>
          <Typography variant="caption" sx={{ color: '#f59e0b', fontWeight: 600 }}>
            Auto-extended · {rangeSummary.length}
          </Typography>
          <HelpTip title="What the optimizer auto-adjusted during the run. 'var → [lo … hi]' = window re-centered on the round's best (box-walk); 'var grew [lo … hi]' = a pinned edge grown into fresh territory; 'min/max a → b' = one edge extended (gradient); 'ripple λ a → b' = ripple penalty escalated because ripple was still over the limit (penalty continuation). You set initial ranges + a starting λ — the algorithm moves both on its own." />
          {rangeSummary.map((e: any) => {
            const isRamp = e.side === 'ramp';
            const isGrow = e.side === 'grow';
            const clr = isRamp ? '#f472b6' : '#f59e0b';
            const label = isRamp
              ? `ripple λ ${e.from} → ${e.to}`
              : e.side === 'walk'
                ? `${e.name} → [${e.to} … ${e.to_hi}]`
                : isGrow
                  ? `${e.name} grew [${e.to} … ${e.to_hi}]`
                  : `${e.name} ${e.side === 'high' ? 'max' : 'min'} ${e.from} → ${e.to}`;
            return (
              <Chip key={`${e.name}:${e.side}`} size="small" variant="outlined"
                label={label}
                sx={{ height: 20, fontSize: 10, color: clr,
                      borderColor: `${clr}73` }} />
            );
          })}
        </Box>
      )}

      {/* Progress chips: iters / evals / best */}
      {(descentRunning || history.length > 0) && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
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

      </>)}

      {/* Metrics table: baseline / current / best */}
      {base && (
        <Table size="small" sx={{ mb: 2, '& td, & th': { py: 0.4, px: 1 } }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ fontSize: 10, color: 'text.secondary' }}>—</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Ripple</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>THD_LL</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Eff</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>Nm/kg</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>T (Nm)</TableCell>
              <TableCell align="right" sx={{ fontSize: 10, color: 'text.secondary' }}>I (A)</TableCell>
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
              Convergence — F = eff×Nm/kg (green, ↑) · ripple % (blue)
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
                  <Line yAxisId="l" type="monotone" dataKey="F" name="F" stroke="#22c55e" dot={{ r: 0.5 }} strokeWidth={1.25} isAnimationActive={false} />
                  <Line yAxisId="r" type="monotone" dataKey="T_ripple_pct" name="Ripple %" stroke="#38bdf8" dot={{ r: 0.5 }} strokeWidth={1.25} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </Box>
          </Box>

          {/* Metric trajectories: torque density + efficiency */}
          <Box sx={{ flex: 1, minWidth: 320 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              Metrics — torque/mass (blue) · efficiency % (green)
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
                  <Line yAxisId="l" type="monotone" dataKey="torque_per_mass" name="Nm/kg" stroke="#3b82f6" dot={{ r: 0.5 }} strokeWidth={1.25} isAnimationActive={false} />
                  <Line yAxisId="r" type="monotone" dataKey="eff_pct" name="Eff %" stroke="#22c55e" dot={{ r: 0.5 }} strokeWidth={1.25} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </Box>
          </Box>
        </Box>
      )}

      {/* 2-D objective-space projection: efficiency (Y) vs torque/mass (X).
          Shown once there are designs OR a standalone baseline line to draw. */}
      {(totalValidPts > 1 || bline) && (
        <Box sx={{ mb: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
            <Typography variant="caption" color="text.secondary">
              Objective space — efficiency vs torque/mass ·{' '}
              <span style={{ color: '#22c55e' }}>feasible</span> /{' '}
              <span style={{ color: '#ef4444' }}>ripple&gt;limit</span> ·{' '}
              <span style={{ color: '#3b82f6' }}>descent path</span> ·{' '}
              <span style={{ color: '#fbbf24' }}>★ best</span>
              {bline && <> · <span style={{ color: '#f59e0b' }}>— baseline</span></>}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
              <Tooltip title="2-D = efficiency vs torque/mass. 3-D lifts ripple onto the height (z) axis, with a translucent plane at the ripple gate — see the eff×td-vs-ripple trade-off at a glance. Drag to rotate." placement="top">
                <ToggleButtonGroup exclusive size="small" value={view3d ? '3d' : '2d'}
                  onChange={(_, m) => m && setView3d(m === '3d')} sx={{ height: 24 }}>
                  <ToggleButton value="2d" sx={{ px: 0.8, fontSize: 10 }}>2D</ToggleButton>
                  <ToggleButton value="3d" sx={{ px: 0.8, fontSize: 10 }}>3D</ToggleButton>
                </ToggleButtonGroup>
              </Tooltip>
              <Tooltip title="Y-axis (efficiency) scaling. Auto = fit to the meaningful designs (descent path + best + feasible) and re-zoom as the run progresses, dropping the uninformative low points. Manual = fix the range below." placement="top">
                <ToggleButtonGroup exclusive size="small" value={yMode}
                  onChange={(_, m) => m && setYMode(m)} sx={{ height: 24 }}>
                  <ToggleButton value="auto"   sx={{ px: 0.8, fontSize: 10 }}>Y: Auto</ToggleButton>
                  <ToggleButton value="manual" sx={{ px: 0.8, fontSize: 10 }}>Manual</ToggleButton>
                </ToggleButtonGroup>
              </Tooltip>
              {yMode === 'manual' && (
                <>
                  <TextField label="Y min" type="number" size="small" value={yMin}
                    onChange={e => setYMin(Math.min(yMax - 0.5, +e.target.value || 0))}
                    sx={{ width: 62 }} inputProps={{ step: 0.5, style: { fontSize: 10, padding: '2px 4px' } }}
                    InputLabelProps={{ sx: { fontSize: 9 } }} />
                  <TextField label="Y max" type="number" size="small" value={yMax}
                    onChange={e => setYMax(Math.max(yMin + 0.5, +e.target.value || 100))}
                    sx={{ width: 62 }} inputProps={{ step: 0.5, style: { fontSize: 10, padding: '2px 4px' } }}
                    InputLabelProps={{ sx: { fontSize: 9 } }} />
                </>
              )}
              {history.length > 0 && loadLineDesigns.some(([, a]) => a.length > 1) && (
                <Tooltip title="Overlay current-sweep load-lines for the finalist designs — a disambiguation tool for AFTER a run, when two candidates are too close to tell apart from the objective points alone. Each curve sweeps current; the ◆ marks the rated torque. Off by default to keep the chart clean." placement="top">
                  <ToggleButton value="ll" selected={showLoadLines} size="small"
                    onChange={() => setShowLoadLines(v => !v)} sx={{ px: 1, py: 0, height: 24, fontSize: 10 }}>
                    Load-lines
                  </ToggleButton>
                </Tooltip>
              )}
            </Box>
          </Box>
          {bline && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
              <Typography variant="caption" sx={{ color: '#f59e0b' }}>
                Baseline +{bline.bump_pct}% I: T/mass {blA!.td.toFixed(2)}→{blB!.td.toFixed(2)} Nm/kg ·
                η {blA!.eff.toFixed(2)}→{blB!.eff.toFixed(2)}% · w {blWeffNmKg.toFixed(2)} / {blWtdPct.toFixed(2)}
              </Typography>
              <HelpTip title={`Auto-weights derived from the baseline current bump: η ×${blWeffNmKg.toFixed(2)} (Nm/kg gained per +I), T/mass ×${blWtdPct.toFixed(2)} (% lost per +I). The optimizer's score is the perpendicular distance above this baseline line.`} />
            </Box>
          )}
          {showLoadLines && loadLineDesigns.some(([, a]) => a.length > 1) && (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '12px', mb: 0.5 }}>
              <Typography variant="caption" color="text.secondary">load-lines · ◆ rated {ratedTorque} N·m:</Typography>
              {loadLineDesigns.map(([name], i) => (
                <Typography key={name} variant="caption" sx={{ color: LL_PALETTE[i % LL_PALETTE.length] }}>● {name}</Typography>
              ))}
            </Box>
          )}
          {/* On-chart ripple cut — drag to visually trim high-ripple points off the
              chart (display only; does NOT change the search constraint above). */}
          {points.length > 0 && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 1, mb: 0.5 }}>
              <Tooltip title="Visually trim points by ripple — filters the chart only, does NOT change the search constraint that steers the optimizer. Slide left to drop high-ripple designs; full right shows every evaluated point." placement="top">
                <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap', cursor: 'help' }}>
                  Show ripple ≤ <strong>{effCut.toFixed(1)}%</strong>
                </Typography>
              </Tooltip>
              {/* value is CLAMPED to the track: pressing "show all" sets a cut
                  above the cap, and the thumb then rests at the right end while
                  the label above states the real number — the control never
                  displays a position it does not have. */}
              <Slider size="small" min={0} max={sliderMax} step={0.1}
                value={Math.min(effCut, sliderMax)}
                onChange={(_, v) => setDisplayCut(v as number)} sx={{ maxWidth: 320, flex: 1 }} />
              {/* Say what the cut is hiding, and what the chart drops for being
                  unplottable — a count that silently shrinks reads as "the run
                  found nothing". One line; the arithmetic lives in the tooltip. */}
              <Tooltip placement="top" title={
                `The backend publishes every evaluated design. `
                + `${hiddenByCut} of them sit above the ${effCut.toFixed(1)}% cut and are hidden here — `
                + (effCut >= _dataMaxRipple
                    ? `nothing is above it.`
                    : `drag right to see more; the slider stops at ${RIPPLE_SLIDER_MAX}% `
                      + `(the worst design in this cloud is ${_dataMaxRipple.toFixed(1)}%), so use `
                      + `"show all" for the rest.`)
                + (nonPhysicalPts > 0
                    ? ` A further ${nonPhysicalPts} carry non-physical numbers (η ≤ 0 or η > 1, `
                      + `T/mass ≤ 0) and are never plotted.` : '')}>
                <Typography variant="caption" color="text.secondary"
                  sx={{ whiteSpace: 'nowrap', cursor: 'help' }}>
                  {shownPts.length}/{points.length} shown
                  {hiddenByCut > 0 ? ` · ${hiddenByCut} above the cut` : ''} ⓘ
                </Typography>
              </Tooltip>
              {/* The cap hides points, so the way past it has to be ON the bar —
                  otherwise "125/125 shown" quietly becomes "40/125" with no way
                  back. "show all" lifts the cut to the worst design in the
                  cloud; "30%" puts it back. */}
              {_dataMaxRipple > sliderMax && (
                <Button size="small" sx={{ minWidth: 0, px: 0.75, fontSize: 10 }}
                  onClick={() => setDisplayCut(effCut > sliderMax
                    ? null : Math.ceil(_dataMaxRipple))}>
                  {effCut > sliderMax ? `${RIPPLE_SLIDER_MAX}%` : 'show all'}
                </Button>
              )}
              {displayCut != null && effCut <= sliderMax && (
                <Button size="small" sx={{ minWidth: 0, px: 0.75, fontSize: 10 }}
                  onClick={() => setDisplayCut(null)}>reset</Button>
              )}
              {/* The Pareto answer, one line, next to the objective's own. */}
              {pareto && (
                <Tooltip placement="top" title={
                  `F is the perpendicular distance above the baseline line; on this machine its `
                  + `normal is 99.9998% along the efficiency axis (1 pp of η = 4.97 Nm/kg ≈ half this `
                  + `machine's torque density) and ripple below the gate earns nothing — so F ≤ 0 does `
                  + `NOT mean "nothing better". ▲ marks candidates at least as good as the starting `
                  + `design on torque, ripple AND efficiency; ${pareto.n_front ?? 0} of `
                  + `${pareto.n_candidates ?? 0} are on the non-dominated front. Only this run's own `
                  + `evals at its own operating point (${Number(pareto.operating_point?.current_a ?? 0).toFixed(1)} A`
                  + `${pareto.operating_point?.gamma_deg != null ? `, γ ${Number(pareto.operating_point.gamma_deg).toFixed(1)}°` : ''}) `
                  + `are compared${pareto.n_other_op ? `; ${pareto.n_other_op} point(s) measured elsewhere are excluded` : ''}. `
                  + `Click a ▲ to apply it.`}>
                  <Typography variant="caption" sx={{ whiteSpace: 'nowrap', cursor: 'help',
                      color: (pareto.n_dominating ?? 0) > 0 ? '#e879f9' : 'text.secondary' }}>
                    ▲ {pareto.n_dominating ?? 0} beat all 3 axes · {pareto.n_front ?? 0} on front ⓘ
                  </Typography>
                </Tooltip>
              )}
            </Box>
          )}
          <Box sx={{ height: 460, width: '100%' }}>
            {view3d ? (
              <Scatter3D points={points.filter((p: any) => p.ripple == null || p.ripple <= effCut)} rippleMax={effCut}
                best={best && best.torque_per_mass != null
                  ? { td: best.torque_per_mass, eff: best.efficiency, ripple: best.T_ripple_pct } : null} />
            ) : (
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 24, left: 8, bottom: 18 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
                <XAxis type="number" dataKey="td" name="Nm/kg" domain={xDomain} allowDataOverflow tick={{ fontSize: 10 }}
                  label={{ value: 'Torque / mass  (Nm/kg)', position: 'insideBottom', offset: -10, fontSize: 11 }} />
                <YAxis type="number" dataKey="eff" name="Eff %" domain={yDomain} allowDataOverflow tick={{ fontSize: 10 }} width={48}
                  label={{ value: 'Efficiency %', angle: -90, position: 'insideLeft', fontSize: 11 }} />
                <ZAxis type="number" dataKey="z" range={[10, 150]} />
                <RTooltip cursor={{ strokeDasharray: '3 3' }}
                  content={({ active, payload }: any) => {
                    if (!active || !payload || !payload.length) return null;
                    const p = payload[0]?.payload || {};
                    return (
                      <div style={{ background: 'var(--app-bg)', border: '1px solid var(--line)', borderRadius: 4,
                                    padding: '6px 9px', fontSize: 11, lineHeight: 1.6 }}>
                        {p.eff != null && <div style={{ color: '#22c55e' }}>Eff %: {Number(p.eff).toFixed(3)}</div>}
                        {p.td  != null && <div style={{ color: '#3b82f6' }}>Nm/kg: {Number(p.td).toFixed(3)}</div>}
                        {p.ripple != null && (
                          <div style={{ color: '#22c55e' }}>
                            Ripple %: {Number(p.ripple).toFixed(2)}
                          </div>
                        )}
                        {p.iter != null && <div style={{ color: 'var(--text-2)' }}>iter: {p.iter}</div>}
                        {p.I    != null && <div style={{ color: 'var(--text-2)' }}>I: {p.I} A</div>}
                      </div>
                    );
                  }} />
                {_blSeg && (
                  <ReferenceLine ifOverflow="hidden" segment={_blSeg as any}
                    stroke="#f59e0b" strokeDasharray="6 4" strokeWidth={1} />
                )}
                {blMarkers.length > 0 && (
                  <Scatter name="baseline" data={blMarkers} fill="#f59e0b" fillOpacity={0.95}
                    shape="cross" isAnimationActive={false}>
                    <LabelList dataKey="tag" position="top" fontSize={9} fill="#f59e0b" />
                  </Scatter>
                )}
                <Scatter name="designs" data={restPts} fill="#22c55e" fillOpacity={0.55} isAnimationActive={false}
                  shape={(p: any) => (
                    <g>
                      {/* invisible fat hit-area so the tiny dot stays clickable */}
                      <circle cx={p.cx} cy={p.cy} r={6} fill="transparent" />
                      <circle cx={p.cx} cy={p.cy} r={2.2} fill="#15803d" fillOpacity={0.9} />
                    </g>
                  )}
                  cursor="pointer" onClick={(d: any) => d && setSelectedPt(d.payload ?? d)} />
                {/* ▲ = beats the starting design on torque AND ripple AND
                    efficiency. Same click-to-pick path as any other point, so
                    one of these can be applied directly. */}
                <Scatter name="▲ beats on all 3" data={domPts} fill="#e879f9" isAnimationActive={false}
                  shape={(p: any) => (
                    <g>
                      <circle cx={p.cx} cy={p.cy} r={7} fill="transparent" />
                      <polygon points={`${p.cx},${p.cy - 4.6} ${p.cx + 4.2},${p.cy + 3.2} ${p.cx - 4.2},${p.cy + 3.2}`}
                        fill="#e879f9" stroke="#a21caf" strokeWidth={0.8} />
                    </g>
                  )}
                  cursor="pointer" onClick={(d: any) => d && setSelectedPt(d.payload ?? d)} />
                <Scatter name="descent path" data={trajPts} fill="#3b82f6"
                  shape={(p: any) => <circle cx={p.cx} cy={p.cy} r={2.2} fill="#1d4ed8" />}
                  line={{ stroke: '#3b82f6', strokeWidth: 1.5 }} lineType="joint" isAnimationActive={false} />
                <Scatter name="★ best" data={bestPt} fill="#fbbf24" shape="star" isAnimationActive={false}
                  cursor="pointer" onClick={(d: any) => d && setSelectedPt(d.payload ?? d)} />
                {selectedPt && selectedPt.td != null && (
                  <Scatter name="● picked" data={[{ td: selectedPt.td, eff: selectedPt.eff, z: 9 }]}
                    fill="none" stroke="#e879f9" strokeWidth={1.25} shape="circle" isAnimationActive={false} />
                )}
                {showLoadLines && loadLineDesigns.map(([name, arr], i) => (
                  <Scatter key={'ll-' + name} name={name}
                    data={arr.map((p: any) => ({ td: p.td, eff: p.eff, z: 1, I: p.I }))}
                    fill={LL_PALETTE[i % LL_PALETTE.length]}
                    line={{ stroke: LL_PALETTE[i % LL_PALETTE.length], strokeWidth: 2.5 }} lineType="joint" isAnimationActive={false}>
                    <LabelList dataKey="I" position="top" formatter={(v: any) => `${v}A`} fill={LL_PALETTE[i % LL_PALETTE.length]} fontSize={9} />
                  </Scatter>
                ))}
                {showLoadLines && ratedMarkers.map((rp: any) => (
                  <Scatter key={'rated-' + rp.name} name={`◆ ${rp.name} @ ${ratedTorque} Nm`}
                    data={[{ td: rp.td, eff: rp.eff, z: rp.z, I: rp.I }]}
                    fill={rp.color} shape="diamond" isAnimationActive={false}>
                    <LabelList dataKey="I" position="right" formatter={(v: any) => `${v}A`} fill={rp.color} fontSize={9} />
                  </Scatter>
                ))}
              </ScatterChart>
            </ResponsiveContainer>
            )}
          </Box>
        </Box>
      )}

      {/* Descent direction per variable (−∂cost/∂var) — the optimizer at work */}
      {gradData.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
            <Typography variant="caption" color="text.secondary">
              Descent direction (−∂cost/∂var) · <span style={{ color: '#22c55e' }}>↑</span>
              {' / '}<span style={{ color: '#ef4444' }}>↓</span>
            </Typography>
            <HelpTip title="Green ↑ = raise the variable, red ↓ = lower it; bar length = sensitivity (−∂cost/∂var)." />
          </Box>
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
              ? `⚠ ${boundary.length} variable${boundary.length === 1 ? '' : 's'} at the range boundary`
              : '✓ hit the physical limit — optimum found'}
          </Typography>
          {boundary.map((f) => (
            <Typography key={f.name} variant="caption"
              sx={{ display: 'block', fontSize: 10.5, color: 'text.secondary' }}>
              <strong>{f.label}</strong>: {f.value.toFixed(2)} {f.pinned === 'high' ? '↑' : '↓'}{' '}
              edge [{f.min.toFixed(2)} … {f.max.toFixed(2)}]
              {f.atHard
                ? <span style={{ color: '#ef4444' }}> · physical limit</span>
                : <span style={{ color: '#f59e0b' }}> · can shift window</span>}
            </Typography>
          ))}
          {softPinned.length > 0 && (
            <Button variant="outlined" color="warning" size="small" sx={{ mt: 0.75 }}
              startIcon={<TrendingDownIcon />} disabled={!connectedToApi}
              onClick={continueWalk}>
              Shift window & continue ({softPinned.length})
            </Button>
          )}
        </Box>
      )}

      {/* Apply — the optimiser's best, OR a user-picked scatter point if one is
          clicked. Click any point on the objective-space chart to pick it. */}
      {best && !descentRunning && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          {selectedPt && selectedPt.overrides ? (
            <>
              <Button variant="outlined" color="success" size="small" startIcon={<PlayArrowIcon />}
                onClick={async () => { await applyDescentPoint(selectedPt); setApplied(true); }}>
                Apply picked point to geometry
              </Button>
              <Typography variant="caption" sx={{ color: '#e879f9' }}>
                ● η {selectedPt.eff != null ? Number(selectedPt.eff).toFixed(2) : '—'}% ·{' '}
                {selectedPt.td != null ? Number(selectedPt.td).toFixed(2) : '—'} Nm/kg ·{' '}
                ripple {selectedPt.ripple != null ? Number(selectedPt.ripple).toFixed(2) : '—'}%
              </Typography>
              <Button variant="text" size="small" color="inherit"
                onClick={() => { setSelectedPt(null); setApplied(false); }}>clear pick</Button>
            </>
          ) : (
            <Button variant="outlined" color="success" size="small"
              startIcon={applied ? <CheckCircleIcon /> : <PlayArrowIcon />}
              disabled={applied}
              onClick={async () => { await applyDescentBest(); setApplied(true); }}>
              {applied ? 'Applied to geometry' : 'Apply best to geometry'}
            </Button>
          )}
          {/* Where the applied design was archived — one line, same wording as
              the one-click card. An apply that was NOT archived says so in red:
              the design then exists only in the editor. */}
          {appliedSave && (
            <>
              <Typography variant="caption"
                sx={{ color: appliedSave.busy ? 'text.secondary'
                      : appliedSave.ok ? '#22c55e' : '#ef4444',
                      fontWeight: appliedSave.ok ? 400 : 700 }}>
                {appliedSave.busy ? 'saving the applied design…' : appliedSaveLine(appliedSave)}
              </Typography>
              {!appliedSave.busy && (
                <HelpTip title={appliedSave.ok
                  ? 'Every apply is archived as a NEW motor in the Motors tab, with the run that '
                    + 'produced it in its description — the source motor is not touched.'
                  : 'The design IS applied, but it was NOT archived: a backend restart would lose '
                    + 'it. Save it by hand (Motors → Save as new motor) or fix the reason shown.'} />
              )}
            </>
          )}
        </Box>
      )}
    </Box>
  );
};

export default DescentPanel;
