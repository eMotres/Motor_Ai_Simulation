import { useEffect, useState } from 'react';
import { Button, Chip, Tooltip } from '@mui/material';
import { applyCatalogDuty } from '../../lib/catalogDutyApply';
import { canWriteServer } from '../../lib/localAuth';
import { useUIStore } from '../../stores/uiStore';
import type { ProjectMotor } from './types';
import { motorLinkAvailability } from './motorAvailability';

/** The open buttons of one project motor row.  Configure has no button here (owner 2026-10-28): a click on the row itself
 *  opens the motor in Configure (DevelopmentPage -> lib/openProjectMotor), so a second "Open in Configure" only repeated it.
 *  Simulation keeps its own load. */
export function MotorOpenActions({ motor, duty, allowMotorDefault = false, engineer, t }: { motor: ProjectMotor; duty?: string; allowMotorDefault?: boolean; engineer: boolean; t: (key: string) => string; projectId?: string; projectTitle?: string; showAll?: boolean }) {
  const [error, setError] = useState('');
  useEffect(() => { setError(''); }, [motor.die, motor.config]);
  const availability = motorLinkAvailability(motor, duty, allowMotorDefault);
  const openSimulation = (targetDuty: string) => {
    setError('');
    void applyCatalogDuty(motor.die, motor.config, targetDuty, canWriteServer(), () => useUIStore.getState().setActiveTab('simulation' as never)).catch(() => setError(t('configureFailed')));
  };
  return <>
    {availability.simulationDuty
      ? engineer && <Button size="small" onClick={() => openSimulation(availability.simulationDuty!)}>{t('openSimulation')}</Button>
      : <>
        {engineer && availability.loadDuty && <Tooltip title={t('loadSimulationTip')}><Button size="small" onClick={() => openSimulation(availability.loadDuty!)}>{t('loadSimulation')}</Button></Tooltip>}
        <Tooltip title={t('notComputedTip')}><Chip size="small" label={t('notComputed')} sx={{ color: 'var(--text-4)', bgcolor: 'var(--panel-2)' }} /></Tooltip>
      </>}
    {error && <Tooltip title={error}><Chip size="small" color="error" label="!" /></Tooltip>}
  </>;
}
