// Admin · Servers — cluster load (node agents): compute hosts only. Job queue
// and MCP traffic live in Agents; usage/cost lives in Usage & pricing.
import React from 'react';
import { Box, Typography } from '@mui/material';
import ServersPanel from '../ServersPanel';

const ServersSection: React.FC = () => (
  <Box>
    <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>Servers</Typography>
    <ServersPanel />
  </Box>
);

export default ServersSection;
