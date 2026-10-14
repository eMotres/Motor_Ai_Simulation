// Admin · 3D downloads — who downloaded which model, under which NDA, and the trace-a-file tool.
import React from 'react';
import { Box, Typography } from '@mui/material';
import Downloads3dPanel from '../Downloads3dPanel';

const Downloads3dSection: React.FC = () => (
  <Box>
    <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>3D downloads</Typography>
    <Downloads3dPanel />
  </Box>
);

export default Downloads3dSection;
