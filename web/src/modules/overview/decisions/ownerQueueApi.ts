/** Owner-queue calls (admin side).  The session header is added by the app's global fetch wrapper, like every other page. */
import type { OqCount, OqItem, OqPriority } from './model.ts';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export class OqHttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}/api/owner-queue${path}`, {
    method, cache: 'no-store', ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  if (!r.ok) {
    let why = `HTTP ${r.status}`;
    try { const j = await r.json() as { detail?: unknown }; if (typeof j.detail === 'string') why = j.detail; } catch { /* keep the status */ }
    throw new OqHttpError(r.status, why);
  }
  return await r.json() as T;
}

export interface OqTokenRec { name: string; created_at: string; created_by: string; revoked_at: string | null; last_used_at: string | null }

export const ownerQueueApi = {
  items: (p: { state?: string; project_id?: string; asked_by?: string; q?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v) as [string, string][]).toString();
    return call<{ items: OqItem[] }>('GET', `/items${qs ? `?${qs}` : ''}`).then((r) => r.items);
  },
  priorities: () => call<{ priorities: Record<string, { priority: OqPriority }> }>('GET', '/priorities').then((r) => r.priorities),
  setPriority: (project_id: string, priority: OqPriority) => call<{ project_id: string; priority: OqPriority }>('POST', '/priority', { project_id, priority }),
  count: () => call<OqCount>('GET', '/count'),
  answer: (id: string, body: { option?: string; text?: string }) => call<OqItem>('POST', `/items/${encodeURIComponent(id)}/answer`, body),
  tokens: () => call<{ tokens: OqTokenRec[] }>('GET', '/tokens').then((r) => r.tokens),
  createToken: (name: string) => call<{ name: string; token: string }>('POST', '/tokens', { name }),
  revokeToken: (name: string) => call<{ ok: boolean }>('POST', `/tokens/${encodeURIComponent(name)}/revoke`),
};
