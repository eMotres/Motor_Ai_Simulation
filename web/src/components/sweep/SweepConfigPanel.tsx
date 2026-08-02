import React, { useEffect, useState } from 'react';
import {
  Box,
  Typography,
  Button,
  IconButton,
  TextField,
  InputAdornment,
  Divider,
  Card,
  CardContent,
  Tooltip,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  ToggleButton,
  ToggleButtonGroup,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import CloseIcon     from '@mui/icons-material/Close';
import TuneIcon      from '@mui/icons-material/Tune';
import RefreshIcon   from '@mui/icons-material/Refresh';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { useMotorStore } from '../../stores/motorStore';
import AutoOptimizePanel from './AutoOptimizePanel';
import DescentPanel from './DescentPanel';
import SweepStudyPanel from './SweepStudyPanel';
import DOEPanel from './DOEPanel';
import HelpTip from '../common/HelpTip';
import SectionLabel from '../common/SectionLabel';

// Non-geometry variables (selected outside the Geometry tab) need their own
// display label/unit since they are absent from the geometry parameter schema.
const SPECIAL_VARS: Record<string, { label: string; unit: string }> = {
  gamma_deg: { label: 'Load angle γ', unit: '°' },
  current_a:  { label: 'Phase current', unit: 'Arms' },
};

// Read a value the Simulation tab persisted in localStorage (keys are sim.*).
// This is the single source of truth for the operating speed / load angle —
// the Sweep tab pulls rpm & γ from here instead of keeping its own copy.
const readSim = <T,>(key: string, def: T): T => {
  try {
    const raw = localStorage.getItem(`sim.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch {
    return def;
  }
};

// ── Card for one variable — set its range (min · max) + step.  ONE place to
//    pick variables + ranges for ALL algorithms (Optimize / Sweep / DOE). ──────

interface SweepVarCardProps {
  paramName: string;
  label: string;
  unit?: string;
  optimize?: boolean;   // Optimize mode → "value + ± change" instead of min/max/step
}

// string-backed number field — a leading "-" (or "0." mid-typing) isn't clobbered
// to NaN, so negative ranges like γ ∈ [-30, 0] type fine.
const RangeField: React.FC<{ label: string; value: number; onChange: (v: number) => void }> =
  ({ label, value, onChange }) => {
    const [s, setS] = useState(String(value));
    // Re-sync the box when `value` changes from OUTSIDE (e.g. switching the
    // per-algorithm variable set) — but NOT while typing: skip if the text already
    // parses to the same number, so an in-progress "-", "0." or "1." isn't clobbered.
    useEffect(() => { if (parseFloat(s) !== value) setS(String(value)); }, [value]);   // eslint-disable-line react-hooks/exhaustive-deps
    return (
      <TextField label={label} size="small" type="text" value={s}
        onChange={e => { const t = e.target.value; setS(t); const n = parseFloat(t); if (Number.isFinite(n)) onChange(n); }}
        inputProps={{ inputMode: 'decimal', style: { fontSize: 12, padding: '4px 8px' } }} sx={{ flex: 1 }} />
    );
  };

const SweepVarCard: React.FC<SweepVarCardProps> = ({ paramName, label, unit, optimize }) => {
  const { sweepConfig, updateVariation, geometry, parameterSchema } = useMotorStore();
  const v = sweepConfig.variations[paramName];
  if (!v || v.mode === 'fixed') return null;
  // Current value, shown as a reference: geometry vars from the Geometry tab,
  // operating-point vars (γ, current) from Simulation.
  const cur = paramName === 'gamma_deg'  ? Number(readSim('gamma', 0))
            : paramName === 'current_a'  ? Number(readSim('current', 85))
            : Number((geometry as Record<string, any>)[paramName] ?? NaN);
  const src = (paramName === 'gamma_deg' || paramName === 'current_a') ? 'Simulation' : 'Geometry';
  // Tidy figures: round to 0.1 (whole numbers for integer params like turns /
  // slot count) so the card shows 4.7 / 14.0 … 23.4 instead of 4.68 / 14.02.
  const isInt = parameterSchema.find(p => p.name === paramName)?.type === 'int';
  const snap = (x: number) => (isInt ? Math.round(x) : Math.round(x * 10) / 10);
  const fmt  = (x: number) => (isInt ? String(Math.round(x)) : snap(x).toFixed(1));
  // Optimize view anchors the ± window on the CURRENT value.
  const anchor = Number.isFinite(cur) ? cur : (Number(v.min) + Number(v.max)) / 2;
  const half   = snap(Math.max(0, (Number(v.max) - Number(v.min)) / 2));

  return (
    <Card variant="outlined" sx={{ mb: 1.5, bgcolor: 'var(--line-soft)' }}>
      <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, flex: 1 }}>
            {label}
            {unit && (
              <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
                ({unit})
              </Typography>
            )}
          </Typography>
          <IconButton size="small" onClick={() => updateVariation(paramName, { mode: 'fixed' })} sx={{ p: 0.25 }}>
            <CloseIcon sx={{ fontSize: 14 }} />
          </IconButton>
        </Box>

        {!optimize && Number.isFinite(cur) && (() => {
          const outLo = cur < Number(v.min), outHi = cur > Number(v.max);
          const out = outLo || outHi;
          // Show the FAITHFUL value (not snapped to 0.1) — a small fractional
          // param like magnet_fill = 0.467 must not display as "0.5" and look like
          // it contradicts "below min 0.48".  Trim trailing zeros for tidiness.
          const shown = isInt ? String(Math.round(cur))
            : cur.toFixed(3).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
          return (
            <Typography variant="caption" sx={{ color: out ? '#fbbf24' : 'text.disabled', mb: 0.75, display: 'block' }}>
              now: {shown}{unit ? ` ${unit}` : ''} (from {src})
              {out && ` — ${outLo ? 'below min' : 'above max'}, widen the range to include it`}
            </Typography>
          );
        })()}

        {optimize ? (
          // Optimize: vary the variable by ± a change window around its CURRENT
          // value — more intuitive than absolute min/max (that's the Sweep view).
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', rowGap: 0.5 }}>
            <Typography variant="caption" sx={{ fontWeight: 700, color: 'var(--text-0)', flexShrink: 0 }}>
              {fmt(anchor)}{unit ? ` ${unit}` : ''}
            </Typography>
            <RangeField label="± change" value={half}
              onChange={d => {
                const a = Math.abs(d);
                updateVariation(paramName, { min: snap(anchor - a), max: snap(anchor + a) });
              }} />
            <Typography variant="caption" sx={{ color: 'text.secondary', whiteSpace: 'nowrap', flexShrink: 0 }}>
              → {fmt(anchor - half)} … {fmt(anchor + half)}{unit ? ` ${unit}` : ''}
            </Typography>
          </Box>
        ) : (
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', rowGap: 0.5 }}>
            <RangeField label="min"  value={Number(v.min)}  onChange={n => updateVariation(paramName, { min: n })} />
            <RangeField label="max"  value={Number(v.max)}  onChange={n => updateVariation(paramName, { max: n })} />
            <RangeField label="step" value={Number(v.step)} onChange={n => updateVariation(paramName, { step: n })} />
          </Box>
        )}
      </CardContent>
    </Card>
  );
};

// ── Main panel ────────────────────────────────────────────────────────────────

const SweepConfigPanel: React.FC = () => {
  const {
    parameterSchema,
    geometry,
    sweepConfig,
    updateVariation,
    setVariations,
    updateOperatingPoint,
    updateRippleThreshold,
    initVariationsFromSchema,
  } = useMotorStore();

  useEffect(() => {
    if (parameterSchema.length > 0) initVariationsFromSchema();
  }, [parameterSchema.length]);

  // ── Operating speed / load angle come from the Simulation tab ───────────────
  // One source of truth: pull rpm & γ from the Simulation tab's localStorage into
  // the operating point whenever the Sweep tab opens (it re-mounts on tab switch),
  // so the optimizer runs at exactly the condition set in Simulation.  A storage
  // listener also catches edits made in another browser window.
  const syncOpFromSim = React.useCallback(() => {
    const rpm     = Number(readSim('rpm',     3950));
    const gamma   = Number(readSim('gamma',   0));
    const current = Number(readSim('current', 85));
    updateOperatingPoint(0, { rpm, gamma_deg: gamma, current_a: current });
    updateOperatingPoint(1, { rpm, gamma_deg: gamma, current_a: current });
  }, [updateOperatingPoint]);
  useEffect(() => {
    syncOpFromSim();
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'sim.rpm' || e.key === 'sim.gamma' || e.key === 'sim.current') syncOpFromSim();
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [syncOpFromSim]);

  const opRpm     = sweepConfig.operatingPoints[0]?.rpm ?? 3950;
  const opGamma   = sweepConfig.operatingPoints[0]?.gamma_deg ?? 0;
  const opCurrent = sweepConfig.operatingPoints[0]?.current_a ?? Number(readSim('current', 85));

  const schemaMap = Object.fromEntries(parameterSchema.map(p => [p.name, p]));
  const sweepEntries = Object.entries(sweepConfig.variations).filter(([, v]) => v.mode !== 'fixed');

  // ── Add-variable dropdown: make the tab self-contained (no need to hunt
  //    chart-icons in the Geometry/Simulation tabs).  Adds as an optimize var.
  const DEFAULT_RANGE: Record<string, { min: number; max: number; step: number }> = {
    gamma_deg: { min: 0, max: 40, step: 2 },   // post-calibration: q-axis = 0, MTPA ~+28
  };
  // Default search window for a variable: operating-point specials, else the
  // CURRENT value ±25 % clamped to the schema (int-aware).  Shared by the
  // dropdown, "Add all", and "Apply DOE importance" so they all seed alike.
  const defaultWindow = (name: string): { min: number; max: number; step: number } => {
    const sch = schemaMap[name];
    let def = DEFAULT_RANGE[name];
    if (!def && name === 'current_a') {                  // default ≈ Simulation current ±40 %
      const i = Math.max(10, Math.round(Number(readSim('current', 100))));
      def = { min: Math.round(i * 0.6), max: Math.round(i * 1.4), step: Math.max(5, Math.round(i * 0.1)) };
    }
    const step = def?.step ?? sch?.step ?? 0.1;
    if (def) return { min: def.min, max: def.max, step };
    // Geometry var → window centred on where the design is NOW (not full schema).
    const lo = sch?.min, hi = sch?.max;
    const cur = Number(geometry?.[name] ?? (lo != null && hi != null ? (Number(lo) + Number(hi)) / 2 : 0));
    const dev = Math.abs(cur) * 0.25 || (lo != null && hi != null ? (Number(hi) - Number(lo)) * 0.1 : 1);
    let min = cur - dev, max = cur + dev;
    if (lo != null) min = Math.max(min, Number(lo));
    if (hi != null) max = Math.min(max, Number(hi));
    if (sch?.type === 'int') { min = Math.round(min); max = Math.round(max); if (max <= min) max = min + 1; }
    else { min = Math.round(min * 100) / 100; max = Math.round(max * 100) / 100; }
    return { min, max, step };
  };
  const addVariable = (name: string) => { if (name) updateVariation(name, { mode: 'optimize', ...defaultWindow(name) }); };
  const activeNames   = new Set(sweepEntries.map(([n]) => n));
  // Only params MARKED for optimization (schema.optimizable !== false) — same
  // whitelist the geometry table + the optimizer use — not the whole schema.
  const availableVars = [
    ...parameterSchema.filter(p => p.type !== 'string' && p.optimizable !== false).map(p => p.name),
    'gamma_deg',
    'current_a',
  ].filter(n => !activeNames.has(n));

  // Bulk actions: add every available GEOMETRY var (NOT the operating-point γ /
  // phase current), or clear all selected variables back to fixed.
  const geomAvailable = availableVars.filter(n => n !== 'gamma_deg' && n !== 'current_a');
  const addAll = () => geomAvailable.forEach(addVariable);
  const removeAll = () => {
    const v = { ...sweepConfig.variations };
    for (const k of Object.keys(v)) v[k] = { ...v[k], mode: 'fixed' };
    setVariations(v);
  };

  // ── DOE → variable screening (close the loop the user asked for) ─────────────
  // Instead of reading the DOE importance by eye, apply it in one click: KEEP
  // every geometry var the DOE flagged as a driver of the OBJECTIVE (efficiency,
  // torque/mass) or the CONSTRAINT (ripple) at/above a % threshold, FIX the rest.
  // Raw torque is excluded — current auto-solve holds it ~constant.  Kept vars
  // keep the window you already set (only fresh ones get a default window).  The
  // optimizer then searches only the vital few → faster, more robust CMA-ES.
  const [impPct, setImpPct] = useState<number>(() => {
    try { return Number(JSON.parse(localStorage.getItem('opt.impPct') ?? '5')) || 5; } catch { return 5; }
  });
  const setImpPctLS = (v: number) => { setImpPct(v); try { localStorage.setItem('opt.impPct', JSON.stringify(v)); } catch { /* ignore */ } };
  const [impInfo, setImpInfo] = useState<{ kept: number; fixed: number; n: number } | null>(null);
  const readDoeImp = (): any => { try { return JSON.parse(localStorage.getItem('doe.lastImportance') || 'null'); } catch { return null; } };
  const doeReady = !!readDoeImp()?.ok;
  const applyDoeImportance = () => {
    const imp = readDoeImp();
    if (!imp?.ok || !imp.targets) return;
    const score: Record<string, number> = {};
    for (const t of ['eff', 'td', 'ripple'])
      for (const row of (imp.targets[t]?.ranking || []))
        score[row.var] = Math.max(score[row.var] ?? 0, Number(row.importance) || 0);
    const thr = impPct / 100;
    const geomNames = parameterSchema.filter(p => p.type !== 'string' && p.optimizable !== false)
      .map(p => p.name).filter(n => n !== 'gamma_deg' && n !== 'current_a');
    const next: Record<string, any> = { ...useMotorStore.getState().sweepConfig.variations };
    let kept = 0;
    for (const n of geomNames) {
      if ((score[n] ?? 0) >= thr) {
        kept++;
        const c = next[n];
        next[n] = (c && c.mode !== 'fixed') ? { ...c, mode: 'optimize' }
                                            : { ...(c || {}), mode: 'optimize', ...defaultWindow(n) };
      } else if (next[n]) {
        next[n] = { ...next[n], mode: 'fixed' };
      }
    }
    setVariations(next);
    setImpInfo({ kept, fixed: geomNames.length - kept, n: Number(imp.n) || 0 });
  };

  // Algorithm sub-menu: Optimize (CMA-ES/Gradient) · Sweep study · DOE/Importance.
  // Optimization Variables + Operating Point stay on top (shared); the sub-menu
  // switches only the algorithm panel below.
  const [algoTab, setAlgoTab] = useState<'optimize' | 'sweep' | 'doe'>(() => {
    try { const t = localStorage.getItem('opt.algoTab'); return (t === 'sweep' || t === 'doe') ? t : 'optimize'; }
    catch { return 'optimize'; }
  });
  // Each algorithm keeps its OWN variable set.  On switch: stash the current
  // algo's variables, then load the target algo's (seeded from the current set
  // the first time it's opened, so nothing silently disappears).  The active set
  // lives in sweepConfig.variations (zustand-persisted) → the algo you reload on
  // shows its own set; the others are restored from localStorage on switch.
  const pickAlgo = (t: 'optimize' | 'sweep' | 'doe') => {
    if (t === algoTab) return;
    try {
      const cur = useMotorStore.getState().sweepConfig.variations;
      localStorage.setItem(`opt.vars.${algoTab}`, JSON.stringify(cur));
      const saved = localStorage.getItem(`opt.vars.${t}`);
      setVariations(saved ? JSON.parse(saved) : cur);
    } catch { /* ignore */ }
    setAlgoTab(t);
    try { localStorage.setItem('opt.algoTab', t); } catch { /* ignore */ }
  };

  // The "now: … (from Geometry)" reference value in each card reads the live
  // geometry store, so it updates on its own when a design is saved/applied.
  // The RANGE (min/max/step) is the user's to set and is NEVER auto-changed —
  // even when an applied design's value lands OUTSIDE the window.  Keeping the
  // window fixed lets the user see WHERE the new value went relative to the range
  // they chose (the card flags an out-of-range value in amber); they widen the
  // range themselves if they want the optimizer to explore around it.

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* ── Header ── */}
      <Box sx={{
        px: 3, py: 1.25,
        borderBottom: '1px solid', borderColor: 'divider',
        display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0,
      }}>
        <ShowChartIcon sx={{ fontSize: 16 }} />
        <Typography variant="subtitle2" sx={{ flex: 1 }}>Optimize</Typography>
        <Typography variant="caption" color="text.disabled">
          {sweepEntries.length} variable{sweepEntries.length === 1 ? '' : 's'}
        </Typography>
      </Box>

      {/* ── Body: config (two columns) + the optimizer ── */}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 3 }}>
        {/* ── Algorithm sub-menu — at the very top; switches the panel below ── */}
        <ToggleButtonGroup value={algoTab} exclusive size="small" fullWidth
          onChange={(_, v) => v && pickAlgo(v)} sx={{ mb: 2 }}>
          <ToggleButton value="optimize" sx={{ textTransform: 'none', fontSize: 12 }}>Optimize</ToggleButton>
          <ToggleButton value="sweep" sx={{ textTransform: 'none', fontSize: 12 }}>Sweep study</ToggleButton>
          <ToggleButton value="doe" sx={{ textTransform: 'none', fontSize: 12 }}>DOE / Importance</ToggleButton>
        </ToggleButtonGroup>
        {/* One-click card FIRST — the user opens Optimize to run it, not to
            scroll past 18 variable cards looking for it ("не вижу графика").
            The variables block below belongs to the manual flow. */}
        {algoTab === 'optimize' && (
          <Box sx={{ mb: 2 }}>
            <AutoOptimizePanel />
            {/* The classic optimizer charts (metrics table, convergence, the
                objective cloud with the baseline line) — user request: back on
                the tab, without the manual controls. Same store as the auto run. */}
            <DescentPanel chartsOnly />
          </Box>
        )}

       {/* The manual flow (variable cards + operating-point column) is HIDDEN
           on the Optimize tab for now — user decision 2026-08-01: debug ONLY
           the automatic optimizer; every knob it needs is assembled server-side.
           The cards still serve the Sweep tab, which grids them. Set
           localStorage 'opt.showManual' = "1" to bring the manual flow back
           without a rebuild (escape hatch for debugging the debugger). */}
       {(algoTab === 'sweep'
         || (algoTab === 'optimize' && (() => {
              try { return localStorage.getItem('opt.showManual') === '1'; }
              catch { return false; }
            })())) && (
        <Box sx={{ display: 'flex', gap: 3, alignItems: 'flex-start' }}>

        {/* Left: optimization variable cards */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1.5 }}>
            <SectionLabel>Optimization Variables</SectionLabel>
            <HelpTip title="Add a parameter (geometry, load angle γ, or phase current) and set its range (min · max) and step. Each algorithm keeps its own set — switch the menu above and these cards follow it (Optimize searches them, Sweep grids them). DOE has its own screening inputs." />
          </Box>

          <FormControl size="small" fullWidth sx={{ mb: 2 }}>
            <InputLabel sx={{ fontSize: 12 }}>+ Add optimization variable</InputLabel>
            <Select
              label="+ Add optimization variable"
              value=""
              onChange={(e) => addVariable(e.target.value as string)}
              MenuProps={{ PaperProps: { sx: { maxHeight: 360 } } }}
              sx={{ fontSize: 12 }}
            >
              {availableVars.length === 0 && (
                <MenuItem value="" disabled sx={{ fontSize: 12 }}>All variables added</MenuItem>
              )}
              {availableVars.map((name) => {
                const lbl  = schemaMap[name]?.label ?? SPECIAL_VARS[name]?.label ?? name;
                const unit = schemaMap[name]?.unit  ?? SPECIAL_VARS[name]?.unit;
                return (
                  <MenuItem key={name} value={name} sx={{ fontSize: 12 }}>
                    {lbl}{unit ? ` (${unit})` : ''}
                  </MenuItem>
                );
              })}
            </Select>
          </FormControl>

          {/* Bulk add/remove — add all geometry vars (not γ / current), or clear all */}
          <Box sx={{ display: 'flex', gap: 1, mb: 1.5, mt: -1 }}>
            <Button size="small" variant="outlined" onClick={addAll} disabled={geomAvailable.length === 0}
              sx={{ textTransform: 'none', fontSize: 11 }}>
              + Add all geometry ({geomAvailable.length})
            </Button>
            <Button size="small" variant="text" color="inherit" onClick={removeAll} disabled={sweepEntries.length === 0}
              sx={{ textTransform: 'none', fontSize: 11 }}>
              Remove all
            </Button>
          </Box>

          {/* DOE → screening: keep the vars DOE flagged as objective / constraint drivers, fix the rest */}
          {algoTab === 'optimize' && (
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 1.5, mt: -0.5, flexWrap: 'wrap' }}>
              <Tooltip title={doeReady
                ? 'Keep variables the DOE flagged as drivers of efficiency / torque-per-mass / ripple at or above the threshold; fix the rest. Kept vars keep their current window.'
                : 'Run DOE / Importance first — no screening result yet'}>
                <span>
                  <Button size="small" variant="outlined" color="secondary" onClick={applyDoeImportance} disabled={!doeReady}
                    sx={{ textTransform: 'none', fontSize: 11 }}>
                    Apply DOE importance
                  </Button>
                </span>
              </Tooltip>
              <TextField label="keep ≥ %" size="small" type="text" value={impPct} disabled={!doeReady}
                onChange={e => { const n = parseFloat(e.target.value); if (Number.isFinite(n)) setImpPctLS(n); }}
                inputProps={{ inputMode: 'decimal', style: { fontSize: 11, padding: '3px 6px', width: 40 } }}
                sx={{ width: 92 }} />
              {impInfo && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  kept {impInfo.kept} · fixed {impInfo.fixed} (DOE n={impInfo.n})
                </Typography>
              )}
            </Box>
          )}

          {sweepEntries.length === 0 ? (
            <Box sx={{ textAlign: 'center', py: 5, color: 'text.disabled' }}>
              <TuneIcon sx={{ fontSize: 40, mb: 1, opacity: 0.2, display: 'block', mx: 'auto' }} />
              <Typography variant="caption">
                No parameters selected for optimization.
              </Typography>
            </Box>
          ) : (
            <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', columnGap: 1.5 }}>
              {sweepEntries.map(([name]) => (
                <SweepVarCard
                  key={name}
                  paramName={name}
                  label={schemaMap[name]?.label ?? SPECIAL_VARS[name]?.label ?? name}
                  unit={schemaMap[name]?.unit ?? SPECIAL_VARS[name]?.unit}
                  optimize={algoTab === 'optimize'}
                />
              ))}
            </Box>
          )}
        </Box>

        <Divider orientation="vertical" flexItem />

        {/* Right: operating point + ripple (narrow — frees width for 2-col variables) */}
        <Box sx={{ flexShrink: 0, width: 280 }}>

          <SectionLabel sx={{ mb: 0.5 }}>Operating Point</SectionLabel>
          <Card variant="outlined" sx={{ mb: 2.5, mt: 0.5, bgcolor: 'var(--line-soft)' }}>
            <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
                <TextField
                  label="Current"
                  size="small"
                  type="number"
                  value={opCurrent}
                  disabled
                  InputProps={{ endAdornment: (
                    <InputAdornment position="end" sx={{ gap: 0.5 }}>
                      A<HelpTip title="Phase current (RMS) — taken from the Simulation tab. The optimizer runs at THIS fixed current and varies only the selected geometry variables." />
                    </InputAdornment>
                  ) }}
                />
                <Box sx={{ display: 'flex', gap: 1 }}>
                  <TextField
                    label="Speed" size="small" type="number" value={opRpm} disabled
                    InputProps={{ endAdornment: <InputAdornment position="end">RPM</InputAdornment> }}
                    sx={{ flex: 1 }}
                  />
                  <TextField
                    label="Load angle γ" size="small" type="number" value={opGamma} disabled
                    InputProps={{ endAdornment: <InputAdornment position="end">°</InputAdornment> }}
                    sx={{ flex: 1 }}
                  />
                </Box>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Typography variant="caption" color="text.disabled">
                    Current, speed &amp; γ — all from the Simulation tab
                  </Typography>
                  <Tooltip title="Re-read Speed & γ from the Simulation tab" placement="top">
                    <Button size="small" onClick={syncOpFromSim} startIcon={<RefreshIcon sx={{ fontSize: 14 }} />}
                      sx={{ fontSize: 10, py: 0, textTransform: 'none', minWidth: 0 }}>
                      Sync
                    </Button>
                  </Tooltip>
                </Box>
              </Box>
            </CardContent>
          </Card>

          <SectionLabel sx={{ mb: 0.5 }}>Torque Ripple</SectionLabel>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <TextField label="limit ≤ %" size="small" type="number"
              value={+((sweepConfig.rippleThreshold ?? 0) * 100).toFixed(2)}
              onChange={e => updateRippleThreshold(Math.max(0, (+e.target.value || 0)) / 100)}
              inputProps={{ min: 0, step: 0.5, style: { fontSize: 12, width: 64 } }}
              InputLabelProps={{ sx: { fontSize: 11 } }} />
            <HelpTip title="Ripple limit for the optimizer. Enforced in the cost only when 'ripple λ' > 0 in the optimizer toolbar below (cost += λ·max(0, ripple% − limit%)/100). With λ = 0 the limit is inactive — trim points visually with the 'ripple ≤ X%' slider under the results chart." />
          </Box>
        </Box>
       </Box>
       )}

        {/* Optimize — the ONE-NUMBER card first: max ripple → Run.  The full
            manual optimizer is not removed, only demoted: it lives under the
            collapsed "Advanced" section, because every knob it exposes now has a
            standing convention behind it and re-deciding them per run is how
            runs stop being comparable. */}
        {/* Same gate as the variables block above: manual optimizer hidden
            while the automatic one is being debugged (opt.showManual = "1"
            to restore). DescentPanel stays in the bundle untouched. */}
        {algoTab === 'optimize' && (() => {
          try { return localStorage.getItem('opt.showManual') === '1'; }
          catch { return false; }
        })() && (
          <Accordion disableGutters elevation={0}
            sx={{ bgcolor: 'transparent', '&:before': { display: 'none' },
                  border: '1px solid var(--border-1, rgba(255,255,255,0.12))', borderRadius: 1 }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ fontSize: 18 }} />}
              sx={{ minHeight: 36, '& .MuiAccordionSummary-content': { my: 0.5 } }}>
              <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: 0.4 }}>
                ADVANCED — manual optimizer (algorithm, weights, ranges, box-walking)
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ pt: 0 }}>
              <DescentPanel />
            </AccordionDetails>
          </Accordion>
        )}
        {/* Sweep study = current × γ grid → η-vs-N·m/kg performance map */}
        {algoTab === 'sweep' && <SweepStudyPanel />}
        {/* DOE = Latin-Hypercube screening → unbiased variable importance */}
        {algoTab === 'doe' && <DOEPanel />}
      </Box>
    </Box>
  );
};

export default SweepConfigPanel;
