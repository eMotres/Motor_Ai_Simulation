import { useEffect, useState } from 'react';
import { Button, Chip, Tooltip } from '@mui/material';
import { applyCatalogDuty } from '../../lib/catalogDutyApply';
import { canWriteServer } from '../../lib/localAuth';
import { useUIStore } from '../../stores/uiStore';
import type { ProjectMotor } from './types';
import { motorLinkAvailability } from './motorAvailability';

export function MotorOpenActions({ motor, duty, allowMotorDefault = false, engineer, t }: { motor: ProjectMotor; duty?: string; allowMotorDefault?: boolean; engineer: boolean; t: (key: string) => string }) {
  const [error, setError] = useState('');
  useEffect(() => { setError(''); }, [motor.die, motor.config]);
  const availability = motorLinkAvailability(motor, duty, allowMotorDefault);
  const open = (mode: 'configure' | 'simulation', targetDuty: string) => {
    setError('');
    void applyCatalogDuty(motor.die, motor.config, targetDuty, canWriteServer(), () => useUIStore.getState().setActiveTab((mode === 'simulation' ? 'simulation' : 'compare') as never)).catch(() => setError(t('configureFailed')));
  };
  return <>
    {availability.configureDuty && <Button size="small" onClick={() => open('configure', availability.configureDuty!)}>{t('openConfigure')}</Button>}
    {availability.simulationDuty
      ? engineer && <Button size="small" onClick={() => open('simulation', availability.simulationDuty!)}>{t('openSimulation')}</Button>
      : <Tooltip title={t('notComputedTip')}><Chip size="small" label={t('notComputed')} sx={{ color: 'var(--text-4)', bgcolor: 'var(--panel-2)' }} /></Tooltip>}
    {error && <Tooltip title={error}><Chip size="small" color="error" label="!" /></Tooltip>}
  </>;
}
