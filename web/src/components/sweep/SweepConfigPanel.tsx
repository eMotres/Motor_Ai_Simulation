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
  Alert,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import CloseIcon from '@mui/icons-material/Close';
import AddIcon from '@mui/icons-material/Add';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import { useMotorStore } from '../../stores/motorStore';
import type { VariationMode } from '../../types/motor';

// ─── Sweep card (right panel) ─────────────────────────────────────────────────

interface SweepCardProps {
  paramName: string;
  label: string;
  unit?: string;
}

const SweepCard: React.FC<SweepCardProps> = ({ paramName, label, unit }) => {
  const { sweepConfig, updateVariation } = useMotorStore();
  const v = sweepConfig.variations[paramName];
  if (!v || v.mode === 'fixed') return null;

  return (
    <Card variant="outlined" sx={{ mb: 1, bgcolor: 'background.default' }}>
      <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {label}
            {unit && <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>({unit})</Typography>}
          </Typography>
          <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
            <Chip
              label={v.mode === 'optimize' ? 'Opt' : 'Sweep'}
              size="small"
              color={v.mode === 'optimize' ? 'success' : 'primary'}
              sx={{ height: 18, fontSize: 10 }}
            />
            <IconButton size="small" onClick={() => updateVariation(paramName, { mode: 'fixed' })} sx={{ p: 0.25 }}>
              <CloseIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Box>
        </Box>

        <Box sx={{ display: 'flex', gap: 1 }}>
          <TextField
            label="Min"
            size="small"
            type="number"
            value={v.min}
            onChange={e => updateVariation(paramName, { min: parseFloat(e.target.value) })}
            sx={{ flex: 1 }}
            inputProps={{ style: { fontSize: 12, padding: '4px 8px' } }}
          />
          <TextField
            label="Max"
            size="small"
            type="number"
            value={v.max}
            onChange={e => updateVariation(paramName, { max: parseFloat(e.target.value) })}
            sx={{ flex: 1 }}
            inputProps={{ style: { fontSize: 12, padding: '4px 8px' } }}
          />
          {v.mode === 'sweep' && (
            <TextField
              label="Шаг"
              size="small"
              type="number"
              value={v.step}
              onChange={e => updateVariation(paramName, { step: parseFloat(e.target.value) })}
              sx={{ flex: 1 }}
              inputProps={{ style: { fontSize: 12, padding: '4px 8px' }, min: 0 }}
            />
          )}
        </Box>
      </CardContent>
    </Card>
  );
};

// ─── Parameter row (left panel) ───────────────────────────────────────────────

interface ParamRowProps {
  name: string;
  label: string;
  unit?: string;
  description?: string;
  paramType: 'float' | 'int' | 'string';
  step?: number;
  min?: number;
  max?: number;
}

const ParamRow: React.FC<ParamRowProps> = ({ name, label, unit, description, paramType, step, min, max }) => {
  const { geometry, sweepConfig, updateVariation, updateGeometryViaApi, updateGeometry, connectedToApi, initVariationsFromSchema } = useMotorStore();
  const currentVal = geometry[name] ?? 0;
  const variation = sweepConfig.variations[name];
  const isSwept = variation?.mode === 'sweep';
  const isOpt   = variation?.mode === 'optimize';

  const handleChange = useCallback((raw: string) => {
    const v = paramType === 'int' ? parseInt(raw, 10) : parseFloat(raw);
    if (isNaN(v)) return;
    if (connectedToApi) updateGeometryViaApi({ [name]: v });
    else updateGeometry({ [name]: v });
  }, [name, paramType, connectedToApi, updateGeometryViaApi, updateGeometry]);

  const addToSweep = (mode: VariationMode) => {
    const cur = Number(currentVal);
    updateVariation(name, {
      mode,
      min: variation?.min ?? (min ?? Math.max(0, cur * 0.5)),
      max: variation?.max ?? (max ?? cur * 1.5),
      step: variation?.step ?? (step ?? Math.max(0.01, Math.abs(cur) * 0.1)),
    });
  };

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        py: 0.5,
        px: 1,
        borderRadius: 1,
        '&:hover': { bgcolor: 'rgba(255,255,255,0.04)' },
        borderLeft: isSwept ? '2px solid #3b82f6' : isOpt ? '2px solid #10b981' : '2px solid transparent',
      }}
    >
      {/* Name */}
      <Tooltip title={description || ''} placement="right" arrow disableHoverListener={!description}>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography variant="body2" noWrap sx={{ lineHeight: 1.3 }}>
            {label}
          </Typography>
          {unit && (
            <Typography variant="caption" color="text.disabled" sx={{ fontSize: 10 }}>
              {unit}
            </Typography>
          )}
        </Box>
      </Tooltip>

      {/* Value input */}
      <TextField
        size="small"
        type="number"
        defaultValue={typeof currentVal === 'number' ? currentVal : currentVal}
        key={`${name}-${currentVal}`}
        onBlur={e => handleChange(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && handleChange((e.target as HTMLInputElement).value)}
        inputProps={{ step, min, max, style: { padding: '4px 8px', fontSize: 13, width: 72 } }}
        sx={{ width: 88, flexShrink: 0 }}
      />

      {/* Sweep / Opt buttons */}
      <Tooltip title="Добавить в Sweep">
        <IconButton
          size="small"
          onClick={() => isSwept ? updateVariation(name, { mode: 'fixed' }) : addToSweep('sweep')}
          color={isSwept ? 'primary' : 'default'}
          sx={{ p: 0.5, opacity: isOpt ? 0.3 : 1 }}
        >
          <ShowChartIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Tooltip>
    </Box>
  );
};

// ─── Add custom parameter ──────────────────────────────────────────────────────

interface CustomParam {
  name: string;
  label: string;
  unit: string;
  value: number;
}

const AddParamForm: React.FC<{ onAdd: (p: CustomParam) => void }> = ({ onAdd }) => {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState('');

  const submit = () => {
    if (!label.trim()) return;
    const name = label.trim().toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');
    onAdd({ name, label: label.trim(), unit: unit.trim(), value: parseFloat(value) || 0 });
    setLabel(''); setValue(''); setUnit('');
    setOpen(false);
  };

  return (
    <Box>
      <Button size="small" startIcon={<AddIcon />} onClick={() => setOpen(o => !o)} sx={{ mt: 1 }}>
        Добавить параметр
      </Button>
      <Collapse in={open}>
        <Box sx={{ display: 'flex', gap: 1, mt: 1, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <TextField label="Название" size="small" value={label} onChange={e => setLabel(e.target.value)} sx={{ flex: 2, minWidth: 120 }} />
          <TextField label="Значение" size="small" type="number" value={value} onChange={e => setValue(e.target.value)} sx={{ width: 90 }} />
          <TextField label="Ед." size="small" value={unit} onChange={e => setUnit(e.target.value)} sx={{ width: 60 }} />
          <Button variant="contained" size="small" onClick={submit} disabled={!label.trim()}>OK</Button>
        </Box>
      </Collapse>
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
    updateGeometry,
  } = useMotorStore();

  const [customParams, setCustomParams] = useState<CustomParam[]>([]);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (parameterSchema.length > 0) initVariationsFromSchema();
  }, [parameterSchema.length]);

  const sweepEntries = Object.entries(sweepConfig.variations).filter(([, v]) => v.mode !== 'fixed');

  const groups = parameterGroups
    .map(g => ({
      ...g,
      params: parameterSchema.filter(p => p.group === g.id),
    }))
    .filter(g => g.params.length > 0);

  const handleAddCustom = (p: CustomParam) => {
    setCustomParams(prev => [...prev, p]);
    updateGeometry({ [p.name]: p.value });
  };

  const toggleGroup = (id: string) => setCollapsedGroups(prev => ({ ...prev, [id]: !prev[id] }));

  const estimateRuns = () => {
    return Object.values(sweepConfig.variations)
      .filter(v => v.mode === 'sweep')
      .reduce((acc, v) => {
        const steps = Math.max(1, Math.ceil((v.max - v.min) / Math.max(v.step || 1, 1e-9)) + 1);
        return acc * steps;
      }, 1);
  };

  const sweepCount    = sweepEntries.filter(([, v]) => v.mode === 'sweep').length;
  const optimizeCount = sweepEntries.filter(([, v]) => v.mode === 'optimize').length;

  // Label lookup for sweep cards
  const labelMap: Record<string, { label: string; unit?: string }> = {};
  parameterSchema.forEach(p => { labelMap[p.name] = { label: p.label, unit: p.unit }; });
  customParams.forEach(p => { labelMap[p.name] = { label: p.label, unit: p.unit }; });

  return (
    <Box sx={{ height: '100%', display: 'flex', overflow: 'hidden' }}>

      {/* ── LEFT: all parameters ───────────────────────────────────────────── */}
      <Box sx={{
        width: 360,
        flexShrink: 0,
        borderRight: '1px solid',
        borderColor: 'divider',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}>
        <Box sx={{ px: 2, py: 1.5, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Typography variant="subtitle2">Все параметры</Typography>
          <Typography variant="caption" color="text.secondary">
            Нажмите <ShowChartIcon sx={{ fontSize: 12, verticalAlign: 'middle' }} /> чтобы добавить в Sweep
          </Typography>
        </Box>

        <Box sx={{ flex: 1, overflowY: 'auto', px: 1, py: 1 }}>
          {groups.map(group => (
            <Box key={group.id} sx={{ mb: 0.5 }}>
              {/* Group header */}
              <Box
                sx={{ display: 'flex', alignItems: 'center', cursor: 'pointer', px: 1, py: 0.5, borderRadius: 1, '&:hover': { bgcolor: 'action.hover' } }}
                onClick={() => toggleGroup(group.id)}
              >
                <Typography variant="caption" color="primary" sx={{ fontWeight: 700, flex: 1 }}>
                  {group.label}
                </Typography>
                {collapsedGroups[group.id] ? <ExpandMoreIcon sx={{ fontSize: 14 }} /> : <ExpandLessIcon sx={{ fontSize: 14 }} />}
              </Box>

              <Collapse in={!collapsedGroups[group.id]}>
                {group.params.map(param => (
                  <ParamRow
                    key={param.name}
                    name={param.name}
                    label={param.label}
                    unit={param.unit}
                    description={param.description}
                    paramType={param.type as 'float' | 'int' | 'string'}
                    step={param.step}
                    min={param.min}
                    max={param.max}
                  />
                ))}
              </Collapse>

              <Divider sx={{ my: 0.5 }} />
            </Box>
          ))}

          {/* Custom params */}
          {customParams.map(p => (
            <ParamRow
              key={p.name}
              name={p.name}
              label={p.label}
              unit={p.unit}
              paramType="float"
            />
          ))}

          <Box sx={{ px: 1 }}>
            <AddParamForm onAdd={handleAddCustom} />
          </Box>
        </Box>
      </Box>

      {/* ── RIGHT: sweep config ────────────────────────────────────────────── */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Header */}
        <Box sx={{
          px: 2, py: 1.5,
          borderBottom: '1px solid',
          borderColor: 'divider',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <Box>
            <Typography variant="subtitle2">Sweep</Typography>
            <Box sx={{ display: 'flex', gap: 0.75, mt: 0.25 }}>
              <Chip size="small" label={`${sweepCount} sweep`}    color="primary" variant={sweepCount   > 0 ? 'filled' : 'outlined'} sx={{ height: 18, fontSize: 10 }} />
              <Chip size="small" label={`${optimizeCount} opt`}   color="success" variant={optimizeCount > 0 ? 'filled' : 'outlined'} sx={{ height: 18, fontSize: 10 }} />
              {sweepCount > 0 && (
                <Chip size="small" label={`~${estimateRuns()} вар.`} variant="outlined" sx={{ height: 18, fontSize: 10 }} />
              )}
            </Box>
          </Box>
          <Button
            variant="contained"
            startIcon={<PlayArrowIcon />}
            disabled={sweepEntries.length === 0 || !connectedToApi}
            size="small"
          >
            Запустить
          </Button>
        </Box>

        <Box sx={{ flex: 1, overflowY: 'auto', p: 2, display: 'flex', flexDirection: 'column', gap: 2 }}>

          {/* Swept params */}
          {sweepEntries.length === 0 ? (
            <Box sx={{ textAlign: 'center', py: 4, color: 'text.disabled' }}>
              <ShowChartIcon sx={{ fontSize: 40, mb: 1, opacity: 0.3 }} />
              <Typography variant="body2">
                Нажмите <ShowChartIcon sx={{ fontSize: 14, verticalAlign: 'middle' }} /> у параметра слева,<br />
                чтобы добавить его в sweep
              </Typography>
            </Box>
          ) : (
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                Параметры для вариации
              </Typography>
              {sweepEntries.map(([name]) => (
                <SweepCard
                  key={name}
                  paramName={name}
                  label={labelMap[name]?.label ?? name}
                  unit={labelMap[name]?.unit}
                />
              ))}
            </Box>
          )}

          <Divider />

          {/* Operating points */}
          <Box>
            <Typography variant="subtitle2" color="primary" sx={{ mb: 1 }}>Рабочие точки</Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
              Каждый вариант геометрии оценивается в двух точках → сегмент в пространстве (Момент/масса, КПД)
            </Typography>

            <Box sx={{ display: 'flex', gap: 2 }}>
              {([0, 1] as const).map(i => (
                <Card key={i} variant="outlined" sx={{ flex: 1, bgcolor: 'background.default' }}>
                  <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
                    <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, display: 'block', mb: 1 }}>
                      Точка {i + 1}
                    </Typography>
                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                      <TextField
                        label="Ток"
                        size="small"
                        type="number"
                        value={sweepConfig.operatingPoints[i].current_a}
                        onChange={e => updateOperatingPoint(i, { current_a: parseFloat(e.target.value) })}
                        InputProps={{ endAdornment: <InputAdornment position="end">A</InputAdornment> }}
                        inputProps={{ min: 0, step: 1 }}
                      />
                      <TextField
                        label="Обороты"
                        size="small"
                        type="number"
                        value={sweepConfig.operatingPoints[i].rpm}
                        onChange={e => updateOperatingPoint(i, { rpm: parseFloat(e.target.value) })}
                        InputProps={{ endAdornment: <InputAdornment position="end">об/мин</InputAdornment> }}
                        inputProps={{ min: 0, step: 100 }}
                      />
                    </Box>
                  </CardContent>
                </Card>
              ))}
            </Box>
          </Box>

          <Divider />

          {/* Ripple threshold */}
          <Box>
            <Typography variant="subtitle2" color="primary" sx={{ mb: 0.5 }}>Пульсации момента</Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              Макс. допустимое (T_max − T_min) / T_mean за один электрический оборот
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <Slider
                value={sweepConfig.rippleThreshold * 100}
                onChange={(_, v) => updateRippleThreshold((v as number) / 100)}
                min={1} max={30} step={0.5}
                sx={{ flex: 1 }}
              />
              <Typography variant="body2" sx={{ minWidth: 42, textAlign: 'right' }}>
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
