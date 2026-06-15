import React, { useEffect } from 'react';
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
  Slider,
  Tooltip,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import CloseIcon     from '@mui/icons-material/Close';
import TuneIcon      from '@mui/icons-material/Tune';
import RefreshIcon   from '@mui/icons-material/Refresh';
import { useMotorStore } from '../../stores/motorStore';
import DescentPanel from './DescentPanel';

// Non-geometry variables (selected outside the Geometry tab) need their own
// display label/unit since they are absent from the geometry parameter schema.
const SPECIAL_VARS: Record<string, { label: string; unit: string }> = {
  gamma_deg: { label: 'Load angle γ', unit: '°' },
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

// ── Card for one optimization variable (current value ± symmetric deviation) ───

interface SweepVarCardProps {
  paramName: string;
  label: string;
  unit?: string;
}

const SweepVarCard: React.FC<SweepVarCardProps> = ({ paramName, label, unit }) => {
  const { sweepConfig, updateVariation, geometry } = useMotorStore();
  const v = sweepConfig.variations[paramName];
  if (!v || v.mode === 'fixed') return null;
  // The search window is the CURRENT geometry value ± a symmetric deviation.
  // The value itself is owned by the Geometry tab (shown read-only here).
  const cur = Number((geometry as Record<string, any>)[paramName] ?? ((Number(v.min) + Number(v.max)) / 2));
  const delta = +Math.max(0, (Number(v.max) - Number(v.min)) / 2).toFixed(4);

  return (
    <Card variant="outlined" sx={{ mb: 1.5, bgcolor: 'rgba(255,255,255,0.02)' }}>
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

        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <TextField
            label="value (current)" size="small" type="number" value={cur} disabled
            inputProps={{ style: { fontSize: 12, padding: '4px 8px' } }}
            sx={{ flex: 1 }}
          />
          <Typography variant="body2" sx={{ color: 'text.secondary', fontWeight: 600 }}>±</Typography>
          <TextField
            label="deviation" size="small" type="number" value={delta}
            onChange={e => {
              const d = Math.max(0, parseFloat(e.target.value) || 0);
              updateVariation(paramName, { min: cur - d, max: cur + d });
            }}
            inputProps={{ min: 0, style: { fontSize: 12, padding: '4px 8px' } }}
            sx={{ flex: 1 }}
          />
          <Typography variant="caption" sx={{ color: 'text.secondary', whiteSpace: 'nowrap', minWidth: 88, textAlign: 'right' }}>
            [{(cur - delta).toFixed(2)} … {(cur + delta).toFixed(2)}]
          </Typography>
        </Box>
      </CardContent>
    </Card>
  );
};

// ── Main panel ────────────────────────────────────────────────────────────────

const SweepConfigPanel: React.FC = () => {
  const {
    parameterSchema,
    sweepConfig,
    updateVariation,
    updateOperatingPoint,
    updateRippleThreshold,
    updateSweepConstraints,
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
    const rpm   = Number(readSim('rpm',   3950));
    const gamma = Number(readSim('gamma', 0));
    updateOperatingPoint(0, { rpm, gamma_deg: gamma });
    updateOperatingPoint(1, { rpm, gamma_deg: gamma });
  }, [updateOperatingPoint]);
  useEffect(() => {
    syncOpFromSim();
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'sim.rpm' || e.key === 'sim.gamma') syncOpFromSim();
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [syncOpFromSim]);

  const ratedTorque = sweepConfig.ratedTorqueNm ?? 30.5;
  const opRpm   = sweepConfig.operatingPoints[0]?.rpm ?? 3950;
  const opGamma = sweepConfig.operatingPoints[0]?.gamma_deg ?? 0;

  const schemaMap = Object.fromEntries(parameterSchema.map(p => [p.name, p]));
  const sweepEntries = Object.entries(sweepConfig.variations).filter(([, v]) => v.mode !== 'fixed');

  // ── Add-variable dropdown: make the tab self-contained (no need to hunt
  //    chart-icons in the Geometry/Simulation tabs).  Adds as an optimize var.
  const DEFAULT_RANGE: Record<string, { min: number; max: number; step: number }> = {
    gamma_deg: { min: -20, max: 0, step: 5 },
  };
  const addVariable = (name: string) => {
    if (!name) return;
    const sch = schemaMap[name];
    const def = DEFAULT_RANGE[name];
    const min  = def?.min  ?? sch?.min  ?? 0;
    const max  = def?.max  ?? sch?.max  ?? 1;
    const step = def?.step ?? sch?.step ?? 0.1;
    updateVariation(name, { mode: 'optimize', min, max, step });
  };
  const activeNames   = new Set(sweepEntries.map(([n]) => n));
  const availableVars = [
    ...parameterSchema.filter(p => p.type !== 'string').map(p => p.name),
    'gamma_deg',
  ].filter(n => !activeNames.has(n));

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
       <Box sx={{ display: 'flex', gap: 4 }}>

        {/* Left: optimization variable cards */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="overline" color="text.secondary" sx={{ fontSize: 10, letterSpacing: 1, display: 'block', mb: 1 }}>
            Optimization Variables
          </Typography>
          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mb: 1.5 }}>
            Add a parameter below (or via the chart icon in <strong>Geometry</strong> / the
            <strong> γ</strong> checkbox in <strong>Simulation</strong>). Each is optimized within
            its current value ± deviation.
          </Typography>

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

          {sweepEntries.length === 0 ? (
            <Box sx={{ textAlign: 'center', py: 5, color: 'text.disabled' }}>
              <TuneIcon sx={{ fontSize: 40, mb: 1, opacity: 0.2, display: 'block', mx: 'auto' }} />
              <Typography variant="caption">
                No parameters selected for optimization.
              </Typography>
            </Box>
          ) : (
            sweepEntries.map(([name]) => (
              <SweepVarCard
                key={name}
                paramName={name}
                label={schemaMap[name]?.label ?? SPECIAL_VARS[name]?.label ?? name}
                unit={schemaMap[name]?.unit ?? SPECIAL_VARS[name]?.unit}
              />
            ))
          )}
        </Box>

        <Divider orientation="vertical" flexItem />

        {/* Right: operating point + ripple */}
        <Box sx={{ flex: 1, minWidth: 0 }}>

          <Typography variant="overline" color="text.secondary" sx={{ fontSize: 10, letterSpacing: 1, display: 'block', mb: 0.5 }}>
            Operating Point
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
            The optimizer targets a <strong>torque</strong> — the phase current is solved automatically
            for each design. Speed &amp; load angle are taken from the <strong>Simulation</strong> tab.
          </Typography>

          <Card variant="outlined" sx={{ mb: 2.5, bgcolor: 'rgba(255,255,255,0.02)' }}>
            <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
                <TextField
                  label="Target torque"
                  size="small"
                  type="number"
                  value={ratedTorque}
                  onChange={e => updateSweepConstraints({ ratedTorqueNm: Math.max(0, parseFloat(e.target.value) || 0) })}
                  InputProps={{ endAdornment: <InputAdornment position="end">N·m</InputAdornment> }}
                  inputProps={{ min: 0, step: 0.5 }}
                  helperText="Each design is solved at the current that delivers this torque"
                  FormHelperTextProps={{ sx: { fontSize: 10, mx: 0 } }}
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
                    Speed &amp; γ from the Simulation tab · current auto-solved
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

          <Typography variant="overline" color="text.secondary" sx={{ fontSize: 10, letterSpacing: 1, display: 'block', mb: 0.5 }}>
            Torque Ripple Constraint
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
            Max allowed (T_max − T_min) / T_mean per electrical cycle
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Slider
              value={sweepConfig.rippleThreshold * 100}
              onChange={(_, v) => updateRippleThreshold((v as number) / 100)}
              min={0} max={10} step={0.5}
              sx={{ flex: 1 }}
            />
            <Typography variant="body2" sx={{ minWidth: 42, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
              {(sweepConfig.rippleThreshold * 100).toFixed(1)}%
            </Typography>
          </Box>
          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1 }}>
            Ripple is the real FEM value (coarse at a low step count). If nothing
            passes the gate, the front falls back to all feasible designs.
          </Typography>
        </Box>
       </Box>

        {/* ── Torque-driven optimizer (gradient / CMA-ES + box-walking) ── */}
        <DescentPanel />
      </Box>
    </Box>
  );
};

export default SweepConfigPanel;
