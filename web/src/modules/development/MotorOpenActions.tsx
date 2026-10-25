import { useEffect, useState } from 'react';
import { Button, Chip, Tooltip } from '@mui/material';
import { applyCatalogDuty } from '../../lib/catalogDutyApply';
import { canWriteServer } from '../../lib/localAuth';
import { openProjectMotor } from '../../lib/openProjectMotor';
import { useUIStore } from '../../stores/uiStore';
import type { ProjectMotor } from './types';
import { motorLinkAvailability } from './motorAvailability';

/** The open buttons of one project motor row.  ONE load path (owner 2026-10-26): "Open in Configure" loads the motor
 *  exactly as the Motors tab does (lib/openProjectMotor -> applyCatalogDuty); Configure then shows the full passport or,
 *  for a motor with computed points only, the points-limited view.  Simulation keeps its own load. */
export function MotorOpenActions({ motor, duty, allowMotorDefault = false, engineer, t, projectId, projectTitle }: { motor: ProjectMotor; duty?: string; allowMotorDefault?: boolean; engineer: boolean; t: (key: string) => string; projectId?: string; projectTitle?: string; showAll?: boolean }) {
  const [error, setError] = useState('');
  useEffect(() => { setError(''); }, [motor.die, motor.config]);
  const availability = motorLinkAvailability(motor, duty, allowMotorDefault);
  const configureDuty = availability.configureDuty ?? availability.pointsDuty;
  const openSimulation = (targetDuty: string) => {
    setError('');
    void applyCatalogDuty(motor.die, motor.config, targetDuty, canWriteServer(), () => useUIStore.getState().setActiveTab('simulation' as never)).catch(() => setError(t('configureFailed')));
  };
  const openConfigure = (targetDuty: string) => {
    setError('');
    if (projectId) void openProjectMotor({ id: projectId, title: projectTitle }, motor, targetDuty).catch(() => setError(t('configureFailed')));
    else void applyCatalogDuty(motor.die, motor.config, targetDuty, canWriteServer(), () => useUIStore.getState().setActiveTab('compare' as never)).catch(() => setError(t('configureFailed')));
  };
  return <>
    {configureDuty && (availability.configureDuty
      ? <Button size="small" onClick={() => openConfigure(configureDuty)}>{t('openConfigure')}</Button>
      : <Tooltip title={t('points.openTip')}><Button size="small" onClick={() => openConfigure(configureDuty)}>{t('openConfigure')}</Button></Tooltip>)}
    {availability.simulationDuty
      ? engineer && <Button size="small" onClick={() => openSimulation(availability.simulationDuty!)}>{t('openSimulation')}</Button>
      : <>
        {engineer && availability.loadDuty && <Tooltip title={t('loadSimulationTip')}><Button size="small" onClick={() => openSimulation(availability.loadDuty!)}>{t('loadSimulation')}</Button></Tooltip>}
        <Tooltip title={t('notComputedTip')}><Chip size="small" label={t('notComputed')} sx={{ color: 'var(--text-4)', bgcolor: 'var(--panel-2)' }} /></Tooltip>
      </>}
    {error && <Tooltip title={error}><Chip size="small" color="error" label="!" /></Tooltip>}
  </>;
}
