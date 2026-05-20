import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  TextField,
  InputAdornment,
  Slider,
  Divider,
} from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';

const OperatingPointsPanel: React.FC = () => {
  const { sweepConfig, updateOperatingPoint, updateRippleThreshold } = useMotorStore();
  const { operatingPoints, rippleThreshold } = sweepConfig;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Typography variant="subtitle2" color="primary">Operating Points</Typography>
      <Typography variant="caption" color="text.secondary">
        Each geometry variant is evaluated at both operating points, forming a segment in the Pareto space (Torque/mass vs Efficiency).
      </Typography>

      <Box sx={{ display: 'flex', gap: 2 }}>
        {([0, 1] as const).map(i => (
          <Card key={i} variant="outlined" sx={{ flex: 1, bgcolor: 'background.default' }}>
            <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, display: 'block', mb: 1 }}>
                Point {i + 1}
              </Typography>

              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                <TextField
                  label="Current"
                  size="small"
                  type="number"
                  value={operatingPoints[i].current_a}
                  onChange={e => updateOperatingPoint(i, { current_a: parseFloat(e.target.value) })}
                  InputProps={{ endAdornment: <InputAdornment position="end">A</InputAdornment> }}
                  inputProps={{ min: 0, step: 1 }}
                />
                <TextField
                  label="Speed"
                  size="small"
                  type="number"
                  value={operatingPoints[i].rpm}
                  onChange={e => updateOperatingPoint(i, { rpm: parseFloat(e.target.value) })}
                  InputProps={{ endAdornment: <InputAdornment position="end">RPM</InputAdornment> }}
                  inputProps={{ min: 0, step: 100 }}
                />
              </Box>
            </CardContent>
          </Card>
        ))}
      </Box>

      <Divider />

      <Box>
        <Typography variant="subtitle2" color="primary" sx={{ mb: 1 }}>Torque Ripple Constraint</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          Max allowed (T_max − T_min) / T_mean per electrical cycle
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Slider
            value={rippleThreshold * 100}
            onChange={(_, v) => updateRippleThreshold((v as number) / 100)}
            min={1}
            max={30}
            step={0.5}
            sx={{ flex: 1 }}
          />
          <Typography variant="body2" sx={{ minWidth: 40 }}>
            {(rippleThreshold * 100).toFixed(1)}%
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

export default OperatingPointsPanel;
