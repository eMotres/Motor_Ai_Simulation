import { useEffect, useState } from 'react';
import { Button, Chip, Tooltip } from '@mui/material';
import { applyCatalogDuty } from '../../lib/catalogDutyApply';
import { canWriteServer } from '../../lib/localAuth';
import { useUIStore } from '../../stores/uiStore';
import { usePointsConfigureStore } from '../../stores/pointsConfigureStore';
import type { ProjectMotor } from './types';
import { motorLinkAvailability } from './motorAvailability';

export function MotorOpenActions({ motor, duty, allowMotorDefault = false, engineer, t, projectId, showAll = false }: { motor: ProjectMotor; duty?: string; allowMotorDefault?: boolean; engineer: boolean; t: (key: string) => string; projectId?: string; showAll?: boolean }) {
  const [error, setError] = useState('');
  useEffect(() => { setError(''); }, [motor.die, motor.config]);
  const availability = motorLinkAvailability(motor, duty, allowMotorDefault);
  const open = (mode: 'configure' | 'simulation', targetDuty: string) => {
    setError('');
    if (mode === 'configure') usePointsConfigureStore.getState().close();   // the full passport path replaces a points-only card
    void applyCatalogDuty(motor.die, motor.config, targetDuty, canWriteServer(), () => useUIStore.getState().setActiveTab((mode === 'simulation' ? 'simulation' : 'compare') as never)).catch(() => setError(t('configureFailed')));
  };
  return <>
    {availability.configureDuty && <Button size="small" onClick={() => open('configure', availability.configureDuty!)}>{t('openConfigure')}</Button>}
    {projectId && availability.pointsDuty && <Tooltip title={t('points.openTip')}><Button size="small" onClick={() => {
      usePointsConfigureStore.getState().open({ projectId, die: motor.die, config: motor.config, duty: availability.pointsDuty, showAll });
      useUIStore.getState().setActiveTab('compare' as never);
    }}>{t('openConfigure')}</Button></Tooltip>}
    {availability.simulationDuty
      ? engineer && <Button size="small" onClick={() => open('simulation', availability.simulationDuty!)}>{t('openSimulation')}</Button>
      : <>
        {engineer && availability.loadDuty && <Tooltip title={t('loadSimulationTip')}><Button size="small" onClick={() => open('simulation', availability.loadDuty!)}>{t('loadSimulation')}</Button></Tooltip>}
        <Tooltip title={t('notComputedTip')}><Chip size="small" label={t('notComputed')} sx={{ color: 'var(--text-4)', bgcolor: 'var(--panel-2)' }} /></Tooltip>
      </>}
    {error && <Tooltip title={error}><Chip size="small" color="error" label="!" /></Tooltip>}
  </>;
}
