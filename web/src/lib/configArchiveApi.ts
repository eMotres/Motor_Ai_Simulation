/** `/api/config-archive` from the browser (verified admins): archive ONE configuration of a die, list and restore (owner
 *  2026-11-22: one motor per project, the rest are archived; never deleted).  An archived configuration leaves every listing;
 *  a non-admin sees it as a missing one. */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export type ConfigRefusalCode = 'project_link' | 'production' | 'running_job';
export interface ArchivedConfig { die: string; config: string; by: string; at: string; reason?: string; /** what "Open" loads */ duty?: string | null }

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

const at = (die: string, cfg: string) => `/api/config-archive/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}`;

export const configArchiveApi = {
  list: () => call<{ configs: ArchivedConfig[] }>('GET', '/api/config-archive'),
  archivePath: (die: string, cfg: string) => `${at(die, cfg)}/archive`,
  restorePath: (die: string, cfg: string) => `${at(die, cfg)}/restore`,
};
