import React, { useEffect } from 'react';
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  Chip,
  Divider,
} from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';
import type { VariationMode } from '../../types/motor';

const MODE_LABELS: Record<VariationMode, string> = {
  fixed: 'Fix',
  sweep: 'Sweep',
  optimize: 'Opt',
};

const MODE_COLORS: Record<VariationMode, 'default' | 'primary' | 'success'> = {
  fixed: 'default',
  sweep: 'primary',
  optimize: 'success',
};

const cellSx = { py: 0.5, px: 0.75, borderBottom: 'none' };
const inputSx = { width: 70 };

const ParameterVariationTable: React.FC = () => {
  const {
    parameterSchema,
    parameterGroups,
    geometry,
    sweepConfig,
    updateVariation,
    initVariationsFromSchema,
  } = useMotorStore();

  useEffect(() => {
    if (parameterSchema.length > 0) {
      initVariationsFromSchema();
    }
  }, [parameterSchema.length]);

  const sweepCount = Object.values(sweepConfig.variations).filter(v => v.mode === 'sweep').length;
  const optimizeCount = Object.values(sweepConfig.variations).filter(v => v.mode === 'optimize').length;

  const groupedParams = parameterGroups.map(group => ({
    ...group,
    parameters: parameterSchema.filter(p => p.group === group.id && p.type !== 'string'),
  })).filter(g => g.parameters.length > 0);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      {/* Summary chips */}
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1 }}>
        <Chip size="small" label={`${sweepCount} sweep`} color="primary" variant={sweepCount > 0 ? 'filled' : 'outlined'} />
        <Chip size="small" label={`${optimizeCount} optimize`} color="success" variant={optimizeCount > 0 ? 'filled' : 'outlined'} />
      </Box>

      {groupedParams.map((group, gi) => (
        <Box key={group.id}>
          <Typography variant="caption" color="primary" sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}>
            {group.label}
          </Typography>

          <Table size="small" sx={{ tableLayout: 'fixed' }}>
            <TableHead>
              <TableRow>
                <TableCell sx={{ ...cellSx, width: '30%', pb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">Parameter</Typography>
                </TableCell>
                <TableCell sx={{ ...cellSx, width: 60, pb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">Value</Typography>
                </TableCell>
                <TableCell sx={{ ...cellSx, width: 60, pb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">Min</Typography>
                </TableCell>
                <TableCell sx={{ ...cellSx, width: 60, pb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">Max</Typography>
                </TableCell>
                <TableCell sx={{ ...cellSx, width: 60, pb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">Step</Typography>
                </TableCell>
                <TableCell sx={{ ...cellSx, pb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">Mode</Typography>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {group.parameters.map(param => {
                const variation = sweepConfig.variations[param.name];
                const mode: VariationMode = variation?.mode ?? 'fixed';
                const isActive = mode !== 'fixed';
                const currentVal = geometry[param.name] ?? 0;

                return (
                  <TableRow key={param.name} sx={{ '&:hover': { bgcolor: 'action.hover' } }}>
                    {/* Parameter name */}
                    <TableCell sx={cellSx}>
                      <Typography variant="caption" title={param.description}>
                        {param.label}
                        {param.unit && (
                          <Typography component="span" variant="caption" color="text.secondary">
                            {' '}({param.unit})
                          </Typography>
                        )}
                      </Typography>
                    </TableCell>

                    {/* Current value */}
                    <TableCell sx={cellSx}>
                      <Typography variant="caption" color={isActive ? 'text.primary' : 'text.secondary'}>
                        {typeof currentVal === 'number' ? currentVal.toFixed(param.step && param.step < 1 ? 2 : 0) : currentVal}
                      </Typography>
                    </TableCell>

                    {/* Min */}
                    <TableCell sx={cellSx}>
                      <TextField
                        size="small"
                        type="number"
                        disabled={!isActive}
                        value={variation?.min ?? (param.min ?? '')}
                        onChange={e => updateVariation(param.name, { min: parseFloat(e.target.value) })}
                        inputProps={{ step: param.step, style: { padding: '2px 4px', fontSize: 11 } }}
                        sx={{ width: 56 }}
                      />
                    </TableCell>

                    {/* Max */}
                    <TableCell sx={cellSx}>
                      <TextField
                        size="small"
                        type="number"
                        disabled={!isActive}
                        value={variation?.max ?? (param.max ?? '')}
                        onChange={e => updateVariation(param.name, { max: parseFloat(e.target.value) })}
                        inputProps={{ step: param.step, style: { padding: '2px 4px', fontSize: 11 } }}
                        sx={{ width: 56 }}
                      />
                    </TableCell>

                    {/* Step */}
                    <TableCell sx={cellSx}>
                      <TextField
                        size="small"
                        type="number"
                        disabled={mode !== 'sweep'}
                        value={variation?.step ?? (param.step ?? '')}
                        onChange={e => updateVariation(param.name, { step: parseFloat(e.target.value) })}
                        inputProps={{ step: param.step, style: { padding: '2px 4px', fontSize: 11 } }}
                        sx={{ width: 56 }}
                      />
                    </TableCell>

                    {/* Mode toggle */}
                    <TableCell sx={cellSx}>
                      <ToggleButtonGroup
                        exclusive
                        size="small"
                        value={mode}
                        onChange={(_, v) => v && updateVariation(param.name, { mode: v as VariationMode })}
                        sx={{ height: 22 }}
                      >
                        {(['fixed', 'sweep', 'optimize'] as VariationMode[]).map(m => (
                          <ToggleButton
                            key={m}
                            value={m}
                            sx={{ px: 0.5, py: 0, fontSize: 10, minWidth: 32, lineHeight: 1 }}
                            color={MODE_COLORS[m]}
                          >
                            {MODE_LABELS[m]}
                          </ToggleButton>
                        ))}
                      </ToggleButtonGroup>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          {gi < groupedParams.length - 1 && <Divider sx={{ mt: 1 }} />}
        </Box>
      ))}
    </Box>
  );
};

export default ParameterVariationTable;
