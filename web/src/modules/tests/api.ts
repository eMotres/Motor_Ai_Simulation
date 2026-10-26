// Tests section — fetch wrappers for /api/motor-tests/* (production side) and /api/projects/{id}/motors/tests (read-only
// project view).  The session token is added by the global fetch interceptor.
import type { CreateDutyAction, TestRecord, TestsPayload } from './types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const enc = encodeURIComponent;

export class TestsApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json().catch(() => null);
  if (!r.ok) {
    const d = j?.detail;
    const msg = typeof d === 'string' ? d : typeof d?.message === 'string' ? d.message : `HTTP ${r.status}`;
    throw new TestsApiError(r.status, msg);
  }
  return j as T;
}

const base = (die: string, config: string) => `/api/motor-tests/${enc(die)}/${enc(config)}`;

export const testsApi = {
  get: (die: string, config: string) => call<TestsPayload>('GET', base(die, config)),
  add: (die: string, config: string, rec: Partial<TestRecord>) => call<TestRecord>('POST', `${base(die, config)}/tests`, rec),
  edit: (die: string, config: string, id: string, rec: Partial<TestRecord>) =>
    call<TestRecord>('PUT', `${base(die, config)}/tests/${enc(id)}`, rec),
  remove: (die: string, config: string, id: string) => call<{ ok: boolean }>('DELETE', `${base(die, config)}/tests/${enc(id)}`),
  setVisibleToCustomer: (die: string, config: string, value: boolean) =>
    call<{ tests_visible_to_customer: boolean }>('PATCH', `${base(die, config)}/settings`, { tests_visible_to_customer: value }),
  /** Read-only project view (Development); 404 = nothing the caller may see. */
  project: (projectId: string, die: string, config: string, showAll = false) =>
    call<TestsPayload>('GET', `/api/projects/${enc(projectId)}/motors/tests?die=${enc(die)}&config=${enc(config)}${showAll ? '&show_all=true' : ''}`),
  projectIndex: async (projectId: string) =>
    (await call<{ motors: Array<{ die: string; config: string; count: number; visible_to_customer: boolean }> }>(
      'GET', `/api/projects/${enc(projectId)}/motors/tests`)).motors,
  /** "Compute it": add the missing operating point as a duty of the configuration (no solve; run it in Simulation). */
  createDuty: (action: CreateDutyAction) => call<unknown>('POST', '/api/family/duty', action.body),
};
