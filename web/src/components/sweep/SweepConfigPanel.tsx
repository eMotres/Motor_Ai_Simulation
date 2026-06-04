import React, { useEffect } from 'react';
import {
  Box,
  Typography,
  Button,
  IconButton,
  TextField,
  InputAdornment,
  Chip,
  Divider,
  Card,
  CardContent,
  Slider,
  ToggleButton,
  ToggleButtonGroup,
  CircularProgress,
  Tooltip,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import CloseIcon     from '@mui/icons-material/Close';
import TuneIcon      from '@mui/icons-material/Tune';
import { useMotorStore } from '../../stores/motorStore';
import type { VariationMode } from '../../types/motor';
import ParetoResults from './ParetoResults';

// ── Card for one sweep/optimize parameter ─────────────────────────────────────

interface SweepVarCardProps {
  paramName: string;
  label: string;
  unit?: string;
}

const SweepVarCard: React.FC<SweepVarCardProps> = ({ paramName, label, unit }) => {
  const { sweepConfig, updateVariation } = useMotorStore();
  const v = sweepConfig.variations[paramName];
  if (!v || v.mode === 'fixed') return null;

  return (
    <Card variant="outlined" sx={{ mb: 1.5, bgcolor: 'rgba(255,255,255,0.02)' }}>
      <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
        {/* Header */}
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, flex: 1 }}>
            {label}
            {unit && (
              <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
                ({unit})
              </Typography>
            )}
          </Typography>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={v.mode}
            onChange={(_, m) => m && updateVariation(paramName, { mode: m as VariationMode })}
            sx={{ height: 22, mr: 1 }}
          >
            <ToggleButton value="sweep"    color="primary" sx={{ px: 1, py: 0, fontSize: 10, minWidth: 50 }}>Sweep</ToggleButton>
            <ToggleButton value="optimize" color="success" sx={{ px: 1, py: 0, fontSize: 10, minWidth: 42 }}>Optimize</ToggleButton>
          </ToggleButtonGroup>
          <IconButton
            size="small"
            onClick={() => updateVariation(paramName, { mode: 'fixed' })}
            sx={{ p: 0.25 }}
          >
            <CloseIcon sx={{ fontSize: 14 }} />
          </IconButton>
        </Box>

        {/* Range inputs */}
        <Box sx={{ display: 'flex', gap: 1 }}>
          <TextField
            label="Min"
            size="small"
            type="number"
            value={v.min}
            onChange={e => updateVariation(paramName, { min: parseFloat(e.target.value) })}
            inputProps={{ style: { fontSize: 12, padding: '4px 8px' } }}
            sx={{ flex: 1 }}
          />
          <TextField
            label="Max"
            size="small"
            type="number"
            value={v.max}
            onChange={e => updateVariation(paramName, { max: parseFloat(e.target.value) })}
            inputProps={{ style: { fontSize: 12, padding: '4px 8px' } }}
            sx={{ flex: 1 }}
          />
          {v.mode === 'sweep' && (
            <TextField
              label="Step"
              size="small"
              type="number"
              value={v.step}
              onChange={e => updateVariation(paramName, { step: parseFloat(e.target.value) })}
              inputProps={{ min: 0, style: { fontSize: 12, padding: '4px 8px' } }}
              sx={{ flex: 1 }}
            />
          )}
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
    updateOperatingPoint,
    updateRippleThreshold,
    connectedToApi,
    initVariationsFromSchema,
    runOptimization,
    optimizationResult,
    optimizationRunning,
    optimizationProgress,
    optimizationError,
  } = useMotorStore();

  // FEM scan settings: frames per FULL electrical period (losses need a whole
  // period; 18 = 3 samples per 6·k ripple cycle) and the geometry cap.
  const [scanSteps, setScanSteps] = React.useState(18);
  const [maxGeom,   setMaxGeom]   = React.useState(24);

  useEffect(() => {
    if (parameterSchema.length > 0) initVariationsFromSchema();
  }, [parameterSchema.length]);

  const schemaMap = Object.fromEntries(parameterSchema.map(p => [p.name, p]));

  const sweepEntries   = Object.entries(sweepConfig.variations).filter(([, v]) => v.mode !== 'fixed');
  const sweepCount     = sweepEntries.filter(([, v]) => v.mode === 'sweep').length;
  const optimizeCount  = sweepEntries.filter(([, v]) => v.mode === 'optimize').length;

  // Grid size: ∏ (#values per sweep var).  floor(+ε) so an exact 2.0 doesn't
  // round up to 3 (the old ceil counted 81 combos as 144).
  const estimateRuns = () =>
    Object.values(sweepConfig.variations)
      .filter(v => v.mode === 'sweep')
      .reduce((acc, v) => {
        const steps = Math.max(1, Math.floor((v.max - v.min) / Math.max(v.step || 1, 1e-9) + 1e-9) + 1);
        return acc * steps;
      }, 1);

  // Default the geometry cap to the FULL design count (sweep grid × optimize
  // spread) so the scan computes exactly the points the Sweep Variables define —
  // no subsampling.  The user can still lower the field for a quick subset.
  useEffect(() => {
    const full = estimateRuns() * Math.pow(4, optimizeCount);
    setMaxGeom(Math.max(1, Math.min(400, full)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(sweepConfig.variations)]);

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* ── Header ── */}
      <Box sx={{
        px: 3, py: 1.25,
        borderBottom: '1px solid', borderColor: 'divider',
        display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0,
      }}>
        <ShowChartIcon sx={{ fontSize: 16 }} />
        <Typography variant="subtitle2" sx={{ flex: 1 }}>Sweep / Optimize</Typography>
        <Chip size="small" label={`${sweepCount} sweep`}  color="primary" variant={sweepCount   > 0 ? 'filled' : 'outlined'} sx={{ height: 18, fontSize: 10 }} />
        <Chip size="small" label={`${optimizeCount} opt`} color="success" variant={optimizeCount > 0 ? 'filled' : 'outlined'} sx={{ height: 18, fontSize: 10 }} />
        {sweepCount > 0 && (
          <Chip size="small" label={`~${estimateRuns()} variants`} variant="outlined" sx={{ height: 18, fontSize: 10 }} />
        )}
        <Tooltip title="FEM frames per FULL electrical period. Losses (iron/magnet eddy via dB/dt) need a whole period to be correct. 18 = 3 samples per 6·k ripple cycle; 36 resolves the ripple finely (slower). Snapped to a divisor of 72 slip nodes." placement="top">
          <TextField label="steps/T" type="number" size="small" value={scanSteps}
            onChange={e => setScanSteps(Math.max(6, Math.min(72, Math.round(+e.target.value) || 18)))}
            inputProps={{ min: 6, max: 72, style: { fontSize: 11, padding: '3px 6px', width: 38 } }}
            InputLabelProps={{ sx: { fontSize: 10 } }} sx={{ ml: 1 }} />
        </Tooltip>
        <Tooltip title="Cap on the number of geometries evaluated (each is a real FEM transient × 2 currents). Sweep grids and optimize spreads are subsampled to this." placement="top">
          <TextField label="max geom" type="number" size="small" value={maxGeom}
            onChange={e => setMaxGeom(Math.max(1, Math.min(400, Math.round(+e.target.value) || 24)))}
            inputProps={{ min: 1, max: 400, style: { fontSize: 11, padding: '3px 6px', width: 38 } }}
            InputLabelProps={{ sx: { fontSize: 10 } }} />
        </Tooltip>
        <Button
          variant="contained"
          startIcon={optimizationRunning
            ? <CircularProgress size={14} color="inherit" />
            : <PlayArrowIcon />}
          disabled={sweepEntries.length === 0 || !connectedToApi || optimizationRunning}
          size="small"
          sx={{ flexShrink: 0, ml: 1 }}
          onClick={() => runOptimization(scanSteps, maxGeom)}
        >
          {optimizationRunning
            ? `Scanning ${optimizationProgress?.done ?? 0}/${optimizationProgress?.total ?? 0}…`
            : 'Run FEM scan'}
        </Button>
      </Box>

      {/* ── Body: config (two columns) + results ── */}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 3 }}>
       <Box sx={{ display: 'flex', gap: 4 }}>

        {/* Left: sweep variable cards */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="overline" color="text.secondary" sx={{ fontSize: 10, letterSpacing: 1, display: 'block', mb: 1 }}>
            Sweep Variables
          </Typography>
          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mb: 2 }}>
            Select parameters in the <strong>Geometry</strong> tab using the chart icon.
          </Typography>

          {sweepEntries.length === 0 ? (
            <Box sx={{ textAlign: 'center', py: 5, color: 'text.disabled' }}>
              <TuneIcon sx={{ fontSize: 40, mb: 1, opacity: 0.2, display: 'block', mx: 'auto' }} />
              <Typography variant="caption">
                No parameters selected for sweep or optimization.
              </Typography>
            </Box>
          ) : (
            sweepEntries.map(([name]) => (
              <SweepVarCard
                key={name}
                paramName={name}
                label={schemaMap[name]?.label ?? name}
                unit={schemaMap[name]?.unit}
              />
            ))
          )}
        </Box>

        <Divider orientation="vertical" flexItem />

        {/* Right: operating points + ripple */}
        <Box sx={{ flex: 1, minWidth: 0 }}>

          <Typography variant="overline" color="text.secondary" sx={{ fontSize: 10, letterSpacing: 1, display: 'block', mb: 0.5 }}>
            Operating Points
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
            Each geometry variant is evaluated at both points → segment in Pareto space (Torque/mass vs Efficiency)
          </Typography>

          <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
            {([0, 1] as const).map(i => (
              <Card key={i} variant="outlined" sx={{ flex: 1, bgcolor: 'rgba(255,255,255,0.02)' }}>
                <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
                  <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700, display: 'block', mb: 1 }}>
                    Point {i + 1}
                  </Typography>
                  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                    <TextField
                      label="Current"
                      size="small"
                      type="number"
                      value={sweepConfig.operatingPoints[i].current_a}
                      onChange={e => updateOperatingPoint(i, { current_a: parseFloat(e.target.value) })}
                      InputProps={{ endAdornment: <InputAdornment position="end">A</InputAdornment> }}
                      inputProps={{ min: 0, step: 1 }}
                    />
                    <TextField
                      label="Speed"
                      size="small"
                      type="number"
                      value={sweepConfig.operatingPoints[i].rpm}
                      onChange={e => updateOperatingPoint(i, { rpm: parseFloat(e.target.value) })}
                      InputProps={{ endAdornment: <InputAdornment position="end">RPM</InputAdornment> }}
                      inputProps={{ min: 0, step: 100 }}
                    />
                  </Box>
                </CardContent>
              </Card>
            ))}
          </Box>

          <Divider sx={{ mb: 2.5 }} />

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
              min={1} max={50} step={0.5}
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

        {/* ── Results: Pareto front ── */}
        {optimizationError && (
          <Typography color="error" variant="caption" sx={{ display: 'block', mt: 2 }}>
            Optimization error: {optimizationError}
          </Typography>
        )}
        {optimizationResult && <ParetoResults result={optimizationResult} />}
      </Box>
    </Box>
  );
};

export default SweepConfigPanel;
