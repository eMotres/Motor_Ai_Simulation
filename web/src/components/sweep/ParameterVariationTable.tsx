import React, { useEffect, useCallback, useState } from 'react';
import {
  Box,
  Typography,
  TextField,
  IconButton,
  Tooltip,
  Divider,
  Chip,
  Button,
} from '@mui/material';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import CloseIcon     from '@mui/icons-material/Close';
import RefreshIcon   from '@mui/icons-material/Refresh';
import AddIcon       from '@mui/icons-material/Add';
import { useMotorStore } from '../../stores/motorStore';
import type { VariationMode } from '../../types/motor';
import AddParameterDialog from '../parameters/AddParameterDialog';

const ParameterVariationTable: React.FC = () => {
  const [dialogOpen, setDialogOpen] = useState(false);
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
    fetchSchemaFromApi,
  } = useMotorStore();

  useEffect(() => {
    if (parameterSchema.length > 0) initVariationsFromSchema();
  }, [parameterSchema.length]);

  const handleValueChange = useCallback((name: string, raw: string, type: 'float' | 'int') => {
    const v = type === 'int' ? parseInt(raw, 10) : parseFloat(raw);
    if (isNaN(v)) return;
    if (connectedToApi) updateGeometryViaApi({ [name]: v });
    else updateGeometry({ [name]: v });
  }, [connectedToApi, updateGeometryViaApi, updateGeometry]);

  const toggleSweep = useCallback((name: string) => {
    const variation = sweepConfig.variations[name];
    const isActive = variation?.mode !== 'fixed' && variation?.mode !== undefined;
    if (isActive) {
      updateVariation(name, { mode: 'fixed' });
    } else {
      const cur = Number(geometry[name] ?? 0);
      const schema = parameterSchema.find(p => p.name === name);
      updateVariation(name, {
        mode: 'sweep',
        min:  variation?.min  ?? schema?.min  ?? Math.max(0, cur * 0.5),
        max:  variation?.max  ?? schema?.max  ?? cur * 1.5,
        step: variation?.step ?? schema?.step ?? Math.max(0.01, Math.abs(cur) * 0.1),
      });
    }
  }, [sweepConfig, geometry, parameterSchema, updateVariation]);

  const groups = parameterGroups
    .map(g => ({
      ...g,
      params: parameterSchema.filter(p => p.group === g.id && p.type !== 'string'),
    }))
    .filter(g => g.params.length > 0);

  const fieldSx = {
    width: 52,
    '& .MuiInputBase-input': { px: '4px', py: '4px', fontSize: 12 },
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

      {/* Header: param count + add + reload */}
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1, px: 0.5, gap: 1 }}>
        <Chip
          label={`${parameterSchema.length} parameters`}
          size="small"
          variant="outlined"
          sx={{ fontSize: 10, height: 20 }}
        />
        <Box sx={{ flex: 1 }} />
        <Button
          size="small"
          startIcon={<AddIcon sx={{ fontSize: 14 }} />}
          onClick={() => setDialogOpen(true)}
          sx={{ fontSize: 11, py: 0.25, px: 1, minHeight: 24 }}
        >
          Add
        </Button>
        <Tooltip title="Reload schema from API">
          <IconButton size="small" onClick={fetchSchemaFromApi} sx={{ p: 0.4, opacity: 0.6, '&:hover': { opacity: 1 } }}>
            <RefreshIcon sx={{ fontSize: 15 }} />
          </IconButton>
        </Tooltip>
      </Box>

      <AddParameterDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />

      {groups.map((group, gi) => (
        <Box key={group.id}>
          {gi > 0 && <Divider sx={{ my: 1 }} />}
          <Typography
            variant="caption"
            color="primary"
            sx={{ fontWeight: 700, display: 'block', px: 0.5, py: 0.5, textTransform: 'uppercase', fontSize: 10, letterSpacing: 0.5 }}
          >
            {group.label}
          </Typography>

          {group.params.map(param => {
            const variation = sweepConfig.variations[param.name];
            const isActive  = variation?.mode !== 'fixed' && variation?.mode !== undefined;
            const currentVal = geometry[param.name] ?? 0;

            return (
              <Box
                key={param.name}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  px: 0.5,
                  py: 0.4,
                  borderRadius: 1,
                  borderLeft: isActive ? '2px solid #3b82f6' : '2px solid transparent',
                  bgcolor: isActive ? 'rgba(59,130,246,0.05)' : 'transparent',
                  '&:hover': { bgcolor: isActive ? 'rgba(59,130,246,0.08)' : 'rgba(255,255,255,0.03)' },
                }}
              >
                {/* Parameter name */}
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap sx={{ lineHeight: 1.4 }}>
                    {param.label}
                    {param.unit && (
                      <Typography component="span" variant="caption" color="text.disabled" sx={{ ml: 0.5 }}>
                        ({param.unit})
                      </Typography>
                    )}
                  </Typography>
                </Box>

                {/* Editable value */}
                <TextField
                  size="small"
                  type="number"
                  defaultValue={typeof currentVal === 'number'
                    ? currentVal.toFixed(param.step && param.step < 0.1 ? 3 : param.step && param.step < 1 ? 2 : 1)
                    : currentVal}
                  key={`val-${param.name}-${currentVal}`}
                  onBlur={e => handleValueChange(param.name, e.target.value, param.type as 'float' | 'int')}
                  onKeyDown={e => {
                    if (e.key === 'Enter')
                      handleValueChange(param.name, (e.target as HTMLInputElement).value, param.type as 'float' | 'int');
                  }}
                  inputProps={{ step: param.step, min: param.min, max: param.max }}
                  sx={fieldSx}
                />

                {/* Add / remove from sweep */}
                <Tooltip title={isActive ? 'Remove from sweep' : 'Add to sweep'}>
                  <IconButton
                    size="small"
                    color={isActive ? 'primary' : 'default'}
                    onClick={() => toggleSweep(param.name)}
                    sx={{ p: 0.4, opacity: isActive ? 1 : 0.35, '&:hover': { opacity: 1 } }}
                  >
                    {isActive
                      ? <CloseIcon     sx={{ fontSize: 15 }} />
                      : <ShowChartIcon sx={{ fontSize: 15 }} />}
                  </IconButton>
                </Tooltip>
              </Box>
            );
          })}
        </Box>
      ))}
    </Box>
  );
};

export default ParameterVariationTable;
