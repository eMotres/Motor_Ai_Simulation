export interface ProjectCompletenessLike {
  filled_required?: number; total_required?: number; filled?: number; required?: number;
}

export interface AdminProjectMemberLike { email: string; role: 'customer' | 'engineer' | 'owner'; pending?: boolean }

export const projectAdminMembers = (
  members: AdminProjectMemberLike[] = [], pendingInvites: AdminProjectMemberLike[] = [],
): AdminProjectMemberLike[] => {
  const byEmail = new Map<string, AdminProjectMemberLike>();
  for (const member of members) byEmail.set(member.email.toLowerCase(), { ...member, pending: false });
  for (const invite of pendingInvites) {
    const key = invite.email.toLowerCase();
    if (!byEmail.has(key)) byEmail.set(key, { ...invite, pending: true });
  }
  return [...byEmail.values()];
};

export const projectRoleBody = (email: string, role: AdminProjectMemberLike['role']) => ({ email, role });
export const projectOpenIntent = (projectId: string) => ({ storageKey: projectOpenStorageKey, projectId });
export { projectOpenEvent, projectOpenStorageKey };

export const projectCompletenessLabel = (value?: ProjectCompletenessLike): string => {
  const filled = value?.filled_required ?? value?.filled;
  const total = value?.total_required ?? value?.required;
  return filled == null || total == null ? '—' : `${filled}/${total}`;
};

export const validProjectId = (value: string): boolean => /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value.trim().toLowerCase());

export const adminProjectPaths = {
  members: (id: string) => `/api/admin/projects/${encodeURIComponent(id)}/members`,
  member: (id: string, email: string) => `/api/admin/projects/${encodeURIComponent(id)}/members?email=${encodeURIComponent(email)}`,
  motors: (id: string) => `/api/admin/projects/${encodeURIComponent(id)}/motors`,
  motor: (id: string, die: string, config: string) => `/api/admin/projects/${encodeURIComponent(id)}/motors?die=${encodeURIComponent(die)}&config=${encodeURIComponent(config)}`,
};

export const projectMotorKey = (die: string, config: string): string => `${die}/${config}`;
import { projectOpenEvent, projectOpenStorageKey } from '../../modules/development/projectOpenIntent.ts';
