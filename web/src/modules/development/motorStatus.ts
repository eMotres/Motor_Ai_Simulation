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

/** The statuses the status Select offers - the server's rule (development.status_change_allowed): staff every status; a customer
 *  none (owner 2026-11-06: the stage is MOTRES's to move), so just the current one.  Closing an own open project is a button of
 *  its own on the page, not a menu entry. */
export function allowedProjectStatuses(staff: boolean, current: ProjectStatus): ProjectStatus[] {
  return staff ? [...PROJECT_STATUSES] : [current];
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
