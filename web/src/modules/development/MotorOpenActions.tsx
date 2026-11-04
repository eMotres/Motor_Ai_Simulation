import { useEffect, useState } from 'react';
import { Button, Chip, Tooltip } from '@mui/material';
import { applyCatalogDuty } from '../../lib/catalogDutyApply';
import { canWriteServer } from '../../lib/localAuth';
import { useUIStore } from '../../stores/uiStore';
import type { ProjectMotor } from './types';
import { motorLinkAvailability } from './motorAvailability';
import { ReadOnlySimulationDialog } from '../../components/simulation/ReadOnlySimulationView';
import { canOpenReadOnlySimulation } from '../../lib/simReadOnly';

/** The open buttons of one project motor row.  Configure has no button here (owner 2026-10-28): a click on the row itself
 *  opens the motor in Configure (DevelopmentPage -> lib/openProjectMotor), so a second "Open in Configure" only repeated it.
 *  Simulation keeps its own load. */
export function MotorOpenActions({ motor, duty, allowMotorDefault = false, engineer, t, projectId, showAll = false }: { motor: ProjectMotor; duty?: string; allowMotorDefault?: boolean; engineer: boolean; t: (key: string) => string; projectId?: string; projectTitle?: string; showAll?: boolean }) {
  const [error, setError] = useState('');
  const [viewOpen, setViewOpen] = useState(false);
  useEffect(() => { setError(''); }, [motor.die, motor.config]);
  const availability = motorLinkAvailability(motor, duty, allowMotorDefault);
  // Everyone who works on the project may LOOK at its computed points in the Simulation view (owner 2026-11-04); only the
  // owner runs.  Staff keep the live tab's own door above.  The view is read-only on the server too (GET only).
  const stored = duty ? motor.results?.find((item) => item.duty === duty) : motor.results?.find((item) => item.has_result === true);
  const viewDuty = stored?.has_result === true && typeof stored.duty === 'string' && stored.duty.trim() ? stored.duty : null;
  const showView = !!projectId && motor.available !== false && canOpenReadOnlySimulation({ staff: engineer, hasPoint: viewDuty != null });
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
    {showView && <>
      <Tooltip title={t('simView.buttonTip')}><Button size="small" onClick={() => setViewOpen(true)}>{t('simView.button')}</Button></Tooltip>
      {viewOpen && <ReadOnlySimulationDialog open onClose={() => setViewOpen(false)} projectId={projectId!} die={motor.die} config={motor.config} duty={viewDuty} showAll={showAll} />}
    </>}
    {error && <Tooltip title={error}><Chip size="small" color="error" label="!" /></Tooltip>}
  </>;
}
