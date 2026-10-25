// Open a PROJECT motor: exactly what loading it from the Motors tab does (owner 2026-10-26: one display system).
//
// There is no second "points-only card" path any more.  A click on a project motor (the Development motors table row, the
// project's open buttons, a strip chip) loads the motor through `applyCatalogDuty` - the Motors tab's own entry - and shows
// Configure.  Configure decides how to display it: the full passport when the motor has one, the POINTS-LIMITED view of the
// stored computed points when it has none (components/compare/CompareTab, modules/development/pointsLimited).  The project
// is remembered so the Motors tab highlights its motors (stores/projectFocusStore).
import { applyCatalogDuty } from './catalogDutyApply';
import { canWriteServer } from './localAuth';
import { useProjectFocusStore } from '../stores/projectFocusStore';
import { useUIStore } from '../stores/uiStore';
import { dutyToOpen } from '../modules/development/pointsLimited.ts';
import { developmentApi } from '../modules/development/api';
import type { ProjectMotor } from '../modules/development/types';

type OpenableMotor = Pick<ProjectMotor, 'die' | 'config' | 'configure_duty' | 'default_duty' | 'duties' | 'results'>;

/** Load `motor` of project `project` (duty: the one asked for, else the best default) and switch to Configure.
 *  Rejects with the load's own message; resolves with the duty that was loaded. */
export async function openProjectMotor(project: { id: string; title?: string | null }, motor: OpenableMotor, preferredDuty?: string | null): Promise<string> {
  const duty = dutyToOpen(motor, preferredDuty);
  if (!duty) throw new Error('no duty to load');
  const known = useProjectFocusStore.getState().focus;
  useProjectFocusStore.getState().set({ id: project.id, title: project.title ?? (known?.id === project.id ? known.title : null) });
  await applyCatalogDuty(motor.die, motor.config, duty, canWriteServer(), () => useUIStore.getState().setActiveTab('compare' as never));
  return duty;
}

/** The same open for a motor known only by name (a strip chip): its duties come from the project's motor list. */
export async function openProjectMotorByRef(project: { id: string; title?: string | null }, ref: { die: string; config: string }): Promise<string> {
  const motors = await developmentApi.motors(project.id, true);
  const found = motors.find((m) => m.die === ref.die && m.config === ref.config);
  if (!found) throw new Error('motor not found in this project');
  return openProjectMotor(project, found);
}
