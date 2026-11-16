/** `/api/die-archive` from the browser (verified admins): archive a workspace / published die, list and restore.  A shared die
 *  keeps its Retire (`/api/family/die/{die}/retire`), whose restore is `/api/family/die/{die}/restore`. */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export type RefusalCode = 'running_job' | 'open_production' | 'project_link' | 'product_revision';
export interface Refusal { code: RefusalCode; names: string[] }
export interface ArchiveCheck { die: string; layer: string; archivable: boolean; archived: boolean; refusals: Refusal[] }
export interface ArchivedDie { die: string; layer: string; by: string; at: string; reason?: string; configs?: string[] | null;
  /** a shared die retired for all users (a tombstone), restored through the family route */
  retired?: boolean }

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${r.status}`);
  }
  return data as T;
}

const at = (die: string) => `/api/die-archive/${encodeURIComponent(die)}`;

export const dieArchiveApi = {
  list: () => call<{ dies: ArchivedDie[] }>('GET', '/api/die-archive'),
  check: (die: string) => call<ArchiveCheck>('GET', at(die)),
  archive: (die: string, reason = '') => call<ArchivedDie>('POST', `${at(die)}/archive`, { confirm: die, reason }),
  restore: (d: Pick<ArchivedDie, 'die' | 'retired'>) => call<unknown>('POST', d.retired
    ? `/api/family/die/${encodeURIComponent(d.die)}/restore` : `${at(d.die)}/restore`, d.retired ? {} : undefined),
};
