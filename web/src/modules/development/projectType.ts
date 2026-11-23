// The project type (coordinator 2026-11-23): Motor (every earlier project; the key is absent) or Controller (internal for now).
// Staff choose it in the New project dialog; afterwards only an admin changes it, because it swaps the whole specification form.
const API = (import.meta.env?.VITE_API_URL ?? 'http://localhost:8001') as string;

export type ProjectType = 'motor' | 'controller';
export const PROJECT_TYPES: readonly ProjectType[] = ['motor', 'controller'];

/** The type of a project summary: `controller` only when the server says so, else `motor`. */
export function projectTypeOf(project: object | null | undefined): ProjectType {
  return (project as { project_type?: unknown } | null | undefined)?.project_type === 'controller' ? 'controller' : 'motor';
}

/** What the create dialog adds to its payload: nothing for a motor project (the request stays as before). */
export function projectTypePayload(staff: boolean, type: ProjectType): { project_type?: 'controller' } {
  return staff && type === 'controller' ? { project_type: 'controller' } : {};
}

/** PATCH the type of a project (admins only; the server refuses everybody else with 403). */
export async function setProjectType<T>(projectId: string, type: ProjectType): Promise<T> {
  const r = await fetch(`${API}/api/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_type: type }),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${r.status}`);
  }
  return data as T;
}

/** The project list in two groups, Motors then Controllers (owner 2026-11-23), each keeping the order it was given in; an empty
 *  group is left out. */
export function groupByType<P extends object>(projects: readonly P[]): { type: ProjectType; projects: P[] }[] {
  return PROJECT_TYPES.map((type) => ({ type, projects: projects.filter((p) => projectTypeOf(p) === type) })).filter((g) => g.projects.length > 0);
}
