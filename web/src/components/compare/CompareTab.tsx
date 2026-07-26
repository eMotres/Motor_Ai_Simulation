/**
 * CompareTab — host for the "Configure" tab: the Configurator, i.e. the simple
 * tuner (pick a reference passport, tune length / turns / wire / connection,
 * instant T/P/V/η).
 *
 * The saved-simulations diff that used to share this tab behind a toggle now has
 * its own top-level "Compare" tab (ComparePanel), so comparison points live where
 * you would look for them instead of behind a mode switch.
 */
import React from 'react';
import { Box } from '@mui/material';
import ConfiguratorPanel from './ConfiguratorPanel';

const CompareTab: React.FC = () => (
  <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column',
    bgcolor: 'var(--panel-2)', overflow: 'hidden' }}>
    <Box sx={{ flex: 1, minHeight: 0 }}>
      <ConfiguratorPanel />
    </Box>
  </Box>
);

export default CompareTab;
