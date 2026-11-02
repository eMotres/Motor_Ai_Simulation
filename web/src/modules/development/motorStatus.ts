import type { ProjectMotor, ProjectSnapshot, ProjectStatus } from './types';

export type MotorStatus = 'current' | 'trial';

/** Older project records predate status and are current by default. */
export const motorStatus = (motor: ProjectMotor): MotorStatus => motor.status === 'trial' ? 'trial' : 'current';

export function visibleProjectMotors(motors: ProjectMotor[], showAll: boolean): ProjectMotor[] {
  return showAll ? motors : motors.filter((motor) => motorStatus(motor) === 'current');
}

export function visibleProjectVersions(motors: ProjectMotor[], snapshots: ProjectSnapshot[], showAll: boolean): ProjectMotor[] {
  const visible = visibleProjectMotors(motors, showAll);
  return showAll ? [...visible, ...snapshots.map((snapshot) => ({
    die: snapshot.die ?? '', config: snapshot.config ?? snapshot.id, kind: 'history' as const,
    snapshot_id: snapshot.id, created_at: snapshot.created_at, author_display_name: snapshot.author_display_name,
    note: snapshot.note, source: snapshot.source, results: snapshot.results,
  }))] : visible;
}

export const PROJECT_STATUSES: readonly ProjectStatus[] = ['spec', 'design', 'simulation', 'quote', 'prototype', 'production', 'closed'];
/** The engineering stages a customer may set; quote / prototype / production are MOTRES staff's (owner 2026-11-02). */
const CUSTOMER_STATUSES: readonly ProjectStatus[] = ['spec', 'design', 'simulation'];

/** The statuses the status Select offers - the server's rule (development.status_change_allowed): staff every status; a
 *  customer the engineering stages and closing, only while the project is in one of them (else just the current one). */
export function allowedProjectStatuses(staff: boolean, current: ProjectStatus): ProjectStatus[] {
  if (staff) return [...PROJECT_STATUSES];
  if (!CUSTOMER_STATUSES.includes(current)) return [current];
  return [...CUSTOMER_STATUSES, 'closed'];
}

export interface PendingProjectStatus {
  projectId: string;
  current: ProjectStatus;
  proposed: ProjectStatus;
}

export function proposeProjectStatus(projectId: string, current: ProjectStatus, proposed: ProjectStatus): PendingProjectStatus | null {
  return current === proposed ? null : { projectId, current, proposed };
}

export function cancelProjectStatus(_pending: PendingProjectStatus | null): null {
  return null;
}

export function pendingStatusForProject(pending: PendingProjectStatus | null, projectId: string): PendingProjectStatus | null {
  return pending?.projectId === projectId ? pending : null;
}

export function acceptedProjectStatus(pending: PendingProjectStatus | null, projectId: string, current: ProjectStatus): ProjectStatus | null {
  return pending?.projectId === projectId && pending.current === current ? pending.proposed : null;
}
