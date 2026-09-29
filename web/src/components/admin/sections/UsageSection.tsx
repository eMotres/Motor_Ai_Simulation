// Admin · Usage & pricing — machine time per account/client, and cost basis.
import React from 'react';
import { Box, Typography } from '@mui/material';
import UsagePanel from '../UsagePanel';

const UsageSection: React.FC = () => (
  <Box>
    <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>Usage &amp; pricing</Typography>
    <UsagePanel />
  </Box>
);

export default UsageSection;
