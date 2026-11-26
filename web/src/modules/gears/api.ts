// Gear module calls: /api/gears (staff only; auth headers come from the global fetch wrapper).
// Routes: POST template | evaluate | assembly | pose, GET catalogues.  Every POST answers the core's schema-v1 record;
// a refused body answers 422 {"detail":{"error":"validation","errors":[{path,code,message}]}}, path being an RFC 6901 pointer.
import type { CataloguesAnswer, FieldError, GearboxResponse, PlainBody, RequestRecord } from './types';

const API = (import.meta.env?.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${String(API).replace(/\/$/, '')}/api/gears`;

export class GearsApiError extends Error {
  status: number;
  errors: FieldError[];
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
  return new GearsApiError(status, typeof detail === 'string' ? detail : `HTTP ${status}`);
}

async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
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
