import { approveBody, settingsPath, type DigestSettingsView, type DigestSummary, type DigestTarget } from './digestModel';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, init);
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { const j = await r.json(); if (typeof j?.detail === 'string') detail = j.detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export interface DigestPreview { to: string; subject: string; text: string; html: string; recipients: string[]; content_hash: string }
export interface DigestDraftDetail extends Omit<DigestSummary, 'items' | 'recipients'> {
  items: Array<{ kind: string; ts: string; en: string; zh: string; link: string | null; requires: string[] }>;
  more: number; recipients: Array<{ email: string; name: string; role: string; lang: string }>;
  skipped: Array<{ email: string; reason: string }>; text_body: string; content_hash: string;
}

export const digestApi = {
  settings: (t: DigestTarget) => call<DigestSettingsView>(settingsPath(t)),
  saveSettings: (t: DigestTarget, change: Record<string, unknown>) => call<DigestSettingsView>(settingsPath(t), json('PUT', change)),
  list: () => call<{ pending: DigestSummary[]; history: DigestSummary[]; smtp_configured: boolean }>('/api/admin/digests'),
  detail: (id: string) => call<DigestDraftDetail>(`/api/admin/digests/${encodeURIComponent(id)}`),
  preview: (id: string, recipient: string) => call<DigestPreview>(`/api/admin/digests/${encodeURIComponent(id)}/preview?recipient=${encodeURIComponent(recipient)}`),
  /** THE approval - the only call that sends.  Reached from one button, behind a confirmation. */
  send: (id: string, contentHash: string) => call<{ sent: number; failed: number; skipped: Array<{ email: string; reason: string }>; aborted: string; outstanding?: string[] }>(
    `/api/admin/digests/${encodeURIComponent(id)}/send`, json('POST', approveBody(contentHash))),
  discard: (id: string) => call<DigestSummary>(`/api/admin/digests/${encodeURIComponent(id)}/discard`, { method: 'POST' }),
  /** Make the due drafts now (sends nothing). */
  refresh: (dryRun = false) => call<{ dry_run: boolean; rows: Array<{ scope: string; action: string; reason?: string; items?: number; recipients?: number }> }>(
    '/api/admin/digests/run', json('POST', { dry_run: dryRun })),
  log: () => call<{ log: Array<Record<string, unknown>> }>('/api/admin/digests/log'),
};
