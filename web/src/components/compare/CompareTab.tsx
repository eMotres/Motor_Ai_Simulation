/**
 * CompareTab — host for the "Configure" tab.  Switches between:
 *   • Configurator — the simple tuner: pick a reference passport, tune
 *     length / turns / wire / connection, instant T/P/V/η, compare configs.
 *   • Saved simulations — the original diff of saved FEM runs (admin tool).
 * Defaults to the Configurator; the choice persists in localStorage.
 */
import React, { useState } from 'react';
import { Box, ToggleButton, ToggleButtonGroup } from '@mui/material';
import ConfiguratorPanel from './ConfiguratorPanel';
import ComparePanel from './ComparePanel';

type Mode = 'configurator' | 'saved';

const CompareTab: React.FC = () => {
  const [mode, setMode] = useState<Mode>(() =>
    (localStorage.getItem('configure.mode') === 'saved' ? 'saved' : 'configurator'));
  const change = (m: Mode) => { setMode(m); try { localStorage.setItem('configure.mode', m); } catch { /* ignore */ } };

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', bgcolor: '#060d17', overflow: 'hidden' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 2, pt: 1 }}>
        <ToggleButtonGroup exclusive size="small" value={mode} onChange={(_, v) => v && change(v as Mode)}>
          <ToggleButton value="configurator" sx={{ px: 1.75, py: 0.4, fontSize: 12, textTransform: 'none', color: '#94a3b8', borderColor: '#334155',
            '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>
            Configurator
          </ToggleButton>
          <ToggleButton value="saved" sx={{ px: 1.75, py: 0.4, fontSize: 12, textTransform: 'none', color: '#94a3b8', borderColor: '#334155',
            '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>
            Saved simulations
          </ToggleButton>
        </ToggleButtonGroup>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {mode === 'configurator' ? <ConfiguratorPanel /> : <ComparePanel />}
      </Box>
    </Box>
  );
};

export default CompareTab;
