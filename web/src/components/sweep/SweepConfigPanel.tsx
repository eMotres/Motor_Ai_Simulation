import React, { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Divider,
  Tabs,
  Tab,
  Alert,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import ParameterVariationTable from './ParameterVariationTable';
import OperatingPointsPanel from './OperatingPointsPanel';
import { useMotorStore } from '../../stores/motorStore';

type SweepTab = 'parameters' | 'operating';

const SweepConfigPanel: React.FC = () => {
  const [tab, setTab] = useState<SweepTab>('parameters');
  const { sweepConfig, connectedToApi } = useMotorStore();

  const sweepCount = Object.values(sweepConfig.variations).filter(v => v.mode === 'sweep').length;
  const optimizeCount = Object.values(sweepConfig.variations).filter(v => v.mode === 'optimize').length;
  const hasActive = sweepCount > 0 || optimizeCount > 0;

  const estimateRuns = () => {
    const vars = Object.values(sweepConfig.variations);
    return vars
      .filter(v => v.mode === 'sweep')
      .reduce((acc, v) => {
        const steps = Math.max(1, Math.ceil((v.max - v.min) / (v.step || 1)) + 1);
        return acc * steps;
      }, 1);
  };

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 2, overflow: 'hidden' }}>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
        <Box>
          <Typography variant="h6">Sweep / Optimize</Typography>
          <Typography variant="caption" color="text.secondary">
            Configure parameter ranges and operating points for Pareto optimization
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<PlayArrowIcon />}
          disabled={!hasActive || !connectedToApi}
          size="small"
        >
          Run
        </Button>
      </Box>

      {hasActive && (
        <Alert severity="info" sx={{ mb: 1, py: 0.5 }}>
          {sweepCount > 0 && `${sweepCount} sweep param(s) → ~${estimateRuns()} combinations. `}
          {optimizeCount > 0 && `${optimizeCount} parameter(s) to optimize.`}
        </Alert>
      )}

      {/* Sub-tabs */}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ borderBottom: 1, borderColor: 'divider', mb: 1.5, minHeight: 36 }}>
        <Tab label="Parameters" value="parameters" sx={{ minHeight: 36, py: 0 }} />
        <Tab label="Operating Points" value="operating" sx={{ minHeight: 36, py: 0 }} />
      </Tabs>

      {/* Content — scrollable */}
      <Box sx={{ flex: 1, overflowY: 'auto' }}>
        {tab === 'parameters' && <ParameterVariationTable />}
        {tab === 'operating' && <OperatingPointsPanel />}
      </Box>
    </Box>
  );
};

export default SweepConfigPanel;
