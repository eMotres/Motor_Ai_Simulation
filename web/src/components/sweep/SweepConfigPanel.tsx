import React, { useEffect, useState, useCallback } from 'react';
import {
  Box,
  Typography,
  Button,
  IconButton,
  TextField,
  InputAdornment,
  Tooltip,
  Chip,
  Divider,
  Card,
  CardContent,
  Slider,
  Collapse,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material';
import PlayArrowIcon    from '@mui/icons-material/PlayArrow';
import ShowChartIcon    from '@mui/icons-material/ShowChart';
import CloseIcon        from '@mui/icons-material/Close';
import AddIcon          from '@mui/icons-material/Add';
import ExpandMoreIcon   from '@mui/icons-material/ExpandMore';
import ExpandLessIcon   from '@mui/icons-material/ExpandLess';
import TuneIcon         from '@mui/icons-material/Tune';
import { useMotorStore } from '../../stores/motorStore';
import type { VariationMode, ParameterSchema } from '../../types/motor';

// ─── Sweep variable card (right panel) ───────────────────────────────────────

interface SweepCardProps {
  paramName: string;
  label: string;
  unit?: string;
  schema?: ParameterSchema;
}

const SweepCard: React.FC<SweepCardProps> = ({ paramName, label, unit, schema }) => {
  const { sweepConfig, updateVariation } = useMotorStore();
  const v = sweepConfig.variations[paramName];
  if (!v || v.mode === 'fixed') return null;

  return (
    <Card variant="outlined" sx={{ mb: 1, bgcolor: 'rgba(255,255,255,0.02)' }}>
      <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
        {/* Header row */}
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, flex: 1 }}>
            {label}
            {unit && <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>({unit})</Typography>}
          </Typography>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={v.mode}
            onChange={(_, m) => m && updateVariation(paramName, { mode: m as VariationMode })}
            sx={{ height: 22, mr: 1 }}
          >
            <ToggleButton value="sweep"    color="primary" sx={{ px: 1, py: 0, fontSize: 10, minWidth: 46 }}>Sweep</ToggleButton>
            <ToggleButton value="optimize" color="success" sx={{ px: 1, py: 0, fontSize: 10, minWidth: 38 }}>Opt</ToggleButton>
          </ToggleButtonGroup>
          <IconButton size="small" onClick={() => updateVariation(paramName, { mode: 'fixed' })} sx={{ p: 0.25 }}>
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
            inputProps={{ step: schema?.step, style: { fontSize: 12, padding: '4px 8px' } }}
            sx={{ flex: 1 }}
          />
          <TextField
            label="Max"
            size="small"
            type="number"
            value={v.max}
            onChange={e => updateVariation(paramName, { max: parseFloat(e.target.value) })}
            inputProps={{ step: schema?.step, style: { fontSize: 12, padding: '4px 8px' } }}
            sx={{ flex: 1 }}
          />
          {v.mode === 'sweep' && (
            <TextField
              label="Step"
              size="small"
              type="number"
              value={v.step}
              onChange={e => updateVariation(paramName, { step: parseFloat(e.target.value) })}
              inputProps={{ step: schema?.step, min: 0, style: { fontSize: 12, padding: '4px 8px' } }}
              sx={{ flex: 1 }}
            />
          )}
        </Box>
      </CardContent>
    </Card>
  );
};

// ─── Parameter selector row (left panel) ─────────────────────────────────────

interface ParamSelectorRowProps {
  param: ParameterSchema;
}

const ParamSelectorRow: React.FC<ParamSelectorRowProps> = ({ param }) => {
  const { sweepConfig, updateVariation, geometry } = useMotorStore();
  const variation = sweepConfig.variations[param.name];
  const isActive  = variation?.mode !== 'fixed' && variation?.mode !== undefined;
  const currentVal = geometry[param.name];

  const toggle = () => {
    if (isActive) {
      updateVariation(param.name, { mode: 'fixed' });
    } else {
      const cur = Number(currentVal ?? 0);
      updateVariation(param.name, {
        mode: 'sweep',
        min: variation?.min  ?? (param.min  ?? Math.max(0, cur * 0.5)),
        max: variation?.max  ?? (param.max  ?? cur * 1.5),
        step: variation?.step ?? (param.step ?? Math.max(0.01, Math.abs(cur) * 0.1)),
      });
    }
  };

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        px: 1.5,
        py: 0.5,
        borderRadius: 1,
        cursor: 'pointer',
        bgcolor: isActive ? 'rgba(59,130,246,0.08)' : 'transparent',
        borderLeft: isActive ? '2px solid #3b82f6' : '2px solid transparent',
        '&:hover': { bgcolor: isActive ? 'rgba(59,130,246,0.12)' : 'rgba(255,255,255,0.04)' },
      }}
      onClick={toggle}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap sx={{ lineHeight: 1.3 }}>
          {param.label}
          {param.unit && (
            <Typography component="span" variant="caption" color="text.disabled" sx={{ ml: 0.5 }}>
              ({param.unit})
            </Typography>
          )}
        </Typography>
        {currentVal !== undefined && (
          <Typography variant="caption" color="text.disabled" sx={{ fontSize: 10 }}>
            current: {typeof currentVal === 'number' ? currentVal.toFixed(param.step && param.step < 0.1 ? 3 : param.step && param.step < 1 ? 2 : 1) : currentVal}
          </Typography>
        )}
      </Box>
      <Tooltip title={isActive ? 'Remove from sweep' : 'Add to sweep'}>
        <IconButton
          size="small"
          color={isActive ? 'primary' : 'default'}
          onClick={e => { e.stopPropagation(); toggle(); }}
          sx={{ p: 0.25, opacity: isActive ? 1 : 0.4, '&:hover': { opacity: 1 } }}
        >
          {isActive ? <CloseIcon sx={{ fontSize: 14 }} /> : <ShowChartIcon sx={{ fontSize: 14 }} />}
        </IconButton>
      </Tooltip>
    </Box>
  );
};

// ─── Main panel ───────────────────────────────────────────────────────────────

const SweepConfigPanel: React.FC = () => {
  const {
    parameterSchema,
    parameterGroups,
    sweepConfig,
    updateOperatingPoint,
    updateRippleThreshold,
    connectedToApi,
    initVariationsFromSchema,
  } = useMotorStore();

  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (parameterSchema.length > 0) initVariationsFromSchema();
  }, [parameterSchema.length]);

  const toggleGroup = (id: string) =>
    setCollapsedGroups(prev => ({ ...prev, [id]: !prev[id] }));

  const sweepEntries   = Object.entries(sweepConfig.variations).filter(([, v]) => v.mode !== 'fixed');
  const sweepCount     = sweepEntries.filter(([, v]) => v.mode === 'sweep').length;
  const optimizeCount  = sweepEntries.filter(([, v]) => v.mode === 'optimize').length;

  const schemaMap = Object.fromEntries(parameterSchema.map(p => [p.name, p]));

  const estimateRuns = () =>
    Object.values(sweepConfig.variations)
      .filter(v => v.mode === 'sweep')
      .reduce((acc, v) => {
        const steps = Math.max(1, Math.ceil((v.max - v.min) / Math.max(v.step || 1, 1e-9)) + 1);
        return acc * steps;
      }, 1);

  const groups = parameterGroups
    .map(g => ({ ...g, params: parameterSchema.filter(p => p.group === g.id && p.type !== 'string') }))
    .filter(g => g.params.length > 0);

  return (
    <Box sx={{ height: '100%', display: 'flex', overflow: 'hidden', pt: '40px' /* MaterialControls height */ }}>

      {/* ── LEFT: geometry parameter selector ─────────────────────────────── */}
      <Box sx={{
        width: 300,
        flexShrink: 0,
        borderRight: '1px solid',
        borderColor: 'divider',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <Box sx={{ px: 2, py: 1.25, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Typography variant="subtitle2" sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <TuneIcon sx={{ fontSize: 16 }} /> Geometry Parameters
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Click a parameter to add it to the sweep
          </Typography>
        </Box>

        {/* Grouped parameter list */}
        <Box sx={{ flex: 1, overflowY: 'auto', py: 0.5 }}>
          {groups.map(group => (
            <Box key={group.id}>
              <Box
                sx={{ display: 'flex', alignItems: 'center', px: 1.5, py: 0.5, cursor: 'pointer', '&:hover': { bgcolor: 'action.hover' } }}
                onClick={() => toggleGroup(group.id)}
              >
                <Typography variant="caption" color="primary" sx={{ fontWeight: 700, flex: 1, textTransform: 'uppercase', fontSize: 10, letterSpacing: 0.5 }}>
                  {group.label}
                </Typography>
                {collapsedGroups[group.id]
                  ? <ExpandMoreIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
                  : <ExpandLessIcon sx={{ fontSize: 14, color: 'text.disabled' }} />}
              </Box>

              <Collapse in={!collapsedGroups[group.id]}>
                {group.params.map(p => <ParamSelectorRow key={p.name} param={p} />)}
              </Collapse>
            </Box>
          ))}
        </Box>
      </Box>

      {/* ── RIGHT: sweep configuration ────────────────────────────────────── */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Sticky header */}
        <Box sx={{
          px: 2, py: 1.25,
          borderBottom: '1px solid',
          borderColor: 'divider',
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          flexShrink: 0,
        }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, flexWrap: 'wrap' }}>
            <Typography variant="subtitle2" sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <ShowChartIcon sx={{ fontSize: 16 }} /> Sweep / Optimize
            </Typography>
            <Chip size="small" label={`${sweepCount} sweep`}   color="primary" variant={sweepCount   > 0 ? 'filled' : 'outlined'} sx={{ height: 18, fontSize: 10 }} />
            <Chip size="small" label={`${optimizeCount} opt`}  color="success" variant={optimizeCount > 0 ? 'filled' : 'outlined'} sx={{ height: 18, fontSize: 10 }} />
            {sweepCount > 0 && (
              <Chip size="small" label={`~${estimateRuns()} variants`} variant="outlined" sx={{ height: 18, fontSize: 10 }} />
            )}
          </Box>
          <Button
            variant="contained"
            startIcon={<PlayArrowIcon />}
            disabled={sweepEntries.length === 0 || !connectedToApi}
            size="small"
            sx={{ flexShrink: 0 }}
          >
            Run
          </Button>
        </Box>

        {/* Scrollable content */}
        <Box sx={{ flex: 1, overflowY: 'auto', p: 2, display: 'flex', flexDirection: 'column', gap: 2.5 }}>

          {/* Sweep variables */}
          <Box>
            <Typography variant="overline" color="text.secondary" sx={{ fontSize: 10, letterSpacing: 1 }}>
              Sweep Variables
            </Typography>

            {sweepEntries.length === 0 ? (
              <Box sx={{ textAlign: 'center', py: 3, color: 'text.disabled' }}>
                <ShowChartIcon sx={{ fontSize: 32, mb: 0.5, opacity: 0.25, display: 'block', mx: 'auto' }} />
                <Typography variant="caption">
                  Select parameters from the left panel
                </Typography>
              </Box>
            ) : (
              <Box sx={{ mt: 1 }}>
                {sweepEntries.map(([name]) => (
                  <SweepCard
                    key={name}
                    paramName={name}
                    label={schemaMap[name]?.label ?? name}
                    unit={schemaMap[name]?.unit}
                    schema={schemaMap[name]}
                  />
                ))}
              </Box>
            )}
          </Box>

          <Divider />

          {/* Operating points */}
          <Box>
            <Typography variant="overline" color="text.secondary" sx={{ fontSize: 10, letterSpacing: 1, display: 'block', mb: 1 }}>
              Operating Points
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
              Each geometry variant is evaluated at both points → segment in Pareto space (Torque/mass vs Efficiency)
            </Typography>

            <Box sx={{ display: 'flex', gap: 2 }}>
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
          </Box>

          <Divider />

          {/* Torque ripple */}
          <Box>
            <Typography variant="overline" color="text.secondary" sx={{ fontSize: 10, letterSpacing: 1, display: 'block', mb: 0.5 }}>
              Torque Ripple Constraint
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              Max allowed (T_max − T_min) / T_mean per electrical cycle
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <Slider
                value={sweepConfig.rippleThreshold * 100}
                onChange={(_, v) => updateRippleThreshold((v as number) / 100)}
                min={1} max={30} step={0.5}
                sx={{ flex: 1 }}
              />
              <Typography variant="body2" sx={{ minWidth: 42, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                {(sweepConfig.rippleThreshold * 100).toFixed(1)}%
              </Typography>
            </Box>
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default SweepConfigPanel;
