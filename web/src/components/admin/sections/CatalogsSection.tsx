// Admin · Catalogs — FEM passports (Configurator reference scaling) and the
// platform module registry (kernel pipeline proof).
import React from 'react';
import { Box, Typography } from '@mui/material';
import PassportManager from '../PassportManager';
import ModulesPanel from '../ModulesPanel';

const CatalogsSection: React.FC = () => (
  <Box>
    <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>Catalogs</Typography>
    <PassportManager />
    <ModulesPanel />
  </Box>
);

export default CatalogsSection;
