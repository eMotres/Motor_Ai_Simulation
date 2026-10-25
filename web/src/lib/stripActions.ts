// What a click on a project-motor chip of the strip does: the same as the project page's "Configure" for a motor
// with stored points - the points-only Configure card (stores/pointsConfigureStore).  It never loads the live machine.
import { usePointsConfigureStore } from '../stores/pointsConfigureStore';
import { useUIStore } from '../stores/uiStore';
import type { StripMotor } from './stripContext';

export function applyPointsOpen(projectId: string, motor: StripMotor): void {
  usePointsConfigureStore.getState().open({ projectId, die: motor.die, config: motor.config, duty: null, showAll: motor.status === 'trial' });
  useUIStore.getState().setActiveTab('compare' as never);
}
