import type { StripMotor, StripProject } from '../../lib/stripContext.ts';
import { motorLinkAvailability } from './motorAvailability.ts';
import { motorStatus } from './motorStatus.ts';
import type { ProjectMotor, ProjectSummary } from './types.ts';

/** The project and its motors as the strip under the tabs needs them (null: no project open).
 *  The project's motors list (`motors`) is loaded lazily with the Motors view; until then the summary's linked motors
 *  stand in (status unknown = current, points availability unknown).  Order is the project's order, so the last
 *  current one is the latest. */
export function projectStripContext(project: Pick<ProjectSummary, 'id' | 'title' | 'linked_motors'> | null, motors: readonly ProjectMotor[]): StripProject | null {
  if (!project) return null;
  const loaded = motors.filter((m) => m.kind !== 'history' && m.die && m.config);
  const list: StripMotor[] = loaded.length > 0
    ? loaded.map((m) => ({
      die: m.die, config: m.config, status: motorStatus(m),
      pointsReady: m.can_open_points === true || motorLinkAvailability(m, undefined, true).pointsDuty !== null,
    }))
    : (project.linked_motors ?? []).filter((m) => m.die && m.config).map((m) => ({ die: m.die, config: m.config }));
  return { id: project.id, title: project.title, motors: list };
}
