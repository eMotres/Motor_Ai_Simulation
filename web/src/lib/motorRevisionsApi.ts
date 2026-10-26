/** `/api/motor-revisions` from the browser (product revisions Rev A, Rev B ...; pure helpers: lib/motorRevisions.ts). */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export class RevisionApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new RevisionApiError(r.status, typeof detail === 'string' ? detail : `HTTP ${r.status}`);
  }
  return data as T;
}

export interface RevisionWriteAnswer { ok: boolean; rev: string; die?: string; config?: string; project_id?: string | null }

export const motorRevisionsApi = {
  index: async () => (await call<{ revisions?: unknown[] }>('GET', '/api/motor-revisions/index')).revisions ?? [],
  assign: (body: { die: string; config: string; project_id?: string; agreed_with_client?: boolean }) =>
    call<RevisionWriteAnswer & { lineage: { id: string } }>('POST', '/api/motor-revisions/assign', body),
  create: (lineage: string, body: { reason: string; new_die?: boolean; from_rev?: string }) =>
    call<RevisionWriteAnswer>('POST', `/api/motor-revisions/${encodeURIComponent(lineage)}/new`, body),
};
