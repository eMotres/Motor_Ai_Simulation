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
import PointsConfigure from './PointsConfigure';
import StoredResultsDrawer from './StoredResultsDrawer';
import { useLoadedMotorView } from '../../lib/useLoadedMotorView';
import { configureShow } from '../../modules/development/pointsLimited';

// ONE display system for motors (owner 2026-10-26).  Whatever loaded the motor - the Motors tab or a project - Configure
// shows the loaded one.  With a full passport it is the ordinary Configurator; a motor that has no passport but stores
// computed duty points opens points-limited (the server says which: lib/useLoadedMotorView).  While the answer is not
// there (loading, failed, no loaded motor) the ordinary Configurator stays exactly as it was.
const CompareTab: React.FC = () => {
  const loaded = useLoadedMotorView();
  const pointsLimited = loaded.status === 'ready' && loaded.view !== null && configureShow(loaded.view) === 'points-limited';
  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column',
      bgcolor: 'var(--panel-2)', overflow: 'hidden' }}>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {pointsLimited && loaded.view
          ? <PointsConfigure view={loaded.view} loadedDuty={loaded.selection?.duty ?? null} />
          : <ConfiguratorPanel />}
      </Box>
      {loaded.status === 'ready' && loaded.view && !pointsLimited
        && <StoredResultsDrawer view={loaded.view} duty={loaded.selection?.duty ?? null} />}
    </Box>
  );
};

export default CompareTab;
