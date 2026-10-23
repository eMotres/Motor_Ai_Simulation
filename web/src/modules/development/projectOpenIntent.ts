export const projectOpenStorageKey = 'development.openProject';
export const projectOpenEvent = 'development:open-project';

export interface ProjectOpenStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** Claim one queued admin-to-Development navigation without consuming a newer intent. */
export function claimProjectOpenIntent(storage: ProjectOpenStorage, expectedId?: string): string | null {
  const projectId = storage.getItem(projectOpenStorageKey);
  if (!projectId || (expectedId !== undefined && projectId !== expectedId)) return null;
  storage.removeItem(projectOpenStorageKey);
  return projectId;
}

/** Put a failed navigation back only when no later admin action has replaced it. */
export function restoreProjectOpenIntent(storage: ProjectOpenStorage, projectId: string): void {
  if (!storage.getItem(projectOpenStorageKey)) storage.setItem(projectOpenStorageKey, projectId);
}
