// Admin · Usage — machine time per account/client (CPU-hours, wall-hours, job counts).
import React from 'react';
import { Box, Typography } from '@mui/material';
import UsagePanel from '../UsagePanel';

const UsageSection: React.FC = () => (
  <Box>
    <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>Usage</Typography>
    <UsagePanel />
  </Box>
);

export default UsageSection;
