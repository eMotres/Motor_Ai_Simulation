import React, { useEffect, useCallback } from 'react';
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
  Tooltip,
} from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';
import type { VariationMode } from '../../types/motor';

// ─── Helpers ─────────────────────────────────────────────────────────────────

const MODE_COLOR: Record<VariationMode, 'default' | 'primary' | 'success'> = {
  fixed: 'default',
  sweep: 'primary',
  optimize: 'success',
};

function fmtVal(v: number | string, step?: number): string {
  if (typeof v === 'string') return v;
  const decimals = step && step < 1 ? 2 : step && step < 0.1 ? 3 : 1;
  return v.toFixed(decimals);
}

const numFieldSx = {
  width: 68,
  '& .MuiInputBase-input': { px: '6px', py: '3px', fontSize: 12 },
  '& .MuiOutlinedInput-root': { borderRadius: '4px' },
};

const hdrCell = { py: 0.5, px: 0.75, fontSize: 11, color: 'text.disabled', borderBottom: '1px solid', borderColor: 'divider', whiteSpace: 'nowrap' as const };
const dataCell = { py: 0.25, px: 0.75, borderBottom: 'none' };

// ─── Component ───────────────────────────────────────────────────────────────

const ParameterVariationTable: React.FC = () => {
  const {
    parameterSchema,
    parameterGroups,
    geometry,
    sweepConfig,
    updateVariation,
    updateGeometryViaApi,
    updateGeometry,
    connectedToApi,
    initVariationsFromSchema,
  } = useMotorStore();

  useEffect(() => {
    if (parameterSchema.length > 0) initVariationsFromSchema();
  }, [parameterSchema.length]);

  const sweepCount   = Object.values(sweepConfig.variations).filter(v => v.mode === 'sweep').length;
  const optimizeCount = Object.values(sweepConfig.variations).filter(v => v.mode === 'optimize').length;

  const handleValueChange = useCallback((name: string, raw: string, type: 'float' | 'int') => {
    const v = type === 'int' ? parseInt(raw, 10) : parseFloat(raw);
    if (isNaN(v)) return;
    if (connectedToApi) updateGeometryViaApi({ [name]: v });
    else updateGeometry({ [name]: v });
  }, [connectedToApi, updateGeometryViaApi, updateGeometry]);

  const groups = parameterGroups
    .map(g => ({
      ...g,
      params: parameterSchema.filter(p => p.group === g.id && p.type !== 'string'),
    }))
    .filter(g => g.params.length > 0);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      {/* Summary */}
      <Box sx={{ display: 'flex', gap: 1, mb: 0.5 }}>
        <Chip size="small" label={`${sweepCount} sweep`}    color="primary" variant={sweepCount   > 0 ? 'filled' : 'outlined'} />
        <Chip size="small" label={`${optimizeCount} opt`}   color="success" variant={optimizeCount > 0 ? 'filled' : 'outlined'} />
      </Box>

      {groups.map((group, gi) => (
        <Box key={group.id}>
          <Typography variant="caption" color="primary" sx={{ fontWeight: 700, display: 'block', mt: gi > 0 ? 1 : 0, mb: 0.25 }}>
            {group.label}
          </Typography>

          <Table size="small" sx={{ tableLayout: 'fixed', width: '100%' }}>
            <TableHead>
              <TableRow>
                <TableCell sx={{ ...hdrCell, width: '28%' }}>Параметр</TableCell>
                <TableCell sx={{ ...hdrCell, width: 72 }}>Знач.</TableCell>
                <TableCell sx={{ ...hdrCell, width: 72 }}>Min</TableCell>
                <TableCell sx={{ ...hdrCell, width: 72 }}>Max</TableCell>
                <TableCell sx={{ ...hdrCell, width: 72 }}>Шаг</TableCell>
                <TableCell sx={{ ...hdrCell }}>Режим</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {group.params.map(param => {
                const variation = sweepConfig.variations[param.name];
                const mode: VariationMode = variation?.mode ?? 'fixed';
                const isActive = mode !== 'fixed';
                const isSweep  = mode === 'sweep';
                const currentVal = geometry[param.name] ?? 0;

                return (
                  <TableRow
                    key={param.name}
                    sx={{ '&:hover': { bgcolor: 'rgba(255,255,255,0.03)' } }}
                  >
                    {/* Name */}
                    <TableCell sx={dataCell}>
                      <Tooltip title={param.description} placement="right" arrow>
                        <Typography variant="caption" sx={{ lineHeight: 1.4 }}>
                          {param.label}
                          {param.unit && (
                            <Typography component="span" variant="caption" color="text.disabled" sx={{ ml: 0.5 }}>
                              ({param.unit})
                            </Typography>
                          )}
                        </Typography>
                      </Tooltip>
                    </TableCell>

                    {/* Current value — editable, syncs to API */}
                    <TableCell sx={dataCell}>
                      <TextField
                        size="small"
                        type="number"
                        defaultValue={fmtVal(currentVal, param.step)}
                        key={`val-${param.name}-${currentVal}`}
                        onBlur={e => handleValueChange(param.name, e.target.value, param.type as 'float' | 'int')}
                        onKeyDown={e => {
                          if (e.key === 'Enter') handleValueChange(param.name, (e.target as HTMLInputElement).value, param.type as 'float' | 'int');
                        }}
                        inputProps={{ step: param.step, min: param.min, max: param.max }}
                        sx={{ ...numFieldSx, '& .MuiInputBase-input': { ...numFieldSx['& .MuiInputBase-input'], color: isActive ? '#fff' : 'text.secondary' } }}
                      />
                    </TableCell>

                    {/* Min */}
                    <TableCell sx={dataCell}>
                      <TextField
                        size="small"
                        type="number"
                        disabled={!isActive}
                        value={variation?.min ?? (param.min ?? '')}
                        onChange={e => updateVariation(param.name, { min: parseFloat(e.target.value) })}
                        inputProps={{ step: param.step }}
                        sx={numFieldSx}
                      />
                    </TableCell>

                    {/* Max */}
                    <TableCell sx={dataCell}>
                      <TextField
                        size="small"
                        type="number"
                        disabled={!isActive}
                        value={variation?.max ?? (param.max ?? '')}
                        onChange={e => updateVariation(param.name, { max: parseFloat(e.target.value) })}
                        inputProps={{ step: param.step }}
                        sx={numFieldSx}
                      />
                    </TableCell>

                    {/* Step */}
                    <TableCell sx={dataCell}>
                      <TextField
                        size="small"
                        type="number"
                        disabled={!isSweep}
                        value={variation?.step ?? (param.step ?? '')}
                        onChange={e => updateVariation(param.name, { step: parseFloat(e.target.value) })}
                        inputProps={{ step: param.step, min: 0 }}
                        sx={numFieldSx}
                      />
                    </TableCell>

                    {/* Mode */}
                    <TableCell sx={dataCell}>
                      <ToggleButtonGroup
                        exclusive
                        size="small"
                        value={mode}
                        onChange={(_, v) => v && updateVariation(param.name, { mode: v as VariationMode })}
                        sx={{ height: 24 }}
                      >
                        <ToggleButton value="fixed"    color={MODE_COLOR.fixed}    sx={{ px: 0.75, py: 0, fontSize: 10, minWidth: 30 }}>Fix</ToggleButton>
                        <ToggleButton value="sweep"    color={MODE_COLOR.sweep}    sx={{ px: 0.75, py: 0, fontSize: 10, minWidth: 38 }}>Sweep</ToggleButton>
                        <ToggleButton value="optimize" color={MODE_COLOR.optimize} sx={{ px: 0.75, py: 0, fontSize: 10, minWidth: 30 }}>Opt</ToggleButton>
                      </ToggleButtonGroup>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          {gi < groups.length - 1 && <Divider sx={{ mt: 1 }} />}
        </Box>
      ))}
    </Box>
  );
};

export default ParameterVariationTable;
