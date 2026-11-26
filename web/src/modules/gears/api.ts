// Gear module calls: /api/gears (staff only; auth headers come from the global fetch wrapper).
// Routes: POST template | evaluate | assembly | pose, GET catalogues.  Every POST answers the core's schema-v1 record;
// a refused body answers 422 {"detail":{"error":"validation","errors":[{path,code,message}]}}, path being an RFC 6901 pointer.
import type { DrivetrainRequest, DrivetrainResponse } from './drivetrain';
import type { BoxList, BoxRecord, CataloguesAnswer, FieldError, GearboxResponse, PlainBody, RequestRecord } from './types';

const API = (import.meta.env?.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${String(API).replace(/\/$/, '')}/api/gears`;

export class GearsApiError extends Error {
  status: number;
  errors: FieldError[];
  /** the store's error code ("stale_version", "not_found", ...) when the body carries one */
  code: string | null = null;
  /** the stored version a stale save lost against (409 stale_version) */
  currentVersion: number | null = null;
  constructor(status: number, message: string, errors: FieldError[] = []) {
    super(message); this.status = status; this.errors = errors;
  }
}

/** The field errors of an error body (pure; tests call it). */
export function parseErrorBody(status: number, json: unknown): GearsApiError {
  const detail = (json as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === 'object' && Array.isArray((detail as { errors?: unknown }).errors)) {
    const errors = ((detail as { errors: unknown[] }).errors)
      .filter((e): e is Record<string, unknown> => !!e && typeof e === 'object')
      .map((e) => ({ path: String(e.path ?? ''), code: String(e.code ?? 'invalid_value'), message: String(e.message ?? '') }));
    return new GearsApiError(status, errors[0]?.message || `HTTP ${status}`, errors);
  }
  if (detail && typeof detail === 'object' && typeof (detail as { error?: unknown }).error === 'string') {
    const d = detail as { error: string; message?: unknown; current_version?: unknown };
    const err = new GearsApiError(status, typeof d.message === 'string' ? d.message : d.error);
    err.code = d.error;
    if (typeof d.current_version === 'number') err.currentVersion = d.current_version;
    return err;
  }
  return new GearsApiError(status, typeof detail === 'string' ? detail : `HTTP ${status}`);
}

async function call<T>(method: 'GET' | 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method, signal, cache: 'no-store',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let j: unknown = null;
  try { j = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) throw parseErrorBody(r.status, j);
  return j as T;
}

export const gearsApi = {
  /** plain editor fields -> a complete canonical request record (the decoder accepts nothing hand-built) */
  template: (body: PlainBody, signal?: AbortSignal) => call<RequestRecord>('POST', '/template', body, signal),
  evaluate: (request: RequestRecord, signal?: AbortSignal) => call<GearboxResponse>('POST', '/evaluate', request, signal),
  assembly: (request: RequestRecord, signal?: AbortSignal) => call<GearboxResponse>('POST', '/assembly', request, signal),
  pose: (request: RequestRecord, signal?: AbortSignal) => call<GearboxResponse>('POST', '/pose', request, signal),
  catalogues: (signal?: AbortSignal) => call<CataloguesAnswer>('GET', '/catalogues', undefined, signal),
};

const q = (project: string | null | undefined, extra: Record<string, string | number | null | undefined> = {}): string => {
  const p = new URLSearchParams();
  if (project) p.set('project', project);
  for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined) p.set(k, String(v));
  const text = p.toString();
  return text ? `?${text}` : '';
};

/** Saved gearbox records (/api/gears/boxes).  `project` is the scope: blank is the workspace. */
export const boxesApi = {
  list: (project: string | null, state: 'active' | 'archived' | 'all' = 'all', signal?: AbortSignal) =>
    call<BoxList>('GET', `/boxes${q(project, { state })}`, undefined, signal),
  get: (project: string | null, id: string, signal?: AbortSignal) => call<BoxRecord>('GET', `/boxes/${encodeURIComponent(id)}${q(project)}`, undefined, signal),
  create: (project: string | null, body: { name: string; request: RequestRecord; response?: unknown }) =>
    call<BoxRecord>('POST', `/boxes${q(project)}`, body),
  update: (project: string | null, id: string, body: { version: number; name?: string; request?: RequestRecord; response?: unknown }) =>
    call<BoxRecord>('PUT', `/boxes/${encodeURIComponent(id)}${q(project)}`, body),
  archive: (project: string | null, id: string, version: number) => call<BoxRecord>('DELETE', `/boxes/${encodeURIComponent(id)}${q(project, { version })}`),
  restore: (project: string | null, id: string, version: number) => call<BoxRecord>('POST', `/boxes/${encodeURIComponent(id)}/restore${q(project, { version })}`),
};

export const drivetrainApi = {
  /** one gearbox behind one motor configuration's SAVED points at output load rows (read only; nothing is solved) */
  run: (body: DrivetrainRequest, signal?: AbortSignal) => call<DrivetrainResponse>('POST', '/drivetrain', body, signal),
};
