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
import { Box, CircularProgress, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import ConfiguratorPanel from './ConfiguratorPanel';
import PointsConfigure from './PointsConfigure';
import StoredResultsDrawer from './StoredResultsDrawer';
import { useLoadedMotorView } from '../../lib/useLoadedMotorView';
import { configureShow } from '../../modules/development/pointsLimited';

// ONE display system for motors (owner 2026-10-26).  Whatever loaded the motor - the Motors tab or a project - Configure
// shows the REQUESTED motor, drawn ONCE:
//   - the view of that motor is fetched at the click, in parallel with the machine load (lib/useLoadedMotorView);
//   - until it is here, and while the machine load of a full-passport motor still runs, a clean "loading <motor>" state is
//     drawn - never the previous motor's numbers, never the ordinary Configurator that would be swapped away a moment later;
//   - a points-limited motor is shown as soon as its view arrives (it needs none of the machine load, which goes on behind it);
//   - a motor with a passport gets the ordinary Configurator once its load has finished.
// With no motor requested or loaded the ordinary Configurator stays exactly as it was.
const CompareTab: React.FC = () => {
  const { t } = useTranslation('development');
  const loaded = useLoadedMotorView();
  const pointsLimited = loaded.status === 'ready' && loaded.view !== null && configureShow(loaded.view) === 'points-limited';
  const waiting = loaded.selection !== null && !pointsLimited && (loaded.status === 'loading' || loaded.applying);
  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column',
      bgcolor: 'var(--panel-2)', overflow: 'hidden' }}>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {waiting && loaded.selection
          ? (
            <Box role="status" data-testid="configure-loading" sx={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1.5, color: 'var(--text-3)' }}>
              <CircularProgress size={18} />
              <Typography sx={{ fontSize: 13 }}>{t('points.loadingMotor', { motor: `${loaded.selection.die} / ${loaded.selection.config}` })}</Typography>
            </Box>)
          : pointsLimited && loaded.view
            ? <PointsConfigure view={loaded.view} loadedDuty={loaded.selection?.duty ?? null} />
            : <ConfiguratorPanel />}
      </Box>
      {!waiting && loaded.status === 'ready' && loaded.view && !pointsLimited
        && <StoredResultsDrawer view={loaded.view} duty={loaded.selection?.duty ?? null} />}
    </Box>
  );
};

export default CompareTab;
