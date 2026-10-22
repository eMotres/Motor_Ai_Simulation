import type { DevelopmentSchema, ProjectSummary, ProjectThreadMessage, ProjectMotor, SpecState, SpecSnapshot } from './types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const enc = encodeURIComponent;

export class DevelopmentApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) { super(message); this.status = status; this.body = body; }
}
async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, { method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : `HTTP ${r.status}`;
    throw new DevelopmentApiError(r.status, message, data);
  }
  return data as T;
}
const p = (id: string) => `/api/projects/${enc(id)}`;

export const developmentApi = {
  list: async () => (await call<{ projects: ProjectSummary[] }>('GET', '/api/projects')).projects,
  schema: () => call<DevelopmentSchema>('GET', '/api/projects/schema'),
  create: (v: { id: string; title: string; customer_org: string; owner_engineer?: string }) => call<ProjectSummary>('POST', '/api/projects', v),
  get: (id: string) => call<ProjectSummary>('GET', p(id)),
  update: (id: string, v: Partial<Pick<ProjectSummary, 'title' | 'customer_org' | 'owner_engineer' | 'status' | 'notes'>>) => call<ProjectSummary>('PATCH', p(id), v),
  spec: (id: string) => call<SpecState>('GET', `${p(id)}/spec`),
  saveDraft: (id: string, draft: Record<string, unknown>, revision: number) => call<SpecState>('PUT', `${p(id)}/spec`, { draft, revision }),
  submitSpec: (id: string, revision: number) => call<{ version: number; revision: number; spec: Record<string, unknown>; completeness: SpecState['completeness'] }>('POST', `${p(id)}/spec/submit`, { revision }),
  versions: (id: string) => call<{ versions: SpecState['versions'] }>('GET', `${p(id)}/spec/versions`),
  version: (id: string, n: number) => call<SpecSnapshot>('GET', `${p(id)}/spec/versions/${n}`),
  thread: async (id: string) => (await call<{ messages: ProjectThreadMessage[] }>('GET', `${p(id)}/thread`)).messages,
  postMessage: (id: string, text: string, field_ref?: string) => call<ProjectThreadMessage>('POST', `${p(id)}/thread`, { text, ...(field_ref ? { field_ref } : {}) }),
  invite: (id: string, email: string, role: string) => call<unknown>('POST', `${p(id)}/members`, { email, role }),
  motors: async (id: string) => (await call<{ motors: ProjectMotor[] }>('GET', `${p(id)}/motors`)).motors,
  linkMotor: (id: string, die: string, config: string) => call<unknown>('POST', `${p(id)}/motors`, { die, config }),
  unlinkMotor: (id: string, die: string, config: string) => call<unknown>('DELETE', `${p(id)}/motors?die=${enc(die)}&config=${enc(config)}`),
};
